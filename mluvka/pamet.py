"""Paměť chyb — lokální ukládání uživatelských oprav přepisů v Historii."""

from __future__ import annotations

import difflib
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from mluvka import config

_PAMET_LOCK = threading.Lock()
_EDGE_PUNCT = ",!?;:„“\"'()[]"
MAX_PHRASE_WORDS = 4
MAX_PHRASE_LEN = 60


def _clean_phrase(text: str) -> str:
    return text.strip().strip(_EDGE_PUNCT).strip()


def extract_changes(puvodni: str, opraveno: str) -> list[list[str]]:
    """Extrahuje krátké slovní náhrady (max 4 slova na straně) pomocí SequenceMatcher."""
    orig_words = str(puvodni or "").split()
    new_words = str(opraveno or "").split()
    if not orig_words or not new_words:
        return []
    sm = difflib.SequenceMatcher(None, orig_words, new_words, autojunk=False)
    changes: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        if (i2 - i1) > MAX_PHRASE_WORDS or (j2 - j1) > MAX_PHRASE_WORDS:
            continue
        z = _clean_phrase(" ".join(orig_words[i1:i2]))
        na = _clean_phrase(" ".join(new_words[j1:j2]))
        if z.endswith(".") and na.endswith("."):
            z = z[:-1].rstrip()
            na = na[:-1].rstrip()
        if not z or not na or z == na:
            continue
        if len(z) > MAX_PHRASE_LEN or len(na) > MAX_PHRASE_LEN:
            continue
        key = (z.casefold(), na.casefold())
        if key in seen:
            continue
        seen.add(key)
        changes.append([z, na])
    return changes


def _load_entries_unlocked(data: Path | None = None) -> list[dict[str, Any]]:
    path = config.pamet_jsonl_path(data)
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


def load_entries(data: Path | None = None) -> list[dict[str, Any]]:
    with _PAMET_LOCK:
        return _load_entries_unlocked(data)


def add(
    entry_id: str,
    puvodni: str,
    opraveno: str,
    *,
    cas: datetime | str | None = None,
    data: Path | None = None,
) -> dict[str, Any]:
    """Uloží opravený záznam a extrahované krátké náhrady do DATA/pamet_chyb.jsonl."""
    if isinstance(cas, datetime):
        ts = cas.isoformat(timespec="seconds")
    elif isinstance(cas, str) and cas.strip():
        ts = cas.strip()
    else:
        ts = datetime.now().astimezone().isoformat(timespec="seconds")

    puv_str = str(puvodni or "")
    opr_str = str(opraveno or "")
    zmeny = extract_changes(puv_str, opr_str)
    record: dict[str, Any] = {
        "id": str(entry_id or ""),
        "cas": ts,
        "puvodni": puv_str,
        "opraveno": opr_str,
        "zmeny": zmeny,
    }
    path = config.pamet_jsonl_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _PAMET_LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def recent_examples(limit: int = 5, *, data: Path | None = None) -> list[tuple[str, str]]:
    """Vrátí posledních nejvýše `limit` dvojic (puvodni, opraveno) pro kontext AI úprav."""
    if limit <= 0:
        return []
    entries = load_entries(data)
    out: list[tuple[str, str]] = []
    for entry in reversed(entries):
        puv = str(entry.get("puvodni", "") or "").strip()
        opr = str(entry.get("opraveno", "") or "").strip()
        if puv and opr and puv != opr:
            out.append((puv[:200], opr[:200]))
            if len(out) >= limit:
                break
    out.reverse()
    return out


def top(n: int = 20, *, data: Path | None = None) -> list[dict[str, Any]]:
    """Vrátí agregované opravené dvojice s počty výskytů, nejnovější první."""
    if n <= 0:
        return []
    entries = load_entries(data)
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for seq, entry in enumerate(entries):
        cas = str(entry.get("cas", "") or "")
        zmeny = entry.get("zmeny") or []
        if not isinstance(zmeny, list):
            continue
        for pair in zmeny:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            z = str(pair[0]).strip()
            na = str(pair[1]).strip()
            if not z or not na or z == na:
                continue
            key = (z.casefold(), na.casefold())
            item = agg.get(key)
            if item is None:
                agg[key] = {"z": z, "na": na, "count": 1, "cas": cas, "_order": seq}
            else:
                item["z"] = z
                item["na"] = na
                item["count"] += 1
                item["cas"] = cas
                item["_order"] = seq
    ordered = sorted(agg.values(), key=lambda x: x["_order"], reverse=True)[:n]
    return [
        {"z": item["z"], "na": item["na"], "count": item["count"], "cas": item["cas"]}
        for item in ordered
    ]


def clear(*, data: Path | None = None) -> int:
    """Vymaže celý soubor pamet_chyb.jsonl a vrátí počet odstraněných záznamů."""
    path = config.pamet_jsonl_path(data)
    with _PAMET_LOCK:
        count = len(_load_entries_unlocked(data))
        path.unlink(missing_ok=True)
        return count
