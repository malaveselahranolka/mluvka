"""Jednotkové testy pro llm, audio, tray, overlay a Win32 struktury."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from mluvka import audio, hotkey, llm, output, overlay, tray, winproc


def test_win32_struct_sizes_on_64bit() -> None:
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(hotkey.KBDLLHOOKSTRUCT) == 24
        assert ctypes.sizeof(output.INPUT) == 40
        assert ctypes.sizeof(winproc.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144


def test_job_object_kills_child_on_close(tmp_path: Path) -> None:
    job = winproc.JobObject()
    log_path = tmp_path / "dummy.log"
    proc, log_f = winproc.start_hidden_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        log_path,
        job=job,
    )
    assert proc.poll() is None
    job.close()
    rc = proc.wait(timeout=5.0)
    assert rc is not None
    log_f.close()


def test_llm_helpers() -> None:
    assert llm.validate_cleanup_ratio("Ahoj světe jak se máš", "Ahoj světe, jak se máš?") == "Ahoj světe, jak se máš?"
    assert llm.validate_cleanup_ratio("Dlouhá věta o deseti slovech", "A") == "Dlouhá věta o deseti slovech"
    # škrtnuté přeřeknutí smí text výrazně zkrátit
    raw = "Takže ne, chtěl bych jako první udělat novou nabídku. Ne, ne, ne, nechtěl bych to, chtěl bych spíš návrh."
    assert llm.validate_cleanup_ratio(raw, "Chtěl bych spíš návrh.") == "Chtěl bych spíš návrh."
    # odpověď místo úpravy (výrazně delší) se zahodí
    answer = "def prumer(xs): return sum(xs) / len(xs)  # průměr čísel v seznamu"
    assert llm.validate_cleanup_ratio("Napiš funkci na průměr.", answer) == "Napiš funkci na průměr."


def test_audio_helpers_and_sounds(tmp_path: Path) -> None:
    assert audio.rms_to_level(0.0) == 0.0
    assert audio.rms_to_level(10.0 ** (-60.0 / 20.0)) == 0.0
    assert audio.rms_to_level(10.0 ** (-12.0 / 20.0)) == 1.0
    assert audio.rms_to_level(1.0) == 1.0

    sig_48k = np.sin(np.linspace(0, 10, 48000, dtype=np.float32))
    out_16k = audio.resample_to_16k(sig_48k, 48000)
    assert out_16k.dtype == np.float32
    assert len(out_16k) == 16000

    start_wav, stop_wav = audio.ensure_sounds(tmp_path)
    for p in (start_wav, stop_wav):
        assert p.is_file()
        with wave.open(str(p), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            sr = wf.getframerate()
            dur = wf.getnframes() / sr
            assert abs(dur - 0.070) < 0.002
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
            peak_dbfs = 20.0 * np.log10(np.max(np.abs(pcm)) / 32767.0)
            assert -18.5 <= peak_dbfs <= -17.5


def test_tray_icons_and_autostart(tmp_path: Path) -> None:
    for stav in ("pripraveno", "nahravam", "pozastaveno", "nacita"):
        img = tray.kresli_ikonu(stav)
        assert img.size == (64, 64)
        assert img.mode == "RGBA"

    startup_dir = tmp_path / "Startup"
    cmd = tray.set_autostart(True, startup_dir=startup_dir, data=tmp_path, repo=tmp_path)
    assert cmd.is_file()
    txt = cmd.read_text(encoding="utf-8")
    assert "pythonw.exe" in txt
    assert "-m mluvka" in txt

    tray.set_autostart(False, startup_dir=startup_dir)
    assert not cmd.exists()


def test_overlay_meter_formulas() -> None:
    h_norm = overlay.compute_meter_heights("nahravam", 0.5, 1.0, reduced_motion=False)
    assert len(h_norm) == 9
    assert all(3.0 <= h <= 18.0 for h in h_norm)

    h_red = overlay.compute_meter_heights("prepisuji", 0.0, 1.0, reduced_motion=True)
    assert h_red == [0.0, 0.0, 0.0, 6.0, 6.0, 6.0, 0.0, 0.0, 0.0]




def test_config_store_preserves_broken_json_on_save(tmp_path: Path) -> None:
    from mluvka import config

    store = config.ConfigStore(tmp_path)
    store.path.write_text("{broken json", encoding="utf-8")
    assert store.reload_if_changed() is False
    with pytest.raises(config.ConfigInvalidError):
        store.set_value("zvuky", False)
    assert store.cfg["zvuky"] is True
    assert store.path.read_text(encoding="utf-8") == "{broken json"


def test_recorder_max_duration_fires_off_callback_thread(monkeypatch) -> None:
    import threading
    import time

    cb_holder = {}

    class FakeStream:
        def __init__(self, **kwargs):
            cb_holder["cb"] = kwargs["callback"]
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        def close(self):
            pass

    monkeypatch.setattr(audio.sd, "InputStream", FakeStream)
    rec = audio.Recorder()
    rec._max_samples = 480
    done = threading.Event()
    caller_tids: list[int] = []
    cb_tid: list[int] = []

    def on_max():
        caller_tids.append(threading.get_ident())
        rec.stop()
        done.set()

    rec.start(on_max_duration=on_max)
    rec._max_samples = 480
    cb_tid.append(threading.get_ident())
    cb_holder["cb"](np.ones((480, 1), dtype=np.float32), 480, None, None)
    assert done.wait(timeout=2.0)
    assert caller_tids[0] != cb_tid[0]


def test_overlay_styles_applied_before_first_deiconify() -> None:
    ov = overlay.Overlay()
    try:
        assert ov._hwnd != 0
        ex_before = int(overlay.user32.GetWindowLongPtrW(ov._hwnd, overlay.GWL_EXSTYLE))
        assert ex_before & overlay.WS_EX_NOACTIVATE
        assert ex_before & overlay.WS_EX_TOOLWINDOW
        assert ex_before & overlay.WS_EX_TRANSPARENT
    finally:
        ov.root.destroy()






def test_allowed_hosts_adds_claude_only_with_key(monkeypatch) -> None:
    from mluvka import config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.allowed_hosts({"kanal_webhook": "", "claude_api_klic": ""}) == []
    assert config.allowed_hosts({"kanal_webhook": "https://hooks.slack.com/x", "claude_api_klic": "k"}) == [
        "hooks.slack.com",
        "api.anthropic.com",
    ]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert config.allowed_hosts({}) == ["api.anthropic.com"]


def test_claude_ai_without_key_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.is_available(tmp_path) is False
    assert llm.ClaudeAI(tmp_path).upravit_text("Ahoj světe") == "Ahoj světe"  # bez klíče vrátí původní text


def test_slovnik_pro_ai_collects_words_and_correction_targets() -> None:
    cfg = {"slovnik": {"slova": ["Claude Code", "Supabase", "Claude Code"], "opravy": [["klód", "Claude"], ["bad"]]}}
    assert llm.slovnik_pro_ai(cfg) == "Claude Code, Supabase, Claude"
    assert llm.slovnik_pro_ai({}) == ""
