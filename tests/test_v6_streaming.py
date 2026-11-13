"""Testy pro Task v6 — rychlejší diktování (timing log, streaming ASR, warm Claude)."""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic
import httpx2
import numpy as np
import pytest

from mluvka import app as app_mod
from mluvka import asr, audio, config, llm


def _cleanup_mluvka_loggers() -> None:
    root = logging.getLogger("mluvka")
    for h in list(root.handlers):
        h.flush()
        root.removeHandler(h)
        h.close()


def _make_speech_with_pauses(total_s: float, pauses_s: list[tuple[float, float]], sr: int = 16000) -> np.ndarray:
    t = np.arange(int(total_s * sr), dtype=np.float32) / float(sr)
    sig = (0.20 * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32)
    for start_s, end_s in pauses_s:
        sig[int(start_s * sr) : int(end_s * sr)] = 0.0
    return sig


def test_a_timing_log_fields_dictation_and_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app_mod.setup_logging(tmp_path)
    try:
        store = config.ConfigStore(tmp_path)
        store.update_values({"ai_uprava": False, "historie": False, "kanal_zapnuto": False})

        fake_overlay = MagicMock()
        fake_tray = MagicMock()
        fake_whisper = MagicMock()
        fake_whisper.transcribe.return_value = "Tajný diktát bez AI."

        with (
            patch("mluvka.overlay.Overlay", return_value=fake_overlay),
            patch("mluvka.tray.TrayController", return_value=fake_tray),
            patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
            patch("mluvka.output.paste_text", return_value=True),
        ):
            inst = app_mod.MluvkaApp(store, data=tmp_path)
            inst._model_loaded = True
            inst._whisper_ready.set()

            loud = np.full(16000, 0.2, dtype=np.float32)
            # 1) Diktát bez AI -> ai_s=0.00
            inst._handle_transcription_job(
                "dictation",
                (loud, store.snapshot(), "Code.exe", "", None, time.monotonic() - 0.05, None),
            )

            # 2) Diktát se zapnutou AI -> ai_s > 0
            store.update_values({"ai_uprava": True, "claude_api_klic": "sk-ant-test"})
            fake_whisper.transcribe.return_value = "Tajný diktát s AI."

            def slow_cleanup(txt: str, cfg=None, priklady=None) -> str:
                time.sleep(0.05)
                return "Tajný výsledek z AI."

            with patch.object(inst._ai, "upravit_text", side_effect=slow_cleanup):
                inst._handle_transcription_job(
                    "dictation",
                    (loud, store.snapshot(), "Code.exe", "", None, time.monotonic() - 0.05, None),
                )

            # 3) Příkazový režim -> "Příkaz dokončen" se všemi poli
            fake_whisper.transcribe.return_value = "Tajný hlasový příkaz."

            def slow_cmd(pokyn: str, sel: str = "") -> str:
                time.sleep(0.05)
                return "Tajný výsledek příkazu."

            with patch.object(inst._ai, "proved_prikaz", side_effect=slow_cmd):
                inst._handle_transcription_job(
                    "command",
                    (loud, store.snapshot(), "Code.exe", "vybraný text", "schránka", time.monotonic() - 0.05, None),
                )
    finally:
        _cleanup_mluvka_loggers()

    log_txt = config.log_file_path(tmp_path).read_text(encoding="utf-8")
    for secret in ("Tajný", "diktát", "výsledek", "příkaz", "vybraný", "schránka"):
        assert secret not in log_txt

    dict_lines = [line for line in log_txt.splitlines() if "Přepis dokončen:" in line]
    assert len(dict_lines) == 2
    pat = re.compile(
        r"audio_s=\d+\.\d{2} prepis_s=\d+\.\d{2} ai_s=(\d+\.\d{2}) celkem_s=(\d+\.\d{2}) chars=(\d+)"
    )
    m_off = pat.search(dict_lines[0])
    assert m_off is not None
    assert float(m_off.group(1)) == 0.0
    assert float(m_off.group(2)) >= 0.04
    assert int(m_off.group(3)) == len("Tajný diktát bez AI.")

    m_on = pat.search(dict_lines[1])
    assert m_on is not None
    assert float(m_on.group(1)) >= 0.04
    assert float(m_on.group(2)) >= 0.08
    assert int(m_on.group(3)) == len("Tajný výsledek z AI.")

    cmd_lines = [line for line in log_txt.splitlines() if "Příkaz dokončen:" in line]
    assert len(cmd_lines) == 1
    m_cmd = pat.search(cmd_lines[0])
    assert m_cmd is not None
    assert float(m_cmd.group(1)) >= 0.04
    assert float(m_cmd.group(2)) >= 0.08
    assert int(m_cmd.group(3)) == len("Tajný výsledek příkazu.")


