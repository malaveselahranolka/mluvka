"""Lokální ASR přes whisper-server.exe a čisté funkce pro přípravu audia."""

from __future__ import annotations

import io
import json
import logging
import math
import os
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from collections.abc import Callable
from typing import IO

import numpy as np

if sys.platform == "darwin":
    from mluvka import config, winproc_mac as winproc
else:
    from mluvka import config, winproc

log = logging.getLogger("mluvka.asr")

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480  # 30 ms při 16 kHz
MAX_CHUNK_SAMPLES = 28 * SAMPLE_RATE
MIN_CUT_SAMPLES = 18 * SAMPLE_RATE
SILENCE_FLOOR_DBFS = -55.0

_HALLUCINATIONS = {
    "titulky vytvořil johnyx",
    "děkuji za pozornost",
    "díky za pozornost",
    "titulky",
    "konec",
    "www.hradeckesluzby.cz",
}
_STRIP_PUNCT = string.punctuation + "„“‚‘—–…«» \t\r\n"


def calc_audio_ctx(num_samples: int) -> int:
    """Vypočte audio_ctx = ceil(samples/320) + 32, zaokrouhleno nahoru na násobek 16, max 1500."""
    base = math.ceil(max(0, int(num_samples)) / 320) + 32
    ctx = ((base + 15) // 16) * 16
    return min(1500, ctx)


def peak_block_rms_dbfs(audio: np.ndarray, block_samples: int = FRAME_SAMPLES) -> float:
    """Vrátí maximální RMS úroveň přes 30ms bloky v dBFS."""
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(arr) == 0:
        return -math.inf
    n_full = len(arr) // block_samples
    peak_sq = 0.0
    if n_full > 0:
        blocks = arr[: n_full * block_samples].reshape(n_full, block_samples)
        peak_sq = float(np.max(np.mean(blocks * blocks, axis=1)))
    rem = arr[n_full * block_samples :]
    if len(rem) > 0:
        peak_sq = max(peak_sq, float(np.mean(rem * rem)))
    rms = math.sqrt(max(0.0, peak_sq))
    if rms <= 1e-10:
        return -math.inf
    return 20.0 * math.log10(rms)


def trim_silence(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    pad_ms: int = 200,
) -> np.ndarray:
    """Ořízne úvodní a koncové ticho při zachování 200ms okraje."""
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(arr) == 0:
        return arr
    frame = max(1, int(round(sample_rate * 0.030)))
    n_frames = math.ceil(len(arr) / frame)
    padded = np.zeros(n_frames * frame, dtype=np.float32)
    padded[: len(arr)] = arr
    rms = np.sqrt(np.mean(padded.reshape(n_frames, frame) ** 2, axis=1))
    peak = float(np.max(rms))
    if peak <= 1e-10:
        return arr
    thresh = min(10.0 ** (-50.0 / 20.0), max(10.0 ** (-60.0 / 20.0), peak * 0.05))
    active = np.flatnonzero(rms >= thresh)
    if len(active) == 0:
        return arr
    pad_samples = int(round(sample_rate * (pad_ms / 1000.0)))
    start = max(0, int(active[0]) * frame - pad_samples)
    end = min(len(arr), (int(active[-1]) + 1) * frame + pad_samples)
    return arr[start:end]


def find_cut_point(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> int:
    """Najde nejtišší 30ms rámec (300ms klouzavý průměr) v intervalu [18 s, 28 s]."""
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    max_samples = 28 * sample_rate
    min_cut = 18 * sample_rate
    frame = int(round(0.030 * sample_rate))
    ma_frames = max(1, int(round(0.300 / 0.030)))

    if len(arr) <= max_samples:
        return len(arr)

    seg = arr[:max_samples]
    n_frames = len(seg) // frame
    frame_energy = np.mean(
        seg[: n_frames * frame].reshape(n_frames, frame).astype(np.float64) ** 2,
        axis=1,
    )
    ones = np.ones(ma_frames, dtype=np.float64)
    valid_counts = np.convolve(np.ones(n_frames, dtype=np.float64), ones, mode="same")
    ma = np.convolve(frame_energy, ones, mode="same") / np.maximum(valid_counts, 1.0)
    min_idx = min_cut // frame
    max_idx = min(n_frames - 1, max_samples // frame)
    best_idx = min_idx + int(np.argmin(ma[min_idx : max_idx + 1]))
    return min(max_samples, max(min_cut, best_idx * frame + frame // 2))


def split_chunks(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> list[np.ndarray]:
    """Rozdělí audio delší než 28 s v nejtišším 30ms rámci (300ms klouzavý průměr) v intervalu [18 s, 28 s]."""
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    max_samples = 28 * sample_rate

    if len(arr) <= max_samples:
        return [arr]

    chunks: list[np.ndarray] = []
    pos = 0
    while pos < len(arr):
        remaining = len(arr) - pos
        if remaining <= max_samples:
            chunks.append(arr[pos:])
            break
        cut = find_cut_point(arr[pos:], sample_rate)
        chunks.append(arr[pos : pos + cut])
        pos += cut

    return chunks


PROMPT_MAX_ZNAKU = 120


def kratky_prompt(slova: list[str], max_znaku: int = PROMPT_MAX_ZNAKU) -> str:
    """Nápověda pro whisper z prvních slov slovníku, nejvýš max_znaku znaků.

    Dlouhá nápověda (desítky slov) CzechSpeech rozbije: přepis 29 s řeči skončil po 8 znacích.
    Celý slovník dostává Haiku, takže tady stačí krátký výběr.
    """
    out: list[str] = []
    delka = 0
    for w in slova:
        w = str(w).strip()
        if not w:
            continue
        pridat = len(w) + (2 if out else 0)
        if delka + pridat > max_znaku:
            break
        out.append(w)
        delka += pridat
    return ", ".join(out)


def is_hallucination(text: str) -> bool:
    """True pokud celý výstup chunku odpovídá známé české Whisper halucinaci."""
    cleaned = " ".join(text.strip().strip(_STRIP_PUNCT).split()).casefold()
    return cleaned in _HALLUCINATIONS


def encode_wav_pcm16(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    clipped = np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = np.round(clipped * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def build_multipart_form(
    fields: dict[str, str],
    file_field: tuple[str, str, bytes, str],
) -> tuple[bytes, str]:
    boundary = f"----MluvkaBoundary{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    field_name, filename, data, content_type = file_field
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


class WhisperServer:
    """Řídí životní cyklus lokálního whisper-server.exe a HTTP přepisy."""

    def __init__(
        self,
        data: Path | None = None,
        *,
        exe_path: Path | None = None,
        model_path: Path | None = None,
    ) -> None:
        self._data = data
        env_exe = os.environ.get("MLUVKA_WHISPER_EXE", "").strip()
        env_model = os.environ.get("MLUVKA_WHISPER_MODEL", "").strip()
        self._exe = (
            Path(exe_path)
            if exe_path is not None
            else (Path(env_exe) if env_exe else config.whisper_exe_path(data))
        )
        self._model = (
            Path(model_path)
            if model_path is not None
            else (Path(env_model) if env_model else config.whisper_model_path(data))
        )
        self._log_path = (data or config.data_dir()) / "whisper-server.log"
        self._job: winproc.JobObject | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_file: IO[str] | None = None
        self._port: int = 0
        self._infer_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def port(self) -> int:
        return self._port

    @property
    def model_path(self) -> Path:
        return self._model

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def start(self, timeout_s: float = 60.0) -> None:
        if not self._exe.is_file():
            exe_name = "whisper-server" if sys.platform == "darwin" else "whisper-server.exe"
            raise FileNotFoundError(f"Chybí {exe_name}: {self._exe}")
        if not self._model.is_file():
            raise FileNotFoundError(f"Chybí model: {self._model}")

        with self._lifecycle_lock:
            self._closed = False
            self._port = winproc.free_port()
            job = winproc.JobObject()
            cmd = [
                str(self._exe),
                "-m",
                str(self._model),
                "-l",
                "cs",
                "-nt",
                "-fa",
                "--host",
                "127.0.0.1",
                "--port",
                str(self._port),
                "-t",
                "4",
            ]
            try:
                proc, log_file = winproc.start_hidden_process(
                    cmd, self._log_path, job=job, cwd=self._exe.parent
                )
            except Exception:
                job.close()
                raise
            self._job = job
            self._proc = proc
            self._log_file = log_file

        try:
            self._wait_healthy(timeout_s=timeout_s)
            if self._closed:
                raise RuntimeError("whisper-server byl během startu uzavřen")
            self._warmup()
        except Exception:
            self.close()
            raise

    def _wait_healthy(self, timeout_s: float) -> None:
        url = f"http://127.0.0.1:{self._port}/health"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._closed:
                raise RuntimeError("whisper-server byl uzavřen")
            proc = self._proc
            if proc is None:
                raise RuntimeError("whisper-server není spuštěn")
            rc = proc.poll()
            if rc is not None:
                raise RuntimeError(f"whisper-server předčasně skončil s kódem {rc}")
            try:
                with self._opener.open(url, timeout=2.0) as resp:
                    if resp.status == 200:
                        payload = json.loads(resp.read().decode("utf-8"))
                        if payload.get("status") == "ok":
                            return
            except urllib.error.HTTPError as exc:
                if exc.code != 503:
                    log.debug("Health HTTP %s", exc.code)
            except Exception:
                pass
            time.sleep(0.25)
        raise TimeoutError("whisper-server neodpověděl /health 200 v časovém limitu")

    def _warmup(self) -> None:
        rng = np.random.default_rng(0)
        noise = rng.normal(0.0, 1e-4, SAMPLE_RATE).astype(np.float32)
        with self._infer_lock:
            self._infer_chunk(noise, prompt="")

    def _infer_chunk(self, chunk: np.ndarray, prompt: str, *, beam_size: int = 1) -> str:
        wav_bytes = encode_wav_pcm16(chunk, SAMPLE_RATE)
        fields = {
            "response_format": "json",
            "language": "cs",
            "no_timestamps": "true",
            "temperature": "0",
            "temperature_inc": "0.2",
            # Pevné audio_ctx=1500 brání rozpadu CUDA FlashAttention (-fa) mezi požadavky různé délky (WER 39 % -> 6.4 %).
            "audio_ctx": "1500",
        }
        if int(beam_size) > 1:
            fields["beam_size"] = str(int(beam_size))
        if prompt:
            fields["prompt"] = prompt
        body, content_type = build_multipart_form(
            fields, ("file", "audio.wav", wav_bytes, "audio/wav")
        )
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._port}/inference",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with self._opener.open(req, timeout=60.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return str(payload.get("text", "")).strip()

    def _resolve_beam_size(self, cfg: dict | None, beam_size: int | None) -> int:
        default_beam = int(config.DEFAULT_CONFIG.get("beam_size", 5))
        if beam_size is not None:
            if isinstance(beam_size, bool):
                raise ValueError("beam_size musí být celé číslo >= 1.")
            return max(1, int(beam_size))
        if cfg is not None:
            raw = cfg.get("beam_size", default_beam)
            if raw is None or isinstance(raw, bool):
                return default_beam
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                return default_beam
        cfg_path = config.config_path(self._data)
        if not cfg_path.is_file():
            return default_beam
        try:
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default_beam
        if not isinstance(loaded, dict) or "beam_size" not in loaded:
            return default_beam
        raw = loaded["beam_size"]
        if isinstance(raw, bool):
            return default_beam
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return default_beam

    def transcribe(
        self,
        audio_f32_16k: np.ndarray,
        *,
        cfg: dict | None = None,
        prompt: str | None = None,
        beam_size: int | None = None,
    ) -> str:
        if peak_block_rms_dbfs(audio_f32_16k) < SILENCE_FLOOR_DBFS:
            return ""
        if prompt is None and cfg is not None:
            slovnik = cfg.get("slovnik") or {}
            slova = [str(w).strip() for w in (slovnik.get("slova") or []) if str(w).strip()]
            if cfg.get("slovnik_do_promptu", True) and slova:
                prompt = kratky_prompt(slova)
            else:
                prompt = ""
        prompt = prompt or ""
        resolved_beam = self._resolve_beam_size(cfg, beam_size)

        trimmed = trim_silence(audio_f32_16k, SAMPLE_RATE)
        chunks = split_chunks(trimmed, SAMPLE_RATE)
        texts: list[str] = []
        with self._infer_lock:
            for chunk in chunks:
                out = self._infer_chunk(chunk, prompt=prompt, beam_size=resolved_beam)
                if out and not is_hallucination(out):
                    texts.append(out)
        return " ".join(texts)

    def close(self) -> None:
        self._closed = True
        with self._lifecycle_lock:
            proc = self._proc
            log_file = self._log_file
            job = self._job
            self._proc = None
            self._log_file = None
            self._job = None
        winproc.terminate_process(proc, log_file)
        if job is not None:
            job.close()


class StreamingTranscriber:
    """Průběžný přepis audia > 28 s během nahrávání se stejnými body řezu jako split_chunks.

    Pravidlo pro ořez ticha (trim_silence):
    - Krátké diktáty (<= 28 s, tj. bez předběžných řezů) jdou v celku až po uvolnění klávesy přes
      server.transcribe(), tedy identicky jako dříve.
    - Delší nahrávky se řežou po surových blocích přes find_cut_point (hranice přesně odpovídají
      split_chunks(full_audio)) a každý blok se přepíše přes server.transcribe(chunk), který na něm
      provede kontrolu prahu ticha, trim_silence (200ms okraj), _infer_chunk i filtr halucinací.
    """

    def __init__(
        self,
        server: WhisperServer | None,
        get_audio: Callable[[int], np.ndarray],
        *,
        get_server: Callable[[], WhisperServer] | None = None,
        cfg: dict | None = None,
        prompt: str | None = None,
        beam_size: int | None = None,
        poll_interval_s: float = 1.0,
        ready_event: threading.Event | None = None,
        is_ready_fn: Callable[[], bool] | None = None,
    ) -> None:
        if server is None and get_server is None:
            raise ValueError("WhisperServer nebo get_server musí být zadán.")
        self._server = server
        self._get_server_fn = get_server
        self._get_audio = get_audio
        self._cfg = cfg
        self._prompt = prompt
        self._beam_size = beam_size
        self._poll_interval_s = max(0.01, float(poll_interval_s))
        self._ready_event = ready_event
        self._is_ready_fn = is_ready_fn

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0
        self._texts: list[str] = []
        self._chunk_sizes: list[int] = []
        self._cancelled = False
        self._finished = False
        self._error: Exception | None = None

    def _get_server(self) -> WhisperServer:
        if self._get_server_fn is not None:
            return self._get_server_fn()
        assert self._server is not None
        return self._server

    @property
    def offset(self) -> int:
        with self._lock:
            return self._offset

    @property
    def texts(self) -> list[str]:
        with self._lock:
            return list(self._texts)

    @property
    def chunk_sizes(self) -> list[int]:
        with self._lock:
            return list(self._chunk_sizes)

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def error(self) -> Exception | None:
        with self._lock:
            return self._error

    @property
    def is_running(self) -> bool:
        with self._lock:
            t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._cancelled or self._finished or self._thread is not None:
                return
            t = threading.Thread(target=self._run, name="mluvka-stream-asr", daemon=True)
            self._thread = t
            t.start()

    def stop_polling(self) -> None:
        self._stop_event.set()

    def cancel(self, *, wait: bool = True, timeout: float = 2.0) -> None:
        with self._lock:
            self._cancelled = True
            self._texts.clear()
            self._chunk_sizes.clear()
            self._offset = 0
            self._error = None
            t = self._thread
        self._stop_event.set()
        if wait and t is not None and threading.current_thread() is not t:
            t.join(timeout=timeout)
            with self._lock:
                self._texts.clear()
                self._chunk_sizes.clear()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                if self._ready_event is not None and not self._ready_event.is_set():
                    self._stop_event.wait(min(0.2, self._poll_interval_s))
                    continue
                if self._is_ready_fn is not None and not self._is_ready_fn():
                    break
                self._drain_ready_chunks()
                self._stop_event.wait(self._poll_interval_s)
        except Exception as exc:
            with self._lock:
                self._texts.clear()
                self._chunk_sizes.clear()
                if not self._cancelled:
                    self._error = exc
            self._stop_event.set()

    def _drain_ready_chunks(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                if self._cancelled:
                    return
                offset = self._offset
            raw_pending = self._get_audio(offset)
            if not isinstance(raw_pending, np.ndarray):
                try:
                    pending = np.asarray(raw_pending, dtype=np.float32).reshape(-1)
                except (TypeError, ValueError):
                    break
            else:
                pending = raw_pending.reshape(-1)
            if len(pending) <= MAX_CHUNK_SAMPLES:
                break

            if self._is_ready_fn is not None and not self._is_ready_fn():
                break
            cut = find_cut_point(pending, SAMPLE_RATE)
            chunk = pending[:cut]
            out = self._get_server().transcribe(
                chunk,
                cfg=self._cfg,
                prompt=self._prompt,
                beam_size=self._beam_size,
            )
            with self._lock:
                if self._cancelled:
                    self._texts.clear()
                    self._chunk_sizes.clear()
                    return
                if out:
                    self._texts.append(out)
                self._chunk_sizes.append(cut)
                self._offset += cut

    def finish(self, full_audio: np.ndarray, *, cfg: dict | None = None) -> str:
        self._stop_event.set()
        with self._lock:
            self._finished = True
            t = self._thread
        if t is not None and threading.current_thread() is not t:
            t.join()

        with self._lock:
            if self._cancelled:
                return ""
            if self._error is not None:
                err = self._error
                self._texts.clear()
                self._chunk_sizes.clear()
                raise err
            offset = self._offset
            texts = list(self._texts)
            sizes = list(self._chunk_sizes)

        arr = np.asarray(full_audio, dtype=np.float32).reshape(-1)
        use_cfg = cfg if cfg is not None else self._cfg
        remainder = arr[offset:]

        if len(remainder) > 0:
            try:
                if offset == 0 and len(arr) <= MAX_CHUNK_SAMPLES:
                    out = self._get_server().transcribe(
                        arr,
                        cfg=use_cfg,
                        prompt=self._prompt,
                        beam_size=self._beam_size,
                    )
                    if out:
                        texts.append(out)
                    sizes.append(len(arr))
                else:
                    for sub in split_chunks(remainder, SAMPLE_RATE):
                        out = self._get_server().transcribe(
                            sub,
                            cfg=use_cfg,
                            prompt=self._prompt,
                            beam_size=self._beam_size,
                        )
                        if out:
                            texts.append(out)
                        sizes.append(len(sub))
            except Exception as exc:
                with self._lock:
                    self._texts.clear()
                    self._chunk_sizes.clear()
                    self._error = exc
                raise

        with self._lock:
            if self._cancelled:
                return ""
            self._texts = texts
            self._chunk_sizes = sizes
            self._offset = len(arr)

        return " ".join(texts)
