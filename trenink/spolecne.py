"""Čisté pomocné funkce pro tréninkovou pipeline (bez torch, pyarrow a scipy)."""

from __future__ import annotations

import json
import math
import os
import random
import re
import threading
import time
import unicodedata
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence, TypeVar

T = TypeVar("T")

MIN_DURATION_S = 1.0
MAX_DURATION_S = 30.0
TARGET_SAMPLE_RATE = 16000

_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH_CHARS = {"\u200b", "\u200c", "\u200d", "\ufeff"}


def data_dir(data: Path | None = None) -> Path:
    if data is not None:
        return Path(data)
    override = os.environ.get("MLUVKA_DATA", "").strip()
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Mluvka"
    return Path.home() / "AppData" / "Local" / "Mluvka"


def trenink_dir(data: Path | None = None) -> Path:
    return data_dir(data) / "trenink"


def base_model_dir(data: Path | None = None) -> Path:
    return trenink_dir(data) / "base"


def cv_dir(data: Path | None = None) -> Path:
    return trenink_dir(data) / "cv"


def diktaty_dir(data: Path | None = None) -> Path:
    return trenink_dir(data) / "diktaty"


def history_jsonl(data: Path | None = None) -> Path:
    return data_dir(data) / "historie.jsonl"


def data_sets_dir(data: Path | None = None) -> Path:
    return trenink_dir(data) / "data"


def dataset_dir(sada: str, data: Path | None = None) -> Path:
    return data_sets_dir(data) / sada


def runs_dir(data: Path | None = None) -> Path:
    return trenink_dir(data) / "runs"


def tools_dir(data: Path | None = None) -> Path:
    return trenink_dir(data) / "tools"


def models_dir(data: Path | None = None) -> Path:
    return data_dir(data) / "models"


def vysledky_path(data: Path | None = None) -> Path:
    return trenink_dir(data) / "vysledky.json"


def doladeni_dir(data: Path | None = None) -> Path:
    return trenink_dir(data) / "doladeni"


def doladeni_stav_path(data: Path | None = None) -> Path:
    return doladeni_dir(data) / "stav.json"


def unique_run_name(base_name: str, data: Path | None = None) -> str:
    """Vrátí jedinečný název běhu, který nekoliduje s existujícím `runs/` ani modely v `models/`."""
    data_root = data_dir(data)
    rdir = runs_dir(data_root)
    mdir = models_dir(data_root)
    candidate = str(base_name)
    suffix = 2
    while (
        (rdir / candidate).exists()
        or (mdir / f"ggml-{candidate}-q8_0.bin").exists()
        or (mdir / f"ggml-{candidate}-q5_0.bin").exists()
    ):
        candidate = f"{base_name}-{suffix}"
        suffix += 1
    return candidate


def whisper_quantize_exe(data: Path | None = None) -> Path:
    return data_dir(data) / "bin" / "whisper" / "whisper-quantize.exe"


def clean_label_text(text: Any) -> str:
    """Normalizuje popisek pro Whisper: NFC, bez řídicích znaků/tabů a vícenásobných mezer."""
    if text is None:
        return ""
    raw = str(text)
    normalized = unicodedata.normalize("NFC", raw)
    cleaned_chars: list[str] = []
    for ch in normalized:
        if ch in _ZERO_WIDTH_CHARS:
            continue
        if ch in ("\t", "\r", "\n"):
            cleaned_chars.append(" ")
            continue
        if unicodedata.category(ch) == "Cc":
            continue
        cleaned_chars.append(ch)
    return _WS_RE.sub(" ", "".join(cleaned_chars)).strip()


def valid_duration(
    seconds: Any,
    *,
    min_s: float = MIN_DURATION_S,
    max_s: float = MAX_DURATION_S,
) -> bool:
    """Vrátí True, pokud délka nahrávky spadá do intervalu [min_s, max_s]."""
    if isinstance(seconds, bool):
        return False
    try:
        val = float(seconds)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(val):
        return False
    return float(min_s) <= val <= float(max_s)


