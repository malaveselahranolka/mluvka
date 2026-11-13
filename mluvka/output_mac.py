"""Schránka a vkládání textu na macOS přes NSPasteboard a Quartz CGEvent."""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("mluvka.output_mac")

MAC_KEY_C = 8
MAC_KEY_V = 9
CONCEALED_PASTEBOARD_TYPE = "org.nspasteboard.ConcealedType"
SYNTHETIC_EVENT_TAG = 0x4D4C564B  # 'MLVK' — označení syntetických událostí pro filtr v hotkey_mac

VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_VK_TO_MAC_KEY: dict[int, int] = {
    VK_LSHIFT: 56,
    VK_RSHIFT: 60,
    VK_LCONTROL: 59,
    VK_RCONTROL: 62,
    VK_LMENU: 58,
    VK_RMENU: 61,
    VK_LWIN: 55,
    VK_RWIN: 54,
}
_RELEASE_BEFORE_INPUT = (
    VK_LSHIFT,
    VK_RSHIFT,
    VK_LCONTROL,
    VK_RCONTROL,
    VK_LMENU,
    VK_RMENU,
    VK_LWIN,
    VK_RWIN,
)


def _tag_synthetic_event(quartz_mod: Any, ev: Any) -> None:
    setter = getattr(quartz_mod, "CGEventSetIntegerValueField", None)
    if callable(setter):
        user_data_field = getattr(quartz_mod, "kCGEventSourceUserData", 42)
        setter(ev, user_data_field, SYNTHETIC_EVENT_TAG)


def _cocoa_pasteboard_and_nsdata() -> tuple[Any, str, Any]:
    nspasteboard = None
    pb_str_type = None
    nsdata = None
    try:
        import Cocoa

        nspasteboard = getattr(Cocoa, "NSPasteboard", None)
        pb_str_type = getattr(Cocoa, "NSPasteboardTypeString", None)
        nsdata = getattr(Cocoa, "NSData", None)
    except ImportError:
        pass
    if nspasteboard is None or pb_str_type is None:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        nspasteboard = NSPasteboard
        pb_str_type = pb_str_type or NSPasteboardTypeString
    if nsdata is None:
        try:
            from Foundation import NSData

            nsdata = NSData
        except ImportError:
            nsdata = None
    return nspasteboard, str(pb_str_type), nsdata


def _pasteboard() -> tuple[Any, str, Any]:
    nspb, str_type, nsdata = _cocoa_pasteboard_and_nsdata()
    return nspb.generalPasteboard(), str_type, nsdata


def get_clipboard_text() -> tuple[str | None, int]:
    pb, str_type, _ = _pasteboard()
    seq = int(pb.changeCount())
    raw = pb.stringForType_(str_type)
    if raw is None:
        return None, seq
    return str(raw), seq


def set_clipboard_text(text: str) -> int | None:
    pb, str_type, nsdata = _pasteboard()
    pb.clearContents()
    ok = pb.setString_forType_(text, str_type)
    if not ok:
        return None
    if nsdata is not None and hasattr(pb, "setData_forType_"):
        try:
            pb.setData_forType_(nsdata.data(), CONCEALED_PASTEBOARD_TYPE)
        except Exception:
            pass
    elif hasattr(pb, "setString_forType_"):
        try:
            pb.setString_forType_("", CONCEALED_PASTEBOARD_TYPE)
        except Exception:
            pass
    return int(pb.changeCount())


def send_mask_key() -> None:
    """Na macOS není potřeba maskovací klávesa."""
    return None


def release_modifiers(vks: tuple[int, ...] = _RELEASE_BEFORE_INPUT) -> None:
    import Quartz

    key_state = getattr(Quartz, "CGEventSourceKeyState", None)
    if not callable(key_state):
        return
    src_state = getattr(Quartz, "kCGEventSourceStateCombinedSessionState", 0)
    tap = getattr(Quartz, "kCGHIDEventTap", 0)
    for vk in vks:
        mac_code = _VK_TO_MAC_KEY.get(vk)
        if mac_code is None:
            continue
        if key_state(src_state, mac_code):
            ev = Quartz.CGEventCreateKeyboardEvent(None, mac_code, False)
            if ev:
                Quartz.CGEventSetFlags(ev, 0)
                _tag_synthetic_event(Quartz, ev)
                Quartz.CGEventPost(tap, ev)


def _send_cmd_combo(mac_keycode: int) -> bool:
    import Quartz

    tap = getattr(Quartz, "kCGHIDEventTap", 0)
    cmd_flag = int(getattr(Quartz, "kCGEventFlagMaskCommand", 0x00100000))
    down = Quartz.CGEventCreateKeyboardEvent(None, mac_keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, mac_keycode, False)
    if not down or not up:
        return False
    Quartz.CGEventSetFlags(down, cmd_flag)
    Quartz.CGEventSetFlags(up, cmd_flag)
    _tag_synthetic_event(Quartz, down)
    _tag_synthetic_event(Quartz, up)
    Quartz.CGEventPost(tap, down)
    Quartz.CGEventPost(tap, up)
    return True


def grab_selection() -> tuple[str, str | None]:
    """Uloží schránku, uvolní modifikátory, pošle Cmd+C, počká 150 ms a vrátí (výběr, předchozí_text)."""
    prev_text, seq_before = get_clipboard_text()
    release_modifiers(_RELEASE_BEFORE_INPUT)
    _send_cmd_combo(MAC_KEY_C)
    time.sleep(0.150)
    new_text, seq_after = get_clipboard_text()
    if seq_after != seq_before and new_text is not None:
        return new_text, prev_text
    return "", prev_text


def restore_clipboard_if_unchanged(prev_text: str | None, expected_seq: int | None) -> None:
    if prev_text is None or expected_seq is None:
        return
    pb, _, _ = _pasteboard()
    if int(pb.changeCount()) == expected_seq:
        set_clipboard_text(prev_text)


def paste_text(
    text: str,
    *,
    append_space: bool = True,
    prev_text: str | None = ...,  # type: ignore[assignment]
) -> bool:
    """Vloží text přes NSPasteboard a Cmd+V, po 250 ms obnoví předchozí schránku."""
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
    if not _send_cmd_combo(MAC_KEY_V):
        log.error("CGEventPost Cmd+V selhal.")
        return False

    time.sleep(0.250)
    restore_clipboard_if_unchanged(prev_text, our_seq)
    return True