def test_b_recorder_snapshot_incremental_resampling_and_session_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cb_holder: dict[str, object] = {}

    class FakeStream:
        def __init__(self, **kwargs):
            cb_holder["cb"] = kwargs["callback"]
            cb_holder["sr"] = kwargs["samplerate"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(audio.sd, "InputStream", FakeStream)
    rec = audio.Recorder()
    rec.start()
    sid1 = rec.session_id
    cb = cb_holder["cb"]

    b1 = np.full((480, 1), 0.1, dtype=np.float32)
    b2 = np.full((480, 1), 0.2, dtype=np.float32)
    b3 = np.full((480, 1), 0.3, dtype=np.float32)
    cb(b1, 480, None, None)
    cb(b2, 480, None, None)

    s_all = rec.snapshot(0, session_id=sid1)
    assert len(s_all) == 960
    assert np.allclose(s_all[:480], 0.1)
    assert np.allclose(s_all[480:], 0.2)
    # Změna vráceného snímku nesmí změnit interní buffer rekordéru.
    s_all[:] = -9.0

    cb(b3, 480, None, None)
    s_tail = rec.snapshot(600, session_id=sid1)
    assert len(s_tail) == 840
    assert np.allclose(s_tail[:360], 0.2)
    assert np.allclose(s_tail[360:], 0.3)
    assert len(rec.snapshot(9999, session_id=sid1)) == 0

    full = rec.stop()
    assert len(full) == 1440
    assert np.allclose(full[:480], 0.1)
    assert np.allclose(full[480:960], 0.2)
    assert np.allclose(full[960:], 0.3)

    # Nová nahrávka nesmí být vidět přes staré session_id
    rec.start()
    sid2 = rec.session_id
    assert sid2 != sid1
    cb2 = cb_holder["cb"]
    cb2(np.full((480, 1), 0.5, dtype=np.float32), 480, None, None)
    assert len(rec.snapshot(0, session_id=sid1)) == 0
    assert len(rec.snapshot(0, session_id=sid2)) == 480
    rec.cancel()

    # Záložní cesta při jiném vzorkovacím kmitočtu (48 kHz -> 16 kHz)
    def fake_stream_48k(**kwargs):
        if kwargs["samplerate"] == 16000:
            raise RuntimeError("16k nepodporováno")
        return FakeStream(**kwargs)

    monkeypatch.setattr(audio.sd, "InputStream", fake_stream_48k)
    monkeypatch.setattr(audio.sd, "query_devices", lambda *a, **k: {"default_samplerate": 48000})
    rec.start()
    cb48 = cb_holder["cb"]
    cb48(np.full((4800, 1), 0.25, dtype=np.float32), 4800, None, None)
    snap_16k = rec.snapshot(800)
    assert len(snap_16k) == 800
    assert np.allclose(snap_16k, 0.25, atol=1e-4)
    rec.cancel()


def test_b_find_cut_point_and_streaming_equivalence_with_split_chunks(tmp_path: Path) -> None:
    sr = asr.SAMPLE_RATE
    # 92 s se zřetelnými tichými mezerami v [18, 28] s blocích
    sig = _make_speech_with_pauses(
        92.0,
        [(21.5, 22.2), (43.0, 43.8), (67.2, 67.9), (87.0, 87.5)],
        sr=sr,
    )
    expected_chunks = asr.split_chunks(sig, sr)
    assert len(expected_chunks) >= 4
    expected_sizes = [len(c) for c in expected_chunks]
    assert sum(expected_sizes) == len(sig)

    # Přímý test refaktorovaného find_cut_point proti split_chunks
    pos = 0
    cut_sizes: list[int] = []
    while pos < len(sig):
        if len(sig) - pos <= asr.MAX_CHUNK_SAMPLES:
            cut_sizes.append(len(sig) - pos)
            break
        c = asr.find_cut_point(sig[pos:], sr)
        cut_sizes.append(c)
        pos += c
    assert cut_sizes == expected_sizes

    # Dávkový výsledek pro každý očekávaný chunk přes stejnou per-chunk cestu
    cfg = {"slovnik_do_promptu": True, "slovnik": {"slova": ["Mluvka"]}, "beam_size": 5}
    srv_batch = asr.WhisperServer(tmp_path)
    batch_inputs: list[np.ndarray] = []

    def fake_infer_batch(chunk: np.ndarray, prompt: str, *, beam_size: int = 1) -> str:
        batch_inputs.append(chunk.copy())
        return f"blok-{len(batch_inputs)}"

    with patch.object(srv_batch, "_infer_chunk", side_effect=fake_infer_batch):
        batch_texts = [srv_batch.transcribe(ch, cfg=cfg) for ch in expected_chunks]
        expected_out = " ".join(t for t in batch_texts if t)

    # Průběžný přepis po 0.5 s krocích
    srv_stream = asr.WhisperServer(tmp_path)
    stream_inputs: list[np.ndarray] = []
    captured_len = 0
    lock = threading.Lock()

    def get_audio(start_idx: int) -> np.ndarray:
        with lock:
            end = captured_len
        if start_idx >= end:
            return np.zeros(0, dtype=np.float32)
        return sig[start_idx:end].copy()

    def fake_infer_stream(chunk: np.ndarray, prompt: str, *, beam_size: int = 1) -> str:
        assert prompt == "Mluvka"
        assert beam_size == 5
        stream_inputs.append(chunk.copy())
        if len(stream_inputs) == 2:
            return "Děkuji za pozornost."  # halucinace -> odfiltrovat
        return f"blok-{len(stream_inputs)}"

    with patch.object(srv_stream, "_infer_chunk", side_effect=fake_infer_stream):
        streamer = asr.StreamingTranscriber(srv_stream, get_audio, cfg=cfg, poll_interval_s=0.01)
        streamer.start()
        step = int(0.5 * sr)
        for end in range(step, len(sig) + step, step):
            with lock:
                captured_len = min(len(sig), end)
            time.sleep(0.012)
        streamer.stop_polling()
        stream_out = streamer.finish(sig, cfg=cfg)

    assert streamer.chunk_sizes == expected_sizes
    assert len(stream_inputs) == len(batch_inputs)
    for b_in, s_in in zip(batch_inputs, stream_inputs):
        assert np.array_equal(b_in, s_in)

    # Ověření filtrace halucinací stejně jako v transcribe
    assert "Děkuji za pozornost" not in stream_out
    assert stream_out == " ".join(
        t for i, t in enumerate(batch_texts, start=1) if i != 2
    )
    assert len(expected_out) > 0


def test_b_streaming_short_audio_transcribed_only_at_release(tmp_path: Path) -> None:
    sr = asr.SAMPLE_RATE
    short = _make_speech_with_pauses(12.0, [], sr=sr)
    srv = asr.WhisperServer(tmp_path)
    captured_len = 0
    lock = threading.Lock()

    def get_audio(start_idx: int) -> np.ndarray:
        with lock:
            end = captured_len
        return short[start_idx:end].copy()

    with patch.object(srv, "transcribe", return_value="Krátká věta.") as mock_tx:
        streamer = asr.StreamingTranscriber(srv, get_audio, cfg={}, poll_interval_s=0.01)
        streamer.start()
        for end in range(4000, len(short) + 4000, 4000):
            with lock:
                captured_len = min(len(short), end)
            time.sleep(0.01)
        # Během nahrávání kratšího než 28 s se nesmí přepisovat nic.
        mock_tx.assert_not_called()
        streamer.stop_polling()
        out = streamer.finish(short, cfg={})

    assert out == "Krátká věta."
    mock_tx.assert_called_once()
    passed_audio = mock_tx.call_args.args[0]
    assert np.array_equal(passed_audio, short)


def test_b_streaming_cancel_discards_and_stops_promptly(tmp_path: Path) -> None:
    sr = asr.SAMPLE_RATE
    # 1) Zrušení během spánku mezi poll cykly (5 s interval musí skončit okamžitě)
    srv1 = asr.WhisperServer(tmp_path)
    st1 = asr.StreamingTranscriber(
        srv1,
        lambda _idx: np.zeros(0, dtype=np.float32),
        poll_interval_s=5.0,
    )
    st1.start()
    time.sleep(0.03)
    t0 = time.monotonic()
    st1.cancel()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5
    assert st1.is_running is False
    assert st1.cancelled is True
    assert st1.texts == []

    # 2) Zrušení uprostřed rozpracovaného chunku po úspěšném prvním chunku
    long_sig = _make_speech_with_pauses(62.0, [(21.0, 21.5), (44.0, 44.5)], sr=sr)
    srv2 = asr.WhisperServer(tmp_path)
    entered_second = threading.Event()
    unblock_second = threading.Event()
    calls = {"n": 0}

    def fake_transcribe(chunk: np.ndarray, **kwargs) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "První část."
        entered_second.set()
        unblock_second.wait(timeout=2.0)
        return "Druhá část, která se má zahodit."

    with patch.object(srv2, "transcribe", side_effect=fake_transcribe):
        st2 = asr.StreamingTranscriber(
            srv2,
            lambda idx: long_sig[idx:].copy(),
            poll_interval_s=0.01,
        )
        st2.start()
        assert entered_second.wait(timeout=2.0)
        st2.cancel(wait=False)
        unblock_second.set()
        st2.cancel(wait=True)

    assert st2.is_running is False
    assert st2.texts == []
    assert st2.finish(long_sig) == ""


def test_b_streaming_error_discards_and_app_restarts_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    long_sig = _make_speech_with_pauses(60.0, [(20.0, 20.5)], sr=asr.SAMPLE_RATE)
    srv = asr.WhisperServer(tmp_path)
    calls = {"n": 0}

    def fail_on_second(chunk: np.ndarray, **kwargs) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "Část před chybou."
        raise RuntimeError("GPU reset")

    with patch.object(srv, "transcribe", side_effect=fail_on_second):
        st = asr.StreamingTranscriber(
            srv,
            lambda idx: long_sig[idx:].copy(),
            poll_interval_s=0.01,
        )
        st.start()
        deadline = time.monotonic() + 2.0
        while st.is_running and time.monotonic() < deadline:
            time.sleep(0.01)

    assert st.is_running is False
    assert st.texts == []
    with pytest.raises(RuntimeError, match="GPU reset"):
        st.finish(long_sig)

    # Aplikace musí po chybě restartovat whisper-server a přepsat celý záznam znovu
    store = config.ConfigStore(tmp_path)
    store.update_values({"ai_uprava": False, "historie": False, "kanal_zapnuto": False})
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.transcribe.return_value = "Obnovený přepis po restartu."

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
        patch("mluvka.output.paste_text", return_value=True) as mock_paste,
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._model_loaded = True
        inst._whisper_ready.set()
        inst._handle_transcription_job(
            "dictation",
            (long_sig, store.snapshot(), "Code.exe", "", None, time.monotonic(), st),
        )

    fake_whisper.close.assert_called_once()
    fake_whisper.start.assert_called_once()
    mock_paste.assert_called_once_with("Obnovený přepis po restartu.", append_space=True)


def test_b_app_recording_cancel_and_new_recording_do_not_mix_texts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cb_holder: dict[str, object] = {}

    class FakeStream:
        def __init__(self, **kwargs):
            cb_holder["cb"] = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(audio.sd, "InputStream", FakeStream)
    monkeypatch.setattr(audio, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.winproc, "foreground_process_name", lambda: "Code.exe")

    store = config.ConfigStore(tmp_path)
    store.update_values({"ai_uprava": False, "historie": False, "kanal_zapnuto": False})
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    first_chunk_done = threading.Event()

    def fake_transcribe(chunk: np.ndarray, **kwargs) -> str:
        val = float(np.mean(np.abs(chunk)))
        if val < 0.25:
            first_chunk_done.set()
            return "Starý zrušený text."
        return "Nový čistý diktát."

    fake_whisper.transcribe.side_effect = fake_transcribe

    orig_streamer = asr.StreamingTranscriber

    def fast_streamer(*args, **kwargs):
        kwargs.setdefault("poll_interval_s", 0.01)
        return orig_streamer(*args, **kwargs)

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
        patch("mluvka.asr.StreamingTranscriber", side_effect=fast_streamer),
        patch("mluvka.output.paste_text", return_value=True) as mock_paste,
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._model_loaded = True
        inst._whisper_ready.set()

        # Nahrávka 1 (> 28 s) -> chunk 0 se přepíše -> Esc zruší
        inst._start_recording("hold")
        assert inst._streamer is not None
        cb1 = cb_holder["cb"]
        cb1(np.full((29 * asr.SAMPLE_RATE, 1), 0.15, dtype=np.float32), 29 * asr.SAMPLE_RATE, None, None)
        assert first_chunk_done.wait(timeout=2.0)
        inst._cancel_recording()
        assert inst._work_queue.empty()

        # Nahrávka 2 (krátká) -> nesmí obsahovat nic ze zrušené Nahrávky 1
        inst._start_recording("hold")
        cb2 = cb_holder["cb"]
        cb2(np.full((asr.SAMPLE_RATE, 1), 0.40, dtype=np.float32), asr.SAMPLE_RATE, None, None)
        inst._stop_recording(is_command=False)
        inst._work_queue.put(None)
        inst._worker_loop()

    mock_paste.assert_called_once_with("Nový čistý diktát.", append_space=True)


def test_b_app_max_duration_triggers_stop_and_finishes_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cb_holder: dict[str, object] = {}

    class FakeStream:
        def __init__(self, **kwargs):
            cb_holder["cb"] = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(audio.sd, "InputStream", FakeStream)
    monkeypatch.setattr(audio, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.winproc, "foreground_process_name", lambda: "Code.exe")

    store = config.ConfigStore(tmp_path)
    store.update_values({"ai_uprava": False, "historie": False, "kanal_zapnuto": False})
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.transcribe.return_value = "Dosažen limit."

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
        patch("mluvka.output.paste_text", return_value=True) as mock_paste,
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._hook = MagicMock()
        inst._model_loaded = True
        inst._whisper_ready.set()

        inst._start_recording("hold")
        inst._recorder._max_samples = 480
        cb_holder["cb"](np.full((480, 1), 0.2, dtype=np.float32), 480, None, None)

        deadline = time.monotonic() + 2.0
        while inst._work_queue.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not inst._work_queue.empty()
        inst._hook.cancel_active.assert_called_once()

        inst._work_queue.put(None)
        inst._worker_loop()

    mock_paste.assert_called_once_with("Dosažen limit.", append_space=True)


def test_c_claude_client_uses_default_httpx_client_with_60s_keepalive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = config.ConfigStore(tmp_path)
    store.set_value("claude_api_klic", "sk-ant-keepalive-test")

    ai = llm.ClaudeAI(tmp_path)
    client = ai._get_client()
    try:
        assert isinstance(client._client, anthropic.DefaultHttpxClient)
        assert isinstance(client._client, httpx2.Client)
        pool = client._client._transport._pool
        assert pool._max_connections == 10
        assert pool._max_keepalive_connections == 5
        assert pool._keepalive_expiry == 60
        assert client._client.is_closed is False
        http_ref = client._client
    finally:
        ai.close()
    assert http_ref.is_closed is True


def test_c_warmup_skipped_without_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config.ConfigStore(tmp_path)
    ai = llm.ClaudeAI(tmp_path)
    with patch.object(ai, "_get_client") as mock_get:
        assert ai.warm_up() is False
        assert ai.warm_up_async() is False
        mock_get.assert_not_called()


def test_c_warmup_throttled_20s_and_ignores_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = config.ConfigStore(tmp_path)
    store.set_value("claude_api_klic", "sk-ant-warmup")

    now = {"t": 5.0}
    monkeypatch.setattr(llm.time, "monotonic", lambda: now["t"])

    ai = llm.ClaudeAI(tmp_path)
    fake_client = MagicMock()
    fake_opt_client = MagicMock()
    fake_client.with_options.return_value = fake_opt_client

    with patch.object(ai, "_get_client", return_value=fake_client):
        # První volání i při t=5.0 (< 20 s od startu) musí projít
        assert ai.warm_up(min_interval_s=20.0) is True
        fake_client.with_options.assert_called_once_with(timeout=5.0, max_retries=0)
        fake_opt_client.models.list.assert_called_once_with(limit=1)

        # Do 20 s se požadavek neopakuje
        now["t"] = 24.9
        assert ai.warm_up(min_interval_s=20.0) is False
        assert fake_opt_client.models.list.call_count == 1

        # Po 20 s se spojení znovu zahřeje
        now["t"] = 25.0
        assert ai.warm_up(min_interval_s=20.0) is True
        assert fake_opt_client.models.list.call_count == 2

        # Chyba při zahřívání se potlačí a zaloguje jen na DEBUG bez uživatelských dat
        now["t"] = 50.0
        fake_opt_client.models.list.side_effect = RuntimeError("offline")
        with caplog.at_level(logging.DEBUG, logger="mluvka.llm"):
            assert ai.warm_up(min_interval_s=20.0) is False
        assert any(
            rec.levelno == logging.DEBUG and "Zahřátí Claude spojení selhalo" in rec.message
            for rec in caplog.records
        )


def test_c_app_preload_after_whisper_ready_and_warmup_on_dictation_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(audio, "play_sound", lambda *a, **k: None)

    store = config.ConfigStore(tmp_path)
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.port = 18080

    # 1) Bez klíče se preload ani warm-up nespustí
    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
    ):
        inst_nokey = app_mod.MluvkaApp(store, data=tmp_path)
        with (
            patch.object(inst_nokey._ai, "_get_client") as mock_get,
            patch.object(inst_nokey._ai, "warm_up_async") as mock_warm,
        ):
            inst_nokey._load_whisper_bg()
            time.sleep(0.05)
            mock_get.assert_not_called()

            with inst_nokey._state_lock:
                inst_nokey._recording = True
                inst_nokey._recording_mode = "hold"
            inst_nokey._stop_recording(is_command=False)
            mock_warm.assert_not_called()

    # 2) S klíčem a ai_uprava=True: preload po whisper ready + warm-up při puštění diktátu
    store.update_values({"ai_uprava": True, "claude_api_klic": "sk-ant-key"})
    assert "api.anthropic.com" in config.allowed_hosts(store.snapshot())
    assert "api.anthropic.com" not in config.allowed_hosts({"claude_api_klic": ""})

    preload_called = threading.Event()

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
    ):
        inst_key = app_mod.MluvkaApp(store, data=tmp_path)
        with (
            patch.object(inst_key._ai, "_get_client", side_effect=lambda: preload_called.set()) as mock_get,
            patch.object(inst_key._ai, "warm_up_async") as mock_warm,
        ):
            inst_key._load_whisper_bg()
            assert preload_called.wait(timeout=2.0)
            mock_get.assert_called_once()

            # Diktát se zapnutou AI -> spustí warm_up_async
            with inst_key._state_lock:
                inst_key._recording = True
                inst_key._recording_mode = "hold"
            inst_key._stop_recording(is_command=False)
            mock_warm.assert_called_once()

            # Příkazový režim -> warm_up_async podle specifikace nespouští
            mock_warm.reset_mock()
            with inst_key._state_lock:
                inst_key._recording = True
                inst_key._recording_mode = "command"
            inst_key._stop_recording(is_command=True)
            mock_warm.assert_not_called()

            # Diktát s vypnutou AI -> nespouští preload ani warm_up_async
            store.update_values({"ai_uprava": False})
            mock_get.reset_mock()
            inst_key._load_whisper_bg()
            time.sleep(0.05)
            mock_get.assert_not_called()

            with inst_key._state_lock:
                inst_key._recording = True
                inst_key._recording_mode = "hold"
            inst_key._stop_recording(is_command=False)
            mock_warm.assert_not_called()


