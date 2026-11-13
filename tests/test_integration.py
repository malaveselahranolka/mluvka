"""Integrační test se skutečným whisper-server.exe a FLEURS klipy."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bench import levenshtein_words, load_fleurs, load_wav_16k_f32, normalise_for_wer
from mluvka import asr, config


def _real_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "Mluvka"
    return Path.home() / "AppData" / "Local" / "Mluvka"


def _is_pid_running(pid: int) -> bool:
    res = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return str(pid) in res.stdout


def test_whisper_server_fleurs_5_clips_and_cleanup() -> None:
    real_data = _real_data_dir()
    exe = config.whisper_exe_path(real_data)
    model = config.whisper_model_path(real_data)
    refs_tsv = real_data / "bench" / "fleurs" / "refs.tsv"

    if not exe.is_file():
        pytest.skip(f"Chybí whisper-server.exe: {exe}")
    if not model.is_file() or model.stat().st_size < 500_000_000:
        pytest.skip(f"Chybí nebo je nekompletní model: {model}")
    if not refs_tsv.is_file():
        pytest.skip(f"Chybí FLEURS klipy: {refs_tsv}")

    clips = load_fleurs(real_data)[:5]
    srv = asr.WhisperServer(real_data)
    pid: int | None = None
    try:
        srv.start(timeout_s=60.0)
        pid = srv.pid
        assert pid is not None

        total_edits = 0
        total_words = 0
        for _fname, wav_path, ref in clips:
            arr = load_wav_16k_f32(wav_path)
            hyp = srv.transcribe(arr, cfg=config.DEFAULT_CONFIG)
            ref_w = normalise_for_wer(ref)
            hyp_w = normalise_for_wer(hyp)
            total_edits += levenshtein_words(ref_w, hyp_w)
            total_words += len(ref_w)

        wer = (total_edits / total_words) * 100.0
        assert wer <= 12.0, f"Průměrný WER {wer:.2f} % > 12 %"
    finally:
        srv.close()

    if pid is not None:
        assert not _is_pid_running(pid)
