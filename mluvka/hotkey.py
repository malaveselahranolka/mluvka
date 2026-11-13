"""Globální klávesové zkratky (čistý stavový automat + Win32 WH_KEYBOARD_LL)."""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading

if sys.platform == "win32":
    import ctypes.wintypes as wt
import time
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("mluvka.hotkey")

HOLD_MIN_S = 0.300
DOUBLE_TAP_WINDOW_S = 0.400

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_Z = 0x5A
VK_CAPITAL = 0x14
VK_F9 = 0x78
VK_RSHIFT = 0xA1
VK_RCONTROL = 0xA3
VK_RMENU = 0xA5
VK_RWIN = 0x5C
VK_MAC_FN = 0xFF

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_TIMER = 0x0113
WM_QUIT = 0x0012
WM_APP = 0x8000
LLKHF_INJECTED = 0x10


@dataclass(frozen=True)
class Action:
    name: str
    mode: str = ""


class HotkeyFSM:
    """Čistý stavový automat pro diktovací klávesu, režim příkazu, Esc a Alt+Shift+Z."""

    def __init__(self, klavesa_vk: int = VK_RCONTROL, paused: bool = False) -> None:
        self.klavesa_vk = klavesa_vk
        self.paused = paused
        self._state = "IDLE"
        self._down_t = 0.0
        self._up_t = 0.0
        self._hotkey_down = False
        self._ignore_next_hotkey_up = False
        self._swallow_ups: set[int] = set()
        self._pressed_special: set[int] = set()

    @property
    def state(self) -> str:
        return self._state

    def set_paused(self, paused: bool) -> list[Action]:
        self.paused = paused
        if paused:
            actions: list[Action] = []
            if self._state != "IDLE":
                actions.append(Action("CANCEL"))
            self._reset()
            return actions
        return []

    def cancel_active(self) -> None:
        """Vrátí automat do IDLE bez smazání fyzického stavu držené diktovací klávesy."""
        self._state = "IDLE"
        self._ignore_next_hotkey_up = False
        self._swallow_ups.clear()
        self._pressed_special.clear()

    def _reset(self) -> None:
        self._hotkey_down = False
        self.cancel_active()

    def tick(self, t: float) -> list[Action]:
        if self._state == "PENDING_DOUBLE_TAP" and (t - self._up_t) > DOUBLE_TAP_WINDOW_S:
            self._state = "IDLE"
            return [Action("CANCEL")]
        return []

    def on_key(
        self,
        vk: int,
        down: bool,
        t: float,
        shift: bool,
        alt: bool,
        ctrl: bool,
    ) -> tuple[list[Action], bool]:
        if self.paused:
            return [], False

        actions = self.tick(t)

        if vk == self.klavesa_vk:
            if down:
                if self._hotkey_down:
                    return actions, True
                self._hotkey_down = True
                if self._state == "IDLE":
                    if shift:
                        self._state = "COMMAND"
                        self._down_t = t
                        actions.append(Action("START", "command"))
                    else:
                        self._state = "HOLD"
                        self._down_t = t
                        actions.append(Action("START", "hold"))
                elif self._state == "PENDING_DOUBLE_TAP":
                    if (t - self._up_t) <= DOUBLE_TAP_WINDOW_S:
                        self._state = "HANDS_FREE"
                        self._ignore_next_hotkey_up = True
                        actions.append(Action("HANDS_FREE"))
                    else:
                        self._state = "HOLD"
                        self._down_t = t
                        actions.append(Action("START", "hold"))
                elif self._state == "HANDS_FREE":
                    self._state = "IDLE"
                    self._ignore_next_hotkey_up = True
                    actions.append(Action("STOP"))
                return actions, True

            # key up
            self._hotkey_down = False
            if self._ignore_next_hotkey_up:
                self._ignore_next_hotkey_up = False
                return actions, True
            if self._state == "HOLD":
                if (t - self._down_t) >= HOLD_MIN_S:
                    self._state = "IDLE"
                    actions.append(Action("STOP"))
                else:
                    self._state = "PENDING_DOUBLE_TAP"
                    self._up_t = t
            elif self._state == "COMMAND":
                self._state = "IDLE"
                if (t - self._down_t) >= HOLD_MIN_S:
                    actions.append(Action("STOP_COMMAND"))
                else:
                    actions.append(Action("CANCEL"))
            return actions, True

        if vk == VK_ESCAPE:
            if down:
                if vk in self._pressed_special:
                    return actions, vk in self._swallow_ups
                if self._state != "IDLE":
                    self._pressed_special.add(vk)
                    self._swallow_ups.add(vk)
                    self._state = "IDLE"
                    self._ignore_next_hotkey_up = False
                    actions.append(Action("CANCEL"))
                    return actions, True
                return actions, False
            self._pressed_special.discard(vk)
            if vk in self._swallow_ups:
                self._swallow_ups.discard(vk)
                return actions, True
            return actions, False

        if vk == VK_Z:
            if down:
                if vk in self._pressed_special:
                    return actions, vk in self._swallow_ups
                if self._state == "IDLE" and alt and shift and not ctrl:
                    self._pressed_special.add(vk)
                    self._swallow_ups.add(vk)
                    actions.append(Action("PASTE_LAST"))
                    return actions, True
                return actions, False
            self._pressed_special.discard(vk)
            if vk in self._swallow_ups:
                self._swallow_ups.discard(vk)
                return actions, True
            return actions, False

        return actions, False


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t


    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wt.DWORD),
            ("scanCode", wt.DWORD),
            ("flags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]


    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    user32.SetWindowsHookExW.restype = wt.HHOOK
    user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
    user32.UnhookWindowsHookEx.restype = wt.BOOL
    user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, WPARAM, LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = wt.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.TranslateMessage.restype = wt.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, WPARAM, LPARAM]
    user32.PostThreadMessageW.restype = wt.BOOL
    user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, wt.LPVOID]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
    user32.KillTimer.restype = wt.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wt.DWORD


    class GlobalHotkeyHook:
        """Win32 WH_KEYBOARD_LL na vyhrazeném vlákně s frontou akcí."""

        def __init__(
            self,
            on_action: Callable[[Action], None],
            klavesa_vk: int = VK_RCONTROL,
            paused: bool = False,
        ) -> None:
            self._on_action = on_action
            self._fsm = HotkeyFSM(klavesa_vk=klavesa_vk, paused=paused)
            self._action_queue: queue.SimpleQueue[Action | None] = queue.SimpleQueue()
            self._pending_lock = threading.Lock()
            self._pending_vk: int | None = None
            self._pending_paused: bool | None = None
            self._pending_reset: bool = False
            self._pending_cancel_active: bool = False
            self._hook_handle: wt.HHOOK | None = None
            self._hook_proc: HOOKPROC | None = None
            self._hook_tid: int = 0
            self._ready = threading.Event()
            self._start_err: Exception | None = None
            self._hook_thread: threading.Thread | None = None
            self._dispatch_thread: threading.Thread | None = None

        def _wake_hook_thread(self, tid: int) -> None:
            if tid:
                user32.PostThreadMessageW(tid, WM_APP, 0, 0)
            elif self._hook_thread is None:
                self._apply_pending()

        def set_klavesa_vk(self, vk: int) -> None:
            with self._pending_lock:
                self._pending_vk = vk
                tid = self._hook_tid
            self._wake_hook_thread(tid)

        def set_paused(self, paused: bool) -> None:
            with self._pending_lock:
                self._pending_paused = paused
                tid = self._hook_tid
            self._wake_hook_thread(tid)

        def reset_to_idle(self) -> None:
            with self._pending_lock:
                self._pending_reset = True
                tid = self._hook_tid
            self._wake_hook_thread(tid)

        def cancel_active(self) -> None:
            with self._pending_lock:
                self._pending_cancel_active = True
                tid = self._hook_tid
            self._wake_hook_thread(tid)

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
            elif cancel_act:
                self._fsm.cancel_active()
            if paused is not None:
                for act in self._fsm.set_paused(paused):
                    self._action_queue.put(act)

        def start(self) -> None:
            self._dispatch_thread = threading.Thread(
                target=self._dispatch_loop, name="mluvka-hotkey-dispatch", daemon=True
            )
            self._dispatch_thread.start()
            self._hook_thread = threading.Thread(
                target=self._hook_loop, name="mluvka-hotkey-hook", daemon=True
            )
            self._hook_thread.start()
            self._ready.wait(timeout=5.0)
            if self._start_err is not None:
                raise self._start_err

        def stop(self) -> None:
            if self._hook_tid:
                user32.PostThreadMessageW(self._hook_tid, WM_QUIT, 0, 0)
            if self._hook_thread is not None:
                self._hook_thread.join(timeout=2.0)
            self._action_queue.put(None)
            if self._dispatch_thread is not None:
                self._dispatch_thread.join(timeout=2.0)

        def _dispatch_loop(self) -> None:
            while True:
                act = self._action_queue.get()
                if act is None:
                    break
                try:
                    self._on_action(act)
                except Exception:
                    log.exception("Chyba při zpracování akce klávesové zkratky")

        def _hook_loop(self) -> None:
            self._hook_tid = int(kernel32.GetCurrentThreadId())

            def _callback(n_code: int, w_param: int, l_param: int) -> int:
                if n_code >= HC_ACTION:
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if not (kb.flags & LLKHF_INJECTED):
                        if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
                            self._apply_pending()
                            down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                            shift = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                            alt = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                            ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                            actions, swallow = self._fsm.on_key(
                                int(kb.vkCode),
                                down,
                                time.monotonic(),
                                shift,
                                alt,
                                ctrl,
                            )
                            for act in actions:
                                self._action_queue.put(act)
                            if swallow:
                                return 1
                return user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)

            self._hook_proc = HOOKPROC(_callback)
            self._hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, None, 0)
            if not self._hook_handle:
                self._start_err = ctypes.WinError(ctypes.get_last_error())
                self._ready.set()
                return

            timer_id = user32.SetTimer(None, 0, 50, None)
            self._ready.set()

            msg = wt.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == WM_TIMER:
                        self._apply_pending()
                        for act in self._fsm.tick(time.monotonic()):
                            self._action_queue.put(act)
                    elif msg.message == WM_APP:
                        self._apply_pending()
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                if timer_id:
                    user32.KillTimer(None, timer_id)
                if self._hook_handle:
                    user32.UnhookWindowsHookEx(self._hook_handle)
                    self._hook_handle = None


def __getattr__(name: str) -> Any:
    if name == "GlobalHotkeyHook" and sys.platform != "win32":
        from mluvka.hotkey_mac import GlobalHotkeyHook

        return GlobalHotkeyHook
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
