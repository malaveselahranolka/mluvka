"""Schránka a vkládání textu přes Win32 SendInput."""

from __future__ import annotations

import ctypes
import logging
import sys

if sys.platform == "win32":
    import ctypes.wintypes as wt
import time

log = logging.getLogger("mluvka.output")

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    INPUT_KEYBOARD = 1
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002

    VK_LSHIFT = 0xA0
    VK_RSHIFT = 0xA1
    VK_LCONTROL = 0xA2
    VK_RCONTROL = 0xA3
    VK_LMENU = 0xA4
    VK_RMENU = 0xA5
    VK_LWIN = 0x5B
    VK_RWIN = 0x5C
    VK_C = 0x43
    VK_V = 0x56

    _EXTENDED_VKS = {VK_RCONTROL, VK_RMENU, VK_LWIN, VK_RWIN}
    _RELEASE_BEFORE_INPUT = (VK_LSHIFT, VK_RSHIFT, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)


    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wt.LONG),
            ("dy", wt.LONG),
            ("mouseData", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]


    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wt.WORD),
            ("wScan", wt.WORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]


    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wt.DWORD),
            ("wParamL", wt.WORD),
            ("wParamH", wt.WORD),
        ]


    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]


    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wt.DWORD),
            ("_union", _INPUT_UNION),
        ]


    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.OpenClipboard.restype = wt.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wt.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wt.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
    user32.IsClipboardFormatAvailable.restype = wt.BOOL
    user32.GetClipboardData.argtypes = [wt.UINT]
    user32.GetClipboardData.restype = wt.HANDLE
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    user32.SetClipboardData.restype = wt.HANDLE
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = wt.DWORD
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wt.UINT
    user32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wt.UINT

    # Bez těchto formátů by každý diktát skončil v historii schránky (Win+V) a v cloudové synchronizaci schránky.
    _PRIVATE_CLIPBOARD_FORMATS = (
        "ExcludeClipboardContentFromMonitorProcessing",
        "CanIncludeInClipboardHistory",
        "CanUploadToCloudClipboard",
    )
    # Nepřiřazená virtuální klávesa: vložená mezi stisk a uvolnění Alt/Shift/Win zabrání přepnutí jazyka, Start menu i menu okna.
    VK_MASK = 0xE8

    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalLock.restype = wt.LPVOID
    kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalUnlock.restype = wt.BOOL
    kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
    kernel32.GlobalFree.restype = wt.HGLOBAL


    def _open_clipboard_retry() -> bool:
        for _ in range(10):
            if user32.OpenClipboard(None):
                return True
            time.sleep(0.020)
        return False


    def get_clipboard_text() -> tuple[str | None, int]:
        seq = int(user32.GetClipboardSequenceNumber())
        if not _open_clipboard_retry():
            return None, seq
        try:
            seq = int(user32.GetClipboardSequenceNumber())
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None, seq
            h_mem = user32.GetClipboardData(CF_UNICODETEXT)
            if not h_mem:
                return None, seq
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                return None, seq
            try:
                return ctypes.wstring_at(ptr), seq
            finally:
                kernel32.GlobalUnlock(h_mem)
        finally:
            user32.CloseClipboard()


    def set_clipboard_text(text: str) -> int | None:
        encoded = (text + "\0").encode("utf-16-le")
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h_mem:
            return None
        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            kernel32.GlobalFree(h_mem)
            return None
        try:
            ctypes.memmove(ptr, encoded, len(encoded))
        finally:
            kernel32.GlobalUnlock(h_mem)

        if not _open_clipboard_retry():
            kernel32.GlobalFree(h_mem)
            return None
        try:
            if not user32.EmptyClipboard():
                kernel32.GlobalFree(h_mem)
                return None
            if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                kernel32.GlobalFree(h_mem)
                return None
            # Po úspěšném SetClipboardData vlastní paměť Windows — neuvolňujeme ji.
            _mark_clipboard_private()
        finally:
            user32.CloseClipboard()
        return int(user32.GetClipboardSequenceNumber())


    def _mark_clipboard_private() -> None:
        """Volá se uvnitř otevřené schránky: DWORD 0 ve formátech vylučujících historii a cloud."""
        for name in _PRIVATE_CLIPBOARD_FORMATS:
            fmt = user32.RegisterClipboardFormatW(name)
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
            if not fmt or not h:
                continue
            p = kernel32.GlobalLock(h)
            if p:
                ctypes.memmove(p, (ctypes.c_uint32 * 1)(0), 4)
                kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(fmt, h):
                kernel32.GlobalFree(h)


    def _make_key_input(vk: int, *, key_up: bool) -> INPUT:
        flags = KEYEVENTF_KEYUP if key_up else 0
        if vk in _EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp._union.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        return inp


    def _send_inputs(inputs: list[INPUT]) -> bool:
        if not inputs:
            return True
        arr = (INPUT * len(inputs))(*inputs)
        sent = int(user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)))
        return sent == len(inputs)


    def send_mask_key() -> None:
        _send_inputs([_make_key_input(VK_MASK, key_up=False), _make_key_input(VK_MASK, key_up=True)])


    def release_modifiers(vks: tuple[int, ...] = _RELEASE_BEFORE_INPUT) -> None:
        events: list[INPUT] = []
        for vk in vks:
            if user32.GetAsyncKeyState(vk) & 0x8000:
                events.append(_make_key_input(vk, key_up=True))
        if events:
            mask = [_make_key_input(VK_MASK, key_up=False), _make_key_input(VK_MASK, key_up=True)]
            _send_inputs(mask + events)


    def _send_ctrl_combo(vk: int) -> bool:
        # Pokud je fyzicky držen pravý Ctrl, uvolníme ho před syntetickým Ctrl+klávesa.
        events: list[INPUT] = []
        if user32.GetAsyncKeyState(VK_RCONTROL) & 0x8000:
            events.append(_make_key_input(VK_RCONTROL, key_up=True))
        events.extend(
            [
                _make_key_input(VK_LCONTROL, key_up=False),
                _make_key_input(vk, key_up=False),
                _make_key_input(vk, key_up=True),
                _make_key_input(VK_LCONTROL, key_up=True),
            ]
        )
        return _send_inputs(events)


    def grab_selection() -> tuple[str, str | None]:
        """Uloží schránku, uvolní Shift, pošle Ctrl+C, počká 150 ms a vrátí (výběr, předchozí_text)."""
        prev_text, seq_before = get_clipboard_text()
        release_modifiers((VK_LSHIFT, VK_RSHIFT, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN))
        _send_ctrl_combo(VK_C)
        time.sleep(0.150)
        new_text, seq_after = get_clipboard_text()
        if seq_after != seq_before and new_text is not None:
            return new_text, prev_text
        return "", prev_text


    def restore_clipboard_if_unchanged(prev_text: str | None, expected_seq: int | None) -> None:
        if prev_text is None or expected_seq is None:
            return
        if int(user32.GetClipboardSequenceNumber()) == expected_seq:
            set_clipboard_text(prev_text)


    def paste_text(
        text: str,
        *,
        append_space: bool = True,
        prev_text: str | None = ...,  # type: ignore[assignment]
    ) -> bool:
        """Vloží text přes schránku a Ctrl+V, po 250 ms obnoví předchozí schránku."""
        if not text:
            return True
        if prev_text is ...:
            prev_text, _ = get_clipboard_text()

        to_paste = text if (not append_space or text.endswith("\n")) else text + " "
        our_seq = set_clipboard_text(to_paste)
        if our_seq is None:
            log.error("Nepodařilo se zapsat text do schránky.")
            return False

        release_modifiers()
        if not _send_ctrl_combo(VK_V):
            log.error("SendInput Ctrl+V selhal.")
            return False

        time.sleep(0.250)
        restore_clipboard_if_unchanged(prev_text, our_seq)
        return True

else:
    from mluvka.output_mac import (
        get_clipboard_text as get_clipboard_text,
        grab_selection as grab_selection,
        paste_text as paste_text,
        release_modifiers as release_modifiers,
        restore_clipboard_if_unchanged as restore_clipboard_if_unchanged,
        send_mask_key as send_mask_key,
        set_clipboard_text as set_clipboard_text,
    )
