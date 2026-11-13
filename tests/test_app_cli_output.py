"""Testy pro CLI režim, soukromí logů, schránku a pořadí zpracování v MluvkaApp."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from mluvka import __main__ as cli_main
from mluvka import app as app_mod
from mluvka import config, history, okno, output


def test_cli_soubor_transcribes_wav_with_mocked_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wav_path = tmp_path / "sample.wav"
    pcm = (np.sin(np.linspace(0, 40, 16000)) * 8000).astype("<i2")
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm.tobytes())

    with (
        patch.object(cli_main.asr.WhisperServer, "start"),
        patch.object(cli_main.asr.WhisperServer, "transcribe", return_value="Ehm, dobrý den nový řádek jak se máte"),
        patch.object(cli_main.asr.WhisperServer, "close") as mock_close,
    ):
        rc = cli_main.main(["--soubor", str(wav_path)])
        assert rc == 0
        mock_close.assert_called_once()

    captured = capsys.readouterr()
    assert captured.out.strip() == "Dobrý den\nJak se máte"
    assert "RTF:" in captured.err


def test_app_dictation_privacy_no_transcript_in_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app_mod.setup_logging(tmp_path)
    store = config.ConfigStore(tmp_path)

    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.transcribe.return_value = "Tajná věta s českou diakritikou."

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
        patch("mluvka.output.paste_text", return_value=True) as mock_paste,
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._model_loaded = True
        inst._whisper_ready.set()

        loud = np.full(16000, 0.2, dtype=np.float32)
        inst._handle_transcription_job("dictation", (loud, store.cfg, "Code.exe", "", None))

        mock_paste.assert_called_once_with("Tajná věta s českou diakritikou.", append_space=True)
        fake_overlay.hide.assert_called()

        # Tiché audio -> upozornění bez volání serveru
        quiet = np.full(16000, 1e-4, dtype=np.float32)
        fake_whisper.transcribe.reset_mock()
        inst._handle_transcription_job("dictation", (quiet, store.cfg, "Code.exe", "", None))
        fake_whisper.transcribe.assert_not_called()
        fake_overlay.toast.assert_called_with(
            "Mikrofon nic nezachytil — zkontroluj vstup",
            kind="error",
        )

    for h in list(logging.getLogger("mluvka").handlers):
        h.flush()
        logging.getLogger("mluvka").removeHandler(h)
        h.close()

    log_txt = config.log_file_path(tmp_path).read_text(encoding="utf-8")
    assert "Tajná věta" not in log_txt
    assert "audio_s=" in log_txt
    assert "prepis_s=" in log_txt
    assert "ai_s=" in log_txt
    assert "celkem_s=" in log_txt
    assert "chars=32" in log_txt
    assert "rtf=" not in log_txt

    entries = history.load_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["text"] == "Tajná věta s českou diakritikou."


def test_clipboard_save_and_restore_if_unchanged() -> None:
    prev_text, _ = output.get_clipboard_text()
    try:
        seq1 = output.set_clipboard_text("Původní schránka")
        assert seq1 is not None
        got, got_seq = output.get_clipboard_text()
        assert got == "Původní schránka"

        seq2 = output.set_clipboard_text("Dočasný přepis")
        assert seq2 is not None and seq2 != seq1
        output.restore_clipboard_if_unchanged("Původní schránka", seq2)
        restored, _ = output.get_clipboard_text()
        assert restored == "Původní schránka"

        # Pokud schránku mezitím přepsal někdo jiný, nesmíme ji přepsat zpět
        seq3 = output.set_clipboard_text("Náš text")
        seq4 = output.set_clipboard_text("Cizí text")
        assert seq4 != seq3
        output.restore_clipboard_if_unchanged("Původní schránka", seq3)
        current, _ = output.get_clipboard_text()
        assert current == "Cizí text"
    finally:
        if prev_text is not None:
            output.set_clipboard_text(prev_text)


def _whisper_pids() -> set[int]:
    import csv

    res = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq whisper-server.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    pids: set[int] = set()
    for row in csv.reader(res.stdout.splitlines()):
        if len(row) >= 2 and row[0].strip().lower() == "whisper-server.exe":
            try:
                pids.add(int(row[1]))
            except ValueError:
                pass
    return pids


def test_pythonw_launch_and_stop_process_leaves_no_whisper(tmp_path: Path) -> None:
    if okno.is_app_running():
        pytest.skip("Mluvka už běží v této relaci (mutex Local\\Mluvka je obsazený).")
    # Ověří spuštění přes pythonw.exe v izolovaném MLUVKA_DATA a následné ukončení bez nového zbylého whisper-server.exe
    before_pids = _whisper_pids()
    env = dict(**__import__("os").environ)
    env["MLUVKA_DATA"] = str(tmp_path)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    proc = subprocess.Popen([exe, "-m", "mluvka"], env=env)
    try:
        deadline = time.monotonic() + 8.0
        log_file = config.log_file_path(tmp_path)
        while time.monotonic() < deadline and not log_file.exists():
            time.sleep(0.1)
        assert log_file.exists()
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
    assert (_whisper_pids() - before_pids) == set()


def test_cli_soubor_missing_file_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main.main(["--soubor", str(tmp_path / "missing.wav")])
    assert rc == 1
    assert "Chyba" in capsys.readouterr().err


def test_command_mode_restores_clipboard_on_silence_missing_llm_and_error(tmp_path: Path) -> None:
    store = config.ConfigStore(tmp_path)
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.transcribe.return_value = "převeď na odrážky"

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
        patch("mluvka.output.set_clipboard_text") as mock_set_clip,
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._model_loaded = True
        inst._whisper_ready.set()

        # 1) Ticho -> obnovit schránku
        quiet = np.full(16000, 1e-4, dtype=np.float32)
        inst._transcribing = 1
        inst._handle_transcription_job("command", (quiet, store.cfg, "Code.exe", "vybráno", "původní"))
        mock_set_clip.assert_called_with("původní")

        # 2) Chybějící LLM -> obnovit schránku
        mock_set_clip.reset_mock()
        loud = np.full(16000, 0.2, dtype=np.float32)
        inst._transcribing = 1
        with patch("mluvka.llm.is_available", return_value=False):
            inst._handle_transcription_job("command", (loud, store.cfg, "Code.exe", "vybráno", "původní"))
        mock_set_clip.assert_called_with("původní")

        # 3) Výjimka při volání LLM -> obnovit schránku
        mock_set_clip.reset_mock()
        inst._transcribing = 1
        with (
            patch("mluvka.llm.is_available", return_value=True),
            patch.object(inst._ai, "proved_prikaz", side_effect=RuntimeError("boom")),
        ):
            inst._handle_transcription_job("command", (loud, store.cfg, "Code.exe", "vybráno", "původní"))
        mock_set_clip.assert_called_with("původní")


def test_load_whisper_bg_does_not_overwrite_transcribing_toast(tmp_path: Path) -> None:
    store = config.ConfigStore(tmp_path)
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._transcribing = 1
        inst._load_whisper_bg()
        fake_overlay.toast.assert_not_called()


def test_tray_exits_nacita_after_model_load_during_transcription(tmp_path: Path) -> None:
    import json
    import os

    store = config.ConfigStore(tmp_path)
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.transcribe.return_value = "Dobrý den."

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
        patch("mluvka.output.paste_text", return_value=True),
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._transcribing = 1
        inst._load_whisper_bg()
        fake_tray.set_state.assert_called_with("pripraveno")
        fake_overlay.toast.assert_not_called()

        fake_tray.set_state.reset_mock()
        inst._whisper_ready.set()
        loud = np.full(16000, 0.2, dtype=np.float32)
        inst._handle_transcription_job("dictation", (loud, store.cfg, "Code.exe", "", None))
        assert inst._transcribing == 0
        fake_tray.set_state.assert_called_with("pripraveno")

        # Externí změna config.json se nesmí přepsat kliknutím na přepínač v tray menu
        raw = json.loads(store.path.read_text(encoding="utf-8"))
        raw["zvuky"] = False
        raw["slovnik"]["slova"] = ["Zachováno"]
        store.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        st = store.path.stat()
        os.utime(store.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

        inst._toggle_sounds()
        disk = json.loads(store.path.read_text(encoding="utf-8"))
        assert disk["zvuky"] is True
        assert disk["slovnik"]["slova"] == ["Zachováno"]
