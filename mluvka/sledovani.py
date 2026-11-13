"""Automatické učení uživatelských oprav z cílových aplikací přes Windows UI Automation."""

from __future__ import annotations

import ctypes
import difflib
import logging
import sys
import threading

if sys.platform == "win32":
    import ctypes.wintypes as wt
import time
import unicodedata
from typing import Any, Callable, Protocol

from mluvka import pamet

log = logging.getLogger("mluvka.sledovani")

INITIAL_DELAY_S = 0.3
BASELINE_RETRIES = 3
BASELINE_RETRY_INTERVAL_S = 0.3
POLL_INTERVAL_S = 1.5
MAX_DURATION_S = 120.0

UIA_TEXT_PATTERN_ID = 10014
UIA_VALUE_PATTERN_ID = 10002
UIA_MAX_TEXT_LEN = 20000

MIN_PAIR_SIMILARITY = 0.5
MAX_CHANGED_WORDS_RATIO = 0.4
_UIA_TYPELIB_LOCK = threading.Lock()

LearnedCallback = Callable[[str, list[list[str]]], None]


class FocusReader(Protocol):
    """Rozhraní pro čtení fokusovaného textového pole (UIA nebo testovací fake)."""

    def get_foreground_hwnd(self) -> int: ...

    def get_focused(self) -> Any | None: ...

    def is_password(self, elem: Any) -> bool: ...

    def read_text(self, elem: Any) -> str | None: ...


def is_available() -> bool:
    return sys.platform == "win32"


def status() -> str:
    return "dostupné" if is_available() else "nedostupné"


class UnavailableReader:
    """Náhradní čtečka pro platformy bez podpory (např. macOS)."""

    available = False

    def status(self) -> str:
        return "nedostupné"

    def init_thread(self) -> None:
        return None

    def close_thread(self) -> None:
        return None

    def get_foreground_hwnd(self) -> int:
        return 0

    def get_focused(self) -> Any | None:
        return None

    def is_password(self, elem: Any) -> bool:
        return False

    def read_text(self, elem: Any) -> str | None:
        return None


