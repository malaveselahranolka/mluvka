"""Unit testy pro macOS (Apple Silicon) port s mockovaným pyobjc a sys.platform == 'darwin'."""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_quartz_stub():
    events_posted: list[tuple[int, dict[str, object]]] = []
    tap_callbacks: list[object] = []
    tap_enabled: list[bool] = []
    state = {"cmd_flag": 0x00100000, "tap_obj": object(), "runloop_obj": object(), "return_none_tap": False}

    q = types.ModuleType("Quartz")
    q.kCGSessionEventTap = 1
    q.kCGHIDEventTap = 0
    q.kCGHeadInsertEventTap = 0
    q.kCGEventTapOptionListenOnly = 1
    q.kCGEventKeyDown = 10
    q.kCGEventKeyUp = 11
    q.kCGEventFlagsChanged = 12
    q.kCGKeyboardEventKeycode = 9
    q.kCGEventSourceUserData = 42
    q.kCGEventTapDisabledByTimeout = -2
    q.kCGEventTapDisabledByUserInput = -1
    q.kCGEventFlagMaskShift = 0x00020000
    q.kCGEventFlagMaskControl = 0x00040000
    q.kCGEventFlagMaskAlternate = 0x00080000
    q.kCGEventFlagMaskCommand = 0x00100000
    q.kCGEventFlagMaskAlphaShift = 0x00010000
    q.kCGEventFlagMaskSecondaryFn = 0x00800000
    q.kCFRunLoopCommonModes = "common"
    q.kCFAllocatorDefault = None
    q.kCGEventSourceStateCombinedSessionState = 0

    def CGEventMaskBit(ev_type: int) -> int:
        return 1 << ev_type

    def CGEventGetIntegerValueField(ev, field: int) -> int:
        if field == q.kCGEventSourceUserData:
            return int(ev.get("user_data", 0))
        return int(ev.get("keycode", 0))

    def CGEventSetIntegerValueField(ev: dict[str, object], field: int, val: int) -> None:
        if field == q.kCGEventSourceUserData:
            ev["user_data"] = int(val)
        elif field == q.kCGKeyboardEventKeycode:
            ev["keycode"] = int(val)

    def CGEventGetFlags(ev) -> int:
        return int(ev.get("flags", 0))

    def CGEventCreateKeyboardEvent(_source, keycode: int, key_down: bool) -> dict[str, object]:
        return {"keycode": int(keycode), "key_down": bool(key_down), "flags": 0}

    def CGEventSetFlags(ev: dict[str, object], flags: int) -> None:
        ev["flags"] = int(flags)

    def CGEventPost(tap: int, ev: dict[str, object]) -> None:
        events_posted.append((int(tap), dict(ev)))

    def CGEventSourceKeyState(_src: int, _mac_code: int) -> bool:
        return False

    def CGEventTapCreate(tap, place, options, mask, callback, refcon):
        tap_callbacks.append(callback)
        if state["return_none_tap"]:
            return None
        return state["tap_obj"]

    def CFMachPortCreateRunLoopSource(_alloc, _tap, _order):
        return object()

    def CFRunLoopGetCurrent():
        return state["runloop_obj"]

    def CFRunLoopAddSource(_rl, _src, _mode) -> None:
        return None

    def CGEventTapEnable(_tap, enabled: bool) -> None:
        tap_enabled.append(bool(enabled))

    def CFAbsoluteTimeGetCurrent() -> float:
        return 1000.0

    def CFRunLoopTimerCreate(_alloc, _fire, _interval, _flags, _order, _cb, _ctx):
        return object()

    def CFRunLoopAddTimer(_rl, _timer, _mode) -> None:
        return None

    def CFRunLoopRun() -> None:
        return None

    def CFRunLoopStop(_rl) -> None:
        return None

    def CFRunLoopWakeUp(_rl) -> None:
        return None

    q.CGEventMaskBit = CGEventMaskBit
    q.CGEventGetIntegerValueField = CGEventGetIntegerValueField
    q.CGEventSetIntegerValueField = CGEventSetIntegerValueField
    q.CGEventGetFlags = CGEventGetFlags
    q.CGEventCreateKeyboardEvent = CGEventCreateKeyboardEvent
    q.CGEventSetFlags = CGEventSetFlags
    q.CGEventPost = CGEventPost
    q.CGEventSourceKeyState = CGEventSourceKeyState
    q.CGEventTapCreate = CGEventTapCreate
    q.CFMachPortCreateRunLoopSource = CFMachPortCreateRunLoopSource
    q.CFRunLoopGetCurrent = CFRunLoopGetCurrent
    q.CFRunLoopAddSource = CFRunLoopAddSource
    q.CGEventTapEnable = CGEventTapEnable
    q.CFAbsoluteTimeGetCurrent = CFAbsoluteTimeGetCurrent
    q.CFRunLoopTimerCreate = CFRunLoopTimerCreate
    q.CFRunLoopAddTimer = CFRunLoopAddTimer
    q.CFRunLoopRun = CFRunLoopRun
    q.CFRunLoopStop = CFRunLoopStop
    q.CFRunLoopWakeUp = CFRunLoopWakeUp
    q._posted = events_posted
    q._callbacks = tap_callbacks
    q._enabled = tap_enabled
    q._state = state
    return q