def test_b_app_command_mode_uses_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cb_holder: dict[str, object] = {}

    class FakeStream:
        def __init__(self, **kwargs):
            cb_holder["cb"] = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(audio.sd, "InputStream", FakeStream)
    monkeypatch.setattr(audio, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.winproc, "foreground_process_name", lambda: "Code.exe")
    monkeypatch.setattr(app_mod.output, "grab_selection", lambda: ("vybráno", "původní"))

    store = config.ConfigStore(tmp_path)
    store.update_values({"claude_api_klic": "sk-ant-cmd", "historie": False, "kanal_zapnuto": False})
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    chunk_calls: list[int] = []
    first_chunk_done = threading.Event()

    def fake_transcribe(chunk: np.ndarray, **kwargs) -> str:
        chunk_calls.append(len(chunk))
        if len(chunk_calls) == 1:
            first_chunk_done.set()
            return "přepiš"
        return "do odrážek"

    fake_whisper.transcribe.side_effect = fake_transcribe

    orig_streamer = asr.StreamingTranscriber

    def fast_streamer(*args, **kwargs):
        kwargs.setdefault("poll_interval_s", 0.01)
        return orig_streamer(*args, **kwargs)

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
        patch("mluvka.asr.StreamingTranscriber", side_effect=fast_streamer),
        patch("mluvka.output.paste_text", return_value=True) as mock_paste,
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._model_loaded = True
        inst._whisper_ready.set()

        inst._start_recording("command")
        assert inst._streamer is not None
        cb = cb_holder["cb"]
        cb(np.full((30 * asr.SAMPLE_RATE, 1), 0.2, dtype=np.float32), 30 * asr.SAMPLE_RATE, None, None)
        assert first_chunk_done.wait(timeout=2.0)

        with patch.object(inst._ai, "proved_prikaz", return_value="- výsledek") as mock_cmd:
            inst._stop_recording(is_command=True)
            inst._work_queue.put(None)
            inst._worker_loop()

    assert len(chunk_calls) == 2
    mock_cmd.assert_called_once_with("přepiš do odrážek", "vybráno")
    mock_paste.assert_called_once_with("- výsledek", append_space=False, prev_text="původní")