class UiaReader:
    """Čtení fokusovaného textového pole přes Windows UI Automation (comtypes)."""

    def __init__(self) -> None:
        if sys.platform == "win32":
            self._user32: Any = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.GetForegroundWindow.argtypes = []
            self._user32.GetForegroundWindow.restype = wt.HWND
        else:
            self._user32 = None
        self._uia: Any = None
        self._uia_mod: Any = None
        self._com_initialized = False

    def status(self) -> str:
        return "dostupné" if sys.platform == "win32" else "nedostupné"

    def init_thread(self) -> None:
        """Inicializuje COM v aktuálním pracovním vlákně a načte UIAutomation."""
        if sys.platform != "win32":
            return
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        self._com_initialized = True
        try:
            with _UIA_TYPELIB_LOCK:
                comtypes.client.GetModule("UIAutomationCore.dll")
                from comtypes.gen import UIAutomationClient as U

            self._uia_mod = U
            self._uia = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        except Exception:
            log.debug("sledovani: UIAutomation inicializace selhala", exc_info=True)
            self._uia = None

    def close_thread(self) -> None:
        """Uvolní UIA objekt a ukončí COM v aktuálním vlákně."""
        self._uia = None
        self._uia_mod = None
        if self._com_initialized:
            self._com_initialized = False
            try:
                import comtypes

                comtypes.CoUninitialize()
            except Exception:
                pass

    def get_foreground_hwnd(self) -> int:
        try:
            return int(self._user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    def get_focused(self) -> Any | None:
        if self._uia is None:
            return None
        try:
            elem = self._uia.GetFocusedElement()
            return elem if elem else None
        except Exception:
            return None

    def is_password(self, elem: Any) -> bool:
        if elem is None:
            return False
        try:
            return bool(elem.CurrentIsPassword)
        except Exception:
            # Nedokážeme ověřit, zda jde o heslo — raději prvek považujeme za chráněný.
            return True

    def read_text(self, elem: Any) -> str | None:
        if elem is None or self._uia_mod is None:
            return None
        U = self._uia_mod
        try:
            pat = elem.GetCurrentPattern(UIA_TEXT_PATTERN_ID)
            if pat:
                tp = pat.QueryInterface(U.IUIAutomationTextPattern)
                doc_range = tp.DocumentRange
                if doc_range:
                    text = doc_range.GetText(UIA_MAX_TEXT_LEN)
                    return "" if text is None else str(text)
        except Exception:
            pass

        try:
            vpat = elem.GetCurrentPattern(UIA_VALUE_PATTERN_ID)
            if vpat:
                vp = vpat.QueryInterface(U.IUIAutomationValuePattern)
                val = vp.CurrentValue
                return "" if val is None else str(val)
        except Exception:
            pass
        return None


def _align_key(word: str) -> str:
    """Klíč slova bez interpunkce a velikosti písmen pro zarovnání mezi snímky."""
    stripped = "".join(
        ch for ch in word.casefold() if not unicodedata.category(ch).startswith(("P", "S"))
    )
    return stripped or word.casefold()


def _find_last_subsequence(haystack: list[str], needle: list[str]) -> int:
    n = len(needle)
    if n == 0 or len(haystack) < n:
        return -1
    for i in range(len(haystack) - n, -1, -1):
        if haystack[i : i + n] == needle:
            return i
    return -1


def _contains_vlozeno(vlozeno: str, text: str) -> bool:
    v_words = str(vlozeno or "").split()
    t_words = str(text or "").split()
    return _find_last_subsequence(t_words, v_words) >= 0


def _split_straddle(
    pred_left: list[str],
    pred_right: list[str],
    po_slice: list[str],
) -> int:
    """Najde optimální řez ``k`` v ``po_slice``, který oddělí levou a pravou část nahrazeného bloku."""
    best_k = 0
    best_score = -1.0
    left_target = " ".join(pred_left)
    right_target = " ".join(pred_right)
    for k in range(len(po_slice) + 1):
        l_str = " ".join(po_slice[:k])
        r_str = " ".join(po_slice[k:])
        s_left = (
            difflib.SequenceMatcher(None, left_target, l_str, autojunk=False).ratio()
            if (left_target or l_str)
            else 1.0
        )
        s_right = (
            difflib.SequenceMatcher(None, right_target, r_str, autojunk=False).ratio()
            if (right_target or r_str)
            else 1.0
        )
        score = s_left + s_right
        if score > best_score:
            best_score = score
            best_k = k
    return best_k


def _trim_boundary_continuation(
    pred_keys: list[str],
    po_keys: list[str],
    *,
    from_end: bool,
) -> tuple[int, int]:
    """Oddělí dopsaná slova přilepená k okraji úpravy před začátkem nebo za koncem úseku."""
    target = " ".join(pred_keys)
    n = len(po_keys)
    if n <= 1:
        return 0, n
    best_lo, best_hi = 0, n
    best_score = -1.0
    for count in range(1, n + 1):
        lo, hi = (0, count) if from_end else (n - count, n)
        cand = " ".join(po_keys[lo:hi])
        score = difflib.SequenceMatcher(None, target, cand, autojunk=False).ratio()
        if score > best_score:
            best_score = score
            best_lo, best_hi = lo, hi
    return best_lo, best_hi


def _extract_span(
    pred_words: list[str],
    po_words: list[str],
    start: int,
    end: int,
) -> list[str] | None:
    """Vyřízne z ``po_words`` aktuální podobu vloženého úseku ``pred_words[start:end]``."""
    pred_keys = [_align_key(w) for w in pred_words]
    po_keys = [_align_key(w) for w in po_words]
    sm = difflib.SequenceMatcher(None, pred_keys, po_keys, autojunk=False)
    span: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            # Vkládání PŘED začátek nebo ZA konec úseku je pokračování psaní, ne oprava.
            if start < i1 < end:
                span.extend(po_words[j1:j2])
            continue
        if i2 <= start or i1 >= end:
            continue
        if tag == "delete":
            continue
        if tag == "equal":
            lo = max(i1, start)
            hi = min(i2, end)
            span.extend(po_words[j1 + (lo - i1) : j1 + (hi - i1)])
            continue
        # tag == "replace"
        if i1 >= start and i2 <= end:
            if (j2 - j1) > (i2 - i1) and i2 == end and i1 > start:
                lo, hi = _trim_boundary_continuation(
                    pred_keys[i1:i2], po_keys[j1:j2], from_end=True
                )
                span.extend(po_words[j1 + lo : j1 + hi])
            elif (j2 - j1) > (i2 - i1) and i1 == start and i2 < end:
                lo, hi = _trim_boundary_continuation(
                    pred_keys[i1:i2], po_keys[j1:j2], from_end=False
                )
                span.extend(po_words[j1 + lo : j1 + hi])
            else:
                span.extend(po_words[j1:j2])
        elif i1 < start and i2 > end:
            # Přesah před začátek i za konec úseku najednou znamená rozsáhlý přepis obsahu.
            return None
        elif i1 < start < i2:
            k = _split_straddle(pred_keys[i1:start], pred_keys[start:i2], po_keys[j1:j2])
            span.extend(po_words[j1 + k : j2])
        elif i1 < end < i2:
            k = _split_straddle(pred_keys[i1:end], pred_keys[end:i2], po_keys[j1:j2])
            span.extend(po_words[j1 : j1 + k])
    return span


def _strip_punct_lower(text: str) -> str:
    cleaned = "".join(
        " " if unicodedata.category(ch).startswith("P") else ch
        for ch in text.casefold()
    )
    return " ".join(cleaned.split())


def _is_case_or_punct_only(z: str, na: str) -> bool:
    return _strip_punct_lower(z) == _strip_punct_lower(na)


def najdi_opravy(vlozeno: str, pred: str, po: str) -> tuple[str, list[list[str]]] | None:
    """Porovná snímek ``pred`` a ``po`` v místě vloženého úseku a vrátí ``(upraveny_usek, dvojice)``."""
    v_words = str(vlozeno or "").split()
    pred_words = str(pred or "").split()
    po_words = str(po or "").split()
    if not v_words or not pred_words or not po_words:
        return None

    start = _find_last_subsequence(pred_words, v_words)
    if start < 0:
        return None
    end = start + len(v_words)

    po_span = _extract_span(pred_words, po_words, start, end)
    if not po_span:
        return None

    v_keys = [_align_key(w) for w in v_words]
    po_span_keys = [_align_key(w) for w in po_span]
    if v_keys == po_span_keys:
        return None

    sm_span = difflib.SequenceMatcher(None, v_keys, po_span_keys, autojunk=False)
    changed_words = sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in sm_span.get_opcodes()
        if tag != "equal"
    )
    if (changed_words / len(v_words)) > MAX_CHANGED_WORDS_RATIO:
        return None

    # Slova lišící se jen velikostí písmen nebo interpunkcí sjednotíme jako kotvy,
    # aby SequenceMatcher v extract_changes neslil okolní slova do jedné náhrady.
    v_anchored = list(v_words)
    for tag, i1, i2, j1, _j2 in sm_span.get_opcodes():
        if tag == "equal":
            for off in range(i2 - i1):
                v_anchored[i1 + off] = po_span[j1 + off]
    upraveny_usek = " ".join(po_span)
    raw_pairs = pamet.extract_changes(" ".join(v_anchored), upraveny_usek)
    filtered: list[list[str]] = []
    for z, na in raw_pairs:
        if _is_case_or_punct_only(z, na):
            continue
        ratio = difflib.SequenceMatcher(None, z.casefold(), na.casefold()).ratio()
        if ratio < MIN_PAIR_SIMILARITY:
            continue
        filtered.append([z, na])

    if not filtered:
        return None
    return upraveny_usek, filtered


