"""Testy pro historii (smazání, vyčištění, statistiky), API hlavního okna, zástupce a synchronizaci konfigurace."""

from __future__ import annotations

import json
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from mluvka import __main__ as cli_main
from mluvka import app as app_mod
from mluvka import config, history, hotkey, okno, tray, zastupci


def _seed_entries(data: Path) -> list[str]:
    tz = timezone(timedelta(hours=1))
    times = [
        datetime(2026, 11, 10, 9, 0, tzinfo=tz).isoformat(),
        datetime(2026, 11, 11, 14, 15, tzinfo=tz).isoformat(),
        datetime(2026, 11, 12, 10, 30, tzinfo=tz).isoformat(),
    ]
    rows = [
        {"cas": times[0], "aplikace": "Word", "audio_s": 10.0, "prepis_s": 0.4, "text": "Starší záznam z předvčerejška"},
        {"cas": times[1], "aplikace": "Slack", "audio_s": 30.0, "prepis_s": 0.5, "text": "Příliš žluťoučký kůň úpěl ďábelské ódy"},
        {"cas": times[2], "aplikace": "Code", "audio_s": 30.0, "prepis_s": 0.3, "text": "Krátký test diktování v Mluvce teď"},
    ]
    path = config.history_jsonl_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return times


def test_history_query_delete_clear_and_stats(tmp_path: Path) -> None:
    times = _seed_entries(tmp_path)

    # Největší -> nejnovější první, vyhledávání bez diakritiky i bez rozlišení velikosti
    items, total = history.query_entries("prilis zlutoucky", 0, 50, data=tmp_path)
    assert total == 1
    assert items[0]["cas"] == times[1]

    all_items, total_all = history.query_entries("", 0, 50, data=tmp_path)
    assert total_all == 3
    assert [r["cas"] for r in all_items] == list(reversed(times))

    # Statistiky za 2 dny (od 2026-11-10 12:00 zahrne jen times[1] a times[2])
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone(timedelta(hours=1)))
    st = history.stats(days=2, now=now, data=tmp_path)
    assert st["count"] == 2
    assert st["words"] == 12
    assert st["audio_s"] == 60.0
    assert st["saved_s"] == 0.0  # 12/40*60 - 60 = -42 -> oříznuto na 0
    assert st["wpm"] == 12.0

    # Smazání prostředního záznamu + žádné zbytkové .tmp
    assert history.delete_entry(times[1], data=tmp_path) is True
    assert history.delete_entry("non-existent", data=tmp_path) is False
    rem, rem_total = history.query_entries("", 0, 50, data=tmp_path)
    assert rem_total == 2
    assert [r["cas"] for r in rem] == [times[2], times[0]]
    assert list(tmp_path.glob("*.tmp")) == []

    # Vyčištění všeho
    assert history.clear_all(data=tmp_path) == 2
    assert history.load_entries(tmp_path) == []


def test_history_stats_saved_seconds_positive(tmp_path: Path) -> None:
    tz = timezone(timedelta(hours=1))
    now = datetime(2026, 11, 12, 12, 0, tzinfo=tz)
    row = {
        "cas": (now - timedelta(hours=1)).isoformat(),
        "aplikace": "Notepad",
        "audio_s": 10.0,
        "prepis_s": 0.2,
        "text": " ".join(["slovo"] * 40),
    }
    config.history_jsonl_path(tmp_path).write_text(json.dumps(row) + "\n", encoding="utf-8")
    st = history.stats(days=7, now=now, data=tmp_path)
    assert st["words"] == 40
    assert st["saved_s"] == 50.0  # 40/40*60 - 10 = 50 s
    assert st["wpm"] == 240.0


def test_api_state_hides_key_and_set_api_key_removes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(okno.audio, "list_input_devices", lambda: ["Mic A", "Mic B"])
    store = config.ConfigStore(tmp_path)
    store.set_value("claude_api_klic", "sk-ant-secret-9876")

    api = okno.Api(data=tmp_path)
    st = api.get_state()
    assert st["ok"] is True
    assert "claude_api_klic" not in st["config"]
    assert "sk-ant-secret-9876" not in json.dumps(st)
    assert st["api_key_set"] is True
    assert st["api_key_tail"] == "9876"
    assert st["devices"] == ["Mic A", "Mic B"]

    res_set = api.set_api_key("   sk-ant-new-1234  ")
    assert res_set["ok"] is True
    assert res_set["api_key_set"] is True
    assert res_set["api_key_tail"] == "1234"
    assert config.load_config(tmp_path)["claude_api_klic"] == "sk-ant-new-1234"

    res_rm = api.set_api_key("")
    assert res_rm["ok"] is True
    assert res_rm["api_key_set"] is False
    assert res_rm["api_key_tail"] == ""
    assert config.load_config(tmp_path)["claude_api_klic"] == ""


