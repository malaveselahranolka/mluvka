"""Import slovníku a oprav z odpovědi vlastního AI asistenta (čistě offline parser)."""

from __future__ import annotations

import json
import re
from typing import Any

MAX_WORD_LEN = 60
MAX_WORDS = 300
MAX_PAIRS = 300

SLOVNIK_PROMPT = (
    "Znáš mě z našich konverzací. Připrav mi slovník pro diktovací aplikaci Mluvka, která převádí moji češtinu na text.\n"
    "Vypiš slova, která často používám a která by český model rozpoznávání řeči mohl přepsat špatně: jména lidí, firem a míst, názvy produktů, aplikací a nástrojů, odborné výrazy, anglická slova a zkratky.\n"
    "U slov, kde čekáš konkrétní zkomoleninu, přidej opravu ve tvaru [jak to model nejspíš napíše, jak to má být], například [\"klód kód\", \"Claude Code\"].\n"
    "Odpověz POUZE jako JSON v tomto tvaru, bez dalšího textu:\n"
    "{\"slova\": [\"…\"], \"opravy\": [[\"…\", \"…\"]]}\n"
    "Nejvýš 150 slov a 50 oprav. Nevymýšlej si, uváděj jen to, co ode mě opravdu znáš."
)

_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_-]*)\s*\n?([\s\S]*?)```")


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "\"":
                in_string = False
            continue
        if ch == "\"":
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def _extract_json_dict(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError("Prázdný vstup.")

    candidates: list[str] = []
    for match in _FENCE_RE.finditer(text):
        snippet = _first_balanced_object(match.group(1))
        if snippet:
            candidates.append(snippet)
    direct = _first_balanced_object(text)
    if direct and direct not in candidates:
        candidates.append(direct)

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj

    raise ValueError("Nepodařilo se najít platný JSON objekt.")


def parse(text: str) -> dict[str, Any]:
    """Načte a zvaliduje JSON slovníku z čistého JSONu, markdown bloku i okolního textu."""
    if not isinstance(text, str):
        raise ValueError("Vstup musí být text.")
    obj = _extract_json_dict(text)
    if "slova" not in obj and "opravy" not in obj:
        raise ValueError("JSON musí obsahovat pole slova nebo opravy.")

    raw_slova = obj.get("slova", [])
    raw_opravy = obj.get("opravy", [])
    if not isinstance(raw_slova, list):
        raise ValueError("Pole slova musí být seznam.")
    if not isinstance(raw_opravy, list):
        raise ValueError("Pole opravy musí být seznam.")
    if len(raw_slova) > MAX_WORDS:
        raise ValueError(f"Seznam slov překračuje limit {MAX_WORDS}.")
    if len(raw_opravy) > MAX_PAIRS:
        raise ValueError(f"Seznam oprav překračuje limit {MAX_PAIRS}.")

    slova: list[str] = []
    seen_words: set[str] = set()
    for item in raw_slova:
        if not isinstance(item, str):
            raise ValueError("Každé slovo musí být text.")
        word = item.strip()
        if not word:
            continue
        if len(word) > MAX_WORD_LEN:
            raise ValueError(f"Slovo překračuje limit {MAX_WORD_LEN} znaků.")
        key = word.casefold()
        if key not in seen_words:
            seen_words.add(key)
            slova.append(word)

    opravy: list[list[str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for pair in raw_opravy:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Každá oprava musí být dvojice [z, na].")
        if not isinstance(pair[0], str) or not isinstance(pair[1], str):
            raise ValueError("Oprava musí obsahovat pouze textové řetězce.")
        z = pair[0].strip()
        na = pair[1].strip()
        if not z or not na:
            continue
        if len(z) > MAX_WORD_LEN or len(na) > MAX_WORD_LEN:
            raise ValueError(f"Oprava překračuje limit {MAX_WORD_LEN} znaků.")
        if z == na:
            continue
        key = (z.casefold(), na.casefold())
        if key not in seen_pairs:
            seen_pairs.add(key)
            opravy.append([z, na])

    return {"slova": slova, "opravy": opravy}
