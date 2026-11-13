"""Jednotkové testy čistých funkcí a logiky přepisu v mluvka.asr."""

from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from mluvka import asr


def test_calc_audio_ctx_formula() -> None:
    assert asr.calc_audio_ctx(0) == 32
    assert asr.calc_audio_ctx(16000) == 96
    # ceil(160000/320) + 32 = 532 -> zaokrouhleno nahoru na násobek 16 = 544
    assert asr.calc_audio_ctx(160000) == 544
    assert asr.calc_audio_ctx(480000) == 1500
    assert asr.calc_audio_ctx(900000) == 1500
    for n in (1, 320, 321, 15999, 16000, 64000, 300000):
        ctx = asr.calc_audio_ctx(n)
        assert ctx <= 1500
        assert ctx == 1500 or ctx % 16 == 0


def test_peak_block_rms_dbfs() -> None:
    silent = np.zeros(16000, dtype=np.float32)
    assert asr.peak_block_rms_dbfs(silent) < -55.0

    very_quiet = np.full(16000, 1e-4, dtype=np.float32)
    assert asr.peak_block_rms_dbfs(very_quiet) < -55.0

    loud = np.full(16000, 0.1, dtype=np.float32)
    assert asr.peak_block_rms_dbfs(loud) > -55.0


def test_trim_silence_keeps_200ms_padding() -> None:
    sr = 16000
    sig = np.zeros(3 * sr, dtype=np.float32)
    sig[sr : 2 * sr] = 0.2
    trimmed = asr.trim_silence(sig, sr, pad_ms=200)
    # 1 s řeči + 200 ms před + 200 ms za ≈ 1.4 s (<= 1.5 s)
    assert 1.35 * sr <= len(trimmed) <= 1.50 * sr


def test_split_chunks_short_and_long_with_silent_gap() -> None:
    sr = 16000
    short = np.ones(20 * sr, dtype=np.float32) * 0.1
    assert len(asr.split_chunks(short, sr)) == 1

    # 65 s signál se zřetelnými tichými mezerami na 22.5 s a 45.0 s
    total = 65 * sr
    sig = np.ones(total, dtype=np.float32) * 0.25
    gap1_s = int(22.2 * sr)
    gap1_e = int(22.9 * sr)
    gap2_s = int(44.6 * sr)
    gap2_e = int(45.3 * sr)
    sig[gap1_s:gap1_e] = 0.0
    sig[gap2_s:gap2_e] = 0.0

    chunks = asr.split_chunks(sig, sr)
    assert len(chunks) >= 3
    for ch in chunks:
        assert len(ch) <= 28 * sr

    # První řez musí padnout do první tiché mezery [22.2s, 22.9s]
    cut1 = len(chunks[0])
    assert gap1_s <= cut1 <= gap1_e

    # Druhý řez musí padnout do druhé tiché mezery
    cut2 = len(chunks[0]) + len(chunks[1])
    assert gap2_s <= cut2 <= gap2_e

    # Spojení musí přesně odpovídat vstupu bez ztráty či duplikace vzorků
    rejoined = np.concatenate(chunks)
    assert np.array_equal(rejoined, sig)


def test_hallucination_filter_exact_only() -> None:
    for h in (
        "Titulky vytvořil JohnyX.",
        "děkuji za pozornost!",
        "Díky za pozornost",
        "Titulky.",
        "KONEC",
        "www.hradeckesluzby.cz",
        "www.hradeckesluzby.cz.",
    ):
        assert asr.is_hallucination(h) is True

    assert asr.is_hallucination("To je konec filmu.") is False
    assert asr.is_hallucination("Zapni titulky prosím.") is False


def test_transcribe_skips_silent_audio_and_joins_chunks(tmp_path: Path) -> None:
    srv = asr.WhisperServer(tmp_path)
    with patch.object(srv, "_infer_chunk") as mock_infer:
        assert srv.transcribe(np.full(16000, 1e-4, dtype=np.float32)) == ""
        mock_infer.assert_not_called()

        mock_infer.side_effect = ["Dobrý den.", "Titulky vytvořil JohnyX.", "Jak se máte?"]
        long_audio = np.full(60 * 16000, 0.2, dtype=np.float32)
        cfg = {"slovnik_do_promptu": True, "slovnik": {"slova": ["Claude", "Obsidian"]}}
        out = srv.transcribe(long_audio, cfg=cfg)
        assert out == "Dobrý den. Jak se máte?"
        assert mock_infer.call_count == 3
        for call in mock_infer.call_args_list:
            assert call.kwargs["prompt"] == "Claude, Obsidian"


def test_encode_wav_and_multipart() -> None:
    wav_bytes = asr.encode_wav_pcm16(np.zeros(1600, dtype=np.float32))
    import io

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 1600

    body, ctype = asr.build_multipart_form(
        {"language": "cs"},
        ("file", "audio.wav", wav_bytes, "audio/wav"),
    )
    assert b'name="language"' in body
    assert b'filename="audio.wav"' in body
    assert "multipart/form-data; boundary=" in ctype


def test_split_chunks_unbiased_at_28s_boundary_with_noise() -> None:
    sr = 16000
    sig = np.full(35 * sr, 0.1, dtype=np.float32)
    # 0.08 má energii 0.0064 (> 0.5 * 0.1^2 = 0.005): s nulovým paddingem na okraji 28 s by argmin falešně vybral 28 s.
    pause_s = int(22.0 * sr)
    pause_e = int(22.5 * sr)
    sig[pause_s:pause_e] = 0.08
    chunks = asr.split_chunks(sig, sr)
    assert len(chunks) == 2
    assert pause_s <= len(chunks[0]) <= pause_e

def test_infer_chunk_uses_fixed_1500_audio_ctx_for_cuda_fa(tmp_path: Path) -> None:
    import json
    from unittest.mock import MagicMock, patch

    srv = asr.WhisperServer(tmp_path)
    srv._port = 12345
    resp_mock = MagicMock()
    resp_mock.read.return_value = json.dumps({"text": "Ahoj"}).encode("utf-8")
    resp_cm = MagicMock()
    resp_cm.__enter__.return_value = resp_mock
    captured = {}

    def fake_open(req, timeout=60.0):
        captured["data"] = req.data
        return resp_cm

    with patch.object(srv._opener, "open", side_effect=fake_open):
        out = srv._infer_chunk(np.zeros(16000, dtype=np.float32), prompt="")

    assert out == "Ahoj"
    assert b'name="audio_ctx"\r\n\r\n1500' in captured["data"]
