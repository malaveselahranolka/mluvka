"""Globální klávesové zkratky pro macOS přes Quartz CGEventTapCreate."""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from mluvka.hotkey import (
    DOUBLE_TAP_WINDOW_S,
    HOLD_MIN_S,
    VK_CAPITAL,
    VK_CONTROL,
    VK_ESCAPE,
    VK_F9,
    VK_MAC_FN,
    VK_MENU,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_RWIN,
    VK_SHIFT,
    VK_Z,
    Action,
    HotkeyFSM,
)

log = logging.getLogger("mluvka.hotkey_mac")

MAC_KEY_Z = 6
MAC_KEY_ESCAPE = 53
MAC_KEY_RCOMMAND = 54
MAC_KEY_LCOMMAND = 55
MAC_KEY_LSHIFT = 56
MAC_KEY_CAPS_LOCK = 57
MAC_KEY_LOPTION = 58
MAC_KEY_LCONTROL = 59
MAC_KEY_RSHIFT = 60
MAC_KEY_ROPTION = 61
MAC_KEY_RCONTROL = 62
MAC_KEY_FN = 63
MAC_KEY_F9 = 101

VK_LSHIFT = 0xA0
VK_LCONTROL = 0xA2
VK_LMENU = 0xA4
VK_LWIN = 0x5B

MAC_KEYCODE_TO_VK: dict[int, int] = {
    MAC_KEY_FN: VK_MAC_FN,
    MAC_KEY_ROPTION: VK_RMENU,
    MAC_KEY_RCOMMAND: VK_RWIN,
    MAC_KEY_RCONTROL: VK_RCONTROL,
    MAC_KEY_LOPTION: VK_LMENU,
    MAC_KEY_LCOMMAND: VK_LWIN,
    MAC_KEY_LCONTROL: VK_LCONTROL,
    MAC_KEY_LSHIFT: VK_LSHIFT,
    MAC_KEY_RSHIFT: VK_RSHIFT,
    MAC_KEY_CAPS_LOCK: VK_CAPITAL,
    MAC_KEY_F9: VK_F9,
    MAC_KEY_ESCAPE: VK_ESCAPE,
    MAC_KEY_Z: VK_Z,
}

# Mapování macOS modifier keycodů na jméno masky v modulu Quartz
_MODIFIER_FLAG_NAMES: dict[int, str] = {
    MAC_KEY_FN: "kCGEventFlagMaskSecondaryFn",
    MAC_KEY_ROPTION: "kCGEventFlagMaskAlternate",
    MAC_KEY_LOPTION: "kCGEventFlagMaskAlternate",
    MAC_KEY_RCOMMAND: "kCGEventFlagMaskCommand",
    MAC_KEY_LCOMMAND: "kCGEventFlagMaskCommand",
    MAC_KEY_RCONTROL: "kCGEventFlagMaskControl",
    MAC_KEY_LCONTROL: "kCGEventFlagMaskControl",
    MAC_KEY_LSHIFT: "kCGEventFlagMaskShift",
    MAC_KEY_RSHIFT: "kCGEventFlagMaskShift",
    MAC_KEY_CAPS_LOCK: "kCGEventFlagMaskAlphaShift",
}

SYNTHETIC_EVENT_TAG = 0x4D4C564B

# IOKit NX device-dependent flagy pro rozlišení levé/pravé modifikační klávesy
_MODIFIER_SIDE_MASKS: dict[int, tuple[int, int]] = {
    MAC_KEY_LCONTROL: (0x00000001, 0x00000001 | 0x00002000),
    MAC_KEY_RCONTROL: (0x00002000, 0x00000001 | 0x00002000),
    MAC_KEY_LSHIFT: (0x00000002, 0x00000002 | 0x00000004),
    MAC_KEY_RSHIFT: (0x00000004, 0x00000002 | 0x00000004),
    MAC_KEY_LCOMMAND: (0x00000008, 0x00000008 | 0x00000010),
    MAC_KEY_RCOMMAND: (0x00000010, 0x00000008 | 0x00000010),
    MAC_KEY_LOPTION: (0x00000020, 0x00000020 | 0x00000040),
    MAC_KEY_ROPTION: (0x00000040, 0x00000020 | 0x00000040),
}

PERMISSION_TOAST_MESSAGE = "Povol Sledování vstupu a Zpřístupnění v Nastavení systému"
PERMISSION_LOG_MESSAGE = (
    "Nepodařilo se vytvořit CGEventTapCreate: chybí oprávnění "
    "Sledování vstupu (Input Monitoring) nebo Zpřístupnění (Accessibility). "
    "Povol je v Nastavení systému -> Soukromí a zabezpečení -> Sledování vstupu / Zpřístupnění."
)