def _make_cocoa_pasteboard_stub(initial_text: str | None = "puvodni", initial_seq: int = 10):
    pb_data: dict[str, object] = {
        "change_count": initial_seq,
        "types": {"public.utf8-plain-text": initial_text} if initial_text is not None else {},
    }

    class FakePasteboard:
        @classmethod
        def generalPasteboard(cls):
            return cls()

        def changeCount(self) -> int:
            return int(pb_data["change_count"])  # type: ignore[arg-type]

        def stringForType_(self, typ: str):
            types_map = pb_data["types"]  # type: ignore[assignment]
            return types_map.get(typ)  # type: ignore[union-attr]

        def clearContents(self) -> int:
            pb_data["types"] = {}
            pb_data["change_count"] = int(pb_data["change_count"]) + 1  # type: ignore[arg-type]
            return int(pb_data["change_count"])  # type: ignore[arg-type]

        def setString_forType_(self, value: str, typ: str) -> bool:
            types_map = pb_data["types"]  # type: ignore[assignment]
            types_map[typ] = value  # type: ignore[index]
            return True

        def setData_forType_(self, value: bytes, typ: str) -> bool:
            types_map = pb_data["types"]  # type: ignore[assignment]
            types_map[typ] = value  # type: ignore[index]
            return True

    class FakeNSData:
        @staticmethod
        def data() -> bytes:
            return b""

    cocoa = types.ModuleType("Cocoa")
    cocoa.NSPasteboard = FakePasteboard
    cocoa.NSPasteboardTypeString = "public.utf8-plain-text"
    cocoa.NSData = FakeNSData
    return cocoa, pb_data


def test_all_mluvka_modules_import_cleanly_on_darwin_without_win32_apis(tmp_path: Path):
    repo = Path(__file__).resolve().parent.parent
    probe = f"""
import ctypes
import importlib
import sys
import types
from pathlib import Path

import sounddevice  # načíst nativní DLL před emulací sys.platform == "darwin"
sys.platform = "darwin"

def _boom(*_a, **_kw):
    raise AssertionError("Win32 API nesmí být na macOS voláno")

if hasattr(ctypes, "WinDLL"):
    ctypes.WinDLL = _boom
if hasattr(ctypes, "windll"):
    class _WindllTrap:
        def __getattr__(self, name):
            raise AssertionError(f"ctypes.windll.{{name}} nesmí být na macOS použito")
    ctypes.windll = _WindllTrap()

for forbidden in ("winreg", "comtypes", "comtypes.client", "winsound"):
    sys.modules[forbidden] = None

scproxy = types.ModuleType("_scproxy")
scproxy._get_proxy_settings = dict
scproxy._get_proxies = dict
sys.modules["_scproxy"] = scproxy

for mod_name in ("Quartz", "Cocoa", "AppKit", "Foundation", "objc"):
    m = types.ModuleType(mod_name)
    m.NSWorkspace = object
    m.NSPasteboard = object
    m.NSPasteboardTypeString = "public.utf8-plain-text"
    m.NSData = object
    sys.modules[mod_name] = m
pystray_mod = types.ModuleType("pystray")
pystray_mod.Icon = object
pystray_mod.Menu = object
pystray_mod.MenuItem = object
sys.modules["pystray"] = pystray_mod

mod_files = sorted((Path({str(repo)!r}) / "mluvka").glob("*.py"))
assert len(mod_files) >= 15
importlib.import_module("mluvka")
for p in mod_files:
    if p.stem == "__init__":
        continue
    importlib.import_module(f"mluvka.{{p.stem}}")
importlib.import_module("trenink.spolecne")
print("DARWIN_IMPORTS_OK", len(mod_files))
"""
    env = dict(os.environ)
    env["MLUVKA_DATA"] = str(tmp_path)
    res = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, f"stderr:\n{res.stderr}\nstdout:\n{res.stdout}"
    assert "DARWIN_IMPORTS_OK" in res.stdout


