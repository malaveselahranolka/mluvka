"""Benchmark FLEURS-cs a 60s chunkovaného přepisu přes lokální whisper-server.exe."""

from __future__ import annotations

import copy
import sys
import time
import wave
from pathlib import Path

import numpy as np

from mluvka import asr, config, netguard
from mluvka.wer import levenshtein_words, normalise_for_wer


def load_wav_16k_f32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1, f"{path} není mono"
        assert wf.getsampwidth() == 2, f"{path} není 16-bit PCM"
        assert wf.getframerate() == 16000, f"{path} není 16 kHz"
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def load_fleurs(data: Path) -> list[tuple[str, Path, str]]:
    fleurs_dir = data / "bench" / "fleurs"
    refs_path = fleurs_dir / "refs.tsv"
    items: list[tuple[str, Path, str]] = []
    for line in refs_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fname, ref = line.split("\t", 1)
        items.append((fname, fleurs_dir / fname, ref.strip()))
    return items


def run_benchmark(data: Path | None = None) -> Path:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    data_root = data or config.data_dir()
    netguard.install([])
    cfg_base = copy.deepcopy(config.DEFAULT_CONFIG)
    clips = load_fleurs(data_root)
    loaded = [(fname, load_wav_16k_f32(p), ref) for fname, p, ref in clips]

    silence = np.zeros(int(0.4 * asr.SAMPLE_RATE), dtype=np.float32)
    concat_parts: list[np.ndarray] = []
    concat_refs: list[str] = []
    total_samples = 0
    for _fname, arr, ref in loaded:
        if concat_parts:
            concat_parts.append(silence)
            total_samples += len(silence)
        concat_parts.append(arr)
        concat_refs.append(ref)
        total_samples += len(arr)
        if total_samples >= 60 * asr.SAMPLE_RATE:
            break
    concat_audio = np.concatenate(concat_parts)
    concat_audio_s = len(concat_audio) / float(asr.SAMPLE_RATE)
    concat_ref_words = normalise_for_wer(" ".join(concat_refs))

    lines: list[str] = [
        "# Benchmark CzechSpeech q5_0 (FLEURS-cs)",
        "",
    ]

    server = asr.WhisperServer(data_root)
    try:
        t_start = time.monotonic()
        server.start(timeout_s=60.0)
        startup_s = time.monotonic() - t_start
        lines.append(f"- Start + warm-up serveru: **{startup_s:.2f} s**")
        lines.append(f"- Počet klipů: **{len(loaded)}**")
        lines.append(f"- Délka spojené nahrávky pro 60s test: **{concat_audio_s:.2f} s**")
        lines.append("")

        summary_rows: list[str] = [
            "## Souhrn",
            "",
            "| Režim | Celkový WER | Celkový čas (40 klipů) | 60s+ audio čas | 60s+ audio WER |",
            "|---|---:|---:|---:|---:|",
        ]

        detail_sections: list[str] = []

        for label, use_dict in (
            ("Bez slovníku", False),
            ("Se slovníkem", True),
        ):
            cfg = copy.deepcopy(cfg_base)
            cfg["slovnik_do_promptu"] = use_dict

            total_edits = 0
            total_ref_words = 0
            total_wall = 0.0
            clip_rows: list[str] = [
                f"### {label}",
                "",
                "| Soubor | Audio (s) | Přepis (s) | WER | Reference | Hypotéza |",
                "|---|---:|---:|---:|---|---|",
            ]

            for fname, arr, ref in loaded:
                audio_s = len(arr) / float(asr.SAMPLE_RATE)
                t0 = time.monotonic()
                hyp = server.transcribe(arr, cfg=cfg)
                wall = time.monotonic() - t0
                total_wall += wall

                ref_w = normalise_for_wer(ref)
                hyp_w = normalise_for_wer(hyp)
                edits = levenshtein_words(ref_w, hyp_w)
                total_edits += edits
                total_ref_words += len(ref_w)
                wer = (edits / len(ref_w) * 100.0) if ref_w else 0.0
                ref_cell = ref.replace("|", "\\|").replace("\n", " ")
                hyp_cell = hyp.replace("|", "\\|").replace("\n", " ")
                clip_rows.append(
                    f"| `{fname}` | {audio_s:.2f} | {wall:.2f} | {wer:.1f} % | {ref_cell} | {hyp_cell} |"
                )

            t0 = time.monotonic()
            concat_hyp = server.transcribe(concat_audio, cfg=cfg)
            concat_wall = time.monotonic() - t0
            concat_edits = levenshtein_words(concat_ref_words, normalise_for_wer(concat_hyp))
            concat_wer = (concat_edits / len(concat_ref_words) * 100.0) if concat_ref_words else 0.0
            total_wer = (total_edits / total_ref_words * 100.0) if total_ref_words else 0.0

            summary_rows.append(
                f"| {label} | **{total_wer:.2f} %** | {total_wall:.2f} s | **{concat_wall:.2f} s** | {concat_wer:.2f} % |"
            )
            detail_sections.extend(clip_rows)
            detail_sections.append("")

        lines.extend(summary_rows)
        lines.append("")
        lines.extend(detail_sections)
    finally:
        server.close()

    report = "\n".join(lines) + "\n"
    out_path = data_root / "bench" / "vysledek.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    return out_path


if __name__ == "__main__":
    run_benchmark()
