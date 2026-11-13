"""Trénink — nahrávání vzorových vět, ukládání diktátů a lokální měření WER."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from mluvka import asr, config, wer

SENTENCES_PATH = Path(__file__).resolve().with_name("trenink_vety.txt")
_TRENINK_LOCK = threading.Lock()


def sentence_code(index: int) -> str:
    return f"{int(index):03d}"


def load_sentences(path: Path | None = None) -> list[str]:
    target = path or SENTENCES_PATH
    if not target.is_file():
        return []
    return [
        line.strip()
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(payload)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def wav_duration_and_rate(path: Path) -> tuple[float, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"{path.name} musí být mono 16-bit PCM WAV.")
        rate = int(wf.getframerate())
        frames = int(wf.getnframes())
    seconds = (frames / float(rate)) if rate > 0 else 0.0
    return seconds, rate


def load_wav_float32(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"{path.name} musí být mono 16-bit PCM WAV.")
        rate = int(wf.getframerate())
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return samples, rate


def load_tsv(data: Path | None = None) -> dict[str, dict[str, Any]]:
    tsv_path = config.trenink_vety_tsv_path(data)
    vety_dir = config.trenink_vety_dir_path(data)
    if not tsv_path.is_file():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line in tsv_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        code = parts[0].strip()
        if not code.isdigit():
            continue
        code = f"{int(code):03d}"
        wav_path = vety_dir / f"{code}.wav"
        if not wav_path.is_file():
            continue
        sentence = parts[1].strip()
        try:
            seconds = round(float(parts[2].strip()), 2)
        except ValueError:
            seconds = 0.0
        records[code] = {
            "code": code,
            "sentence": sentence,
            "seconds": seconds,
            "wav_path": wav_path,
        }
    return records


def _write_tsv_unlocked(records: dict[str, dict[str, Any]], data: Path | None) -> None:
    lines: list[str] = []
    for code in sorted(records.keys()):
        rec = records[code]
        sent = " ".join(str(rec["sentence"]).splitlines()).replace("\t", " ").strip()
        sec = f"{float(rec['seconds']):.2f}"
        lines.append(f"{code}\t{sent}\t{sec}")
    content = ("\n".join(lines) + "\n") if lines else ""
    _atomic_write_text(config.trenink_vety_tsv_path(data), content)


def save_sentence_recording(
    index: int,
    samples: np.ndarray,
    *,
    sentence: str | None = None,
    data: Path | None = None,
) -> dict[str, Any]:
    sentences = load_sentences()
    if sentence is None:
        if index < 1 or index > len(sentences):
            raise ValueError(f"Neplatný index věty: {index}")
        sentence_text = sentences[index - 1]
    else:
        if index < 1:
            raise ValueError(f"Neplatný index věty: {index}")
        sentence_text = str(sentence).strip()

    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        raise ValueError("Nahrávka je prázdná.")

    code = sentence_code(index)
    wav_bytes = asr.encode_wav_pcm16(arr, sample_rate=asr.SAMPLE_RATE)
    wav_path = config.trenink_vety_dir_path(data) / f"{code}.wav"
    seconds = round(float(arr.size) / float(asr.SAMPLE_RATE), 2)

    with _TRENINK_LOCK:
        _atomic_write_bytes(wav_path, wav_bytes)
        records = load_tsv(data)
        records[code] = {
            "code": code,
            "sentence": sentence_text,
            "seconds": seconds,
            "wav_path": wav_path,
        }
        _write_tsv_unlocked(records, data)

    return {
        "code": code,
        "index": int(index),
        "sentence": sentence_text,
        "seconds": seconds,
        "wav": str(wav_path),
    }


def save_dictation_wav(
    entry_id: str,
    samples: np.ndarray,
    *,
    data: Path | None = None,
) -> str:
    safe_id = Path(str(entry_id)).stem
    if not safe_id or safe_id in {".", ".."}:
        raise ValueError("Neplatné id diktátu.")
    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        raise ValueError("Prázdný zvuk diktátu.")
    wav_bytes = asr.encode_wav_pcm16(arr, sample_rate=asr.SAMPLE_RATE)
    filename = f"{safe_id}.wav"
    _atomic_write_bytes(config.diktaty_dir_path(data) / filename, wav_bytes)
    return filename


def play_wav(wav_path: Path) -> bool:
    if not wav_path.is_file():
        return False
    import winsound

    winsound.PlaySound(None, winsound.SND_PURGE)
    winsound.PlaySound(str(wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    return True


def play_sentence(index: int, *, data: Path | None = None) -> bool:
    return play_wav(config.trenink_vety_dir_path(data) / f"{sentence_code(index)}.wav")


def stop_playback() -> None:
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def _enrich_worst(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        ref = str(row.get("ref", "") or "")
        hyp = str(row.get("hyp", "") or "")
        out.append(
            {
                "code": str(row.get("code", "")),
                "ref": ref,
                "hyp": hyp,
                "errors": int(row.get("errors", 0) or 0),
                "wer": float(row.get("wer", 0.0) or 0.0),
                "ref_diff": wer.diff_words(hyp, ref),
                "hyp_diff": wer.diff_words(ref, hyp),
            }
        )
    return out


def load_measurement(data: Path | None = None) -> dict[str, Any] | None:
    path = config.trenink_mereni_path(data)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    worst = raw.get("worst")
    if isinstance(worst, list):
        raw["worst"] = _enrich_worst([w for w in worst if isinstance(w, dict)])
    else:
        results = raw.get("results") or []
        bad = [
            r
            for r in results
            if isinstance(r, dict) and int(r.get("errors", 0) or 0) > 0
        ]
        bad.sort(key=lambda r: (float(r.get("wer", 0.0)), int(r.get("errors", 0))), reverse=True)
        raw["worst"] = _enrich_worst(bad[:5])
    return raw


def get_state_summary(data: Path | None = None) -> dict[str, Any]:
    sentences = load_sentences()
    records = load_tsv(data)
    recorded_map = {code: rec["seconds"] for code, rec in records.items()}
    total = len(sentences)
    next_index = 1
    for i in range(1, total + 1):
        if sentence_code(i) not in recorded_map:
            next_index = i
            break
    return {
        "sentences": sentences,
        "total": total,
        "recorded": recorded_map,
        "recorded_count": len(recorded_map),
        "next_index": next_index,
        "measurement": load_measurement(data),
    }


def measure(
    data: Path | None = None,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
    server_factory: Callable[[Path | None], Any] | None = None,
) -> dict[str, Any]:
    records = load_tsv(data)
    items = [records[code] for code in sorted(records.keys())]
    if not items:
        raise ValueError("Nejsou nahrané žádné věty.")

    total = len(items)
    factory = server_factory or (lambda d: asr.WhisperServer(d))
    server = factory(data)
    results: list[dict[str, Any]] = []
    refs: list[str] = []
    hyps: list[str] = []
    try:
        server.start(timeout_s=60.0)
        for idx, rec in enumerate(items, start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Měření bylo přerušeno.")
            samples, rate = load_wav_float32(rec["wav_path"])
            if rate != asr.SAMPLE_RATE:
                raise ValueError(f"Neplatná vzorkovací frekvence souboru {rec['code']}.wav: {rate}")
            hyp = str(server.transcribe(samples, prompt="") or "").strip()
            ref = str(rec["sentence"])
            errors, ref_len, sent_wer = wer.sentence_wer(ref, hyp)
            refs.append(ref)
            hyps.append(hyp)
            results.append(
                {
                    "code": rec["code"],
                    "ref": ref,
                    "hyp": hyp,
                    "errors": errors,
                    "ref_words": ref_len,
                    "wer": round(sent_wer, 4),
                }
            )
            if on_progress is not None:
                on_progress(idx, total)
    finally:
        server.close()

    total_wer = round(wer.compute_wer(refs, hyps), 4)
    bad = [r for r in results if r["errors"] > 0]
    bad.sort(key=lambda r: (r["wer"], r["errors"]), reverse=True)
    worst = _enrich_worst(bad[:5])

    report = {
        "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "n": total,
        "wer": total_wer,
        "results": results,
        "worst": worst,
    }
    _atomic_write_text(
        config.trenink_mereni_path(data),
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    return report


SADA_LABELS: dict[str, str] = {
    "cv_test": "Common Voice",
    "fleurs": "FLEURS",
    "vety_test": "Vlastní věty",
    "diktaty_test": "Vlastní diktáty",
}
_SADA_ORDER = ("cv_test", "fleurs", "vety_test", "diktaty_test")


def parse_vysledky_latest(data: Path | None = None, beam: Any = None) -> dict[str, dict[str, dict[str, Any]]]:
    """Načte DATA/trenink/vysledky.json a vrátí nejnovější záznam pro každé (model, sada).

    Se zadaným beam mají přednost měření se stejným beam, jaký používá aplikace (pokusy s jiným beam ho nepřepíšou)."""
    path = config.trenink_vysledky_path(data)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, list):
        return {}

    best: dict[tuple[str, str], tuple[tuple[Any, ...], dict[str, Any]]] = {}
    local_tz = datetime.now().astimezone().tzinfo
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        model_raw = str(entry.get("model", "") or "").strip()
        sada = str(entry.get("sada", "") or "").strip()
        if not model_raw or not sada:
            continue
        model_name = Path(model_raw).name
        try:
            wer_val = float(entry["wer"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(wer_val):
            continue
        datum = str(entry.get("datum", "") or "")
        try:
            dt = datetime.fromisoformat(datum)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_tz)
            sort_key = (1, dt.timestamp(), datum, idx)
        except ValueError:
            sort_key = (0, 0.0, datum, idx)
        sort_key = (int(beam is None or str(entry.get("beam")) == str(beam)), *sort_key)
        prev = best.get((model_name, sada))
        if prev is None or sort_key >= prev[0]:
            best[(model_name, sada)] = (
                sort_key,
                {
                    "datum": datum,
                    "wer": wer_val,
                    "n": entry.get("n"),
                    "beam": entry.get("beam"),
                    "sekundy": entry.get("sekundy"),
                },
            )

    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for (model_name, sada), (_key, info) in best.items():
        grouped.setdefault(model_name, {})[sada] = info
    return grouped


def format_model_wer(metrics_by_sada: dict[str, dict[str, Any]] | None) -> str:
    """Zformátuje WER výsledky modelu jako 'Common Voice: 7,1 % · FLEURS: 6,4 %'."""
    if not metrics_by_sada:
        return ""
    ordered_keys = [s for s in _SADA_ORDER if s in metrics_by_sada]
    extra_keys = sorted(s for s in metrics_by_sada if s not in _SADA_ORDER)
    parts: list[str] = []
    for sada in (*ordered_keys, *extra_keys):
        info = metrics_by_sada[sada]
        label = SADA_LABELS.get(sada, sada)
        pct = f"{float(info['wer']) * 100.0:.1f}".replace(".", ",")
        parts.append(f"{label}: {pct} %")
    return " · ".join(parts)


def list_models(
    data: Path | None = None,
    *,
    selected_soubor: str | None = None,
    beam: Any = None,
) -> list[dict[str, Any]]:
    """Vrátí seznam dostupných ggml modelů v DATA/models/*.bin s posledním WER."""
    models_dir = config.models_dir_path(data)
    if not models_dir.is_dir():
        return []
    bins = [p for p in models_dir.glob("*.bin") if p.is_file()]
    bins.sort(key=lambda p: (0 if p.name == config.DEFAULT_MODEL_SOUBOR else 1, p.name.lower()))

    try:
        active_name = config.whisper_model_path(data, model_soubor=selected_soubor).name
    except ValueError:
        active_name = config.DEFAULT_MODEL_SOUBOR

    latest = parse_vysledky_latest(data, beam)
    out: list[dict[str, Any]] = []
    for p in bins:
        soubor = p.name
        vychozi = soubor == config.DEFAULT_MODEL_SOUBOR
        metrics = latest.get(soubor, {})
        try:
            size_mb = round(p.stat().st_size / (1024 * 1024), 1)
        except OSError:
            size_mb = 0.0
        out.append(
            {
                "soubor": soubor,
                "nazev": _nazev_modelu(soubor, vychozi),
                "vychozi": vychozi,
                "vybrany": soubor == active_name,
                "wer_text": format_model_wer(metrics),
                "vysledky": metrics,
                "velikost_mb": size_mb,
            }
        )
    return out


def get_models_state(
    data: Path | None = None,
    *,
    selected_soubor: str | None = None,
    beam: Any = None,
) -> dict[str, Any]:
    """Vrátí seznam modelů i název aktuálně aktivního souboru."""
    models = list_models(data, selected_soubor=selected_soubor, beam=beam)
    active = next((m["soubor"] for m in models if m["vybrany"]), config.DEFAULT_MODEL_SOUBOR)
    return {"models": models, "aktivni": active}


_ZNAME_MODELY = {
    "ggml-czechspeech-q8_0.bin": "CzechSpeech q8_0",
    "ggml-cv1-q5_0.bin": "Doladěný na Common Voice (q5_0)",
    "ggml-cv1-q8_0.bin": "Doladěný na Common Voice (q8_0)",
}
_OSOBNI_MODEL_RE = re.compile(r"^ggml-osobni-(\d{4})(\d{2})(\d{2})(?:-\d{4})?-[^/\\]+\.bin$")


def _nazev_modelu(soubor: str, vychozi: bool) -> str:
    if vychozi:
        return "CzechSpeech q5_0"
    match = _OSOBNI_MODEL_RE.match(soubor)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            dt = datetime(year, month, day)
            return f"Doladěný na tvém hlase ({dt.day}. {dt.month}.)"
        except ValueError:
            pass
    return _ZNAME_MODELY.get(soubor, soubor)

