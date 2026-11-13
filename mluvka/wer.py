"""Sdílený výpočet WER a slovního rozdílu pro benchmark i Trénink."""

from __future__ import annotations

import difflib
import unicodedata
from typing import Any


def normalise_for_wer(text: str) -> list[str]:
    """Normalizuje text pro výpočet WER: malá písmena a bez interpunkce/symbolů."""
    no_punct = "".join(
        " " if unicodedata.category(ch).startswith(("P", "S")) else ch
        for ch in text.lower()
    )
    return " ".join(no_punct.split()).split()


def levenshtein_words(ref: list[str], hyp: list[str]) -> int:
    """Spočítá slovní editační vzdálenost (Levenshtein)."""
    n, m = len(ref), len(hyp)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        rw = ref[i - 1]
        for j in range(1, m + 1):
            cost = 0 if rw == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m]


def sentence_wer(ref: str, hyp: str) -> tuple[int, int, float]:
    """Vrátí (počet_chyb, počet_slov_vzoru, wer) pro jednu větu."""
    rw = normalise_for_wer(ref)
    hw = normalise_for_wer(hyp)
    edits = levenshtein_words(rw, hw)
    wer_val = (edits / len(rw)) if rw else (1.0 if edits else 0.0)
    return edits, len(rw), wer_val


def compute_wer(refs: list[str], hyps: list[str]) -> float:
    """Spočítá celkový WER napříč páry (reference, hypotéza)."""
    total_edits = 0
    total_ref = 0
    for ref, hyp in zip(refs, hyps):
        edits, ref_len, _ = sentence_wer(ref, hyp)
        total_edits += edits
        total_ref += ref_len
    if total_ref == 0:
        return 1.0 if total_edits else 0.0
    return total_edits / total_ref


def _tokenize_with_norm(text: str) -> tuple[list[str], list[tuple[str, int]]]:
    tokens = text.split()
    norm_items: list[tuple[str, int]] = []
    for idx, tok in enumerate(tokens):
        for w in normalise_for_wer(tok):
            norm_items.append((w, idx))
    return tokens, norm_items


def diff_words(ref: str, hyp: str) -> list[dict[str, Any]]:
    """Porovná přepis (hyp) se vzorem (ref) a vrátí seznam {word, ok} pro každé slovo přepisu."""
    _ref_tokens, ref_items = _tokenize_with_norm(ref)
    hyp_tokens, hyp_items = _tokenize_with_norm(hyp)
    if not hyp_tokens:
        return []
    ref_norm = [w for w, _ in ref_items]
    hyp_norm = [w for w, _ in hyp_items]
    token_ok = [True] * len(hyp_tokens)
    sm = difflib.SequenceMatcher(None, ref_norm, hyp_norm, autojunk=False)
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            for j in range(j1, j2):
                token_ok[hyp_items[j][1]] = False
    return [{"word": tok, "ok": token_ok[idx]} for idx, tok in enumerate(hyp_tokens)]