def test_api_save_settings_validation_and_autostart(tmp_path: Path, monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(okno.tray, "set_autostart", lambda enable, data=None: calls.append(bool(enable)))
    monkeypatch.setattr(okno.audio, "list_input_devices", lambda: [])

    api = okno.Api(data=tmp_path)

    for bad_patch in [
        {"klavesa_vk": 999},
        {"klavesa_vk": True},
        {"historie_dny": 14},
        {"kanal_webhook": "http://example.com/hook"},
        {"kanal_webhook": "https://"},
        {"neznamy_klic": 123},
        {"zvuky": "ano"},
        {"slovnik": {"slova": [""]}},
        {"slovnik": {"slova": ["x" * 61]}},
        {"slovnik": {"neznamy": []}},
        {"zkratky": [["", "tělo"]]},
    ]:
        res = api.save_settings(bad_patch)
        assert res["ok"] is False
        assert res["error"]

    good = api.save_settings(
        {
            "klavesa_vk": 161,
            "zvuky": False,
            "historie_dny": 90,
            "kanal_webhook": "https://hooks.slack.com/services/T1/B1/abc",
            "kanal_zapnuto": True,
            "autostart": True,
            "slovnik": {
                "slova": ["Mluvka", "Mluvka", "Brno"],
                "opravy": [["mlúvka", "Mluvka"]],
            },
            "zkratky": [["podpis", "S pozdravem,\nJan"]],
        }
    )
    assert good["ok"] is True
    assert good["config"]["klavesa_vk"] == 161
    assert good["config"]["zvuky"] is False
    assert good["config"]["historie_dny"] == 90
    assert good["config"]["slovnik"]["slova"] == ["Mluvka", "Brno"]
    assert good["config"]["slovnik"]["opravy"] == [["mlúvka", "Mluvka"]]
    assert good["config"]["zkratky"] == [["podpis", "S pozdravem,\nJan"]]
    assert calls == [True]


def test_api_open_url_allowlist_and_test_webhook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(okno.audio, "list_input_devices", lambda: [])
    opened: list[str] = []
    monkeypatch.setattr(okno.os, "startfile", lambda u: opened.append(u), raising=False)

    api = okno.Api(data=tmp_path)
    for blocked in [
        "https://evil.example.com",
        "http://console.anthropic.com",
        "https://console.anthropic.com/settings/keys",
        "javascript:alert(1)",
        "",
    ]:
        res = api.open_url(blocked)
        assert res["ok"] is False
    assert opened == []

    ok_res = api.open_url("https://console.anthropic.com")
    assert ok_res["ok"] is True
    assert opened == ["https://console.anthropic.com"]

    api.save_settings({"kanal_webhook": "https://hooks.slack.com/services/T/B/C"})
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(okno.channel, "send_sync", lambda u, t: sent.append((u, t)))
    assert api.test_webhook() == {"ok": True}
    assert sent == [("https://hooks.slack.com/services/T/B/C", "Test z Mluvky")]

    def _fail_http(u, t):
        raise urllib.error.HTTPError(u, 404, "Not Found", {}, None)

    monkeypatch.setattr(okno.channel, "send_sync", _fail_http)
    err_res = api.test_webhook()
    assert err_res["ok"] is False
    assert "404" in err_res["error"]


def test_zastupci_env_passing_and_ico_sizes(tmp_path: Path) -> None:
    ico = zastupci.ensure_ico(tmp_path)
    assert ico == tmp_path / "mluvka.ico"
    assert ico.is_file()
    with Image.open(ico) as im:
        sizes = set(im.info.get("sizes", set()))
    assert set(zastupci.ICO_SIZES).issubset(sizes)

    evil_path = tmp_path / "O'Brien & Co; $(calc).lnk"
    cmd, env = zastupci.build_powershell_call(
        desktop_lnk=evil_path,
        programs_lnk=tmp_path / "Start.lnk",
        target_exe=tmp_path / "venv" / "Scripts" / "pythonw.exe",
        repo=tmp_path / "repo dir",
        icon_file=ico,
    )
    assert cmd[:3] == ["powershell", "-NoProfile", "-Command"]
    ps_script = cmd[3]
    assert "O'Brien" not in ps_script
    assert str(tmp_path) not in ps_script
    assert "$(calc)" not in ps_script
    assert env["MLUVKA_LNK_DESKTOP"] == str(evil_path)
    assert env["MLUVKA_LNK_ARGS"] == "-m mluvka --okno"
    assert env["MLUVKA_LNK_ICON"] == str(ico)
    assert env["MLUVKA_LNK_DESC"] == zastupci.SHORTCUT_DESCRIPTION


def test_tray_first_default_item_and_cli_okno_flag(tmp_path: Path, monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(cli_main.app, "run", lambda open_window=False: calls.append(bool(open_window)) or 0)
    monkeypatch.setattr(cli_main.netguard, "install", lambda hosts: None)
    monkeypatch.setattr(cli_main.config, "load_config", lambda: dict(config.DEFAULT_CONFIG))

    assert cli_main.main([]) == 0
    assert cli_main.main(["--okno"]) == 0
    assert calls == [False, True]

    opened: list[bool] = []
    tc = tray.TrayController(
        get_cfg=lambda: dict(config.DEFAULT_CONFIG),
        is_ready=lambda: True,
        is_paused=lambda: False,
        on_paste_last=lambda: None,
        on_select_mic=lambda n: None,
        on_toggle_ai=lambda: None,
        on_toggle_channel=lambda: None,
        on_toggle_sounds=lambda: None,
        on_toggle_paused=lambda: None,
        on_toggle_autostart=lambda: None,
        on_quit=lambda: None,
        on_open_window=lambda: opened.append(True),
        data=tmp_path,
    )
    first_item = tc._build_menu().items[0]
    assert first_item.text == "Otevřít Mluvku"
    assert first_item.default is True
    first_item(tc._icon)
    assert opened == [True]


def test_app_config_poll_and_immediate_vk_change(tmp_path: Path) -> None:
    store = config.ConfigStore(tmp_path)
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_ai = MagicMock()
    hook = hotkey.GlobalHotkeyHook(on_action=lambda a: None, klavesa_vk=hotkey.VK_RCONTROL)
    hook.start()
    try:
        with (
            patch.object(app_mod.overlay, "Overlay", return_value=fake_overlay),
            patch.object(app_mod.tray, "TrayController", return_value=fake_tray),
            patch.object(app_mod.asr, "WhisperServer", return_value=fake_whisper),
            patch.object(app_mod.llm, "ClaudeAI", return_value=fake_ai),
            patch.object(app_mod.hotkey, "GlobalHotkeyHook", return_value=hook),
            patch.object(app_mod.netguard, "set_allowed_hosts") as mock_ng,
        ):
            application = app_mod.MluvkaApp(store, data=tmp_path)
            time.sleep(0.02)
            # Externí úprava config.json jako by ji provedl proces okna
            other_store = config.ConfigStore(tmp_path)
            other_store.update_values({"klavesa_vk": 161, "zvuky": False, "kanal_zapnuto": True, "kanal_webhook": "https://hooks.slack.com/services/a/b"})

            application._poll_config_timer()
            fake_overlay.root.after.assert_called_once_with(2000, application._poll_config_timer)
            fake_tray.refresh_menu.assert_called_once()
            assert any("hooks.slack.com" in hosts for (hosts,), _ in mock_ng.call_args_list)

            # Hook vlákno se muselo samo probudit přes WM_APP bez jakéhokoli stisku staré klávesy
            deadline = time.monotonic() + 1.0
            while hook._fsm.klavesa_vk != 161 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert hook._fsm.klavesa_vk == 161
    finally:
        hook.stop()


def test_duplicate_launch_opens_window_instead_of_messagebox(tmp_path: Path, monkeypatch) -> None:
    launched: list[Path | None] = []
    monkeypatch.setattr(app_mod.overlay, "enable_dpi_awareness", lambda: None)
    monkeypatch.setattr(app_mod.okno, "launch_window", lambda data=None: launched.append(data))
    monkeypatch.setattr(app_mod.kernel32, "CreateMutexW", lambda *args: 123)
    monkeypatch.setattr(app_mod.kernel32, "CloseHandle", lambda h: 1)
    monkeypatch.setattr(app_mod.ctypes, "get_last_error", lambda: app_mod.ERROR_ALREADY_EXISTS)

    rc = app_mod.run(data=tmp_path)
    assert rc == 0
    assert launched == [tmp_path]


def test_history_concurrent_deletes_and_mixed_tz_zero_audio(tmp_path: Path) -> None:
    import concurrent.futures

    tz = timezone(timedelta(hours=1))
    now = datetime(2026, 11, 12, 12, 0, tzinfo=tz)
    rows = [
        {"cas": datetime(2026, 11, 12, 10, i, tzinfo=tz).isoformat(), "aplikace": "App", "audio_s": 0.0, "prepis_s": 0.1, "text": f"zaznam {i}"}
        for i in range(12)
    ]
    # Přidat naivní ISO čas bez zóny
    rows.append({"cas": "2026-11-12T11:30:00", "aplikace": "App", "audio_s": 0.0, "prepis_s": 0.1, "text": "naivni cas"})
    config.history_jsonl_path(tmp_path).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    st = history.stats(days=7, now=now, data=tmp_path)
    assert st["count"] == 13
    assert st["audio_s"] == 0.0
    assert st["wpm"] == 0.0

    to_delete = [rows[i]["cas"] for i in range(0, 10, 2)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda c: history.delete_entry(c, data=tmp_path), to_delete))
    assert all(results)
    remaining = history.load_entries(tmp_path)
    assert len(remaining) == len(rows) - len(to_delete)
    assert not list(tmp_path.glob("*.tmp"))


def test_config_transient_oserror_keeps_valid_store(tmp_path: Path, monkeypatch) -> None:
    store = config.ConfigStore(tmp_path)
    assert store._disk_valid is True

    ext = config.ConfigStore(tmp_path)
    ext.set_value("zvuky", False)

    orig_read = Path.read_text
    boom = {"hit": False}

    def flaky_read(self: Path, *args, **kwargs):
        if self == store.path and not boom["hit"]:
            boom["hit"] = True
            raise PermissionError("busy replace")
        return orig_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read)
    assert store.reload_if_changed() is False
    assert store._disk_valid is True
    # Na dalším ticku se změna správně načte
    assert store.reload_if_changed() is True
    assert store.get("zvuky") is False