def valid_clip(
    text: Any,
    seconds: Any,
    *,
    min_s: float = MIN_DURATION_S,
    max_s: float = MAX_DURATION_S,
) -> bool:
    """Ověří neprázdný text a přípustnou délku nahrávky."""
    return bool(clean_label_text(text)) and valid_duration(seconds, min_s=min_s, max_s=max_s)


def filter_entries(
    entries: Iterable[dict[str, Any]],
    *,
    min_s: float = MIN_DURATION_S,
    max_s: float = MAX_DURATION_S,
) -> list[dict[str, Any]]:
    """Vyfiltruje položky s platným textem a délkou v [min_s, max_s]."""
    kept: list[dict[str, Any]] = []
    for entry in entries:
        text = clean_label_text(entry.get("text"))
        sec = entry.get("seconds")
        if not text or not valid_duration(sec, min_s=min_s, max_s=max_s):
            continue
        kept.append(
            {
                **entry,
                "text": text,
                "seconds": round(float(sec), 3),
            }
        )
    return kept


def split_train_test(
    items: Sequence[T],
    *,
    test_every: int = 5,
) -> tuple[list[T], list[T]]:
    """Rozdělí posloupnost: každá `test_every`-tá položka (1-based) jde do testu, zbytek do tréninku."""
    if test_every < 2:
        raise ValueError("test_every musí být alespoň 2.")
    train: list[T] = []
    test: list[T] = []
    for idx, item in enumerate(items, start=1):
        if idx % test_every == 0:
            test.append(item)
        else:
            train.append(item)
    return train, test


def atomic_write_bytes(path: Path, payload: bytes) -> None:
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


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def format_manifest_line(file_ref: str, text: str, seconds: float) -> str:
    clean_file = str(file_ref).strip()
    if not clean_file or "\t" in clean_file or "\n" in clean_file or "\r" in clean_file:
        raise ValueError(f"Neplatný název souboru pro manifest: {file_ref!r}")
    clean_text = clean_label_text(text)
    if not clean_text:
        raise ValueError(f"Prázdný text pro soubor {clean_file}")
    sec = float(seconds)
    if not math.isfinite(sec) or sec < 0:
        raise ValueError(f"Neplatná délka pro soubor {clean_file}: {seconds!r}")
    return f"{clean_file}\t{clean_text}\t{sec:.3f}"


def write_manifest(
    path: Path,
    entries: Iterable[dict[str, Any] | tuple[str, str, float]],
) -> Path:
    """Zapíše manifest.tsv ve formátu `file<TAB>text<TAB>seconds`."""
    lines: list[str] = []
    for item in entries:
        if isinstance(item, dict):
            file_ref = str(item["file"])
            text = str(item["text"])
            sec = float(item["seconds"])
        else:
            file_ref, text, sec = item
        lines.append(format_manifest_line(file_ref, text, sec))
    payload = ("\n".join(lines) + "\n") if lines else ""
    atomic_write_text(path, payload)
    return path


def resolve_manifest_file(manifest_path: Path, file_ref: str) -> Path:
    p = Path(file_ref)
    if p.is_absolute():
        return p
    return manifest_path.parent / p


def read_manifest(path: Path, *, resolve_paths: bool = False) -> list[dict[str, Any]]:
    """Načte manifest.tsv (`file<TAB>text<TAB>seconds`)."""
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        if len(parts) < 3:
            continue
        file_ref = parts[0].strip()
        text = clean_label_text(parts[1])
        sec_str = parts[2].strip()
        if file_ref.lower() == "file" and text.lower() == "text" and sec_str.lower() == "seconds":
            continue
        try:
            seconds = float(sec_str)
        except ValueError:
            continue
        if not file_ref or not text or not math.isfinite(seconds):
            continue
        entry: dict[str, Any] = {
            "file": file_ref,
            "text": text,
            "seconds": seconds,
        }
        if resolve_paths:
            entry["wav_path"] = resolve_manifest_file(path, file_ref)
        entries.append(entry)
    return entries


def manifest_hours(entries: Iterable[dict[str, Any]]) -> float:
    return sum(float(e.get("seconds", 0.0) or 0.0) for e in entries) / 3600.0


