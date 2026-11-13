"""Ikona a kontextové menu v oznamovací oblasti Windows (pystray + PIL)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from mluvka import audio, channel, config, history, llm

COLOR_SVESTKA = (0x33, 0x22, 0x3A, 255)
COLOR_MERUNKA = (0xFF, 0xB3, 0x7A, 255)
COLOR_MERUNKA_DIM = (0xFF, 0xB3, 0x7A, round(0.45 * 255))
COLOR_PAUSED_BARS = (0x8A, 0x7D, 0x8F, 255)

_BAR_HEIGHTS_64 = (18, 34, 26)
_BAR_WIDTH_64 = 10
_BAR_GAP_64 = 6
_BAR_RADIUS_64 = 5

CSIDL_STARTUP = 7
log = logging.getLogger("mluvka.tray")

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.SHGetFolderPathW.argtypes = [wt.HWND, ctypes.c_int, wt.HANDLE, wt.DWORD, wt.LPWSTR]
shell32.SHGetFolderPathW.restype = ctypes.c_long


def kresli_ikonu(stav: str) -> Image.Image:
    """Nakreslí ikonu 64x64 RGBA se vyhlazením z 256x256 podle DESIGN_SPEC.md."""
    scale = 4
    size = 64 * scale
    if stav == "nahravam":
        bg = COLOR_MERUNKA
        fg = COLOR_SVESTKA
    elif stav == "pozastaveno":
        bg = COLOR_SVESTKA
        fg = COLOR_PAUSED_BARS
    elif stav == "nacita":
        bg = COLOR_SVESTKA
        fg = COLOR_MERUNKA_DIM
    else:
        bg = COLOR_SVESTKA
        fg = COLOR_MERUNKA

    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_bg = ImageDraw.Draw(base)
    draw_bg.ellipse([0, 0, size - 1, size - 1], fill=bg)

    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_fg = ImageDraw.Draw(overlay)

    bar_w = _BAR_WIDTH_64 * scale
    gap = _BAR_GAP_64 * scale
    radius = _BAR_RADIUS_64 * scale
    total_w = 3 * bar_w + 2 * gap
    x0 = (size - total_w) // 2

    for idx, h64 in enumerate(_BAR_HEIGHTS_64):
        bh = h64 * scale
        bx0 = x0 + idx * (bar_w + gap)
        by0 = (size - bh) // 2
        draw_fg.rounded_rectangle(
            [bx0, by0, bx0 + bar_w - 1, by0 + bh - 1],
            radius=radius,
            fill=fg,
        )

    composed = Image.alpha_composite(base, overlay)
    return composed.resize((64, 64), Image.Resampling.LANCZOS)


def startup_folder() -> Path:
    buf = ctypes.create_unicode_buffer(wt.MAX_PATH)
    try:
        if shell32.SHGetFolderPathW(None, CSIDL_STARTUP, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_cmd_path(folder: Path | None = None) -> Path:
    return (folder or startup_folder()) / "Mluvka.cmd"


def set_autostart(
    enabled: bool,
    *,
    startup_dir: Path | None = None,
    data: Path | None = None,
    repo: Path | None = None,
) -> Path:
    cmd_path = startup_cmd_path(startup_dir)
    if enabled:
        cmd_path.parent.mkdir(parents=True, exist_ok=True)
        d = data or config.data_dir()
        r = repo or config.repo_dir()
        pythonw = d / "venv" / "Scripts" / "pythonw.exe"
        content = (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f'cd /d "{r}"\r\n'
            f'start "" "{pythonw}" -m mluvka\r\n'
        )
        cmd_path.write_text(content, encoding="utf-8")
    else:
        if cmd_path.exists():
            cmd_path.unlink()
    return cmd_path


class TrayController:
    """Ovládání tray ikony a kontextového menu Mluvky."""

    def __init__(
        self,
        *,
        get_cfg: Callable[[], dict],
        is_ready: Callable[[], bool],
        is_paused: Callable[[], bool],
        on_paste_last: Callable[[], None],
        on_select_mic: Callable[[str], None],
        on_toggle_ai: Callable[[], None],
        on_toggle_channel: Callable[[], None],
        on_toggle_sounds: Callable[[], None],
        on_toggle_paused: Callable[[], None],
        on_toggle_autostart: Callable[[], None],
        on_quit: Callable[[], None],
        on_open_window: Callable[[], None] | None = None,
        data: Path | None = None,
    ) -> None:
        self._get_cfg = get_cfg
        self._is_ready = is_ready
        self._is_paused = is_paused
        self._on_paste_last = on_paste_last
        self._on_select_mic = on_select_mic
        self._on_toggle_ai = on_toggle_ai
        self._on_toggle_channel = on_toggle_channel
        self._on_toggle_sounds = on_toggle_sounds
        self._on_toggle_paused = on_toggle_paused
        self._on_toggle_autostart = on_toggle_autostart
        self._on_quit = on_quit
        self._on_open_window = on_open_window
        self._data = data

        self._icons = {
            s: kresli_ikonu(s) for s in ("pripraveno", "nahravam", "pozastaveno", "nacita")
        }
        self._state = "nacita"
        self._icon = pystray.Icon(
            "Mluvka",
            icon=self._icons["nacita"],
            title="Mluvka",
            menu=self._build_menu(),
        )

    def _mic_menu(self) -> pystray.Menu:
        def _items():
            devices = audio.list_input_devices()

            def _selected_device() -> str | None:
                cur = str(self._get_cfg().get("mikrofon", "") or "").strip().casefold()
                if not cur:
                    return None
                for dev in devices:
                    if dev.casefold() == cur:
                        return dev
                for dev in devices:
                    if cur in dev.casefold():
                        return dev
                return None

            yield pystray.MenuItem(
                "Výchozí (systém)",
                lambda icon, item: self._on_select_mic(""),
                checked=lambda item: _selected_device() is None,
                radio=True,
            )
            # pystray rejects actions with more than 2 parameters, so bind the name via a factory.
            def _pick(n: str):
                return lambda icon, item: self._on_select_mic(n)

            for name in devices:
                yield pystray.MenuItem(
                    name,
                    _pick(name),
                    checked=lambda item, n=name: _selected_device() == n,
                    radio=True,
                )

        return pystray.Menu(_items)

    def _open_window_cb(self) -> None:
        if self._on_open_window is not None:
            self._on_open_window()

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Otevřít Mluvku",
                lambda icon, item: self._open_window_cb(),
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: (
                    "Mluvka — připraveno" if self._is_ready() else "Mluvka — načítám model…"
                ),
                None,
                enabled=False,
            ),
            pystray.MenuItem("Vložit poslední přepis", lambda icon, item: self._on_paste_last()),
            pystray.MenuItem(
                "Otevřít historii",
                lambda icon, item: self._open_history(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Mikrofon", self._mic_menu()),
            pystray.MenuItem(
                "AI úprava textu",
                lambda icon, item: self._on_toggle_ai(),
                checked=lambda item: bool(self._get_cfg().get("ai_uprava", False))
                and llm.is_available(self._data),
                enabled=lambda item: llm.is_available(self._data),
            ),
            pystray.MenuItem(
                "Posílat do kanálu",
                lambda icon, item: self._on_toggle_channel(),
                checked=lambda item: bool(self._get_cfg().get("kanal_zapnuto", True)),
                enabled=lambda item: channel.is_valid_webhook_url(
                    str(self._get_cfg().get("kanal_webhook", "") or "")
                ),
            ),
            pystray.MenuItem(
                "Zvuky",
                lambda icon, item: self._on_toggle_sounds(),
                checked=lambda item: bool(self._get_cfg().get("zvuky", True)),
            ),
            pystray.MenuItem(
                "Pozastavit zkratky",
                lambda icon, item: self._on_toggle_paused(),
                checked=lambda item: self._is_paused(),
            ),
            pystray.MenuItem(
                "Spouštět po přihlášení",
                lambda icon, item: self._on_toggle_autostart(),
                checked=lambda item: bool(self._get_cfg().get("autostart", False)),
            ),
            pystray.MenuItem(
                "Upravit nastavení…",
                lambda icon, item: subprocess.Popen(
                    ["notepad.exe", str(config.config_path(self._data))]
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Ukončit", lambda icon, item: self._on_quit()),
        )

    def start(self) -> None:
        self._icon.run_detached()

    def set_state(self, stav: str) -> None:
        if stav in self._icons and stav != self._state:
            self._state = stav
            self._icon.icon = self._icons[stav]
        self._icon.update_menu()

    def refresh_menu(self) -> None:
        self._icon.update_menu()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass

    def _open_history(self) -> None:
        md_path = history.export_markdown(data=self._data)
        try:
            os.startfile(str(md_path))
        except OSError as exc:
            log.warning("os.startfile pro historii selhalo (%s); otevírám v Notepadu.", exc)
            try:
                subprocess.Popen(["notepad.exe", str(md_path)])
            except Exception as exc2:
                log.warning("Nepodařilo se otevřít historii: %s", exc2)
