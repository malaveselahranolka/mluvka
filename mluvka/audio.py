"""Záznam zvuku ze vstupního zařízení a přehrávání zvukových signálů."""

from __future__ import annotations

import logging
import math
import threading
import wave
import winsound
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import sounddevice as sd

from mluvka import config

log = logging.getLogger("mluvka.audio")

TARGET_SAMPLE_RATE = 16000
BLOCK_DURATION_S = 0.030
MAX_RECORDING_S = 20 * 60


def rms_to_level(rms: float) -> float:
    """Převede RMS hodnotu bloku na úroveň 0..1 podle mapování -60..-12 dBFS."""
    if rms <= 1e-10:
        return 0.0
    db = 20.0 * math.log10(rms)
    level = (db + 60.0) / 48.0
    return max(0.0, min(1.0, level))


def resample_to_16k(audio: np.ndarray, src_rate: int) -> np.ndarray:
    """Převzorkuje mono float32 signál na 16 kHz."""
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if src_rate == TARGET_SAMPLE_RATE or len(arr) == 0:
        return arr.astype(np.float32, copy=False)
    # shortcut: lineární numpy interpolace místo scipy (žádné další závislosti)
    if src_rate > TARGET_SAMPLE_RATE:
        win = max(1, int(round(src_rate / TARGET_SAMPLE_RATE)))
        if win > 1 and len(arr) >= win:
            kernel = np.ones(win, dtype=np.float32) / float(win)
            arr = np.convolve(arr, kernel, mode="same")
    n_out = max(1, int(round(len(arr) * TARGET_SAMPLE_RATE / float(src_rate))))
    xp = np.linspace(0.0, 1.0, num=len(arr), endpoint=False)
    x = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x, xp, arr).astype(np.float32)


def list_input_devices() -> list[str]:
    """Vrátí vstupní zařízení výchozího host API."""
    try:
        default_api = sd.default.hostapi
        devices = sd.query_devices()
    except Exception as exc:
        log.warning("Nepodařilo se načíst zvuková zařízení: %s", exc)
        return []
    names: list[str] = []
    seen: set[str] = set()
    for dev in devices:
        if dev.get("hostapi") == default_api and int(dev.get("max_input_channels", 0)) > 0:
            name = str(dev.get("name", "")).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def resolve_input_device(name_substr: str) -> int | None:
    """Najde index vstupního zařízení podle podřetězce názvu (prázdný = výchozí)."""
    needle = (name_substr or "").strip().casefold()
    if not needle:
        return None
    try:
        default_api = sd.default.hostapi
        devices = sd.query_devices()
    except Exception:
        return None

    for api_filter in (default_api, None):
        for idx, dev in enumerate(devices):
            if int(dev.get("max_input_channels", 0)) <= 0:
                continue
            if api_filter is not None and dev.get("hostapi") != api_filter:
                continue
            dev_name = str(dev.get("name", "")).strip().casefold()
            if dev_name == needle:
                return idx
        for idx, dev in enumerate(devices):
            if int(dev.get("max_input_channels", 0)) <= 0:
                continue
            if api_filter is not None and dev.get("hostapi") != api_filter:
                continue
            dev_name = str(dev.get("name", "")).strip().casefold()
            if needle in dev_name:
                return idx
    log.warning("Vybraný mikrofon nebyl nalezen, použit výchozí systémový vstup.")
    return None