class Sledovac:
    """Sledovač oprav v cílové aplikaci v daemon vlákně po vložení diktátu."""

    def __init__(
        self,
        *,
        reader_factory: Callable[[], FocusReader] | None = None,
        initial_delay_s: float = INITIAL_DELAY_S,
        baseline_retries: int = BASELINE_RETRIES,
        baseline_retry_interval_s: float = BASELINE_RETRY_INTERVAL_S,
        poll_interval_s: float = POLL_INTERVAL_S,
        max_duration_s: float = MAX_DURATION_S,
    ) -> None:
        if reader_factory is not None:
            self._reader_factory = reader_factory
        elif sys.platform == "win32":
            self._reader_factory = UiaReader
        else:
            self._reader_factory = UnavailableReader
        self._initial_delay_s = max(0.0, float(initial_delay_s))
        self._baseline_retries = max(1, int(baseline_retries))
        self._baseline_retry_interval_s = max(0.0, float(baseline_retry_interval_s))
        self._poll_interval_s = max(0.005, float(poll_interval_s))
        self._max_duration_s = max(0.0, float(max_duration_s))

        self._lock = threading.Lock()
        self._active_cancel: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._gen = 0

    def is_available(self) -> bool:
        return self._reader_factory is not UnavailableReader

    def status(self) -> str:
        return "dostupné" if self.is_available() else "nedostupné"

    def start(
        self,
        vlozeno: str,
        entry_id: str,
        on_learned: LearnedCallback,
    ) -> None:
        """Spustí sledování pro nově vložený text; případné běžící sledování okamžitě zruší."""
        if self._reader_factory is UnavailableReader:
            self.stop()
            return
        if not str(vlozeno or "").strip():
            self.stop()
            return
        with self._lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
            cancel_ev = threading.Event()
            self._active_cancel = cancel_ev
            self._gen += 1
            gen = self._gen
            thread = threading.Thread(
                target=self._run_worker,
                args=(vlozeno, entry_id, on_learned, cancel_ev, gen),
                name=f"mluvka-sledovani-{gen}",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self, *, wait: bool = False, timeout: float = 1.0) -> None:
        """Zruší právě běžící sledování."""
        with self._lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
                self._active_cancel = None
            thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def _run_worker(
        self,
        vlozeno: str,
        entry_id: str,
        on_learned: LearnedCallback,
        cancel_ev: threading.Event,
        gen: int,
    ) -> None:
        reader = self._reader_factory()
        try:
            if cancel_ev.wait(self._initial_delay_s):
                return
            init_fn = getattr(reader, "init_thread", None)
            if callable(init_fn):
                init_fn()
            self._watch(reader, vlozeno, entry_id, on_learned, cancel_ev)
        except Exception:
            log.debug("sledovani: neočekávaná chyba ve vlákně", exc_info=True)
        finally:
            close_fn = getattr(reader, "close_thread", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    pass
            with self._lock:
                if self._gen == gen:
                    self._active_cancel = None

    def _watch(
        self,
        reader: FocusReader,
        vlozeno: str,
        _entry_id: str,
        on_learned: LearnedCallback,
        cancel_ev: threading.Event,
    ) -> None:
        initial_hwnd = reader.get_foreground_hwnd()
        if not initial_hwnd:
            log.debug("sledovani: žádné foreground okno")
            return

        elem: Any | None = None
        baseline: str | None = None
        for attempt in range(self._baseline_retries):
            if cancel_ev.is_set():
                return
            if reader.get_foreground_hwnd() != initial_hwnd:
                log.debug("sledovani: foreground okno se změnilo při čtení baseline")
                return
            elem = reader.get_focused()
            if elem is not None:
                if reader.is_password(elem):
                    log.debug("sledovani: fokusovaný prvek je heslo, končím")
                    return
                text = reader.read_text(elem)
                if isinstance(text, str) and _contains_vlozeno(vlozeno, text):
                    baseline = text
                    break
            if attempt + 1 < self._baseline_retries:
                if cancel_ev.wait(self._baseline_retry_interval_s):
                    return

        if baseline is None:
            log.debug("sledovani: vložený text nebyl v baseline nalezen")
            return

        last_snapshot = baseline
        deadline = time.monotonic() + self._max_duration_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if cancel_ev.wait(min(self._poll_interval_s, remaining)):
                return
            if reader.get_foreground_hwnd() != initial_hwnd:
                break
            cur_text = reader.read_text(elem)
            if cur_text is None:
                break
            last_snapshot = cur_text

        if cancel_ev.is_set():
            return

        result = najdi_opravy(vlozeno, baseline, last_snapshot)
        if result is None:
            return
        upraveny_usek, dvojice = result
        log.debug("sledovani: naučeno %d dvojic", len(dvojice))
        try:
            on_learned(upraveny_usek, dvojice)
        except Exception:
            log.exception("sledovani: callback on_learned selhal")