def test_save_settings_autostart_failure_does_not_persist_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(okno.audio, "list_input_devices", lambda: [])
    monkeypatch.setattr(
        okno.tray,
        "set_autostart",
        lambda enable, data=None: (_ for _ in ()).throw(PermissionError("denied")),
    )
    api = okno.Api(data=tmp_path)
    res = api.save_settings({"autostart": True, "zvuky": False})
    assert res["ok"] is False
    assert config.load_config(tmp_path)["autostart"] is False
    assert config.load_config(tmp_path)["zvuky"] is True


def test_api_test_api_key_error_cases_and_close(tmp_path: Path, monkeypatch) -> None:
    import anthropic

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(okno.audio, "list_input_devices", lambda: [])
    api = okno.Api(data=tmp_path)

    res_missing = api.test_api_key()
    assert res_missing["ok"] is False
    assert "není nastavený" in res_missing["error"]

    api.set_api_key("sk-ant-test-9999")
    dummy_req = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages", headers={})
    dummy_resp = MagicMock(status_code=401, headers={}, request=dummy_req)
    rl_resp = MagicMock(status_code=429, headers={}, request=dummy_req)
    srv_resp = MagicMock(status_code=503, headers={}, request=dummy_req)

    cases = [
        (None, True, ""),
        (anthropic.AuthenticationError("bad", response=dummy_resp, body=None), False, "Neplatný API klíč"),
        (anthropic.RateLimitError("limit", response=rl_resp, body=None), False, "limit"),
        (anthropic.APIConnectionError(request=dummy_req), False, "api.anthropic.com"),
        (anthropic.APIStatusError("err", response=srv_resp, body=None), False, "503"),
    ]
    for exc, expected_ok, substr in cases:
        closed = []

        timeouts = []

        class _FakeAI:
            def __init__(self, data=None):
                pass

            def chat(self, sys_p, user_p, timeout: float = 30.0):
                timeouts.append(timeout)
                if exc is not None:
                    raise exc
                return "Ahoj"

            def close(self):
                closed.append(True)

        monkeypatch.setattr(okno.llm, "ClaudeAI", _FakeAI)
        r = api.test_api_key()
        assert timeouts == [8.0]
        assert r["ok"] is expected_ok
        if substr:
            assert substr in r["error"]
        assert closed == [True]