def read_wav_info(path: Path) -> tuple[float, int, int, int]:
    """Vrátí (seconds, framerate, channels, sampwidth) pro WAV soubor."""
    with wave.open(str(path), "rb") as wf:
        ch = int(wf.getnchannels())
        sw = int(wf.getsampwidth())
        rate = int(wf.getframerate())
        frames = int(wf.getnframes())
    sec = (frames / float(rate)) if rate > 0 else 0.0
    return sec, rate, ch, sw


def write_wav_pcm16_mono(path: Path, pcm16_le: bytes, rate: int = TARGET_SAMPLE_RATE) -> None:
    """Zapíše mono 16-bit PCM WAV atomicky přes dočasný soubor."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(rate))
            wf.writeframes(pcm16_le)
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


_CISLA_KMENY = ("jedenáct", "dvanáct", "třináct", "čtrnáct", "patnáct", "šestnáct", "sedmnáct", "osmnáct",
                "devatenáct", "dvacet", "dvacít", "třicet", "třicít", "čtyřicet", "čtyřicít", "padesát",
                "šedesát", "sedmdesát", "osmdesát", "devadesát", "tisíc", "milion", "miliard")
_CISLA_PRESNE = {"sto", "sta", "stě", "set", "stech", "stům"}


def vyradit_z_cv(text: str) -> bool:
    """Common Voice píše čísla slovy, Mluvka chce číslice: vyřadí jednoslovné klipy a věty s čísly >= 11."""
    tokens = [t.strip(".,!?;:\"„“()-").lower() for t in text.split()]
    tokens = [t for t in tokens if t]
    if len(tokens) <= 1:
        return True
    return any(t in _CISLA_PRESNE or t.startswith(_CISLA_KMENY) for t in tokens)


def prepare_target_sets(jen_vety: bool) -> tuple[str, ...]:
    """Vrátí názvy datasetů, které má priprav_data.py připravit."""
    if jen_vety:
        return ("vety_train", "vety_test")
    return ("cv_train", "cv_test", "vety_train", "vety_test")


def select_cv_entries(
    cv_rows: Sequence[T],
    *,
    cv_limit: int | None = None,
    seed: int = 42,
) -> list[T]:
    """Vybere nejvýše `cv_limit` položek z `cv_rows` (None = vše, 0 = žádné)."""
    if cv_limit is None:
        return list(cv_rows)
    limit = int(cv_limit)
    if limit < 0:
        raise ValueError("cv_limit nesmí být záporné.")
    if limit == 0:
        return []
    rows = list(cv_rows)
    if limit >= len(rows):
        return rows
    rng = random.Random(int(seed))
    return rng.sample(rows, limit)


def build_training_entries(
    data_root: Path,
    *,
    vety_mult: int = 5,
    cv_limit: int | None = None,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], int, int]:
    """Načte cv_train a vety_train a smíchá je s volitelným limitem na CV klipy."""
    if cv_limit is not None:
        limit = int(cv_limit)
        if limit < 0:
            raise ValueError("cv_limit nesmí být záporné.")
        if limit == 0:
            cv_selected: list[dict[str, Any]] = []
        else:
            cv_manifest = dataset_dir("cv_train", data_root) / "manifest.tsv"
            cv_rows = read_manifest(cv_manifest, resolve_paths=True)
            cv_selected = select_cv_entries(cv_rows, cv_limit=limit, seed=seed)
    else:
        cv_manifest = dataset_dir("cv_train", data_root) / "manifest.tsv"
        cv_rows = read_manifest(cv_manifest, resolve_paths=True)
        cv_selected = list(cv_rows)

    vety_manifest = dataset_dir("vety_train", data_root) / "manifest.tsv"
    vety_rows = read_manifest(vety_manifest, resolve_paths=True)

    combined = list(cv_selected)
    if vety_rows and vety_mult > 0:
        combined.extend(vety_rows * int(vety_mult))
    return combined, len(cv_selected), len(vety_rows)


def _parse_iso_timestamp(raw_cas: str) -> float:
    raw = str(raw_cas or "").strip()
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.timestamp()


def safe_clip_stem(raw_id: Any, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", str(raw_id or ""))
    return cleaned or fallback


def load_history_entries_for_training(data: Path | None = None) -> list[dict[str, Any]]:
    path = history_jsonl(data)
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def select_and_split_diktaty(
    entries: Iterable[dict[str, Any]],
    diktaty_root: Path,
    *,
    test_every: int = 5,
    min_s: float = MIN_DURATION_S,
    max_s: float = MAX_DURATION_S,
) -> dict[str, Any]:
    """Vybere ověřené diktáty (<= max_s), seřadí je podle data a rozdělí do train/test."""
    accepted: list[tuple[tuple[float, int], dict[str, Any]]] = []
    skipped_long = 0
    skipped_short = 0
    verified_s = 0.0
    used_stems: set[str] = set()

    for idx, entry in enumerate(entries):
        if entry.get("overeno") is not True:
            continue
        label = clean_label_text(entry.get("overeno_text"))
        if not label:
            continue
        zvuk_raw = str(entry.get("zvuk", "") or "").strip()
        if not zvuk_raw:
            continue
        wav_name = Path(zvuk_raw).name
        if not wav_name or wav_name in {".", ".."}:
            continue
        wav_path = diktaty_root / wav_name
        if not wav_path.is_file():
            continue

        dur: float | None = None
        try:
            dur, _, _, _ = read_wav_info(wav_path)
        except Exception:
            try:
                fallback = float(entry.get("audio_s", -1.0) or -1.0)
                if math.isfinite(fallback) and fallback >= 0.0:
                    dur = fallback
            except (TypeError, ValueError):
                dur = None

        if dur is not None:
            if dur > float(max_s):
                skipped_long += 1
                continue
            if dur < float(min_s):
                skipped_short += 1
                continue
            verified_s += dur

        base_stem = safe_clip_stem(entry.get("id"), f"diktat-{idx}")
        stem = base_stem
        suffix = 2
        while stem in used_stems:
            stem = f"{base_stem}-{suffix}"
            suffix += 1
        used_stems.add(stem)

        cas = str(entry.get("cas", "") or "")
        sort_key = (_parse_iso_timestamp(cas), idx)
        accepted.append(
            (
                sort_key,
                {
                    "id": str(entry.get("id", "") or stem),
                    "stem": stem,
                    "cas": cas,
                    "text": label,
                    "wav_path": wav_path,
                    "seconds": round(float(dur), 3) if dur is not None else 0.0,
                },
            )
        )

    accepted.sort(key=lambda pair: pair[0])
    ordered = [item for _, item in accepted]
    train_items, test_items = split_train_test(ordered, test_every=test_every)
    return {
        "train": train_items,
        "test": test_items,
        "total_verified": len(ordered),
        "skipped_long": skipped_long,
        "skipped_short": skipped_short,
        "verified_s": round(verified_s, 3),
        "verified_minutes": verified_s / 60.0,
    }


def collect_verified_diktaty(
    data_root: Path,
    *,
    test_every: int = 5,
    min_s: float = MIN_DURATION_S,
    max_s: float = MAX_DURATION_S,
) -> dict[str, Any]:
    """Načte historii z data_root a vrátí rozdělené ověřené diktáty pro připrav_data."""
    entries = load_history_entries_for_training(data_root)
    return select_and_split_diktaty(
        entries,
        diktaty_dir(data_root),
        test_every=test_every,
        min_s=min_s,
        max_s=max_s,
    )


def build_combined_training_entries(
    data_root: Path,
    *,
    vety_mult: int = 5,
    diktaty_mult: int = 3,
    cv_limit: int | None = None,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Načte cv_train, vety_train a diktaty_train a smíchá je s převzorkováním."""
    combined, cv_cnt, vety_cnt = build_training_entries(
        data_root,
        vety_mult=vety_mult,
        cv_limit=cv_limit,
        seed=seed,
    )
    diktaty_manifest = dataset_dir("diktaty_train", data_root) / "manifest.tsv"
    diktaty_rows = read_manifest(diktaty_manifest, resolve_paths=True)
    if diktaty_rows and int(diktaty_mult) > 0:
        combined.extend(diktaty_rows * int(diktaty_mult))
    return combined, cv_cnt, vety_cnt, len(diktaty_rows)