def test_b_streaming_finish_before_start_prevents_orphan_thread() -> None:
    audio_short = _make_speech_with_pauses(2.0, [])
    fake_server = MagicMock()
    fake_server.transcribe.return_value = "krátký"

    streamer = asr.StreamingTranscriber(fake_server, lambda off: audio_short[off:])
    assert streamer.finish(audio_short) == "krátký"
    streamer.start()
    assert not streamer.is_running


def test_b_streaming_resolves_reloaded_whisper_server() -> None:
    audio_1s = _make_speech_with_pauses(1.0, [])
    server_old = MagicMock()
    server_old.transcribe.side_effect = AssertionError("Stará instance nesmí být po reloadu volána")
    server_new = MagicMock()
    server_new.transcribe.return_value = "po reloadu"
    holder = {"s": server_old}

    streamer = asr.StreamingTranscriber(None, lambda off: audio_1s[off:], get_server=lambda: holder["s"])
    holder["s"] = server_new
    assert streamer.finish(audio_1s) == "po reloadu"
    server_new.transcribe.assert_called_once()


def test_c_warmup_key_change_bypasses_previous_throttle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config.ConfigStore(tmp_path).update_values({"claude_api_klic": "sk-ant-key-1"})

    list_calls: list[str] = []

    def make_client(*args, **kwargs):
        k = kwargs["api_key"]
        client = MagicMock()
        opts = MagicMock()
        opts.models.list.side_effect = lambda **kw: list_calls.append(k) or MagicMock()
        client.with_options.return_value = opts
        return client

    with patch("anthropic.Anthropic", side_effect=make_client):
        ai = llm.ClaudeAI(data=tmp_path)
        assert ai.warm_up(min_interval_s=20.0) is True
        assert ai.warm_up(min_interval_s=20.0) is False

        config.ConfigStore(tmp_path).update_values({"claude_api_klic": "sk-ant-key-2"})
        assert ai.warm_up(min_interval_s=20.0) is True
        assert ai.warm_up(min_interval_s=20.0) is False

    assert list_calls == ["sk-ant-key-1", "sk-ant-key-2"]