def test_okno_navigation_guard_and_api_misc(tmp_path: Path, monkeypatch) -> None:
    assert okno._is_allowed_local_navigation("about:blank", "http://127.0.0.1:8123/", "http://127.0.0.1:8123/index.html") is True
    assert okno._is_allowed_local_navigation("http://127.0.0.1:8123/app.css", "http://127.0.0.1:8123/", "http://127.0.0.1:8123/index.html") is True
    assert okno._is_allowed_local_navigation("http://127.0.0.1:8123.evil.com/", "http://127.0.0.1:8123/", "http://127.0.0.1:8123/index.html") is False
    assert okno._is_allowed_local_navigation("https://example.com/", "http://127.0.0.1:8123/", "http://127.0.0.1:8123/index.html") is False
    assert okno._is_allowed_local_navigation("file:///C:/ui/app.js", "file:///C:/ui/", "file:///C:/ui/index.html") is True
    assert okno._is_allowed_local_navigation("file:///C:/Windows/win.ini", "file:///C:/ui/", "file:///C:/ui/index.html") is False
    assert okno._is_allowed_local_navigation("", "file:///C:/ui/", "file:///C:/ui/index.html") is False

    times = _seed_entries(tmp_path)
    api = okno.Api(data=tmp_path)
    h = api.get_history("", 0, 10)
    assert h["ok"] is True and h["total"] == 3 and h["has_more"] is False
    d = api.delete_history(times[0])
    assert d["ok"] is True and d["deleted"] is True
    c = api.clear_history()
    assert c["ok"] is True and c["removed"] == 2

    monkeypatch.setattr(okno.output, "set_clipboard_text", lambda txt: 42)
    assert api.copy_text("ahoj") == {"ok": True}
    monkeypatch.setattr(okno.output, "set_clipboard_text", lambda txt: None)
    assert api.copy_text("ahoj")["ok"] is False

    monkeypatch.setattr(okno, "is_app_running", lambda: False)
    started = []
    monkeypatch.setattr(okno, "launch_detached_module", lambda mod, data=None: started.append((mod, data)))
    assert api.start_app() == {"ok": True, "started": True}
    assert started == [("mluvka", tmp_path)]

    monkeypatch.setattr(okno.os, "startfile", lambda p: (_ for _ in ()).throw(OSError("no shell")), raising=False)
    assert api.open_data_folder()["ok"] is False