def test_windows_does_not_import_pyobjc(tmp_path: Path):
    if sys.platform != "win32":
        pytest.skip("Ověření Windows izolace běží jen na win32")
    repo = Path(__file__).resolve().parent.parent
    probe = """
import importlib
import sys

for mod_name in ("Quartz", "Cocoa", "AppKit", "Foundation"):
    sys.modules[mod_name] = None

for name in (
    "mluvka.config",
    "mluvka.hotkey",
    "mluvka.output",
    "mluvka.winproc",
    "mluvka.asr",
    "mluvka.audio",
    "mluvka.tray",
    "mluvka.overlay",
    "mluvka.app",
    "mluvka.okno",
    "mluvka.sledovani",
    "mluvka.doladeni",
    "mluvka.mereni",
    "mluvka.zastupci",
):
    importlib.import_module(name)
print("WIN32_NO_PYOBJC_OK")
"""
    env = dict(os.environ)
    env["MLUVKA_DATA"] = str(tmp_path)
    res = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, f"stderr:\n{res.stderr}\nstdout:\n{res.stdout}"
    assert "WIN32_NO_PYOBJC_OK" in res.stdout


def test_config_paths_and_defaults_on_darwin(monkeypatch, tmp_path: Path):
    from mluvka import config
    from trenink import spolecne

    fake_home = tmp_path / "Users" / "tester"
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("MLUVKA_DATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    expected = fake_home / "Library" / "Application Support" / "Mluvka"
    assert config.data_dir() == expected
    assert spolecne.data_dir() == expected

    custom = tmp_path / "CustomMluvka"
    monkeypatch.setenv("MLUVKA_DATA", str(custom))
    assert config.data_dir() == custom
    assert spolecne.data_dir() == custom

    # Výchozí klávesa na Macu = Fn (255), globální DEFAULT_CONFIG pro Windows zůstává 163
    assert config.DEFAULT_CONFIG["klavesa_vk"] == 163
    assert config.default_config()["klavesa_vk"] == config.VK_MAC_FN == 255

    # whisper_exe_path: nejdřív data/bin/whisper/whisper-server, pak shutil.which
    local_bin = custom / "bin" / "whisper" / "whisper-server"
    assert config.whisper_exe_path(custom) == local_bin
    assert spolecne.whisper_quantize_exe(custom) == custom / "bin" / "whisper" / "whisper-quantize"

    monkeypatch.setattr(shutil, "which", lambda cmd: "/opt/homebrew/bin/whisper-server" if cmd == "whisper-server" else None)
    assert config.whisper_exe_path(custom) == Path("/opt/homebrew/bin/whisper-server")

    local_bin.parent.mkdir(parents=True, exist_ok=True)
    local_bin.write_text("", encoding="utf-8")
    assert config.whisper_exe_path(custom) == local_bin


def test_hotkey_mac_keycode_mapping():
    from mluvka import hotkey, hotkey_mac

    assert hotkey_mac.mac_keycode_to_vk(63) == hotkey.VK_MAC_FN == 255
    assert hotkey_mac.mac_keycode_to_vk(61) == hotkey.VK_RMENU == 165
    assert hotkey_mac.mac_keycode_to_vk(54) == hotkey.VK_RWIN == 92
    assert hotkey_mac.mac_keycode_to_vk(62) == hotkey.VK_RCONTROL == 163
    assert hotkey_mac.mac_keycode_to_vk(53) == hotkey.VK_ESCAPE == 0x1B
    assert hotkey_mac.mac_keycode_to_vk(6) == hotkey.VK_Z == 0x5A
    assert hotkey_mac.mac_keycode_to_vk(999) is None


def test_hotkey_mac_fn_fsm_press_and_release(monkeypatch):
    from mluvka import hotkey_mac
    from mluvka.hotkey import HOLD_MIN_S, VK_MAC_FN

    quartz = _make_quartz_stub()
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    now = [100.0]
    monkeypatch.setattr(hotkey_mac.time, "monotonic", lambda: now[0])

    actions = []
    hook = hotkey_mac.GlobalHotkeyHook(actions.append, klavesa_vk=VK_MAC_FN)

    triggered = {"done": False}

    def fake_run():
        assert len(quartz._callbacks) == 1
        cb = quartz._callbacks[0]
        # Fn press
        cb(None, quartz.kCGEventFlagsChanged, {"keycode": 63, "flags": quartz.kCGEventFlagMaskSecondaryFn}, None)
        # Puštění po držení >= HOLD_MIN_S
        now[0] += HOLD_MIN_S + 0.25
        cb(None, quartz.kCGEventFlagsChanged, {"keycode": 63, "flags": 0}, None)
        triggered["done"] = True

    quartz.CFRunLoopRun = fake_run
    hook.start()
    hook.stop()

    assert triggered["done"] is True
    assert [a.name for a in actions] == ["START", "STOP"]
    assert actions[0].mode == "hold"


def test_hotkey_mac_permission_denied_logs_and_toasts_without_crash(monkeypatch, caplog):
    from mluvka import hotkey_mac

    quartz = _make_quartz_stub()
    quartz._state["return_none_tap"] = True
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    errors: list[str] = []
    with caplog.at_level(logging.ERROR, logger="mluvka.hotkey_mac"):
        hook = hotkey_mac.GlobalHotkeyHook(lambda _act: None, on_error=errors.append)
        hook.start()
        hook.stop()

    assert hook._permission_denied is True
    assert errors == [hotkey_mac.PERMISSION_TOAST_MESSAGE]
    assert any("CGEventTapCreate" in rec.message for rec in caplog.records)


def test_output_mac_paste_flow_and_concealed_restore(monkeypatch):
    from mluvka import output_mac

    quartz = _make_quartz_stub()
    cocoa, pb_data = _make_cocoa_pasteboard_stub(initial_text="puvodni", initial_seq=10)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "Cocoa", cocoa)
    monkeypatch.setattr(output_mac.time, "sleep", lambda _s: None)

    ok = output_mac.paste_text("ahoj", append_space=True)
    assert ok is True
    # Cmd+V = keycode 9 s kCGEventFlagMaskCommand (down + up)
    assert len(quartz._posted) == 2
    assert all(ev["keycode"] == output_mac.MAC_KEY_V for _tap, ev in quartz._posted)
    assert [ev["key_down"] for _tap, ev in quartz._posted] == [True, False]
    assert all(ev["flags"] == quartz.kCGEventFlagMaskCommand for _tap, ev in quartz._posted)
    # Schránka měla stejný changeCount (11), takže byla obnovena na "puvodni"
    assert pb_data["types"] == {
        "public.utf8-plain-text": "puvodni",
        output_mac.CONCEALED_PASTEBOARD_TYPE: b"",
    }

    # Když se během vložení schránka změnila zvenčí (changeCount != our_seq), obnovení se nesmí přepsat
    quartz._posted.clear()
    original_sleep = output_mac.time.sleep

    def bump_clipboard_during_wait(_s: float) -> None:
        # Během čekání musí být ve schránce ještě náš text s ConcealedType
        assert pb_data["types"]["public.utf8-plain-text"] == "test\n"  # type: ignore[index]
        assert output_mac.CONCEALED_PASTEBOARD_TYPE in pb_data["types"]  # type: ignore[operator]
        pb_data["change_count"] = int(pb_data["change_count"]) + 5  # type: ignore[arg-type]
        pb_data["types"] = {"public.utf8-plain-text": "cizi text"}

    monkeypatch.setattr(output_mac.time, "sleep", bump_clipboard_during_wait)
    ok2 = output_mac.paste_text("test\n", append_space=True)
    monkeypatch.setattr(output_mac.time, "sleep", original_sleep)
    assert ok2 is True
    assert pb_data["types"] == {"public.utf8-plain-text": "cizi text"}

    # grab_selection pošle Cmd+C (keycode 8) a vrátí prázdno pokud se changeCount nezvýšil
    quartz._posted.clear()
    sel, prev = output_mac.grab_selection()
    assert sel == ""
    assert prev == "cizi text"
    assert [ev["keycode"] for _tap, ev in quartz._posted] == [output_mac.MAC_KEY_C, output_mac.MAC_KEY_C]
    output_mac.send_mask_key()


