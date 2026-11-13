"""Čisté textové post-zpracování českého přepisu."""

from __future__ import annotations

import re
from typing import Any

_CMD_RE = re.compile(
    r"[ \t]*(?<!\w)(nový\s+(?:řádek|odstavec))(?!\w)(?:[ \t]*[,.])?[ \t]*",
    re.IGNORECASE | re.UNICODE,
)
_FILLER_RE = re.compile(
    r"(?<!\w)(?:ehm+|hm+|e{3,}|é{3,}|m{3,})(?!\w)(?:[ \t]*,)?[ \t]*",
    re.IGNORECASE | re.UNICODE,
)
_TRAILING_PUNCT_RE = re.compile(r"[ \t.,!?:;…]+$")


def _boundary_pattern(phrase: str) -> str:
    escaped = re.escape(phrase)
    left = r"(?<!\w)" if re.match(r"^\w", phrase, re.UNICODE) else r"(?:^|(?<=\s))"
    right = r"(?!\w)" if re.search(r"\w$", phrase, re.UNICODE) else r"(?:$|(?=\s|[,.!?:;]))"
    return f"{left}{escaped}{right}"


def _capitalise_after_newlines(text: str) -> str:
    return re.sub(
        r"(\n+)([^\W\d_])",
        lambda m: m.group(1) + m.group(2).upper(),
        text,
        flags=re.UNICODE,
    )


def _remove_fillers(text: str) -> str:
    # Sledujeme přesná místa na začátku věty, kde byla výplň odstraněna,
    # abychom nezvětšovali písmena za zkratkami nebo řadovými číslovkami jinde v textu.
    out: list[str] = []
    pos = 0
    recap_pending = False
    for m in _FILLER_RE.finditer(text):
        before = text[pos : m.start()]
        if recap_pending and before:
            before = re.sub(
                r"^([^\W\d_])",
                lambda ch: ch.group(1).upper(),
                before,
                count=1,
                flags=re.UNICODE,
            )
            recap_pending = False
        out.append(before)
        prefix = "".join(out)
        at_sentence_start = (
            prefix == ""
            or prefix.endswith("\n")
            or bool(re.search(r"[.!?][ \t]+$", prefix))
        )
        if at_sentence_start or recap_pending:
            recap_pending = True
        pos = m.end()

    tail = text[pos:]
    if recap_pending and tail:
        tail = re.sub(
            r"^([^\W\d_])",
            lambda ch: ch.group(1).upper(),
            tail,
            count=1,
            flags=re.UNICODE,
        )
    out.append(tail)
    cleaned = "".join(out)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]*\n[ \t]*", "\n", cleaned)
    return _capitalise_after_newlines(cleaned)


def _apply_corrections(text: str, opravy: Any) -> str:
    if not opravy:
        return text
    pairs: list[tuple[str, str]] = []
    for item in opravy:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            wrong, right = str(item[0]).strip(), str(item[1])
            if wrong:
                pairs.append((wrong, right))
    if not pairs:
        return text
    pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
    mapping = {wrong.casefold(): right for wrong, right in pairs}
    pattern = re.compile(
        "|".join(_boundary_pattern(wrong) for wrong, _ in pairs),
        re.IGNORECASE | re.UNICODE,
    )
    return pattern.sub(lambda m: mapping[m.group(0).casefold()], text)


def _normalise_snippets(zkratky: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if isinstance(zkratky, dict):
        for trigger, body in zkratky.items():
            t = str(trigger).strip()
            if t:
                pairs.append((t, str(body)))
    elif isinstance(zkratky, (list, tuple)):
        for item in zkratky:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                t = str(item[0]).strip()
                if t:
                    pairs.append((t, str(item[1])))
            elif isinstance(item, dict):
                t = str(item.get("trigger") or item.get("spoustec") or "").strip()
                body = item.get("text", "")
                if t:
                    pairs.append((t, str(body)))
    pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
    return pairs


def _apply_snippets(text: str, zkratky: Any) -> tuple[str, bool]:
    pairs = _normalise_snippets(zkratky)
    if not pairs:
        return text, False
    stripped = _TRAILING_PUNCT_RE.sub("", text.strip(" \t")).casefold()
    for trigger, body in pairs:
        if stripped == trigger.casefold():
            return body, True
    mapping = {trigger.casefold(): body for trigger, body in pairs}
    pattern = re.compile(
        "|".join(_boundary_pattern(trigger) for trigger, _ in pairs),
        re.IGNORECASE | re.UNICODE,
    )
    return pattern.sub(lambda m: mapping[m.group(0).casefold()], text), False


def uprav(
    text: str,
    cfg: dict[str, Any] | None = None,
    *,
    apply_commands: bool = True,
    apply_snippets: bool = True,
) -> str:
    """Upraví přepis podle pravidel: příkazy, výplně, opravy slovníku, zkratky a úklid mezer."""
    cfg = cfg or {}
    s = re.sub(r"\s+", " ", text or "").strip()
    if not s:
        return ""

    if apply_commands:
        s = _CMD_RE.sub(
            lambda m: "\n\n" if "odstavec" in m.group(1).lower() else "\n",
            s,
        )
        s = _capitalise_after_newlines(s)

    s = _remove_fillers(s)

    slovnik = cfg.get("slovnik") or {}
    s = _apply_corrections(s, slovnik.get("opravy"))

    if apply_snippets:
        s, exact_snippet = _apply_snippets(s, cfg.get("zkratky"))
        if exact_snippet:
            return s

    s = re.sub(r"[ \t]+([,.!?:;])", r"\1", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    return s.strip(" \t")