def mac_keycode_to_vk(keycode: int) -> int | None:
    """Převede macOS virtual keycode na Windows VK kód pro sdílený HotkeyFSM."""
    return MAC_KEYCODE_TO_VK.get(int(keycode))


class GlobalHotkeyHook:
    """macOS Quartz CGEventTapCreate na vyhrazeném vlákně s CFRunLoop a frontou akcí."""

    def __init__(
        self,
        on_action: Callable[[Action], None],
        klavesa_vk: int = VK_MAC_FN,
        paused: bool = False,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_action = on_action
        self._on_error = on_error
        self._fsm = HotkeyFSM(klavesa_vk=klavesa_vk, paused=paused)
        self._action_queue: queue.SimpleQueue[Action | None] = queue.SimpleQueue()
        self._pending_lock = threading.Lock()
        self._pending_vk: int | None = None
        self._pending_paused: bool | None = None
        self._pending_reset: bool = False
        self._pending_cancel_active: bool = False
        self._tap: Any = None
        self._runloop: Any = None
        self._quartz: Any = None
        self._ready = threading.Event()
        self._permission_denied = False
        self._hook_thread: threading.Thread | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._pressed_mod_keys: set[int] = set()

    def _wake_hook_thread(self) -> None:
        rl = self._runloop
        q = self._quartz
        if rl is not None and q is not None:
            wake = getattr(q, "CFRunLoopWakeUp", None)
            if callable(wake):
                try:
                    wake(rl)
                except Exception:
                    pass
        elif self._hook_thread is None:
            self._apply_pending()

    def set_klavesa_vk(self, vk: int) -> None:
        with self._pending_lock:
            self._pending_vk = vk
        self._wake_hook_thread()

    def set_paused(self, paused: bool) -> None:
        with self._pending_lock:
            self._pending_paused = paused
        self._wake_hook_thread()

    def reset_to_idle(self) -> None:
        with self._pending_lock:
            self._pending_reset = True
        self._wake_hook_thread()

    def cancel_active(self) -> None:
        with self._pending_lock:
            self._pending_cancel_active = True
        self._wake_hook_thread()

    def _apply_pending(self) -> None:
        with self._pending_lock:
            vk = self._pending_vk
            paused = self._pending_paused
            reset = self._pending_reset
            cancel_act = self._pending_cancel_active
            self._pending_vk = None
            self._pending_paused = None
            self._pending_reset = False
            self._pending_cancel_active = False
        if vk is not None and vk != self._fsm.klavesa_vk:
            if self._fsm.state != "IDLE":
                self._action_queue.put(Action("CANCEL"))
            self._fsm.klavesa_vk = vk
            self._fsm._reset()
        if reset:
            self._fsm._reset()
            self._pressed_mod_keys.clear()
        elif cancel_act:
            self._fsm.cancel_active()
            self._pressed_mod_keys.clear()
        if paused is not None:
            if paused:
                self._pressed_mod_keys.clear()
            for act in self._fsm.set_paused(paused):
                self._action_queue.put(act)

    def start(self) -> None:
        self._permission_denied = False
        self._ready.clear()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="mluvka-hotkey-dispatch", daemon=True
        )
        self._dispatch_thread.start()
        self._hook_thread = threading.Thread(
            target=self._hook_loop, name="mluvka-hotkey-hook", daemon=True
        )
        self._hook_thread.start()
        self._ready.wait(timeout=5.0)

    def stop(self) -> None:
        rl = self._runloop
        q = self._quartz
        if rl is not None and q is not None:
            stop_fn = getattr(q, "CFRunLoopStop", None)
            if callable(stop_fn):
                try:
                    stop_fn(rl)
                except Exception:
                    pass
        if self._hook_thread is not None:
            self._hook_thread.join(timeout=2.0)
            self._hook_thread = None
        self._action_queue.put(None)
        if self._dispatch_thread is not None:
            self._dispatch_thread.join(timeout=2.0)
            self._dispatch_thread = None

    def _dispatch_loop(self) -> None:
        while True:
            act = self._action_queue.get()
            if act is None:
                break
            try:
                self._on_action(act)
            except Exception:
                log.exception("Chyba při zpracování akce klávesové zkratky")

    def _handle_cg_event(self, _proxy: Any, event_type: int, event: Any, _refcon: Any) -> Any:
        q = self._quartz
        if q is None:
            import Quartz as q
            self._quartz = q

        disabled_timeout = getattr(q, "kCGEventTapDisabledByTimeout", -2)
        disabled_user = getattr(q, "kCGEventTapDisabledByUserInput", -1)
        if event_type in (disabled_timeout, disabled_user):
            if self._tap is not None:
                try:
                    q.CGEventTapEnable(self._tap, True)
                except Exception:
                    pass
            return event

        ev_key_down = getattr(q, "kCGEventKeyDown", 10)
        ev_key_up = getattr(q, "kCGEventKeyUp", 11)
        ev_flags_changed = getattr(q, "kCGEventFlagsChanged", 12)
        if event_type not in (ev_key_down, ev_key_up, ev_flags_changed):
            return event

        user_data_field = getattr(q, "kCGEventSourceUserData", 42)
        try:
            if int(q.CGEventGetIntegerValueField(event, user_data_field)) == SYNTHETIC_EVENT_TAG:
                return event
        except Exception:
            pass

        self._apply_pending()

        keycode_field = getattr(q, "kCGKeyboardEventKeycode", 9)
        keycode = int(q.CGEventGetIntegerValueField(event, keycode_field))
        vk = mac_keycode_to_vk(keycode)
        if vk is None:
            return event

        flags = int(q.CGEventGetFlags(event))
        if event_type == ev_flags_changed:
            flag_attr = _MODIFIER_FLAG_NAMES.get(keycode)
            if not flag_attr:
                return event
            mask = int(getattr(q, flag_attr, 0))
            if not (flags & mask):
                down = False
                self._pressed_mod_keys.discard(keycode)
            elif keycode in _MODIFIER_SIDE_MASKS:
                side_bit, pair_mask = _MODIFIER_SIDE_MASKS[keycode]
                if flags & pair_mask:
                    down = bool(flags & side_bit)
                else:
                    down = keycode not in self._pressed_mod_keys
                if down:
                    self._pressed_mod_keys.add(keycode)
                else:
                    self._pressed_mod_keys.discard(keycode)
            else:
                down = True
        else:
            down = event_type == ev_key_down

        shift = bool(flags & int(getattr(q, "kCGEventFlagMaskShift", 0)))
        alt = bool(flags & int(getattr(q, "kCGEventFlagMaskAlternate", 0)))
        ctrl = bool(flags & int(getattr(q, "kCGEventFlagMaskControl", 0)))

        actions, _swallow = self._fsm.on_key(
            vk,
            down,
            time.monotonic(),
            shift,
            alt,
            ctrl,
        )
        for act in actions:
            self._action_queue.put(act)
        return event

    def _tick_timer_cb(self, *_args: Any) -> None:
        self._apply_pending()
        for act in self._fsm.tick(time.monotonic()):
            self._action_queue.put(act)

    def _hook_loop(self) -> None:
        try:
            import Quartz
        except Exception as exc:
            log.error("Quartz není dostupný (%s); globální zkratky jsou vypnuté.", exc)
            self._permission_denied = True
            self._ready.set()
            if self._on_error is not None:
                try:
                    self._on_error("Chybí pyobjc-framework-Quartz — globální zkratky jsou vypnuté")
                except Exception:
                    log.exception("on_error callback selhal")
            return

        self._quartz = Quartz
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._handle_cg_event,
            None,
        )
        if not tap:
            self._permission_denied = True
            log.error("%s", PERMISSION_LOG_MESSAGE)
            self._ready.set()
            if self._on_error is not None:
                try:
                    self._on_error(PERMISSION_TOAST_MESSAGE)
                except Exception:
                    log.exception("on_error callback selhal")
            return

        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        runloop = Quartz.CFRunLoopGetCurrent()
        self._runloop = runloop
        Quartz.CFRunLoopAddSource(runloop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)

        timer = None
        timer_create = getattr(Quartz, "CFRunLoopTimerCreate", None)
        if callable(timer_create):
            now_cf = float(Quartz.CFAbsoluteTimeGetCurrent())
            timer = timer_create(
                getattr(Quartz, "kCFAllocatorDefault", None),
                now_cf + 0.05,
                0.05,
                0,
                0,
                self._tick_timer_cb,
                None,
            )
            if timer:
                Quartz.CFRunLoopAddTimer(runloop, timer, Quartz.kCFRunLoopCommonModes)

        self._ready.set()
        try:
            Quartz.CFRunLoopRun()
        finally:
            try:
                Quartz.CGEventTapEnable(tap, False)
            except Exception:
                pass
            self._runloop = None
            self._tap = None