def test_okno_dark_mode_and_state_on_darwin(monkeypatch, tmp_path: Path):
    from mluvka import okno

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("MLUVKA_DATA", str(tmp_path))

    monkeypatch.setattr(
        okno.subprocess,
        "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout="Dark\n"),
    )
    assert okno._system_is_dark() is True

    monkeypatch.setattr(
        okno.subprocess,
        "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=1, stdout=""),
    )
    assert okno._system_is_dark() is False

    monkeypatch.setattr(okno, "is_app_running", lambda: False)
    monkeypatch.setattr(okno.audio, "list_input_devices", lambda: [])

    api = okno.Api(data=tmp_path)
    state = api.get_state()
    assert state["ok"] is True
    assert state["platform"] == "darwin"
    assert state["sledovani_available"] is False
    assert state["doladeni_available"] is False

    # Mac VK kódy projdou validací, Windows-only klávesy (např. RShift 161) selžou
    saved = api.save_settings({"klavesa_vk": 165})
    assert saved["ok"] is True
    assert saved["config"]["klavesa_vk"] == 165

    bad = api.save_settings({"klavesa_vk": 161})
    assert bad["ok"] is False


def test_sledovani_unavailable_on_darwin(monkeypatch):
    from mluvka import sledovani

    monkeypatch.setattr(sys, "platform", "darwin")
    assert sledovani.is_available() is False
    assert sledovani.status() == "nedostupné"

    reader = sledovani.UnavailableReader()
    assert reader.status() == "nedostupné"
    assert reader.get_focused() is None
    assert reader.is_password(None) is False
    assert reader.read_text(None) is None

    s = sledovani.Sledovac()
    assert s.is_available() is False
    assert s.status() == "nedostupné"
    s.start("ahoj", "id1", lambda *_a: None)
    assert s._thread is None
    s.stop()