def test_zastupci_remove_shortcuts_and_cli_odebrat(tmp_path: Path, monkeypatch) -> None:
    desk = tmp_path / "Desktop"
    prog = tmp_path / "Programs"
    desk.mkdir()
    prog.mkdir()
    d_lnk = desk / zastupci.SHORTCUT_NAME
    p_lnk = prog / zastupci.SHORTCUT_NAME
    d_lnk.write_bytes(b"lnk")
    p_lnk.write_bytes(b"lnk")

    monkeypatch.setattr(zastupci, "desktop_dir", lambda: desk)
    monkeypatch.setattr(zastupci, "programs_dir", lambda: prog)
    assert zastupci.main(["--odebrat"]) == 0
    assert not d_lnk.exists() and not p_lnk.exists()
    # Idempotentní druhý běh
    assert zastupci.remove_shortcuts() == []
def test_navigation_traversal_and_save_settings_edges(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(okno.audio, "list_input_devices", lambda: [])

    # Traversal s .. a zpětným lomítkem musí být zablokován
    assert okno._is_allowed_local_navigation("file:///C:/ui/../../Windows/win.ini", "file:///C:/ui/", "file:///C:/ui/index.html") is False
    assert okno._is_allowed_local_navigation("http://127.0.0.1:8123/../../x", "http://127.0.0.1:8123/", "http://127.0.0.1:8123/index.html") is False
    assert okno._is_allowed_local_navigation(r"file:///C:/ui\..\win.ini", "file:///C:/ui/", "file:///C:/ui/index.html") is False

    api = okno.Api(data=tmp_path)
    for bad in [
        "abc",
        [],
        {"zkratky": [["a" * 61, "tělo"]]},
        {"zkratky": [["ok", "x" * 4001]]},
        {"mikrofon": 123},
        {"historie_dny": True},
    ]:
        res = api.save_settings(bad)  # type: ignore[arg-type]
        assert res["ok"] is False
        assert res["error"]

    res_key = api.set_api_key(123)  # type: ignore[arg-type]
    assert res_key["ok"] is False
    assert res_key["error"]


def test_get_state_env_short_key_and_netguard_updates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(okno.audio, "list_input_devices", lambda: [])
    ng_calls: list[list[str]] = []
    monkeypatch.setattr(okno.netguard, "set_allowed_hosts", lambda hosts: ng_calls.append(sorted(hosts)))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-123456")
    api = okno.Api(data=tmp_path)
    st_env = api.get_state()
    assert st_env["ok"] is True
    assert st_env["env_key"] is True
    assert st_env["api_key_set"] is False
    assert st_env["api_key_tail"] == ""
    assert "api.anthropic.com" in st_env["allowed_hosts"]

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res_short = api.set_api_key("ab")
    assert res_short["ok"] is True
    assert res_short["api_key_set"] is True
    assert res_short["api_key_tail"] == ""
    assert any("api.anthropic.com" in h for h in ng_calls)

    ng_calls.clear()
    res_wh = api.save_settings({"kanal_zapnuto": True, "kanal_webhook": "https://hooks.slack.com/services/T1/B1/Z"})
    assert res_wh["ok"] is True
    assert any("hooks.slack.com" in h for h in ng_calls)


def test_okno_mutex_launch_and_zastupci_create_shortcuts(tmp_path: Path, monkeypatch) -> None:
    # okno.main při existujícím mutexu jen aktivuje stávající okno a vrátí 0
    focused: list[bool] = []
    closed_handles: list[int] = []
    monkeypatch.setattr(okno.shell32, "SetCurrentProcessExplicitAppUserModelID", lambda app_id: 0)
    monkeypatch.setattr(okno.netguard, "install", lambda hosts: None)
    monkeypatch.setattr(okno.kernel32, "CreateMutexW", lambda *a: 777)
    monkeypatch.setattr(okno.kernel32, "CloseHandle", lambda h: closed_handles.append(h) or 1)
    monkeypatch.setattr(okno.ctypes, "get_last_error", lambda: okno.ERROR_ALREADY_EXISTS)
    monkeypatch.setattr(okno, "focus_existing_window", lambda title="Mluvka": focused.append(True) or True)

    assert okno.main(data=tmp_path) == 0
    assert focused == [True]
    assert closed_handles == [777]

    # launch_window: běží -> focus, neběží -> launch_detached_module
    focus_calls: list[bool] = []
    detached: list[tuple[str, Path | None]] = []
    monkeypatch.setattr(okno, "focus_existing_window", lambda title="Mluvka": focus_calls.append(True) or True)
    monkeypatch.setattr(okno, "launch_detached_module", lambda mod, data=None: detached.append((mod, data)))

    monkeypatch.setattr(okno, "is_window_running", lambda: True)
    okno.launch_window(data=tmp_path)
    assert focus_calls == [True]
    assert detached == []

    monkeypatch.setattr(okno, "is_window_running", lambda: False)
    okno.launch_window(data=tmp_path)
    assert detached == [("mluvka.okno", tmp_path)]

    # zastupci.create_shortcuts s mockovaným subprocess.run
    desk = tmp_path / "Desktop"
    prog = tmp_path / "Programs"
    pyw = tmp_path / "venv" / "Scripts" / "pythonw.exe"
    pyw.parent.mkdir(parents=True)
    pyw.write_bytes(b"")
    runs: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(cmd, env=None, check=False, capture_output=False, text=False, **kw):
        runs.append((cmd, env or {}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(zastupci.subprocess, "run", fake_run)
    d_lnk, p_lnk, ico = zastupci.create_shortcuts(data=tmp_path, repo=tmp_path, desktop=desk, programs=prog)
    assert ico == tmp_path / "mluvka.ico" and ico.is_file()
    assert d_lnk == desk / zastupci.SHORTCUT_NAME
    assert p_lnk == prog / zastupci.SHORTCUT_NAME
    assert len(runs) == 1
    assert runs[0][1]["MLUVKA_LNK_DESKTOP"] == str(d_lnk)
    assert runs[0][1]["MLUVKA_LNK_PROGRAMS"] == str(p_lnk)
    assert runs[0][1]["MLUVKA_LNK_TARGET"] == str(pyw)