def test_b_app_stale_max_duration_does_not_stop_new_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    max_cbs: list[object] = []

    class FakeRecorder:
        session_id = 1

        def start(self, **kwargs):
            max_cbs.append(kwargs["on_max_duration"])

        def snapshot(self, start_index=0, *, session_id=None):
            return np.zeros(0, dtype=np.float32)

        def stop(self):
            return np.full(asr.SAMPLE_RATE, 0.25, dtype=np.float32)

        def cancel(self):
            pass

    monkeypatch.setattr(audio, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.winproc, "foreground_process_name", lambda: "Code.exe")

    store = config.ConfigStore(tmp_path)
    store.update_values({"ai_uprava": False, "historie": False, "kanal_zapnuto": False})
    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.transcribe.return_value = "druhá nahrávka"

    with (
        patch("mluvka.overlay.Overlay", return_value=fake_overlay),
        patch("mluvka.tray.TrayController", return_value=fake_tray),
        patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
        patch("mluvka.audio.Recorder", return_value=FakeRecorder()),
        patch("mluvka.output.paste_text", return_value=True) as mock_paste,
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._model_loaded = True
        inst._whisper_ready.set()

        inst._start_recording("hold")
        stale_max_cb = max_cbs[0]
        inst._cancel_recording()

        inst._start_recording("hold")
        # Zpožděný max-duration callback z první nahrávky nesmí zastavit druhou
        stale_max_cb()
        assert inst._recording is True

        inst._stop_recording(is_command=False)
        inst._work_queue.put(None)
        inst._worker_loop()

    mock_paste.assert_called_once_with("druhá nahrávka", append_space=True)


def test_kratky_prompt_caps_long_dictionary() -> None:
    from mluvka import asr
    slova = [f"Slovo{i}" for i in range(80)]
    p = asr.kratky_prompt(slova)
    assert 0 < len(p) <= asr.PROMPT_MAX_ZNAKU
    assert p.startswith("Slovo0, Slovo1")
    assert asr.kratky_prompt(["Claude", "Supabase"]) == "Claude, Supabase"
    assert asr.kratky_prompt([]) == ""