def test_doladeni_unsupported_message_on_darwin(monkeypatch, tmp_path: Path):
    from mluvka import doladeni

    monkeypatch.setattr(sys, "platform", "darwin")
    expected_msg = "Doladění modelu je zatím jen pro Windows s NVIDIA GPU"

    assert doladeni.is_supported() is False
    stav = doladeni.doladeni_stav(tmp_path)
    assert stav["dostupne"] is False
    assert stav["stav"] == "nepodporovano"
    assert stav["chyba"] == expected_msg

    res_start = doladeni.doladeni_spustit(tmp_path)
    assert res_start == {"ok": False, "error": expected_msg}

    res_use = doladeni.doladeni_pouzit(tmp_path)
    assert res_use == {"ok": False, "error": expected_msg}

    res_stop = doladeni.doladeni_zastavit(tmp_path)
    assert res_stop["ok"] is True
    assert res_stop["dostupne"] is False
    assert res_stop["stav"] == "nepodporovano"
    assert res_stop["chyba"] == expected_msg


def test_winproc_mac_mereni_audio_tray_zastupci_on_darwin(monkeypatch, tmp_path: Path):
    from mluvka import audio, mereni, tray, trenink, winproc_mac, zastupci

    monkeypatch.setattr(sys, "platform", "darwin")

    # winproc_mac JobObject & foreground_process_name
    job = winproc_mac.JobObject()
    job.assign(123)
    job.close()
    assert winproc_mac.process_name_stem("Cursor.exe") == "cursor"

    cocoa = types.ModuleType("Cocoa")

    class FakeURL:
        def path(self) -> str:
            return "/Applications/Cursor.app/Contents/MacOS/Cursor"

    class FakeApp:
        def executableURL(self):
            return FakeURL()

    class FakeWS:
        @classmethod
        def sharedWorkspace(cls):
            return cls()

        def frontmostApplication(self):
            return FakeApp()

    cocoa.NSWorkspace = FakeWS
    monkeypatch.setitem(sys.modules, "Cocoa", cocoa)
    assert winproc_mac.foreground_process_name() == "Cursor"

    # mereni.stroj() na macOS Apple Silicon (Metal)
    monkeypatch.setattr(mereni.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mereni.platform, "mac_ver", lambda: ("14.6", ("", "", ""), "arm64"))

    def fake_run(cmd, **_kw):
        if cmd[:2] == ["system_profiler", "SPDisplaysDataType"]:
            return types.SimpleNamespace(returncode=0, stdout="      Chipset Model: Apple M3 Pro\n")
        if cmd[:2] == ["sysctl", "-n"] and cmd[2] == "machdep.cpu.brand_string":
            return types.SimpleNamespace(returncode=0, stdout="Apple M3 Pro\n")
        if cmd[:2] == ["sysctl", "-n"] and cmd[2] == "hw.memsize":
            return types.SimpleNamespace(returncode=0, stdout=str(18 * 1024**3))
        return types.SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(mereni.subprocess, "run", fake_run)
    info = mereni.stroj(tmp_path)
    assert info["gpu"] == "Apple M3 Pro"
    assert info["cpu"] == "Apple M3 Pro"
    assert info["engine"] == "whisper.cpp (Metal)"
    assert info["os"] == "macOS 14.6"

    # audio & trenink přehrávání přes afplay (bez winsound)
    popen_calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, cmd):
            self.cmd = cmd
            self._polled = None

        def poll(self):
            return self._polled

        def terminate(self):
            self._polled = 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, **_kw):
        popen_calls.append(list(cmd))
        return FakeProc(list(cmd))

    wav = tmp_path / "test.wav"
    wav.write_bytes(b"RIFF")
    with patch.object(audio.subprocess, "Popen", side_effect=fake_popen):
        audio.play_sound("start", True, data=tmp_path)
    assert popen_calls == [["afplay", str(tmp_path / "zvuky" / "start.wav")]]

    popen_calls.clear()
    with patch.object(trenink.subprocess, "Popen", side_effect=fake_popen):
        trenink.play_wav(wav)
        trenink.stop_playback()
    assert popen_calls == [["afplay", str(wav)]]

    # tray LaunchAgent plist
    plist_path = tray.set_autostart(True, startup_dir=tmp_path / "LaunchAgents", data=tmp_path / "data", repo=tmp_path / "repo")
    assert plist_path.name == "cz.mluvka.plist"
    parsed = plistlib.loads(plist_path.read_bytes())
    assert parsed["Label"] == "cz.mluvka"
    assert parsed["RunAtLoad"] is True
    assert parsed["ProgramArguments"] == [str(tmp_path / "data" / "venv" / "bin" / "python"), "-m", "mluvka"]
    assert "/opt/homebrew/bin" in parsed["EnvironmentVariables"]["PATH"]
    assert parsed["EnvironmentVariables"]["MLUVKA_DATA"] == str(tmp_path / "data")
    tray.set_autostart(False, startup_dir=tmp_path / "LaunchAgents")
    assert not plist_path.exists()

    # zastupci no-op
    desk, prog, ico = zastupci.create_shortcuts(data=tmp_path, repo=tmp_path)
    assert isinstance(desk, Path) and isinstance(prog, Path)
    assert not ico.exists()
    assert zastupci.remove_shortcuts() == []
