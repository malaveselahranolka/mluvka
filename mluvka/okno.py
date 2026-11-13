"""Hlavní okno aplikace Mluvka (pywebview/EdgeChromium + JS API)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import posixpath
import subprocess
import sys
import threading
import time
import urllib.error
import weakref
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import mluvka
from mluvka import (
    audio,
    channel,
    config,
    dar,
    doladeni,
    history,
    llm,
    mereni,
    netguard,
    output,
    pamet,
    slovnik_import,
    tray,
    trenink,
    zastupci,
)

log = logging.getLogger("mluvka.okno")
_MODEL_MERENI = mereni.Mereni()  # měření setupu na stránce Model (jen jedno najednou)

APP_MUTEX_NAME = "Local\\Mluvka"
OKNO_MUTEX_NAME = "Local\\MluvkaOkno"
APP_USER_MODEL_ID = "Mluvka.Mluvka"

ALLOWED_VK = frozenset({163, 161, 20, 120})
ALLOWED_HISTORY_DAYS = frozenset({1, 7, 30, 90, 365})
ALLOWED_OPEN_URLS = frozenset(
    {
        "https://console.anthropic.com",
        "https://console.anthropic.com/",
    }
)
ALLOWED_SETTINGS_KEYS = frozenset(
    {
        "klavesa_vk",
        "mikrofon",
        "zvuky",
        "ai_uprava",
        "slovnik_do_promptu",
        "slovnik",
        "zkratky",
        "kanal_webhook",
        "kanal_zapnuto",
        "historie",
        "historie_dny",
        "autostart",
        "trenink_ukladat_zvuk",
    }
)
BOOLEAN_KEYS = frozenset(
    {
        "zvuky",
        "ai_uprava",
        "slovnik_do_promptu",
        "kanal_zapnuto",
        "historie",
        "autostart",
        "trenink_ukladat_zvuk",
    }
)

ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
SW_RESTORE = 9
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXICON = 11
SM_CYICON = 12
SM_CXSMICON = 49
SM_CYSMICON = 50
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
kernel32.OpenMutexW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE,
    wt.DWORD,
    wt.LPWSTR,
    ctypes.POINTER(wt.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL

user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.LoadImageW.argtypes = [
    wt.HINSTANCE,
    wt.LPCWSTR,
    wt.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wt.UINT,
]
user32.LoadImageW.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]
user32.DestroyIcon.restype = wt.BOOL
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wt.LPCWSTR]
shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long


def is_app_running() -> bool:
    h = kernel32.OpenMutexW(SYNCHRONIZE, False, APP_MUTEX_NAME)
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


def is_window_running() -> bool:
    h = kernel32.OpenMutexW(SYNCHRONIZE, False, OKNO_MUTEX_NAME)
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


def _window_process_name(hwnd: int) -> str:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_proc:
        return ""
    try:
        size = wt.DWORD(512)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
    finally:
        kernel32.CloseHandle(h_proc)
    return ""


def find_existing_window(title: str = "Mluvka") -> int:
    matches: list[int] = []

    def _cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length != len(title):
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value != title:
            return True
        proc = _window_process_name(hwnd)
        if "python" in proc:
            matches.append(int(hwnd))
            return False
        return True

    cb = WNDENUMPROC(_cb)
    user32.EnumWindows(cb, 0)
    return matches[0] if matches else 0


def focus_existing_window(title: str = "Mluvka") -> bool:
    hwnd = 0
    for _ in range(20):
        hwnd = find_existing_window(title)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else 5)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True


def _pythonw_executable(data: Path | None = None) -> str:
    curr_pythonw = Path(sys.executable).with_name("pythonw.exe")
    if curr_pythonw.is_file():
        return str(curr_pythonw)
    venv_pythonw = (data or config.data_dir()) / "venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.is_file():
        return str(venv_pythonw)
    return sys.executable


def launch_detached_module(module: str, *, data: Path | None = None) -> subprocess.Popen[bytes]:
    exe = _pythonw_executable(data)
    env = dict(os.environ)
    if data is not None:
        env["MLUVKA_DATA"] = str(data)
    return subprocess.Popen(
        [exe, "-m", module],
        cwd=str(config.repo_dir()),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def launch_window(data: Path | None = None) -> None:
    if is_window_running() and focus_existing_window("Mluvka"):
        return
    launch_detached_module("mluvka.okno", data=data)


def _apply_window_icon(hwnd: int, ico: Path) -> tuple[int, int]:
    if not hwnd or not ico.is_file():
        return 0, 0
    cx_big = int(user32.GetSystemMetrics(SM_CXICON)) or 32
    cy_big = int(user32.GetSystemMetrics(SM_CYICON)) or 32
    cx_small = int(user32.GetSystemMetrics(SM_CXSMICON)) or 16
    cy_small = int(user32.GetSystemMetrics(SM_CYSMICON)) or 16
    h_big = int(
        user32.LoadImageW(None, str(ico), IMAGE_ICON, cx_big, cy_big, LR_LOADFROMFILE) or 0
    )
    h_small = int(
        user32.LoadImageW(None, str(ico), IMAGE_ICON, cx_small, cy_small, LR_LOADFROMFILE) or 0
    )
    if h_big:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
    if h_small:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
    return h_big, h_small


def _destroy_icons(handles: list[int]) -> None:
    for h in handles:
        if h:
            user32.DestroyIcon(h)
    handles.clear()


def _system_is_dark() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(val) == 0
    except Exception:
        return False


def _validate_pair_list(raw: Any, label: str) -> list[list[str]]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"Neplatný seznam pro {label}.")
    out: list[list[str]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"Položka v {label} musí mít dvě textová pole.")
        trig, body = item[0], item[1]
        if not isinstance(trig, str) or not isinstance(body, str):
            raise ValueError(f"Položka v {label} musí obsahovat text.")
        t = trig.strip()
        if not t or len(t) > 60:
            raise ValueError(f"Spouštěč v {label} musí mít 1 až 60 znaků.")
        if len(body) > 4000:
            raise ValueError(f"Text v {label} může mít nejvýše 4000 znaků.")
        out.append([t, body])
    return out


def _validate_words(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("Neplatný seznam slov.")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("Slovo ve slovníku musí být text.")
        word = item.strip()
        if not word:
            raise ValueError("Slovo ve slovníku nesmí být prázdné.")
        if len(word) > 60:
            raise ValueError("Slovo ve slovníku může mít nejvýše 60 znaků.")
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out


def _validate_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("Nastavení musí být objekt.")
    unknown = set(patch.keys()) - ALLOWED_SETTINGS_KEYS
    if unknown:
        raise ValueError(f"Neznámé pole nastavení: {', '.join(sorted(unknown))}.")

    cleaned: dict[str, Any] = {}
    for key, val in patch.items():
        if key == "klavesa_vk":
            if isinstance(val, bool) or not isinstance(val, int) or val not in ALLOWED_VK:
                raise ValueError("Neplatná klávesa pro diktát.")
            cleaned[key] = val
        elif key == "historie_dny":
            if isinstance(val, bool) or not isinstance(val, int) or val not in ALLOWED_HISTORY_DAYS:
                raise ValueError("Neplatná doba uchování historie.")
            cleaned[key] = val
        elif key == "kanal_webhook":
            if not isinstance(val, str):
                raise ValueError("Adresa kanálu musí být text.")
            url = val.strip()
            if url and not channel.is_valid_webhook_url(url):
                raise ValueError("Adresa kanálu musí začínat https:// a mít platnou doménu.")
            cleaned[key] = url
        elif key == "mikrofon":
            if not isinstance(val, str):
                raise ValueError("Název mikrofonu musí být text.")
            cleaned[key] = val.strip()[:200]
        elif key in BOOLEAN_KEYS:
            if not isinstance(val, bool):
                raise ValueError(f"Hodnota {key} musí být ano/ne.")
            cleaned[key] = val
        elif key == "slovnik":
            if not isinstance(val, dict):
                raise ValueError("Neplatná struktura slovníku.")
            unknown_sub = set(val.keys()) - {"slova", "opravy"}
            if unknown_sub:
                raise ValueError(f"Neznámé pole slovníku: {', '.join(sorted(unknown_sub))}.")
            sub_clean: dict[str, Any] = {}
            if "slova" in val:
                sub_clean["slova"] = _validate_words(val["slova"])
            if "opravy" in val:
                sub_clean["opravy"] = _validate_pair_list(val["opravy"], "opravách")
            cleaned[key] = sub_clean
        elif key == "zkratky":
            cleaned[key] = _validate_pair_list(val, "zkratkách")
    return cleaned


class _ApiState:
    """Interní stav instance Api uložený mimo instanci Api (pywebview ochrana)."""

    def __init__(self, data: Path | None) -> None:
        self.data = data
        self.cfg_store = config.ConfigStore(data)
        self.rec_lock = threading.Lock()
        self.recorder: audio.Recorder | None = None
        self.rec_index: int = 0
        self.rec_started_at: float = 0.0
        self.level_lock = threading.Lock()
        self.level_val: float = 0.0
        self.meas_lock = threading.Lock()
        self.meas_cancel = threading.Event()
        self.meas_thread: threading.Thread | None = None
        self.meas_state: dict[str, Any] = {
            "running": False,
            "done": 0,
            "total": 0,
            "result": None,
            "error": "",
        }


_API_STATES: weakref.WeakKeyDictionary[Api, _ApiState] = weakref.WeakKeyDictionary()


def _state_for(api: Api) -> _ApiState:
    return _API_STATES[api]


def _opravy_key_set(cfg: dict[str, Any]) -> set[tuple[str, str]]:
    sl = cfg.get("slovnik") or {}
    opravy = sl.get("opravy") or []
    return {
        (str(p[0]).strip().casefold(), str(p[1]).strip().casefold())
        for p in opravy
        if isinstance(p, (list, tuple)) and len(p) == 2
    }


def _pamet_list(state: _ApiState, limit: int = 20) -> list[dict[str, Any]]:
    cfg = state.cfg_store.snapshot()
    existing = _opravy_key_set(cfg)
    items = pamet.top(limit, data=state.data)
    for item in items:
        item["in_slovnik"] = (item["z"].casefold(), item["na"].casefold()) in existing
    return items


def _build_state(state: _ApiState) -> dict[str, Any]:
    state.cfg_store.reload_if_changed()
    cfg = state.cfg_store.snapshot()
    hosts = config.allowed_hosts(cfg)
    raw_key = str(cfg.pop("claude_api_klic", "") or "").strip()
    env_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    tail = raw_key[-4:] if len(raw_key) >= 4 else ""
    return {
        "ok": True,
        "config": cfg,
        "config_invalid": state.cfg_store.config_invalid,
        "api_key_set": bool(raw_key),
        "api_key_tail": tail,
        "env_key": env_key,
        "devices": audio.list_input_devices(),
        "app_running": is_app_running(),
        "allowed_hosts": hosts,
        "stats": history.stats(days=7, data=state.data),
        "version": mluvka.__version__,
    }


def _cleanup_api(api: Api) -> None:
    state = _API_STATES.get(api)
    if state is None:
        return
    with state.rec_lock:
        rec = state.recorder
        state.recorder = None
        state.rec_index = 0
        state.rec_started_at = 0.0
    if rec is not None:
        try:
            rec.cancel()
        except Exception:
            pass
    trenink.stop_playback()
    with state.meas_lock:
        state.meas_cancel.set()


class Api:
    """Veřejné Python API vystavené do WebView jako window.pywebview.api."""

    def __init__(self, data: Path | None = None) -> None:
        _API_STATES[self] = _ApiState(data)

    def get_state(self) -> dict[str, Any]:
        try:
            return _build_state(_state_for(self))
        except Exception as exc:
            log.exception("Chyba v get_state")
            return {"ok": False, "error": f"Nepodařilo se načíst stav ({type(exc).__name__})."}

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        state = _state_for(self)
        try:
            validated = _validate_patch(patch)
            state.cfg_store.reload_if_changed()
            if state.cfg_store.config_invalid:
                raise config.ConfigInvalidError(config.CONFIG_INVALID_MESSAGE)
            prev_autostart = bool(state.cfg_store.get("autostart", False))
            if "autostart" in validated and validated["autostart"] != prev_autostart:
                tray.set_autostart(validated["autostart"], data=state.data)
            if validated:
                updated = state.cfg_store.update_values(validated)
            else:
                updated = state.cfg_store.snapshot()
            netguard.set_allowed_hosts(config.allowed_hosts(updated))
            return _build_state(state)
        except config.ConfigInvalidError as exc:
            return {"ok": False, "error": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v save_settings")
            return {"ok": False, "error": f"Uložení nastavení selhalo ({type(exc).__name__})."}

    def set_api_key(self, key: str) -> dict[str, Any]:
        state = _state_for(self)
        try:
            if not isinstance(key, str):
                return {"ok": False, "error": "API klíč musí být text."}
            cleaned = key.strip()
            state.cfg_store.set_value("claude_api_klic", cleaned)
            netguard.set_allowed_hosts(config.allowed_hosts(state.cfg_store.snapshot()))
            return _build_state(state)
        except config.ConfigInvalidError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v set_api_key")
            return {"ok": False, "error": f"Uložení klíče selhalo ({type(exc).__name__})."}

    def test_api_key(self) -> dict[str, Any]:
        state = _state_for(self)
        ai: llm.ClaudeAI | None = None
        try:
            state.cfg_store.reload_if_changed()
            netguard.set_allowed_hosts(config.allowed_hosts(state.cfg_store.snapshot()))
            if not llm.is_available(state.data):
                return {
                    "ok": False,
                    "error": "API klíč není nastavený. Zadej klíč a klikni na Uložit.",
                }
            import anthropic

            ai = llm.ClaudeAI(state.data)
            try:
                ai.chat("Odpověz jedním českým slovem.", "Ahoj", timeout=8.0)
            except anthropic.AuthenticationError:
                return {"ok": False, "error": "Neplatný API klíč. Zkontroluj ho v console.anthropic.com."}
            except anthropic.RateLimitError:
                return {"ok": False, "error": "Překročen limit požadavků. Zkus to za chvíli znovu."}
            except (anthropic.APIConnectionError, anthropic.APITimeoutError):
                return {"ok": False, "error": "Nepodařilo se spojit s api.anthropic.com. Zkontroluj připojení k internetu."}
            except anthropic.APIStatusError as exc:
                return {"ok": False, "error": f"Anthropic API vrátilo chybu ({exc.status_code}). Zkontroluj stav účtu a klíč."}
            return {"ok": True}
        except (OSError, PermissionError):
            return {"ok": False, "error": "Nepodařilo se spojit s api.anthropic.com. Zkontroluj síť a uložený klíč."}
        except Exception as exc:
            log.exception("Chyba v test_api_key")
            return {"ok": False, "error": f"Ověření klíče selhalo ({type(exc).__name__})."}
        finally:
            if ai is not None:
                ai.close()

    def test_webhook(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            state.cfg_store.reload_if_changed()
            cfg = state.cfg_store.snapshot()
            netguard.set_allowed_hosts(config.allowed_hosts(cfg))
            url = str(cfg.get("kanal_webhook", "") or "").strip()
            if not channel.is_valid_webhook_url(url):
                return {"ok": False, "error": "Neplatná adresa kanálu. Musí začínat https://."}
            channel.send_sync(url, "Test z Mluvky")
            return {"ok": True}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"Server kanálu vrátil chybu HTTP {exc.code}. Zkontroluj adresu webhooku."}
        except (urllib.error.URLError, TimeoutError, OSError, PermissionError):
            return {"ok": False, "error": "Nepodařilo se spojit se serverem kanálu. Zkontroluj adresu a připojení."}
        except Exception as exc:
            log.exception("Chyba v test_webhook")
            return {"ok": False, "error": f"Odeslání testu selhalo ({type(exc).__name__})."}
    def get_history(self, query: str = "", offset: int = 0, limit: int = 100) -> dict[str, Any]:
        state = _state_for(self)
        try:
            q = query if isinstance(query, str) else ""
            off = max(0, int(offset))
            lim = min(500, max(1, int(limit)))
            items, total = history.query_entries(q, off, lim, data=state.data)
            return {
                "ok": True,
                "items": items,
                "total": total,
                "has_more": (off + len(items)) < total,
            }
        except Exception as exc:
            log.exception("Chyba v get_history")
            return {"ok": False, "error": f"Načtení historie selhalo ({type(exc).__name__})."}

    def delete_history(
        self, entry_id: str = "", cas: str = "", text: str | None = None
    ) -> dict[str, Any]:
        state = _state_for(self)
        try:
            trenink.stop_playback()
            deleted = history.delete_entry(
                str(entry_id or ""),
                cas=str(cas or ""),
                text=str(text) if text is not None else None,
                data=state.data,
            )
            return {
                "ok": True,
                "deleted": deleted,
                "stats": history.stats(days=7, data=state.data),
            }
        except Exception as exc:
            log.exception("Chyba v delete_history")
            return {"ok": False, "error": f"Smazání záznamu selhalo ({type(exc).__name__})."}

    def edit_history(
        self,
        entry_id: str = "",
        new_text: str = "",
        cas: str = "",
        old_text: str | None = None,
    ) -> dict[str, Any]:
        state = _state_for(self)
        try:
            if not isinstance(new_text, str):
                return {"ok": False, "error": "Text musí být řetězec."}
            res = history.update_entry_with_previous(
                str(entry_id or ""),
                new_text,
                cas=str(cas or ""),
                text=str(old_text) if old_text is not None else None,
                data=state.data,
            )
            if res is None:
                return {"ok": False, "error": "Záznam historie nebyl nalezen."}
            entry, prev_text = res
            opraveno_text = str(entry.get("opraveno", ""))
            if prev_text.strip() != opraveno_text.strip():
                record = pamet.add(
                    str(entry.get("id") or entry_id or ""),
                    prev_text,
                    opraveno_text,
                    data=state.data,
                )
            else:
                record = {"zmeny": []}
            state.cfg_store.reload_if_changed()
            existing = _opravy_key_set(state.cfg_store.snapshot())
            suggestions = [
                pair
                for pair in record["zmeny"]
                if (pair[0].casefold(), pair[1].casefold()) not in existing
            ]
            return {
                "ok": True,
                "entry": entry,
                "zmeny": record["zmeny"],
                "suggestions": suggestions,
                "pamet": _pamet_list(state),
                "stats": history.stats(days=7, data=state.data),
            }
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v edit_history")
            return {"ok": False, "error": f"Uložení opravy selhalo ({type(exc).__name__})."}

    def add_oprava(self, z: str, na: str) -> dict[str, Any]:
        state = _state_for(self)
        try:
            if not isinstance(z, str) or not isinstance(na, str):
                return {"ok": False, "error": "Oprava musí obsahovat text."}
            pair = _validate_pair_list([[z, na]], "opravách")[0]
            pair[1] = pair[1].strip()
            if not pair[1]:
                return {"ok": False, "error": "Cílový text opravy nesmí být prázdný."}
            if pair[0] == pair[1]:
                return {"ok": False, "error": "Původní a cílový text se nesmí shodovat."}
            state.cfg_store.reload_if_changed()
            if state.cfg_store.config_invalid:
                raise config.ConfigInvalidError(config.CONFIG_INVALID_MESSAGE)
            cfg = state.cfg_store.snapshot()
            slovnik = cfg.get("slovnik") or {}
            opravy = list(slovnik.get("opravy") or [])
            existing = _opravy_key_set(cfg)
            key = (pair[0].casefold(), pair[1].casefold())
            added = False
            if key not in existing:
                opravy.append(pair)
                state.cfg_store.update_values(
                    {"slovnik": {"slova": list(slovnik.get("slova") or []), "opravy": opravy}}
                )
                added = True
            res = _build_state(state)
            res["added"] = added
            res["pamet"] = _pamet_list(state)
            return res
        except config.ConfigInvalidError as exc:
            return {"ok": False, "error": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v add_oprava")
            return {"ok": False, "error": f"Přidání opravy selhalo ({type(exc).__name__})."}

    def get_pamet(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            state.cfg_store.reload_if_changed()
            return {"ok": True, "items": _pamet_list(state)}
        except Exception as exc:
            log.exception("Chyba v get_pamet")
            return {"ok": False, "error": f"Načtení paměti chyb selhalo ({type(exc).__name__})."}

    def clear_pamet(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            removed = pamet.clear(data=state.data)
            return {"ok": True, "removed": removed, "items": []}
        except Exception as exc:
            log.exception("Chyba v clear_pamet")
            return {"ok": False, "error": f"Vymazání paměti selhalo ({type(exc).__name__})."}

    def clear_history(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            trenink.stop_playback()
            removed = history.clear_all(data=state.data)
            return {
                "ok": True,
                "removed": removed,
                "stats": history.stats(days=7, data=state.data),
            }
        except Exception as exc:
            log.exception("Chyba v clear_history")
            return {"ok": False, "error": f"Smazání historie selhalo ({type(exc).__name__})."}

    def copy_text(self, text: str) -> dict[str, Any]:
        try:
            seq = output.set_clipboard_text(str(text or ""))
            if seq is None:
                return {"ok": False, "error": "Nepodařilo se zapsat text do schránky. Zkus to znovu."}
            return {"ok": True}
        except Exception as exc:
            log.exception("Chyba v copy_text")
            return {"ok": False, "error": f"Kopírování selhalo ({type(exc).__name__})."}

    def copy_slovnik_prompt(self) -> dict[str, Any]:
        try:
            seq = output.set_clipboard_text(slovnik_import.SLOVNIK_PROMPT)
            if seq is None:
                return {"ok": False, "error": "Nepodařilo se zapsat prompt do schránky. Zkus to znovu."}
            return {"ok": True}
        except Exception as exc:
            log.exception("Chyba v copy_slovnik_prompt")
            return {"ok": False, "error": f"Kopírování selhalo ({type(exc).__name__})."}

    def preview_slovnik_import(self, text: str) -> dict[str, Any]:
        state = _state_for(self)
        try:
            parsed = slovnik_import.parse(text)
        except Exception:
            return {
                "ok": False,
                "error": "Odpověď se nepodařilo přečíst. Zkontroluj, že je to JSON ve tvaru z promptu.",
            }
        try:
            state.cfg_store.reload_if_changed()
            cfg = state.cfg_store.snapshot()
            slovnik = cfg.get("slovnik") or {}
            existing_words = {str(w).strip().casefold() for w in (slovnik.get("slova") or [])}
            existing_pairs = _opravy_key_set(cfg)
            new_slova = [w for w in parsed["slova"] if w.casefold() not in existing_words]
            new_opravy = [
                p for p in parsed["opravy"] if (p[0].casefold(), p[1].casefold()) not in existing_pairs
            ]
            existing_count = (
                (len(parsed["slova"]) - len(new_slova))
                + (len(parsed["opravy"]) - len(new_opravy))
            )
            return {
                "ok": True,
                "slova": new_slova,
                "opravy": new_opravy,
                "existing_count": existing_count,
            }
        except Exception as exc:
            log.exception("Chyba v preview_slovnik_import")
            return {"ok": False, "error": f"Náhled importu selhal ({type(exc).__name__})."}

    def apply_slovnik_import(self, data_obj: dict[str, Any]) -> dict[str, Any]:
        state = _state_for(self)
        try:
            if not isinstance(data_obj, dict):
                return {"ok": False, "error": "Neplatná data pro import slovníku."}
            parsed = slovnik_import.parse(json.dumps(data_obj, ensure_ascii=False))
            state.cfg_store.reload_if_changed()
            if state.cfg_store.config_invalid:
                raise config.ConfigInvalidError(config.CONFIG_INVALID_MESSAGE)
            cfg = state.cfg_store.snapshot()
            slovnik = cfg.get("slovnik") or {}
            slova = list(slovnik.get("slova") or [])
            opravy = list(slovnik.get("opravy") or [])
            seen_words = {str(w).strip().casefold() for w in slova}
            seen_pairs = _opravy_key_set(cfg)
            added_slova = 0
            added_opravy = 0
            for w in parsed["slova"]:
                if w.casefold() not in seen_words:
                    seen_words.add(w.casefold())
                    slova.append(w)
                    added_slova += 1
            for z, na in parsed["opravy"]:
                k = (z.casefold(), na.casefold())
                if k not in seen_pairs:
                    seen_pairs.add(k)
                    opravy.append([z, na])
                    added_opravy += 1
            state.cfg_store.update_values({"slovnik": {"slova": slova, "opravy": opravy}})
            res = _build_state(state)
            res["added_slova"] = added_slova
            res["added_opravy"] = added_opravy
            res["pamet"] = _pamet_list(state)
            return res
        except config.ConfigInvalidError as exc:
            return {"ok": False, "error": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v apply_slovnik_import")
            return {"ok": False, "error": f"Import slovníku selhal ({type(exc).__name__})."}
    def get_trenink_state(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            summary = trenink.get_state_summary(state.data)
            with state.meas_lock:
                meas = dict(state.meas_state)
            return {"ok": True, **summary, "measuring": meas}
        except Exception as exc:
            log.exception("Chyba v get_trenink_state")
            return {"ok": False, "error": f"Načtení Tréninku selhalo ({type(exc).__name__})."}

    def trenink_start(self, index: int) -> dict[str, Any]:
        state = _state_for(self)
        try:
            idx = int(index)
            sentences = trenink.load_sentences()
            if idx < 1 or idx > len(sentences):
                return {"ok": False, "error": "Neplatné pořadí věty."}
            trenink.stop_playback()
            state.cfg_store.reload_if_changed()
            mik = str(state.cfg_store.get("mikrofon", "") or "")
            with state.level_lock:
                state.level_val = 0.0

            def _on_level(val: float) -> None:
                with state.level_lock:
                    state.level_val = max(0.0, min(1.0, float(val)))

            rec = audio.Recorder()
            with state.rec_lock:
                old = state.recorder
                state.recorder = None
                if old is not None:
                    try:
                        old.cancel()
                    except Exception:
                        pass
                rec.start(mikrofon=mik, on_level=_on_level)
                state.recorder = rec
                state.rec_index = idx
                state.rec_started_at = time.monotonic()
            return {"ok": True, "index": idx}
        except Exception as exc:
            log.exception("Chyba v trenink_start")
            return {"ok": False, "error": f"Nepodařilo se spustit mikrofon ({type(exc).__name__})."}

    def trenink_stop(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            with state.rec_lock:
                rec = state.recorder
                idx = state.rec_index
                state.recorder = None
                state.rec_index = 0
                state.rec_started_at = 0.0
            with state.level_lock:
                state.level_val = 0.0
            if rec is None or idx <= 0:
                return {"ok": False, "error": "Nahrávání neběží."}
            samples = rec.stop()
            if samples.size == 0:
                return {"ok": False, "error": "Nahrávka je prázdná."}
            saved = trenink.save_sentence_recording(idx, samples, data=state.data)
            summary = trenink.get_state_summary(state.data)
            return {"ok": True, "saved": saved, "trenink": summary}
        except Exception as exc:
            log.exception("Chyba v trenink_stop")
            return {"ok": False, "error": f"Uložení nahrávky selhalo ({type(exc).__name__})."}

    def trenink_cancel(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            with state.rec_lock:
                rec = state.recorder
                state.recorder = None
                state.rec_index = 0
                state.rec_started_at = 0.0
            with state.level_lock:
                state.level_val = 0.0
            if rec is not None:
                rec.cancel()
            return {"ok": True}
        except Exception as exc:
            log.exception("Chyba v trenink_cancel")
            return {"ok": False, "error": f"Zrušení nahrávání selhalo ({type(exc).__name__})."}

    def trenink_play(self, index: int) -> dict[str, Any]:
        state = _state_for(self)
        try:
            ok = trenink.play_sentence(int(index), data=state.data)
            if not ok:
                return {"ok": False, "error": "Pro tuto větu zatím není uložená nahrávka."}
            return {"ok": True}
        except Exception as exc:
            log.exception("Chyba v trenink_play")
            return {"ok": False, "error": f"Přehrání selhalo ({type(exc).__name__})."}

    def diktaty_k_overeni(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        state = _state_for(self)
        try:
            off = max(0, int(offset))
            lim = min(500, max(1, int(limit)))
            state.cfg_store.reload_if_changed()
            cfg = state.cfg_store.snapshot()
            ukladat_zvuk = bool(cfg.get("trenink_ukladat_zvuk", False))
            summary = history.dictation_summary_and_list(
                offset=off, limit=lim, data=state.data
            )
            return {"ok": True, "ukladat_zvuk": ukladat_zvuk, **summary}
        except Exception as exc:
            log.exception("Chyba v diktaty_k_overeni")
            return {
                "ok": False,
                "error": f"Načtení diktátů selhalo ({type(exc).__name__}).",
            }

    def overit_diktat(self, entry_id: str = "", text: str = "") -> dict[str, Any]:
        state = _state_for(self)
        try:
            if not isinstance(entry_id, str) or not entry_id.strip():
                return {"ok": False, "error": "Chybí identifikátor diktátu."}
            if not isinstance(text, str):
                return {"ok": False, "error": "Přepis musí být text."}
            updated = history.verify_dictation(
                entry_id.strip(), text, data=state.data
            )
            if updated is None:
                return {"ok": False, "error": "Záznam historie nebyl nalezen."}
            summary = history.dictation_summary_and_list(
                offset=0, limit=1, data=state.data
            )
            return {
                "ok": True,
                "entry": updated,
                "verified_count": summary["verified_count"],
                "total": summary["total"],
                "verified_s": summary["verified_s"],
            }
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v overit_diktat")
            return {
                "ok": False,
                "error": f"Uložení ověřeného přepisu selhalo ({type(exc).__name__}).",
            }

    def smazat_diktat_zvuk(self, entry_id: str = "") -> dict[str, Any]:
        state = _state_for(self)
        try:
            if not isinstance(entry_id, str) or not entry_id.strip():
                return {"ok": False, "error": "Chybí identifikátor diktátu."}
            trenink.stop_playback()
            deleted = history.delete_dictation_audio(
                entry_id.strip(), data=state.data
            )
            if not deleted:
                return {"ok": False, "error": "Záznam historie nebyl nalezen."}
            summary = history.dictation_summary_and_list(
                offset=0, limit=1, data=state.data
            )
            return {
                "ok": True,
                "deleted": True,
                "verified_count": summary["verified_count"],
                "total": summary["total"],
                "verified_s": summary["verified_s"],
            }
        except Exception as exc:
            log.exception("Chyba v smazat_diktat_zvuk")
            return {
                "ok": False,
                "error": f"Smazání nahrávky selhalo ({type(exc).__name__}).",
            }

    def prehrat_diktat(self, entry_id: str = "") -> dict[str, Any]:
        state = _state_for(self)
        try:
            if not isinstance(entry_id, str) or not entry_id.strip():
                return {"ok": False, "error": "Chybí identifikátor diktátu."}
            wav_path = history.get_dictation_wav_path(
                entry_id.strip(), data=state.data
            )
            if wav_path is None:
                return {"ok": False, "error": "Nahrávka diktátu nebyla nalezena."}
            ok = trenink.play_wav(wav_path)
            if not ok:
                return {"ok": False, "error": "Nahrávka diktátu nebyla nalezena."}
            return {"ok": True}
        except Exception as exc:
            log.exception("Chyba v prehrat_diktat")
            return {
                "ok": False,
                "error": f"Přehrání selhalo ({type(exc).__name__}).",
            }

    def get_level(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            with state.rec_lock:
                recording = state.recorder is not None
                started = state.rec_started_at
            with state.level_lock:
                level = state.level_val if recording else 0.0
            elapsed_s = max(0.0, time.monotonic() - started) if recording and started > 0 else 0.0
            return {
                "ok": True,
                "recording": recording,
                "level": round(level, 3),
                "elapsed_s": round(elapsed_s, 1),
            }
        except Exception:
            return {"ok": False, "recording": False, "level": 0.0, "elapsed_s": 0.0}

    def zmerit(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            if doladeni.doladeni_stav(state.data).get("stav") == "bezi":
                return {"ok": False, "error": "Nejdřív počkej, až skončí doladění modelu."}
            records = trenink.load_tsv(state.data)
            if not records:
                return {"ok": False, "error": "Nejsou nahrané žádné věty k měření."}
            total = len(records)
            with state.meas_lock:
                if state.meas_state["running"]:
                    return {"ok": True, **state.meas_state}
                state.meas_cancel = threading.Event()
                cancel_ev = state.meas_cancel
                state.meas_state = {
                    "running": True,
                    "done": 0,
                    "total": total,
                    "result": None,
                    "error": "",
                }

            def _on_progress(done: int, tot: int) -> None:
                with state.meas_lock:
                    state.meas_state["done"] = done
                    state.meas_state["total"] = tot

            def _worker() -> None:
                try:
                    report = trenink.measure(
                        state.data,
                        on_progress=_on_progress,
                        cancel_event=cancel_ev,
                    )
                    with state.meas_lock:
                        state.meas_state = {
                            "running": False,
                            "done": report["n"],
                            "total": report["n"],
                            "result": report,
                            "error": "",
                        }
                except Exception as exc:
                    log.exception("Měření WER selhalo")
                    with state.meas_lock:
                        state.meas_state = {
                            "running": False,
                            "done": 0,
                            "total": total,
                            "result": trenink.load_measurement(state.data),
                            "error": str(exc) or f"Měření selhalo ({type(exc).__name__}).",
                        }

            t = threading.Thread(target=_worker, name="mluvka-wer-measure", daemon=True)
            with state.meas_lock:
                state.meas_thread = t
            t.start()
            return {"ok": True, "running": True, "done": 0, "total": total, "result": None, "error": ""}
        except Exception as exc:
            log.exception("Chyba v zmerit")
            return {"ok": False, "error": f"Spuštění měření selhalo ({type(exc).__name__})."}

    def zmerit_stav(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            with state.meas_lock:
                snap = dict(state.meas_state)
            if not snap["running"] and snap["result"] is None and not snap["error"]:
                snap["result"] = trenink.load_measurement(state.data)
            return {"ok": True, **snap}
        except Exception as exc:
            log.exception("Chyba v zmerit_stav")
            return {"ok": False, "error": f"Zjištění stavu měření selhalo ({type(exc).__name__})."}

    def vytvor_dar(self, include_diktaty: bool = False, souhlasy: Any = None) -> dict[str, Any]:
        state = _state_for(self)
        try:
            if not isinstance(souhlasy, (list, tuple)) or len(souhlasy) != 3 or not all(
                x is True for x in souhlasy
            ):
                return {"ok": False, "error": "Pro vytvoření balíčku potvrď všechny tři body souhlasu."}
            state.cfg_store.reload_if_changed()
            if state.cfg_store.config_invalid:
                raise config.ConfigInvalidError(config.CONFIG_INVALID_MESSAGE)
            zip_path = dar.vytvor_balicek(state.data, bool(include_diktaty), cfg_store=state.cfg_store)
            return {
                "ok": True,
                "path": str(zip_path),
                "filename": zip_path.name,
                "folder": str(zip_path.parent),
            }
        except config.ConfigInvalidError as exc:
            return {"ok": False, "error": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v vytvor_dar")
            return {"ok": False, "error": f"Vytvoření balíčku selhalo ({type(exc).__name__})."}

    def modely(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            state.cfg_store.reload_if_changed()
            selected = state.cfg_store.get("model_soubor")
            info = trenink.get_models_state(state.data, selected_soubor=selected, beam=state.cfg_store.get("beam_size"))
            return {"ok": True, **info, "config_invalid": state.cfg_store.config_invalid}
        except Exception as exc:
            log.exception("Chyba v modely")
            return {"ok": False, "error": f"Načtení modelů selhalo ({type(exc).__name__})."}

    def vybrat_model(self, soubor: str) -> dict[str, Any]:
        state = _state_for(self)
        try:
            return doladeni.select_model(state.data, state.cfg_store, soubor)
        except config.ConfigInvalidError as exc:
            return {"ok": False, "error": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v vybrat_model")
            return {"ok": False, "error": f"Výběr modelu selhal ({type(exc).__name__})."}

    # ---------- stránka Model ----------

    def model_stav(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            state.cfg_store.reload_if_changed()
            info = mereni.sestav_stav(state.data, state.cfg_store.snapshot(), llm.is_available(state.data))
            return {"ok": True, **info, "mereni_bezi": _MODEL_MERENI.stav if _MODEL_MERENI.bezi() else None}
        except Exception as exc:
            log.exception("Chyba v model_stav")
            return {"ok": False, "error": f"Načtení stránky Model selhalo ({type(exc).__name__})."}

    def _model_setup(self, setup_id: Any) -> tuple[Any, dict[str, Any]]:
        state = _state_for(self)
        state.cfg_store.reload_if_changed()
        info = mereni.sestav_stav(state.data, state.cfg_store.snapshot(), llm.is_available(state.data))
        found = next((x for x in info["setupy"] if x["id"] == setup_id), None)
        if found is None:
            raise ValueError("Tohle nastavení už neexistuje.")
        return state, found

    def model_pouzit(self, setup_id: str) -> dict[str, Any]:
        try:
            state, s = self._model_setup(setup_id)
            if not s["pouzitelny"]:
                return {"ok": False, "error": "Úprava přes Haiku potřebuje API klíč pro Claude."}
            if state.cfg_store.config_invalid:
                return {"ok": False, "error": "config.json obsahuje chybu. Oprav ho, nebo ho smaž a Mluvka vytvoří nový."}
            state.cfg_store.set_value("model_soubor", config.validate_model_soubor(s["model_soubor"]))
            state.cfg_store.set_value("beam_size", int(s["beam"]))
            state.cfg_store.set_value("ai_uprava", bool(s["haiku"]))
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v model_pouzit")
            return {"ok": False, "error": f"Nastavení se nepodařilo použít ({type(exc).__name__})."}

    def model_pridat(self, model_soubor: str, beam: Any, haiku: Any) -> dict[str, Any]:
        state = _state_for(self)
        try:
            if bool(haiku) and not llm.is_available(state.data):
                return {"ok": False, "error": "Úprava přes Haiku potřebuje API klíč pro Claude."}
            return {"ok": True, "setup": mereni.pridat_setup(state.data, model_soubor, beam, bool(haiku))}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v model_pridat")
            return {"ok": False, "error": f"Přidání selhalo ({type(exc).__name__})."}

    def model_odebrat(self, setup_id: str) -> dict[str, Any]:
        state = _state_for(self)
        try:
            mereni.odebrat_setup(state.data, str(setup_id))
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v model_odebrat")
            return {"ok": False, "error": f"Odebrání selhalo ({type(exc).__name__})."}

    def model_zmerit(self, setup_id: str) -> dict[str, Any]:
        try:
            state, s = self._model_setup(setup_id)
            if not s["pouzitelny"]:
                return {"ok": False, "error": "Úprava přes Haiku potřebuje API klíč pro Claude."}
            with state.meas_lock:
                if state.meas_state.get("running"):
                    return {"ok": False, "error": "Nejdřív počkej, až skončí měření v Tréninku."}
            if doladeni.doladeni_stav(state.data).get("stav") == "bezi":
                return {"ok": False, "error": "Nejdřív počkej, až doběhne doladění."}
            _MODEL_MERENI.spustit(state.data, s, state.cfg_store.snapshot())
            return {"ok": True}
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v model_zmerit")
            return {"ok": False, "error": f"Měření se nepodařilo spustit ({type(exc).__name__})."}

    def model_mereni_stav(self) -> dict[str, Any]:
        return {"ok": True, "stav": dict(_MODEL_MERENI.stav) if _MODEL_MERENI.stav else None}

    def doladeni_stav(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            return {"ok": True, **doladeni.doladeni_stav(state.data)}
        except Exception as exc:
            log.exception("Chyba v doladeni_stav")
            return {"ok": False, "error": f"Načtení stavu doladění selhalo ({type(exc).__name__})."}

    def doladeni_spustit(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            with state.meas_lock:
                meas_running = bool(state.meas_state.get("running"))
            return doladeni.doladeni_spustit(state.data, mereni_bezi=meas_running)
        except Exception as exc:
            log.exception("Chyba v doladeni_spustit")
            return {"ok": False, "error": f"Spuštění doladění selhalo ({type(exc).__name__})."}

    def doladeni_zastavit(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            return doladeni.doladeni_zastavit(state.data)
        except Exception as exc:
            log.exception("Chyba v doladeni_zastavit")
            return {"ok": False, "error": f"Zastavení doladění selhalo ({type(exc).__name__})."}

    def doladeni_pouzit(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            return doladeni.doladeni_pouzit(state.data, cfg_store=state.cfg_store)
        except config.ConfigInvalidError as exc:
            return {"ok": False, "error": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Chyba v doladeni_pouzit")
            return {"ok": False, "error": f"Aktivace modelu selhala ({type(exc).__name__})."}

    def otevrit_slozku(self, path: str) -> dict[str, Any]:
        state = _state_for(self)
        try:
            raw = str(path or "").strip()
            if not raw:
                return {"ok": False, "error": "Cesta nesmí být prázdná."}
            docs_root = dar.documents_dir().resolve()
            data_root = (state.data or config.data_dir()).resolve()
            allowed_roots = (docs_root, data_root)
            resolved = Path(raw).resolve(strict=False)
            if not any(resolved.is_relative_to(r) for r in allowed_roots):
                return {"ok": False, "error": "Otevření této složky není povoleno."}
            folder = resolved if resolved.is_dir() else resolved.parent
            if not any(folder.is_relative_to(r) for r in allowed_roots) or not folder.is_dir():
                return {"ok": False, "error": "Cílová složka neexistuje nebo není povolená."}
            os.startfile(str(folder))
            return {"ok": True, "folder": str(folder)}
        except Exception as exc:
            log.exception("Chyba v otevrit_slozku")
            return {"ok": False, "error": f"Složku se nepodařilo otevřít ({type(exc).__name__})."}

    def open_data_folder(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            target = state.data or config.data_dir()
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
            return {"ok": True}
        except Exception as exc:
            log.exception("Chyba v open_data_folder")
            return {"ok": False, "error": f"Složku s daty se nepodařilo otevřít ({type(exc).__name__})."}

    def start_app(self) -> dict[str, Any]:
        state = _state_for(self)
        try:
            if is_app_running():
                return {"ok": True, "started": False}
            launch_detached_module("mluvka", data=state.data)
            return {"ok": True, "started": True}
        except Exception as exc:
            log.exception("Chyba v start_app")
            return {"ok": False, "error": f"Spuštění Mluvky selhalo ({type(exc).__name__})."}

    def open_url(self, url: str) -> dict[str, Any]:
        try:
            target = str(url or "").strip()
            if target not in ALLOWED_OPEN_URLS:
                return {"ok": False, "error": "Otevření tohoto odkazu není povoleno."}
            os.startfile("https://console.anthropic.com")
            return {"ok": True}
        except Exception as exc:
            log.exception("Chyba v open_url")
            return {"ok": False, "error": f"Otevření prohlížeče selhalo ({type(exc).__name__})."}


def _is_allowed_local_navigation(uri: str, allowed_prefix: str, real_url: str) -> bool:
    if not uri:
        return False
    if uri == real_url or uri == "about:blank":
        return True
    if not allowed_prefix:
        return False
    target = urlsplit(uri)
    base = urlsplit(allowed_prefix)
    if target.scheme.lower() != base.scheme.lower():
        return False
    if target.netloc.lower() != base.netloc.lower():
        return False
    raw_base_dir = unquote(base.path)
    raw_target_path = unquote(target.path)
    if not raw_base_dir.endswith("/") or "\\" in raw_target_path:
        return False
    if ".." in raw_target_path.split("/"):
        return False
    base_norm = posixpath.normpath(raw_base_dir).rstrip("/") + "/"
    target_norm = posixpath.normpath(raw_target_path)
    if base.scheme.lower() == "file":
        return target_norm.lower().startswith(base_norm.lower())
    return target_norm.startswith(base_norm)


def _attach_navigation_guard(window: Any) -> None:
    native = getattr(window, "native", None)
    browser = getattr(native, "browser", None) if native is not None else None
    wv = getattr(browser, "webview", None) if browser is not None else None
    if wv is None:
        return
    real_url = str(getattr(window, "real_url", "") or "")
    prefix = ""
    if real_url.startswith("http://127.0.0.1:"):
        prefix = real_url.rsplit("/", 1)[0] + "/"
    elif real_url.startswith("file:///"):
        prefix = real_url.rsplit("/", 1)[0] + "/"

    def _on_nav_starting(_sender: Any, args: Any) -> None:
        uri = str(getattr(args, "Uri", "") or "")
        if not _is_allowed_local_navigation(uri, prefix, real_url):
            args.Cancel = True

    wv.NavigationStarting += _on_nav_starting
    if hasattr(wv, "NewWindowRequested"):
        def _on_new_window(_sender: Any, args: Any) -> None:
            uri = str(getattr(args, "Uri", "") or "")
            if not _is_allowed_local_navigation(uri, prefix, real_url):
                args.Handled = True

        wv.NewWindowRequested += _on_new_window


def main(data: Path | None = None) -> int:
    initial_cfg = config.load_config(data)
    netguard.install(config.allowed_hosts(initial_cfg))

    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass

    h_mutex = kernel32.CreateMutexW(None, False, OKNO_MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if h_mutex:
            kernel32.CloseHandle(h_mutex)
        focus_existing_window("Mluvka")
        return 0

    icon_handles: list[int] = []
    icon_lock = threading.Lock()
    icons_closed = False
    api: Api | None = None
    try:
        ico_file = zastupci.ensure_ico(data)
        ui_index = Path(__file__).resolve().parent / "ui" / "index.html"
        bg = "#1C1420" if _system_is_dark() else "#F7F5F8"

        import webview

        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        api = Api(data)
        window = webview.create_window(
            "Mluvka",
            url=str(ui_index),
            js_api=api,
            width=1040,
            height=700,
            min_size=(860, 560),
            background_color=bg,
        )
        assert window is not None

        def _sync_native_chrome() -> None:
            native = getattr(window, "native", None)
            hwnd = 0
            if native is not None:
                try:
                    hwnd = int(native.Handle.ToInt64())
                except Exception:
                    hwnd = 0
            if not hwnd:
                hwnd = find_existing_window("Mluvka")
            if hwnd:
                big, small = _apply_window_icon(hwnd, ico_file)
                with icon_lock:
                    if icons_closed:
                        for h in (big, small):
                            if h:
                                user32.DestroyIcon(h)
                        return
                    _destroy_icons(icon_handles)
                    if big:
                        icon_handles.append(big)
                    if small:
                        icon_handles.append(small)

        def _on_before_show(_win: Any = None) -> None:
            _sync_native_chrome()
            _attach_navigation_guard(window)

        def _on_shown(_win: Any = None) -> None:
            _sync_native_chrome()

        def _on_closed(_win: Any = None) -> None:
            nonlocal icons_closed
            if api is not None:
                _cleanup_api(api)
            with icon_lock:
                icons_closed = True
                _destroy_icons(icon_handles)

        window.events.before_show += _on_before_show
        window.events.shown += _on_shown
        window.events.closed += _on_closed

        webview.start(private_mode=True, icon=str(ico_file))
        return 0
    finally:
        if api is not None:
            _cleanup_api(api)
        with icon_lock:
            icons_closed = True
            _destroy_icons(icon_handles)
        if h_mutex:
            kernel32.CloseHandle(h_mutex)


if __name__ == "__main__":
    raise SystemExit(main())