class Recorder:
    """Nahrává mono audio do paměti pouze během aktivního diktování."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._samplerate: int = TARGET_SAMPLE_RATE
        self._recorded_samples: int = 0
        self._max_samples: int = MAX_RECORDING_S * TARGET_SAMPLE_RATE
        self._limit_triggered = False
        self._session_id: int = 0

    @property
    def session_id(self) -> int:
        with self._lock:
            return self._session_id

    def start(
        self,
        mikrofon: str = "",
        on_level: Callable[[float], None] | None = None,
        on_max_duration: Callable[[], None] | None = None,
    ) -> None:
        with self._lock:
            self._close_locked()
            self._session_id += 1
            self._chunks = []
            self._recorded_samples = 0
            self._limit_triggered = False

            dev_idx = resolve_input_device(mikrofon)

            def _callback(indata: np.ndarray, frames: int, _time_info: Any, status: Any) -> None:
                if status:
                    log.debug("Stav audio streamu: %s", status)
                mono = np.array(indata[:, 0], dtype=np.float32, copy=True)
                self._chunks.append(mono)
                self._recorded_samples += len(mono)
                if on_level is not None and len(mono) > 0:
                    rms = float(np.sqrt(np.mean(mono * mono)))
                    on_level(rms_to_level(rms))
                if (
                    on_max_duration is not None
                    and not self._limit_triggered
                    and self._recorded_samples >= self._max_samples
                ):
                    self._limit_triggered = True
                    # Spustit mimo PortAudio callback vlákno, jinak Pa_StopStream zablokuje sám sebe.
                    threading.Thread(
                        target=on_max_duration,
                        name="mluvka-max-dur",
                        daemon=True,
                    ).start()

            stream = None
            try:
                self._samplerate = TARGET_SAMPLE_RATE
                self._max_samples = MAX_RECORDING_S * TARGET_SAMPLE_RATE
                stream = sd.InputStream(
                    samplerate=TARGET_SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=480,
                    device=dev_idx,
                    callback=_callback,
                )
                stream.start()
            except Exception:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                info = sd.query_devices(dev_idx, "input")
                native_sr = int(round(float(info.get("default_samplerate") or 44100)))
                self._samplerate = native_sr
                self._max_samples = MAX_RECORDING_S * native_sr
                stream = sd.InputStream(
                    samplerate=native_sr,
                    channels=1,
                    dtype="float32",
                    blocksize=max(1, int(round(native_sr * BLOCK_DURATION_S))),
                    device=dev_idx,
                    callback=_callback,
                )
                try:
                    stream.start()
                except Exception:
                    try:
                        stream.close()
                    except Exception:
                        pass
                    raise
            self._stream = stream

    def snapshot(self, start_index: int = 0, *, session_id: int | None = None) -> np.ndarray:
        """Vrátí kopii dosud zachycených 16kHz mono float32 vzorků od start_index bez rušení callbacku."""
        start = max(0, int(start_index))
        with self._lock:
            if session_id is not None and session_id != self._session_id:
                return np.zeros(0, dtype=np.float32)
            chunks = list(self._chunks)
            sr = self._samplerate
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        if sr == TARGET_SAMPLE_RATE:
            parts: list[np.ndarray] = []
            pos = 0
            for ch in chunks:
                end = pos + len(ch)
                if end > start:
                    s = max(0, start - pos)
                    parts.append(ch[s:])
                pos = end
            if not parts:
                return np.zeros(0, dtype=np.float32)
            if len(parts) == 1:
                return parts[0].astype(np.float32, copy=True)
            return np.concatenate(parts).astype(np.float32, copy=False)
        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        resampled = resample_to_16k(audio, sr)
        if start >= len(resampled):
            return np.zeros(0, dtype=np.float32)
        return resampled[start:].astype(np.float32, copy=True)

    def stop(self) -> np.ndarray:
        with self._lock:
            self._close_locked()
            self._session_id += 1
            chunks = self._chunks
            sr = self._samplerate
            self._chunks = []
            self._recorded_samples = 0
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        return resample_to_16k(audio, sr)

    def cancel(self) -> None:
        with self._lock:
            self._close_locked()
            self._session_id += 1
            self._chunks = []
            self._recorded_samples = 0

    def _close_locked(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None


def _write_tone_wav(path: Path, f0: float, f1: float) -> None:
    sr = 44100
    dur = 0.070
    n = int(round(sr * dur))
    t = np.arange(n, dtype=np.float64) / float(sr)
    phase = 2.0 * math.pi * (f0 * t + 0.5 * (f1 - f0) / dur * (t * t))
    sig = np.sin(phase)

    fade = max(1, int(round(sr * 0.005)))
    env = np.ones(n, dtype=np.float64)
    ramp = np.linspace(0.0, 1.0, fade, endpoint=True)
    env[:fade] = ramp
    env[-fade:] = ramp[::-1]
    sig *= env

    peak = float(np.max(np.abs(sig))) or 1.0
    amp = 32767.0 * (10.0 ** (-18.0 / 20.0))
    pcm = np.round(sig / peak * amp).astype("<i2")

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def ensure_sounds(data: Path | None = None) -> tuple[Path, Path]:
    sdir = config.sounds_dir_path(data)
    start_path = sdir / "start.wav"
    stop_path = sdir / "stop.wav"
    if not start_path.exists():
        _write_tone_wav(start_path, 880.0, 1320.0)
    if not stop_path.exists():
        _write_tone_wav(stop_path, 1320.0, 880.0)
    return start_path, stop_path


def play_sound(
    kind: Literal["start", "stop"],
    enabled: bool,
    data: Path | None = None,
) -> None:
    if not enabled:
        return
    try:
        start_path, stop_path = ensure_sounds(data)
        target = start_path if kind == "start" else stop_path
        winsound.PlaySound(str(target), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as exc:
        log.debug("Přehrání zvuku selhalo: %s", exc)