def test_hotkey_mac_alternative_keys_and_lazy_import(monkeypatch):
    from mluvka import hotkey, hotkey_mac

    quartz = _make_quartz_stub()
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    now = [200.0]
    monkeypatch.setattr(hotkey_mac.time, "monotonic", lambda: now[0])

    # Otestovat Pravý Option (61->165), Pravý Command (54->92), Pravý Control (62->163)
    cases = [
        (61, hotkey.VK_RMENU, quartz.kCGEventFlagMaskAlternate),
        (54, hotkey.VK_RWIN, quartz.kCGEventFlagMaskCommand),
        (62, hotkey.VK_RCONTROL, quartz.kCGEventFlagMaskControl),
    ]
    for mac_code, vk, mask in cases:
        actions = []
        hook = hotkey_mac.GlobalHotkeyHook(actions.append, klavesa_vk=vk)

        def run_events(mc=mac_code, fl=mask):
            cb = quartz._callbacks[-1]
            cb(None, quartz.kCGEventFlagsChanged, {"keycode": mc, "flags": fl}, None)
            now[0] += hotkey.HOLD_MIN_S + 0.2
            cb(None, quartz.kCGEventFlagsChanged, {"keycode": mc, "flags": 0}, None)

        quartz.CFRunLoopRun = run_events
        hook.start()
        hook.stop()
        assert [a.name for a in actions] == ["START", "STOP"], f"Selhalo pro mac keycode {mac_code}"

    # Lazy __getattr__ na hotkey.py pro sys.platform == "darwin"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert hotkey.__getattr__("GlobalHotkeyHook") is hotkey_mac.GlobalHotkeyHook
