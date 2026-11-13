"""Vstupní bod aplikace a CLI režim (--soubor)."""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

from mluvka import app, asr, audio, config, llm, netguard, text


def _read_wav_f32_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sampwidth == 2:
        arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sampwidth == 4:
        arr = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sampwidth == 1:
        arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Nepodporovaná bitová hloubka WAV: {sampwidth * 8} bitů")

    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    return audio.resample_to_16k(arr, sr)


def _transcribe_file(wav_path: Path) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    app.setup_logging()
    cfg = config.load_config()
    try:
        samples = _read_wav_f32_16k(wav_path)
    except (FileNotFoundError, wave.Error, ValueError, OSError) as exc:
        print(f"Chyba při čtení WAV souboru: {exc}", file=sys.stderr)
        return 1
    audio_s = len(samples) / float(asr.SAMPLE_RATE)

    server = asr.WhisperServer()
    ai = llm.ClaudeAI()
    try:
        try:
            server.start(timeout_s=60.0)
        except FileNotFoundError as exc:
            print(f"Chyba: {exc}", file=sys.stderr)
            return 1
        t0 = time.monotonic()
        raw = server.transcribe(samples, cfg=cfg)
        prepis_s = time.monotonic() - t0
        out = text.uprav(raw, cfg)
        if bool(cfg.get("ai_uprava", False)) and llm.is_available() and out:
            out = ai.upravit_text(out, cfg)
        print(out)
        rtf = prepis_s / audio_s if audio_s > 0 else 0.0
        print(f"[audio: {audio_s:.2f} s | přepis: {prepis_s:.2f} s | RTF: {rtf:.3f}]", file=sys.stderr)
        return 0
    finally:
        ai.close()
        server.close()


def main(argv: list[str] | None = None) -> int:
    # Síťová ochrana musí být nainstalována jako úplně první krok před spuštěním aplikace.
    initial_cfg = config.load_config()
    netguard.install(config.allowed_hosts(initial_cfg))

    parser = argparse.ArgumentParser(prog="mluvka", description="Mluvka — lokální diktování pro Windows")
    parser.add_argument("--soubor", type=Path, help="Přepíše zadaný WAV soubor a skončí")
    parser.add_argument("--okno", action="store_true", help="Otevře hlavní okno Mluvky")
    args = parser.parse_args(argv)
    if args.soubor is not None:
        return _transcribe_file(args.soubor)
    return app.run(open_window=args.okno)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
