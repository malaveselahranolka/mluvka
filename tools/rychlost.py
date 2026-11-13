"""Benchmark rychlosti a shody výstupu průběžného přepisu (StreamingTranscriber) vs. dávkový přepis."""

from __future__ import annotations

import copy
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import load_fleurs, load_wav_16k_f32
from mluvka import asr, config, netguard
from mluvka.wer import levenshtein_words, normalise_for_wer


def build_concat_audio(data_root: Path, target_s: float = 90.0) -> tuple[np.ndarray, str, int]:
    clips = load_fleurs(data_root)
    silence = np.zeros(int(0.4 * asr.SAMPLE_RATE), dtype=np.float32)
    parts: list[np.ndarray] = []
    refs: list[str] = []
    total_samples = 0
    clip_count = 0
    target_samples = int(target_s * asr.SAMPLE_RATE)
    for _fname, wav_path, ref in clips:
        arr = load_wav_16k_f32(wav_path)
        if parts:
            parts.append(silence)
            total_samples += len(silence)
        parts.append(arr)
        refs.append(ref)
        total_samples += len(arr)
        clip_count += 1
        if total_samples >= target_samples:
            break
    return np.concatenate(parts).astype(np.float32, copy=False), " ".join(refs), clip_count


def run_benchmark(
    data_root: Path | None = None,
    *,
    target_s: float = 90.0,
    step_sleep_s: float = 0.08,
) -> dict[str, object]:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = data_root or config.data_dir()
    netguard.install([])
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)

    audio_arr, ref_text, clip_count = build_concat_audio(root, target_s=target_s)
    audio_s = len(audio_arr) / float(asr.SAMPLE_RATE)

    server = asr.WhisperServer(root)
    try:
        t_start = time.monotonic()
        server.start(timeout_s=60.0)
        startup_s = time.monotonic() - t_start

        # 1) Dávkový přepis až po uvolnění klávesy (dosavadní chování)
        t0 = time.monotonic()
        hyp_batch = server.transcribe(audio_arr, cfg=cfg)
        batch_release_s = time.monotonic() - t0

        # 2) Průběžný přepis po 0.5s krocích (zrychlená simulace reálného diktování)
        step_samples = int(0.5 * asr.SAMPLE_RATE)
        buf_lock = threading.Lock()
        captured: list[np.ndarray] = []

        def get_audio(start_idx: int) -> np.ndarray:
            with buf_lock:
                if not captured:
                    return np.zeros(0, dtype=np.float32)
                full = np.concatenate(captured)
            if start_idx >= len(full):
                return np.zeros(0, dtype=np.float32)
            return full[start_idx:].astype(np.float32, copy=True)

        streamer = asr.StreamingTranscriber(
            server,
            get_audio,
            cfg=cfg,
            poll_interval_s=0.05,
        )
        streamer.start()
        for pos in range(0, len(audio_arr), step_samples):
            chunk = audio_arr[pos : pos + step_samples]
            with buf_lock:
                captured.append(chunk)
            time.sleep(step_sleep_s)

        streamer.stop_polling()
        t_rel = time.monotonic()
        hyp_stream = streamer.finish(audio_arr, cfg=cfg)
        stream_release_s = time.monotonic() - t_rel

        ref_w = normalise_for_wer(ref_text)
        batch_w = normalise_for_wer(hyp_batch)
        stream_w = normalise_for_wer(hyp_stream)

        diff_edits = levenshtein_words(batch_w, stream_w)
        diff_wer_pct = (diff_edits / len(batch_w) * 100.0) if batch_w else 0.0
        batch_wer_pct = (levenshtein_words(ref_w, batch_w) / len(ref_w) * 100.0) if ref_w else 0.0
        stream_wer_pct = (levenshtein_words(ref_w, stream_w) / len(ref_w) * 100.0) if ref_w else 0.0

        speedup = (batch_release_s / stream_release_s) if stream_release_s > 0 else float("inf")
        result = {
            "port": server.port,
            "model": server.model_path.name,
            "startup_s": startup_s,
            "clips": clip_count,
            "audio_s": audio_s,
            "batch_release_s": batch_release_s,
            "stream_release_s": stream_release_s,
            "speedup": speedup,
            "chunk_sizes": streamer.chunk_sizes,
            "diff_edits": diff_edits,
            "batch_words": len(batch_w),
            "diff_wer_pct": diff_wer_pct,
            "batch_wer_pct": batch_wer_pct,
            "stream_wer_pct": stream_wer_pct,
            "hyp_batch": hyp_batch,
            "hyp_stream": hyp_stream,
        }
        print(f"Model: {result['model']} (port {result['port']}, start {startup_s:.2f} s)")
        print(f"Audio: {audio_s:.2f} s ({clip_count} FLEURS klipů)")
        print(f"At-release transcribe(): {batch_release_s:.2f} s (WER vs ref: {batch_wer_pct:.2f} %)")
        print(
            f"Streaming release->text: {stream_release_s:.2f} s "
            f"(WER vs ref: {stream_wer_pct:.2f} %, zrychlení {speedup:.1f}x)"
        )
        print(f"Chunky (vzorky): {streamer.chunk_sizes}")
        print(
            f"Rozdíl streaming vs batch: {diff_edits}/{len(batch_w)} slov "
            f"(WER mezi výstupy: {diff_wer_pct:.2f} %)"
        )
        return result
    finally:
        server.close()


if __name__ == "__main__":
    run_benchmark()