def test_hotkey_mac_ignores_synthetic_events_and_distinguishes_left_right_modifiers(monkeypatch):
    from mluvka import hotkey, hotkey_mac, output_mac

    quartz = _make_quartz_stub()
    cocoa, _pb = _make_cocoa_pasteboard_stub(initial_text="puvodni", initial_seq=5)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "Cocoa", cocoa)
    monkeypatch.setattr(output_mac.time, "sleep", lambda _s: None)

    now = [300.0]
    monkeypatch.setattr(hotkey_mac.time, "monotonic", lambda: now[0])

    # Simulace: Pravý Ctrl (62) i modifikátory jsou fyzicky drženy při Cmd+V / grab_selection
    pressed_hw = {62, 61, 56}
    quartz.CGEventSourceKeyState = lambda _src, mac_code: mac_code in pressed_hw

    # release_modifiers musí uvolnit i Pravý Ctrl (VK_RCONTROL -> 62) a označit je SYNTHETIC_EVENT_TAG
    output_mac.release_modifiers()
    posted_codes = [ev["keycode"] for _t, ev in quartz._posted]
    assert 62 in posted_codes
    assert all(ev.get("user_data") == output_mac.SYNTHETIC_EVENT_TAG for _t, ev in quartz._posted)

    # Spuštění příkazového režimu (Shift + Pravý Option 61): syntetické key-upy z release_modifiers nesmí režim shodit
    actions = []
    hook = hotkey_mac.GlobalHotkeyHook(actions.append, klavesa_vk=hotkey.VK_RMENU)
    hook._quartz = quartz
    shift_opt = quartz.kCGEventFlagMaskShift | quartz.kCGEventFlagMaskAlternate | 0x00000002 | 0x00000040
    hook._handle_cg_event(None, quartz.kCGEventFlagsChanged, {"keycode": 61, "flags": shift_opt}, None)
    for _tap, syn_ev in list(quartz._posted):
        ev_type = quartz.kCGEventKeyDown if syn_ev["key_down"] else quartz.kCGEventKeyUp
        hook._handle_cg_event(None, ev_type, syn_ev, None)
        hook._handle_cg_event(None, quartz.kCGEventFlagsChanged, syn_ev, None)
    now[0] += hotkey.HOLD_MIN_S + 0.1
    hook._handle_cg_event(None, quartz.kCGEventFlagsChanged, {"keycode": 61, "flags": 0}, None)

    emitted = []
    while not hook._action_queue.empty():
        emitted.append(hook._action_queue.get_nowait())
    assert [(a.name, a.mode) for a in emitted] == [("START", "command"), ("STOP_COMMAND", "")]

    # Nezávislost levého a pravého Option (Left Option držen: 0x20, Right Option stisk+puštění: 0x40)
    hook2 = hotkey_mac.GlobalHotkeyHook(lambda _a: None, klavesa_vk=hotkey.VK_RMENU)
    hook2._quartz = quartz
    left_only = quartz.kCGEventFlagMaskAlternate | 0x00000020
    both_opt = quartz.kCGEventFlagMaskAlternate | 0x00000020 | 0x00000040
    hook2._handle_cg_event(None, quartz.kCGEventFlagsChanged, {"keycode": 58, "flags": left_only}, None)
    hook2._handle_cg_event(None, quartz.kCGEventFlagsChanged, {"keycode": 61, "flags": both_opt}, None)
    now[0] += hotkey.HOLD_MIN_S + 0.1
    hook2._handle_cg_event(None, quartz.kCGEventFlagsChanged, {"keycode": 61, "flags": left_only}, None)

    emitted2 = []
    while not hook2._action_queue.empty():
        emitted2.append(hook2._action_queue.get_nowait())
    assert [a.name for a in emitted2] == ["START", "STOP"]


