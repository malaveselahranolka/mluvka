"""Lokální historie přepisů v JSONL a export do Markdownu."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mluvka import config

MAX_VERIFIED_TEXT_CHARS = 5000


def entry_text(entry: dict[str, Any]) -> str:
    """Vrátí zobrazený text záznamu (ručně opravený, nebo původní přepis)."""
    opraveno = entry.get("opraveno")
    if isinstance(opraveno, str) and opraveno.strip():
        return opraveno.strip()
    return str(entry.get("text", "") or "")


def _parse_entry_dt(raw_cas: str, tzinfo: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw_cas)
    except ValueError:
        return None
    if dt.tzinfo is None and tzinfo is not None:
        return dt.replace(tzinfo=tzinfo)
    if dt.tzinfo is not None and tzinfo is None:
        return dt.replace(tzinfo=None)
    return dt


_HISTORY_LOCK = threading.Lock()


def _unlink_history_md(data: Path | None) -> None:
    config.history_md_path(data).unlink(missing_ok=True)


def _unlink_entry_audio(entry: dict[str, Any], data: Path | None) -> None:
    raw = str(entry.get("zvuk", "") or "").strip()
    if not raw:
        return
    name = Path(raw).name
    if not name or name in {".", ".."}:
        return
    audio_path = config.diktaty_dir_path(data) / name
    audio_path.unlink(missing_ok=True)


def _dictation_wav_path(entry: dict[str, Any], data: Path | None) -> Path | None:
    raw = str(entry.get("zvuk", "") or "").strip()
    if not raw:
        return None
    name = Path(raw).name
    if not name or name in {".", ".."}:
        return None
    wav_path = config.diktaty_dir_path(data) / name
    return wav_path if wav_path.is_file() else None


def _entry_timestamp(entry: dict[str, Any]) -> float:
    raw = str(entry.get("cas", "") or "").strip()
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.timestamp()


def _rewrite_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    # shortcut: souběžný zápis z tray procesu během přepisu přes temp+replace se může ztratit;
    # při potřebě nahradit zámkem souboru. V rámci jednoho procesu serializováno přes _HISTORY_LOCK.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
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


def _load_entries_unlocked(data: Path | None = None) -> list[dict[str, Any]]:
    path = config.history_jsonl_path(data)
    if not path.exists():
        return []
    raw_text = ""
    for attempt in range(3):
        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
            break
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.02)
    entries: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
        except json.JSONDecodeError:
            continue
    return entries


def strip_diacritics(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    ).casefold()


def append_entry(
    text: str,
    surovy: str,
    audio_s: float,
    prepis_s: float,
    aplikace: str,
    *,
    cas: datetime | None = None,
    entry_id: str | None = None,
    zvuk: str = "",
    prepis: str = "",
    data: Path | None = None,
) -> dict[str, Any]:
    ts = (cas or datetime.now().astimezone()).isoformat(timespec="seconds")
    uid = entry_id or uuid.uuid4().hex
    entry: dict[str, Any] = {
        "id": uid,
        "cas": ts,
        "text": text,
        "surovy": surovy,
        "audio_s": round(float(audio_s), 2),
        "prepis_s": round(float(prepis_s), 2),
        "aplikace": aplikace,
    }
    zvuk_name = Path(zvuk).name if str(zvuk or "").strip() else ""
    if zvuk_name:
        entry["zvuk"] = zvuk_name
    prepis_clean = str(prepis or "").strip()
    if prepis_clean:
        entry["prepis"] = prepis_clean
    path = config.history_jsonl_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _HISTORY_LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_entries(data: Path | None = None) -> list[dict[str, Any]]:
    with _HISTORY_LOCK:
        return _load_entries_unlocked(data)


def query_entries(
    query: str = "",
    offset: int = 0,
    limit: int = 100,
    *,
    data: Path | None = None,
) -> tuple[list[dict[str, Any]], int]:
    entries = load_entries(data)
    entries.reverse()
    needle = strip_diacritics(query.strip()) if query else ""
    if needle:
        entries = [
            e for e in entries if needle in strip_diacritics(entry_text(e))
        ]
    total = len(entries)
    start = max(0, int(offset))
    count = max(0, int(limit))
    return entries[start : start + count], total


def _find_entry_index(
    entries: list[dict[str, Any]],
    entry_id: str = "",
    *,
    cas: str = "",
    text: str | None = None,
) -> int:
    if entry_id:
        for idx in range(len(entries) - 1, -1, -1):
            if str(entries[idx].get("id", "")) == entry_id:
                return idx
        # Zpětná kompatibilita pro volání delete_entry(cas, data=...) nad starými záznamy bez id.
        if not cas and text is None:
            for idx in range(len(entries) - 1, -1, -1):
                if not entries[idx].get("id") and str(entries[idx].get("cas", "")) == entry_id:
                    return idx
        return -1

    if not cas:
        return -1
    for idx in range(len(entries) - 1, -1, -1):
        entry = entries[idx]
        if entry.get("id"):
            continue
        if str(entry.get("cas", "")) != cas:
            continue
        if text is None or str(entry.get("text", "")) == text:
            return idx
    return -1


def delete_entry(
    entry_id: str = "",
    *,
    cas: str = "",
    text: str | None = None,
    data: Path | None = None,
) -> bool:
    path = config.history_jsonl_path(data)
    if not entry_id and not cas:
        return False
    with _HISTORY_LOCK:
        if not path.exists():
            return False
        entries = _load_entries_unlocked(data)
        match_idx = _find_entry_index(entries, entry_id, cas=cas, text=text)
        if match_idx < 0:
            return False
        removed = entries[match_idx]
        kept = entries[:match_idx] + entries[match_idx + 1 :]
        _rewrite_entries(path, kept)
        _unlink_history_md(data)
        _unlink_entry_audio(removed, data)
        return True


def update_entry_with_previous(
    entry_id: str = "",
    new_text: str = "",
    *,
    cas: str = "",
    text: str | None = None,
    data: Path | None = None,
) -> tuple[dict[str, Any], str] | None:
    cleaned = str(new_text).strip()
    if not cleaned:
        raise ValueError("Text nesmí být prázdný.")
    path = config.history_jsonl_path(data)
    if not entry_id and not cas:
        return None
    with _HISTORY_LOCK:
        if not path.exists():
            return None
        entries = _load_entries_unlocked(data)
        match_idx = _find_entry_index(entries, entry_id, cas=cas, text=text)
        if match_idx < 0:
            return None
        target = entries[match_idx]
        prev = entry_text(target)
        target["opraveno"] = cleaned
        _rewrite_entries(path, entries)
        _unlink_history_md(data)
        return copy.deepcopy(target), prev


def update_text(
    entry_id: str,
    new_text: str,
    *,
    cas: str = "",
    text: str | None = None,
    data: Path | None = None,
) -> dict[str, Any] | None:
    res = update_entry_with_previous(
        entry_id, new_text, cas=cas, text=text, data=data
    )
    return res[0] if res is not None else None


def clear_all(*, data: Path | None = None) -> int:
    path = config.history_jsonl_path(data)
    with _HISTORY_LOCK:
        entries = _load_entries_unlocked(data) if path.exists() else []
        count = len(entries)
        for entry in entries:
            _unlink_entry_audio(entry, data)
        diktaty_dir = config.diktaty_dir_path(data)
        if diktaty_dir.is_dir():
            for wav_file in diktaty_dir.glob("*.wav"):
                wav_file.unlink(missing_ok=True)
        if path.exists():
            _rewrite_entries(path, [])
        _unlink_history_md(data)
        return count


def stats(
    days: int = 7,
    *,
    now: datetime | None = None,
    data: Path | None = None,
) -> dict[str, Any]:
    if days <= 0:
        return {"words": 0, "count": 0, "audio_s": 0.0, "saved_s": 0.0, "wpm": 0.0}
    ref_now = now or datetime.now().astimezone()
    cutoff = ref_now - timedelta(days=days)
    words = 0
    count = 0
    audio_s = 0.0
    for entry in load_entries(data):
        dt = _parse_entry_dt(str(entry.get("cas", "")), cutoff.tzinfo)
        if dt is None or dt < cutoff:
            continue
        count += 1
        words += len(entry_text(entry).split())
        try:
            audio_s += max(0.0, float(entry.get("audio_s", 0.0) or 0.0))
        except (TypeError, ValueError):
            pass
    saved_s = max(0.0, (words / 40.0) * 60.0 - audio_s)
    wpm = (words / (audio_s / 60.0)) if audio_s > 0.0 else 0.0
    return {
        "words": words,
        "count": count,
        "audio_s": round(audio_s, 2),
        "saved_s": round(saved_s, 1),
        "wpm": round(wpm, 1),
    }


def last_transcript(data: Path | None = None) -> str:
    entries = load_entries(data)
    for entry in reversed(entries):
        txt = entry_text(entry)
        if txt:
            return txt
    return ""


def prune_history(
    max_days: int = 30,
    *,
    now: datetime | None = None,
    data: Path | None = None,
) -> int:
    path = config.history_jsonl_path(data)
    if not path.exists() or max_days <= 0:
        return 0
    ref_now = now or datetime.now().astimezone()
    cutoff = ref_now - timedelta(days=max_days)
    with _HISTORY_LOCK:
        if not path.exists():
            return 0
        kept: list[dict[str, Any]] = []
        removed_entries: list[dict[str, Any]] = []
        for entry in _load_entries_unlocked(data):
            dt = _parse_entry_dt(str(entry.get("cas", "")), cutoff.tzinfo)
            if dt is None:
                removed_entries.append(entry)
                continue
            if dt >= cutoff:
                kept.append(entry)
            else:
                removed_entries.append(entry)
        removed = len(removed_entries)
        if removed > 0:
            _rewrite_entries(path, kept)
            _unlink_history_md(data)
            for entry in removed_entries:
                _unlink_entry_audio(entry, data)
        return removed


def export_markdown(data: Path | None = None, limit: int = 200) -> Path:
    entries = load_entries(data)[-limit:]
    entries.reverse()
    lines = ["# Historie diktování — Mluvka", ""]
    if not entries:
        lines.append("_Zatím žádné záznamy._")
    else:
        for entry in entries:
            cas = entry.get("cas", "")
            app = entry.get("aplikace", "") or "neznámá aplikace"
            audio_s = entry.get("audio_s", "")
            prepis_s = entry.get("prepis_s", "")
            txt = entry_text(entry).strip()
            lines.append(f"## {cas} — {app} ({audio_s} s audio / {prepis_s} s přepis)")
            lines.append("")
            lines.append(txt)
            lines.append("")
    md_path = config.history_md_path(data)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def open_history_markdown(data: Path | None = None) -> Path:
    md_path = export_markdown(data=data)
    os.startfile(str(md_path))
    return md_path


def _find_strict_entry_index(entries: list[dict[str, Any]], entry_id: str) -> int:
    clean_id = str(entry_id or "").strip()
    if not clean_id:
        return -1
    for idx in range(len(entries) - 1, -1, -1):
        if str(entries[idx].get("id", "")).strip() == clean_id:
            return idx
    return -1


def _is_verified_entry(entry: dict[str, Any]) -> bool:
    return bool(entry.get("overeno") is True and str(entry.get("overeno_text", "") or "").strip())


def dictation_summary_and_list(
    offset: int = 0,
    limit: int = 50,
    *,
    data: Path | None = None,
) -> dict[str, Any]:
    """Vrátí uložené diktáty s WAV nahrávkou a polem prepis (nejnovější první)."""
    start = max(0, int(offset))
    count = max(1, min(500, int(limit)))
    with _HISTORY_LOCK:
        entries = _load_entries_unlocked(data)
    eligible: list[tuple[int, dict[str, Any]]] = []
    for idx, entry in enumerate(entries):
        uid = str(entry.get("id", "") or "").strip()
        raw_prepis = entry.get("prepis")
        if not uid or not isinstance(raw_prepis, str) or not raw_prepis.strip():
            continue
        if _dictation_wav_path(entry, data) is None:
            continue
        eligible.append((idx, entry))

    eligible.sort(key=lambda pair: (_entry_timestamp(pair[1]), pair[0]), reverse=True)

    verified_count = 0
    verified_s = 0.0
    total_s = 0.0
    for _, entry in eligible:
        try:
            dur = max(0.0, float(entry.get("audio_s", 0.0) or 0.0))
        except (TypeError, ValueError):
            dur = 0.0
        total_s += dur
        if _is_verified_entry(entry):
            verified_count += 1
            verified_s += dur

    page = eligible[start : start + count]
    items: list[dict[str, Any]] = []
    for _, entry in page:
        is_verified = _is_verified_entry(entry)
        try:
            dur = round(max(0.0, float(entry.get("audio_s", 0.0) or 0.0)), 2)
        except (TypeError, ValueError):
            dur = 0.0
        items.append(
            {
                "id": str(entry.get("id", "")),
                "cas": str(entry.get("cas", "")),
                "audio_s": dur,
                "prepis": str(entry.get("prepis", "")),
                "overeno": is_verified,
                "overeno_text": str(entry.get("overeno_text", "") or "") if is_verified else "",
            }
        )

    total = len(eligible)
    return {
        "items": items,
        "total": total,
        "verified_count": verified_count,
        "verified_s": round(verified_s, 2),
        "total_s": round(total_s, 2),
        "has_more": (start + len(items)) < total,
    }


def get_dictation_wav_path(entry_id: str, *, data: Path | None = None) -> Path | None:
    """Ověří id v historii a vrátí cestu k existujícímu WAV diktátu."""
    with _HISTORY_LOCK:
        entries = _load_entries_unlocked(data)
    idx = _find_strict_entry_index(entries, entry_id)
    if idx < 0:
        return None
    return _dictation_wav_path(entries[idx], data)


def verify_dictation(
    entry_id: str,
    text: str,
    *,
    data: Path | None = None,
) -> dict[str, Any] | None:
    """Uloží doslovný ověřený přepis (overeno_text + overeno: true) na záznam v historii."""
    clean_id = str(entry_id or "").strip()
    if not clean_id:
        raise ValueError("Chybí identifikátor diktátu.")
    if not isinstance(text, str):
        raise ValueError("Přepis musí být text.")
    if len(text) > MAX_VERIFIED_TEXT_CHARS:
        raise ValueError(f"Přepis může mít nejvýše {MAX_VERIFIED_TEXT_CHARS} znaků.")
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Přepis nesmí být prázdný.")

    path = config.history_jsonl_path(data)
    with _HISTORY_LOCK:
        if not path.exists():
            return None
        entries = _load_entries_unlocked(data)
        match_idx = _find_strict_entry_index(entries, clean_id)
        if match_idx < 0:
            return None
        target = entries[match_idx]
        if not isinstance(target.get("prepis"), str) or not str(target.get("prepis", "")).strip():
            raise ValueError("Záznam nemá uložený surový přepis diktátu.")
        if _dictation_wav_path(target, data) is None:
            raise ValueError("Nahrávka diktátu už neexistuje.")
        target["overeno"] = True
        target["overeno_text"] = cleaned
        _rewrite_entries(path, entries)
        _unlink_history_md(data)
        return copy.deepcopy(target)


def delete_dictation_audio(entry_id: str, *, data: Path | None = None) -> bool:
    """Smaže WAV nahrávku diktátu a vymaže zvuk/prepis/overeno ze záznamu."""
    clean_id = str(entry_id or "").strip()
    if not clean_id:
        return False
    path = config.history_jsonl_path(data)
    with _HISTORY_LOCK:
        if not path.exists():
            return False
        entries = _load_entries_unlocked(data)
        match_idx = _find_strict_entry_index(entries, clean_id)
        if match_idx < 0:
            return False
        target = entries[match_idx]
        _unlink_entry_audio(target, data)
        target.pop("zvuk", None)
        target.pop("prepis", None)
        target.pop("overeno", None)
        target.pop("overeno_text", None)
        _rewrite_entries(path, entries)
        _unlink_history_md(data)
        return True

