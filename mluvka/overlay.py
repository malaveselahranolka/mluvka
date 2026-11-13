"""Plovoucí stavová kapsle (tkinter + Win32/DWM) dle DESIGN_SPEC.md."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import logging
import math
import queue
import struct
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import Any

log = logging.getLogger("mluvka.overlay")

# Barvy dle DESIGN_SPEC.md
COLOR_SVESTKA = "#33223A"
COLOR_SVESTKA_OKRAJ = 0x0053354A  # COLORREF 0x00BBGGRR pro #4A3553
COLOR_TEXT = "#F6EEF7"
COLOR_TEXT_TICHY = "#BFA9C6"
COLOR_MERUNKA = "#FFB37A"
COLOR_LEVANDULE = "#C9B6FF"
COLOR_CHYBA = "#FF9B9B"

TARGET_ALPHA = 0.96
BASE_HEIGHT = 36
BASE_PAD_X = 14
BASE_METER_W = 51
BASE_BAR_W = 3
BASE_BAR_GAP = 3
BASE_BAR_MAX_H = 18.0
BASE_BAR_MIN_H = 3.0
BASE_GAP_10 = 10
BASE_GAP_8 = 8
BASE_BOTTOM_OFFSET = 24
BASE_TOAST_MAX_W = 360

STATE_WIDTHS = {
    "nahravam": 128,
    "volne_ruce": 212,
    "prikaz": 176,
    "prepisuji": 148,
}
STATE_LABELS = {
    "volne_ruce": "Ctrl ukončí",
    "prikaz": "Příkaz",
    "prepisuji": "Přepisuji",
}
BAR_WEIGHTS = (0.35, 0.50, 0.70, 0.88, 1.00, 0.88, 0.70, 0.50, 0.35)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except Exception:
    shcore = None
try:
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
except Exception:
    dwmapi = None

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

MONITOR_DEFAULTTONEAREST = 2
SPI_GETCLIENTAREAANIMATION = 0x1042
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


LRESULT = ctypes.c_ssize_t
LONG_PTR = ctypes.c_ssize_t

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wt.HWND
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HMONITOR
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.GetDpiForSystem.argtypes = []
user32.GetDpiForSystem.restype = wt.UINT
user32.SetProcessDPIAware.argtypes = []
user32.SetProcessDPIAware.restype = wt.BOOL
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, wt.LPVOID, wt.UINT]
user32.SystemParametersInfoW.restype = wt.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = LONG_PTR
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, LONG_PTR]
user32.SetWindowLongPtrW.restype = LONG_PTR
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.PrintWindow.restype = wt.BOOL

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, wt.LPVOID, wt.LPVOID, wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.DeleteDC.restype = wt.BOOL

if shcore is not None:
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    shcore.SetProcessDpiAwareness.restype = ctypes.c_long
    shcore.GetDpiForMonitor.argtypes = [wt.HMONITOR, ctypes.c_int, ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT)]
    shcore.GetDpiForMonitor.restype = ctypes.c_long

if dwmapi is not None:
    dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, wt.LPVOID, wt.DWORD]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long


def enable_dpi_awareness() -> None:
    if shcore is not None:
        try:
            shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def is_reduced_motion() -> bool:
    val = wt.BOOL(1)
    try:
        if user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(val), 0):
            return not bool(val.value)
    except Exception:
        pass
    return False


def _foreground_work_area_and_dpi() -> tuple[tuple[int, int, int, int], int]:
    hwnd_fg = user32.GetForegroundWindow()
    hmon = user32.MonitorFromWindow(hwnd_fg, MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        work = (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)
    else:
        w = int(user32.GetSystemMetrics(0)) or 1920
        h = int(user32.GetSystemMetrics(1)) or 1080
        work = (0, 0, w, h)

    dpi = 96
    if shcore is not None and hmon:
        dx = wt.UINT(96)
        dy = wt.UINT(96)
        try:
            if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                dpi = int(dx.value) or 96
                return work, dpi
        except Exception:
            pass
    try:
        dpi = int(user32.GetDpiForSystem()) or 96
    except Exception:
        dpi = 96
    return work, dpi


def _scale(px: float, dpi: int) -> int:
    return round(px * dpi / 96.0)


def _truncate_ellipsis(font: tkfont.Font, text: str, max_px: int) -> str:
    if font.measure(text) <= max_px:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid].rstrip() + ell) <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ell


def compute_meter_heights(
    state: str,
    level: float,
    t: float,
    reduced_motion: bool,
) -> list[float]:
    """Vypočte výšky 9 sloupců metru (v px při 96 DPI)."""
    if state == "prepisuji":
        if reduced_motion:
            return [0.0, 0.0, 0.0, 6.0, 6.0, 6.0, 0.0, 0.0, 0.0]
        return [
            3.0 + 7.0 * max(0.0, math.cos(2.0 * math.pi * t / 0.9 - i * 0.55))
            for i in range(9)
        ]

    heights: list[float] = []
    for i, w in enumerate(BAR_WEIGHTS):
        if reduced_motion:
            h = BASE_BAR_MAX_H * level * w
        else:
            h = BASE_BAR_MAX_H * level * w * (1.0 + 0.12 * math.sin(t * 7.0 + i * 1.3))
        heights.append(max(BASE_BAR_MIN_H, min(BASE_BAR_MAX_H, h)))
    return heights


class Overlay:
    """Vláknově bezpečná plovoucí kapsle nad hlavním vláknem Tk."""

    def __init__(self) -> None:
        enable_dpi_awareness()
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.0)
        self._root.configure(bg=COLOR_SVESTKA)
        self._root.withdraw()

        self._canvas = tk.Canvas(
            self._root,
            bg=COLOR_SVESTKA,
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill="both", expand=True)

        self._families = set(tkfont.families())
        self._dpi = 96
        self._font_timer: tkfont.Font | None = None
        self._font_label: tkfont.Font | None = None
        self._ensure_fonts(96)

        self._state: str = ""
        self._visible = False
        self._target_level = 0.0
        self._smooth_level = 0.0
        self._start_t = time.monotonic()
        self._toast_text = ""
        self._toast_kind = "info"
        self._toast_after_id: str | None = None
        self._fade_after_id: str | None = None
        self._anim_after_id: str | None = None
        self._hwnd: int = 0
        self._styles_applied = False

        self._bar_ids: list[int] = []
        self._timer_id: int | None = None
        self._label_id: int | None = None
        self._toast_id: int | None = None

        self._apply_win32_styles()
        self._root.after(16, self._poll_queue)

    @property
    def root(self) -> tk.Tk:
        return self._root

    @property
    def canvas(self) -> tk.Canvas:
        return self._canvas

    def show(self, state: str) -> None:
        self._queue.put(("show", state))

    def set_level(self, level: float) -> None:
        clamped = max(0.0, min(1.0, float(level)))
        self._queue.put(("level", clamped))

    def toast(self, text: str, kind: str = "info") -> None:
        self._queue.put(("toast", (str(text), kind)))

    def hide(self) -> None:
        self._queue.put(("hide", None))

    def call(self, fn: Any) -> None:
        self._queue.put(("call", fn))

    def quit(self) -> None:
        self._queue.put(("quit", None))

    def run(self) -> None:
        self._root.mainloop()

    def _ensure_fonts(self, dpi: int) -> None:
        if self._font_timer is not None and self._dpi == dpi:
            return
        self._dpi = dpi
        px = -max(1, _scale(12, dpi))
        timer_family = (
            "Segoe UI Variable Text Semibold"
            if "Segoe UI Variable Text Semibold" in self._families
            else "Segoe UI"
        )
        label_family = (
            "Segoe UI Variable Text"
            if "Segoe UI Variable Text" in self._families
            else "Segoe UI"
        )
        self._font_timer = tkfont.Font(root=self._root, family=timer_family, size=px, weight="bold")
        self._font_label = tkfont.Font(root=self._root, family=label_family, size=px, weight="normal")

    def _apply_win32_styles(self) -> None:
        self._root.update_idletasks()
        wid = self._root.winfo_id()
        hwnd = int(user32.GetParent(wid) or wid)
        if not hwnd:
            return
        if self._styles_applied and hwnd == self._hwnd:
            return
        self._hwnd = hwnd
        ex_style = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        ex_style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        ex_style &= ~WS_EX_APPWINDOW
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex_style)
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        if dwmapi is not None:
            try:
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_WINDOW_CORNER_PREFERENCE,
                    ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
                    4,
                )
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_BORDER_COLOR,
                    ctypes.byref(wt.DWORD(COLOR_SVESTKA_OKRAJ)),
                    4,
                )
            except Exception:
                pass
        self._styles_applied = True

    def _cancel_fade(self) -> None:
        if self._fade_after_id is not None:
            self._root.after_cancel(self._fade_after_id)
            self._fade_after_id = None

    def _cancel_toast_timer(self) -> None:
        if self._toast_after_id is not None:
            self._root.after_cancel(self._toast_after_id)
            self._toast_after_id = None

    def _cancel_anim(self) -> None:
        if self._anim_after_id is not None:
            self._root.after_cancel(self._anim_after_id)
            self._anim_after_id = None

    def _poll_queue(self) -> None:
        latest_level: float | None = None
        try:
            while True:
                cmd, arg = self._queue.get_nowait()
                if cmd == "level":
                    latest_level = float(arg)
                elif cmd == "show":
                    self._handle_show(str(arg))
                elif cmd == "toast":
                    text, kind = arg
                    self._handle_toast(text, kind)
                elif cmd == "hide":
                    self._handle_hide()
                elif cmd == "call":
                    arg()
                elif cmd == "quit":
                    self._cancel_fade()
                    self._cancel_toast_timer()
                    self._cancel_anim()
                    self._root.quit()
                    return
        except queue.Empty:
            pass
        if latest_level is not None:
            self._target_level = latest_level
        self._root.after(16, self._poll_queue)

    def _handle_show(self, state: str) -> None:
        if state not in STATE_WIDTHS:
            return
        self._cancel_toast_timer()
        self._cancel_fade()
        prev_state = self._state
        was_visible = self._visible
        self._state = state

        if state == "volne_ruce" and prev_state == "nahravam":
            pass
        elif state in ("nahravam", "volne_ruce", "prikaz"):
            if not (was_visible and prev_state == state):
                self._start_t = time.monotonic()
                self._smooth_level = 0.0
                self._target_level = 0.0

        self._layout_and_position()
        self._reveal(was_visible)
        self._start_anim()

    def _handle_toast(self, text: str, kind: str) -> None:
        self._cancel_toast_timer()
        self._cancel_fade()
        self._cancel_anim()
        was_visible = self._visible
        self._state = "zprava"
        self._toast_text = text
        self._toast_kind = kind
        self._layout_and_position()
        self._reveal(was_visible)
        self._toast_after_id = self._root.after(2400, self._handle_hide)

    def _handle_hide(self) -> None:
        self._cancel_toast_timer()
        self._cancel_fade()
        self._cancel_anim()
        if not self._visible:
            self._state = ""
            return
        if is_reduced_motion():
            self._finish_hide()
            return
        self._fade_step(0.96, 0.0, 8, 20, on_done=self._finish_hide)

    def _finish_hide(self) -> None:
        self._visible = False
        self._state = ""
        self._root.attributes("-alpha", 0.0)
        self._root.withdraw()

    def _reveal(self, was_visible: bool) -> None:
        self._apply_win32_styles()
        self._root.deiconify()
        self._apply_win32_styles()
        self._visible = True
        if was_visible or is_reduced_motion():
            self._root.attributes("-alpha", TARGET_ALPHA)
            return
        self._root.attributes("-alpha", 0.0)
        self._fade_step(0.0, TARGET_ALPHA, 6, 20, on_done=None)

    def _fade_step(
        self,
        start_a: float,
        end_a: float,
        steps: int,
        step_ms: int,
        on_done: Any,
        idx: int = 1,
    ) -> None:
        alpha = start_a + (end_a - start_a) * (idx / float(steps))
        self._root.attributes("-alpha", max(0.0, min(TARGET_ALPHA, alpha)))
        if idx >= steps:
            self._fade_after_id = None
            if on_done is not None:
                on_done()
            return
        self._fade_after_id = self._root.after(
            step_ms,
            lambda: self._fade_step(start_a, end_a, steps, step_ms, on_done, idx + 1),
        )

    def _layout_and_position(self) -> None:
        work, dpi = _foreground_work_area_and_dpi()
        self._ensure_fonts(dpi)
        assert self._font_timer is not None and self._font_label is not None

        pad_x = _scale(BASE_PAD_X, dpi)
        win_h = _scale(BASE_HEIGHT, dpi)
        cy = win_h / 2.0

        self._canvas.delete("all")
        self._bar_ids = []
        self._timer_id = None
        self._label_id = None
        self._toast_id = None

        if self._state == "zprava":
            max_w = _scale(BASE_TOAST_MAX_W, dpi)
            max_text_w = max(10, max_w - 2 * pad_x)
            shown = _truncate_ellipsis(self._font_label, self._toast_text, max_text_w)
            text_w = self._font_label.measure(shown)
            win_w = min(max_w, max(2 * pad_x + 1, text_w + 2 * pad_x))
            color = COLOR_CHYBA if self._toast_kind == "error" else COLOR_TEXT
            self._toast_id = self._canvas.create_text(
                win_w / 2.0,
                cy,
                text=shown,
                fill=color,
                font=self._font_label,
                anchor="center",
            )
        else:
            win_w = _scale(STATE_WIDTHS[self._state], dpi)
            meter_w = _scale(BASE_METER_W, dpi)
            gap10 = _scale(BASE_GAP_10, dpi)
            gap8 = _scale(BASE_GAP_8, dpi)
            bar_w = max(1, _scale(BASE_BAR_W, dpi))

            label_txt = STATE_LABELS.get(self._state, "")
            has_timer = self._state in ("nahravam", "volne_ruce", "prikaz")
            timer_slot_w = self._font_timer.measure("00:00") if has_timer else 0
            label_w = self._font_label.measure(label_txt) if label_txt else 0

            content_w = meter_w + gap10
            if has_timer:
                content_w += timer_slot_w
                if label_txt:
                    content_w += gap8 + label_w
            elif label_txt:
                content_w += label_w

            x0 = max(pad_x, (win_w - content_w) // 2)
            bar_color = COLOR_LEVANDULE if self._state == "prikaz" else COLOR_MERUNKA

            for i in range(9):
                raw_bx = x0 + (1.5 + i * 6.0) * dpi / 96.0
                bx = float(round(raw_bx)) if bar_w % 2 == 0 else (math.floor(raw_bx) + 0.5)
                item_id = self._canvas.create_line(
                    bx,
                    cy,
                    bx,
                    cy,
                    width=bar_w,
                    capstyle=tk.ROUND,
                    fill=bar_color,
                )
                self._bar_ids.append(item_id)

            cursor_x = x0 + meter_w + gap10
            if has_timer:
                elapsed = max(0, int(time.monotonic() - self._start_t))
                timer_str = f"{elapsed // 60}:{elapsed % 60:02d}"
                self._timer_id = self._canvas.create_text(
                    cursor_x,
                    cy,
                    text=timer_str,
                    fill=COLOR_TEXT,
                    font=self._font_timer,
                    anchor="w",
                )
                cursor_x += timer_slot_w + gap8

            if label_txt:
                self._label_id = self._canvas.create_text(
                    cursor_x,
                    cy,
                    text=label_txt,
                    fill=COLOR_TEXT_TICHY,
                    font=self._font_label,
                    anchor="w",
                )

        left, _top, right, bottom = work
        x = left + (right - left - win_w) // 2
        y = bottom - _scale(BASE_BOTTOM_OFFSET, dpi) - win_h
        self._canvas.configure(width=win_w, height=win_h)
        self._root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def _start_anim(self) -> None:
        self._cancel_anim()
        self._anim_tick()

    def _anim_tick(self) -> None:
        if self._state not in STATE_WIDTHS:
            self._anim_after_id = None
            return
        now = time.monotonic()
        reduced = is_reduced_motion()
        k = 0.55 if self._target_level > self._smooth_level else 0.12
        self._smooth_level += (self._target_level - self._smooth_level) * k

        heights = compute_meter_heights(self._state, self._smooth_level, now, reduced)
        dpi = self._dpi
        win_h = _scale(BASE_HEIGHT, dpi)
        cy = win_h / 2.0
        bar_w = max(1, _scale(BASE_BAR_W, dpi))

        for item_id, h_96 in zip(self._bar_ids, heights):
            if h_96 <= 0.0:
                self._canvas.itemconfigure(item_id, state="hidden")
                continue
            self._canvas.itemconfigure(item_id, state="normal")
            h_px = max(float(bar_w), h_96 * dpi / 96.0)
            half_seg = max(0.0, (h_px - bar_w) / 2.0)
            x1, _y1, _x2, _y2 = self._canvas.coords(item_id)
            self._canvas.coords(item_id, x1, cy - half_seg, x1, cy + half_seg)

        if self._timer_id is not None:
            elapsed = max(0, int(now - self._start_t))
            self._canvas.itemconfigure(self._timer_id, text=f"{elapsed // 60}:{elapsed % 60:02d}")

        self._anim_after_id = self._root.after(33, self._anim_tick)


def _capture_overlay_shot(ov: Overlay, out_path: Path) -> None:
    ov.root.update_idletasks()
    ov.root.update()
    w = ov.root.winfo_width()
    h = ov.root.winfo_height()
    x = ov.root.winfo_rootx()
    y = ov.root.winfo_rooty()

    try:
        from PIL import ImageGrab

        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        img.save(out_path)
        return
    except Exception:
        pass

    from PIL import Image

    hwnd = ov._hwnd or int(user32.GetParent(ov.root.winfo_id()) or ov.root.winfo_id())
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old_bmp = gdi32.SelectObject(mem, bmp)
    try:
        user32.PrintWindow(hwnd, mem, 2)
        bmi = struct.pack("IiiHHIIIIII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = (ctypes.c_ubyte * (w * h * 4))()
        gdi32.GetDIBits(mem, bmp, 0, h, buf, bmi, 0)
        img = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1)
    finally:
        if old_bmp:
            gdi32.SelectObject(mem, old_bmp)
        gdi32.DeleteDC(mem)
        gdi32.DeleteObject(bmp)
        user32.ReleaseDC(0, hdc)
    img.save(out_path)


def _run_demo(shots_dir: Path | None = None) -> None:
    ov = Overlay()
    stop_flag = threading.Event()
    if shots_dir is not None:
        shots_dir.mkdir(parents=True, exist_ok=True)
        from mluvka.tray import kresli_ikonu

        for stav in ("pripraveno", "nahravam", "pozastaveno", "nacita"):
            icon_64 = kresli_ikonu(stav)
            for size in (16, 24, 32):
                from PIL import Image

                icon_64.resize((size, size), Image.Resampling.LANCZOS).save(
                    shots_dir / f"tray_{stav}_{size}.png"
                )

    def _drive() -> None:
        states = [
            ("show", "nahravam", 3.0),
            ("show", "volne_ruce", 3.0),
            ("show", "prikaz", 3.0),
            ("show", "prepisuji", 3.0),
            ("toast_info", "Mluvka je připravená. Drž pravý Ctrl a mluv.", 2.4),
            ("toast_error", "Mikrofon nic nezachytil — zkontroluj vstup", 2.4),
        ]
        shot_idx = 0
        for kind, val, dur in states:
            if stop_flag.is_set():
                return
            if kind == "show":
                ov.show(val)
            elif kind == "toast_info":
                ov.toast(val, kind="info")
            else:
                ov.toast(val, kind="error")

            t0 = time.monotonic()
            shot_taken = False
            while time.monotonic() - t0 < dur and not stop_flag.is_set():
                if kind == "show" and val != "prepisuji":
                    phase = (time.monotonic() - t0) * 3.0
                    ov.set_level(0.55 + 0.4 * math.sin(phase))
                if shots_dir is not None and not shot_taken and (time.monotonic() - t0) >= 0.45:
                    shot_taken = True
                    shot_idx += 1
                    done = threading.Event()
                    shot_path = shots_dir / f"{shot_idx:02d}_{val if kind == 'show' else kind}.png"
                    ov.call(lambda p=shot_path, d=done: (_capture_overlay_shot(ov, p), d.set()))
                    d_ok = done.wait(timeout=2.0)
                    if not d_ok:
                        log.warning("Timeout při ukládání snímku")
                time.sleep(0.05)
        ov.quit()

    driver = threading.Thread(target=_drive, daemon=True)
    driver.start()
    try:
        ov.run()
    finally:
        stop_flag.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mluvka.overlay")
    parser.add_argument("--demo", action="store_true", help="Přehraje ukázku všech stavů")
    parser.add_argument("--shots", type=Path, default=None, help="Uloží snímky stavů a ikon")
    args = parser.parse_args(argv)
    if args.demo:
        _run_demo(args.shots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