def test_overlay_subprocess_client_protocol(monkeypatch):
    import io
    import json
    from mluvka import overlay

    written_buf = io.BytesIO()
    state = {"polled": None, "waited": False, "terminated": False}

    class FakeStdin:
        def write(self, data: bytes) -> int:
            return written_buf.write(data)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeProc:
        def __init__(self, *_a, **_kw):
            self.stdin = FakeStdin()

        def poll(self):
            return state["polled"]

        def wait(self, timeout=None):
            state["waited"] = True
            state["polled"] = 0
            return 0

        def terminate(self):
            state["terminated"] = True
            state["polled"] = 0

    monkeypatch.setattr(overlay.subprocess, "Popen", FakeProc)
    client = overlay.OverlaySubprocessClient(python_exe="/usr/bin/python3")
    client.start()
    client.show("nahravam")
    client.toast("Hotovo", "info")
    client.hide()
    client.quit()

    lines = [json.loads(line) for line in written_buf.getvalue().decode("utf-8").splitlines()]
    assert lines == [
        {"op": "show", "state": "nahravam"},
        {"op": "toast", "text": "Hotovo", "kind": "info"},
        {"op": "hide"},
        {"op": "quit"},
    ]
    assert state["waited"] is True
    # Volání po quit() nesmí spadnout
    client.show("nahravam")


def test_okno_focus_existing_window_and_tray_env_autostart_on_darwin(monkeypatch, tmp_path: Path):
    import plistlib
    from mluvka import okno, tray

    monkeypatch.setattr(sys, "platform", "darwin")
    data_dir = tmp_path / "mluvka_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".okno.lock").write_text("4242\n", encoding="utf-8")

    activated: list[tuple[int, int]] = []

    class FakeApp:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def activateWithOptions_(self, opts: int) -> bool:
            activated.append((self.pid, opts))
            return True

    class FakeNSRunningApp:
        @classmethod
        def runningApplicationWithProcessIdentifier_(cls, pid: int):
            return FakeApp(pid)

    cocoa = types.ModuleType("Cocoa")
    cocoa.NSRunningApplication = FakeNSRunningApp
    cocoa.NSApplicationActivateIgnoringOtherApps = 2
    monkeypatch.setitem(sys.modules, "Cocoa", cocoa)

    monkeypatch.setenv("MLUVKA_DATA", str(data_dir))
    assert okno.focus_existing_window("Mluvka") is True
    assert activated == [(4242, 2)]

    # tray LaunchAgent zachová MLUVKA_DATA z env i když data kwarg není předán
    monkeypatch.setenv("MLUVKA_DATA", str(data_dir))
    plist_path = tray.set_autostart(True, startup_dir=tmp_path / "LaunchAgents", repo=tmp_path / "repo")
    parsed = plistlib.loads(plist_path.read_bytes())
    assert parsed["EnvironmentVariables"]["MLUVKA_DATA"] == str(data_dir)
def test_hotkey_mac_missing_quartz_calls_on_error(monkeypatch):
    from mluvka import hotkey_mac

    monkeypatch.setitem(sys.modules, "Quartz", None)
    errors: list[str] = []
    hook = hotkey_mac.GlobalHotkeyHook(lambda _a: None, on_error=errors.append)
    hook.start()
    hook.stop()
    assert hook._permission_denied is True
    assert len(errors) == 1
    assert "Quartz" in errors[0]


def test_ui_app_js_uses_valid_vk_meta_for_empty_state():
    app_js = (Path(__file__).resolve().parent.parent / "mluvka" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "vkMeta(" not in app_js
    assert "state.platform" not in app_js
    assert "emptyDictateMessage()" in app_js
    assert "VK_META[vk]" in app_js
    assert "state.data && state.data.platform" in app_js
