"""Příprava dat z Common Voice 17 cs parquetů a vlastních vět (`DATA/trenink/vety.tsv`).

Spouštět v tréninkovém venv (`DATA/venv-train/Scripts/python.exe`).
"""

from __future__ import annotations

import argparse
import io
import math
import multiprocessing
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
from scipy.signal import resample_poly

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trenink import spolecne

_FFMPEG_CACHE: str | None = None
_FFMPEG_SEARCHED = False


def _find_ffmpeg() -> str | None:
    global _FFMPEG_CACHE, _FFMPEG_SEARCHED
    if _FFMPEG_SEARCHED:
        return _FFMPEG_CACHE
    _FFMPEG_SEARCHED = True
    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG_CACHE = found
        return found
    local_app = Path(os.environ.get("LOCALAPPDATA", "") or Path.home() / "AppData" / "Local")
    winget_dir = local_app / "Microsoft" / "WinGet" / "Packages"
    if winget_dir.is_dir():
        for exe in winget_dir.rglob("ffmpeg.exe"):
            if exe.is_file():
                _FFMPEG_CACHE = str(exe)
                return _FFMPEG_CACHE
    return None


def _resample_to_pcm16_mono(samples: np.ndarray, orig_sr: int) -> tuple[bytes, float]:
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1, dtype=np.float32)
    seconds = len(arr) / float(orig_sr) if orig_sr > 0 else 0.0
    if int(orig_sr) != spolecne.TARGET_SAMPLE_RATE and len(arr) > 0:
        g = math.gcd(spolecne.TARGET_SAMPLE_RATE, int(orig_sr))
        up = spolecne.TARGET_SAMPLE_RATE // g
        down = int(orig_sr) // g
        arr = resample_poly(arr, up, down).astype(np.float32)
        seconds = len(arr) / float(spolecne.TARGET_SAMPLE_RATE)
    pcm = np.round(np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return pcm, seconds


def _decode_via_ffmpeg(mp3_bytes: bytes | None, file_path: Path | None) -> tuple[bytes, float]:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("soundfile selhal a ffmpeg.exe nebyl nalezen.")
    if mp3_bytes is not None:
        cmd = [
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            str(spolecne.TARGET_SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
        res = subprocess.run(cmd, input=mp3_bytes, capture_output=True, check=False)
    else:
        cmd = [
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(file_path),
            "-ac",
            "1",
            "-ar",
            str(spolecne.TARGET_SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
        res = subprocess.run(cmd, capture_output=True, check=False)
    if res.returncode != 0 or not res.stdout:
        err = res.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg dekódování selhalo (kód {res.returncode}): {err}")
    pcm = res.stdout
    seconds = (len(pcm) // 2) / float(spolecne.TARGET_SAMPLE_RATE)
    return pcm, seconds


def _process_clip(task: tuple[str, Any, str, str]) -> dict[str, Any] | None:
    kind, payload, raw_text, out_path_str = task
    out_path = Path(out_path_str)
    text = spolecne.clean_label_text(raw_text)
    if not text:
        return None

    src_mtime_ns = 0
    if kind == "wav_path":
        try:
            src_mtime_ns = Path(payload).stat().st_mtime_ns
        except OSError:
            return None

    # Idempotence: již převedené validní 16 kHz mono WAV soubory znovu nedekódujeme
    # (u lokálních wav_path jen pokud cílový soubor není starší než zdrojový).
    if out_path.is_file() and out_path.stat().st_size > 44:
        try:
            out_mtime_ns = out_path.stat().st_mtime_ns
            if kind != "wav_path" or out_mtime_ns >= src_mtime_ns:
                seconds, sr, ch, sw = spolecne.read_wav_info(out_path)
                if sr == spolecne.TARGET_SAMPLE_RATE and ch == 1 and sw == 2:
                    if spolecne.valid_duration(seconds):
                        return {
                            "file": out_path.name,
                            "text": text,
                            "seconds": round(seconds, 3),
                        }
                    return None
        except Exception:
            pass

    pcm: bytes
    seconds: float
    try:
        src = io.BytesIO(payload) if kind == "bytes" else Path(payload)
        samples, sr = sf.read(src, dtype="float32", always_2d=True)
        mono = samples.mean(axis=1, dtype=np.float32) if samples.shape[1] > 1 else samples[:, 0]
        raw_sec = len(mono) / float(sr) if sr > 0 else 0.0
        if not spolecne.valid_duration(raw_sec):
            return None
        pcm, seconds = _resample_to_pcm16_mono(mono, int(sr))
    except Exception:
        mp3_bytes = payload if kind == "bytes" else None
        src_path = None if kind == "bytes" else Path(payload)
        try:
            pcm, seconds = _decode_via_ffmpeg(mp3_bytes, src_path)
        except Exception as exc:
            print(f"Varování: přeskakuji poškozený klip {out_path.name} ({exc})", file=sys.stderr)
            return None

    if not spolecne.valid_duration(seconds):
        return None

    spolecne.write_wav_pcm16_mono(out_path, pcm, spolecne.TARGET_SAMPLE_RATE)
    return {
        "file": out_path.name,
        "text": text,
        "seconds": round(seconds, 3),
    }


def _detect_cv_columns(parquet_file: pq.ParquetFile) -> tuple[str, str]:
    names = list(parquet_file.schema_arrow.names)
    audio_col = "audio" if "audio" in names else next(
        (n for n in names if "audio" in n.lower()),
        names[0],
    )
    text_col = "sentence" if "sentence" in names else next(
        (n for n in names if n.lower() in ("text", "transcription", "sentence")),
        names[1],
    )
    return audio_col, text_col


def _extract_audio_bytes(audio_obj: Any, shard_dir: Path) -> bytes | None:
    if isinstance(audio_obj, dict):
        b = audio_obj.get("bytes")
        if b:
            return bytes(b)
        p = audio_obj.get("path")
        if p:
            fp = shard_dir / str(p)
            if fp.is_file():
                return fp.read_bytes()
    elif isinstance(audio_obj, (bytes, bytearray, memoryview)):
        return bytes(audio_obj)
    return None


def _iter_cv_batches(
    shards: list[Path],
    out_dir: Path,
    batch_size: int = 128,
) -> Iterator[list[tuple[str, Any, str, str]]]:
    src_idx = 0
    for shard in shards:
        pf = pq.ParquetFile(shard)
        audio_col, text_col = _detect_cv_columns(pf)
        for record_batch in pf.iter_batches(batch_size=batch_size, columns=[audio_col, text_col]):
            audio_list = record_batch.column(0).to_pylist()
            text_list = record_batch.column(1).to_pylist()
            batch: list[tuple[str, Any, str, str]] = []
            for audio_obj, sentence in zip(audio_list, text_list):
                wav_name = f"{src_idx:06d}.wav"
                src_idx += 1
                cleaned = spolecne.clean_label_text(sentence)
                if not cleaned or spolecne.vyradit_z_cv(cleaned):
                    continue
                audio_bytes = _extract_audio_bytes(audio_obj, shard.parent)
                if not audio_bytes:
                    continue
                batch.append(("bytes", audio_bytes, cleaned, str(out_dir / wav_name)))
            if batch:
                yield batch


def prepare_cv_set(
    sada: str,
    shards: list[Path],
    data_root: Path,
    *,
    max_items: int | None,
    workers: int,
    pool: Any | None = None,
) -> list[dict[str, Any]]:
    if not shards:
        print(f"{sada}: nenalezeny žádné parquet soubory (přeskakuji).")
        return []
    out_dir = spolecne.dataset_dir(sada, data_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    accepted: list[dict[str, Any]] = []

    def _run_with_pool(active_pool: Any) -> None:
        batch_sz = 32 if (max_items is not None and max_items <= 64) else 128
        for batch in _iter_cv_batches(shards, out_dir, batch_size=batch_sz):
            if max_items is None:
                results = active_pool.map(_process_clip, batch)
                accepted.extend(item for item in results if item is not None)
                continue
            offset = 0
            while offset < len(batch) and len(accepted) < max_items:
                need = max_items - len(accepted)
                sub = batch[offset : offset + need]
                offset += need
                results = active_pool.map(_process_clip, sub)
                for item in results:
                    if item is not None:
                        accepted.append(item)
                        if len(accepted) >= max_items:
                            break
            if len(accepted) >= max_items:
                break

    if pool is not None:
        _run_with_pool(pool)
    else:
        with multiprocessing.Pool(processes=max(1, workers)) as owned_pool:
            _run_with_pool(owned_pool)

    manifest_path = out_dir / "manifest.tsv"
    spolecne.write_manifest(manifest_path, accepted)
    hrs = spolecne.manifest_hours(accepted)
    print(f"{sada}: {len(accepted)} klipů, {hrs:.3f} h -> {manifest_path}")
    return accepted


def prepare_vety_sets(
    data_root: Path,
    *,
    workers: int,
    pool: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vety_tsv = spolecne.trenink_dir(data_root) / "vety.tsv"
    vety_dir = spolecne.trenink_dir(data_root) / "vety"
    if not vety_tsv.is_file():
        return [], []

    raw_records: list[tuple[int, str, Path]] = []
    for line in vety_tsv.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        code_raw = parts[0].strip()
        if not code_raw.isdigit():
            continue
        code = f"{int(code_raw):03d}"
        wav_path = vety_dir / f"{code}.wav"
        text = spolecne.clean_label_text(parts[1])
        if not wav_path.is_file() or not text:
            continue
        try:
            dur, _, _, _ = spolecne.read_wav_info(wav_path)
        except Exception:
            dur = -1.0
        if dur >= 0 and not spolecne.valid_duration(dur):
            continue
        raw_records.append((int(code_raw), text, wav_path))

    raw_records.sort(key=lambda r: r[0])
    train_raw, test_raw = spolecne.split_train_test(raw_records, test_every=5)

    def _run_subset(sada: str, subset: list[tuple[int, str, Path]]) -> list[dict[str, Any]]:
        out_dir = spolecne.dataset_dir(sada, data_root)
        # Použijeme stabilní kód věty (`000001.wav`, ...), aby posun rozdělení nesmíchal starý zvuk s novým textem.
        tasks = [
            ("wav_path", str(wav_p), text, str(out_dir / f"{code_int:06d}.wav"))
            for code_int, text, wav_p in subset
        ]
        return _materialize_wav_subset(sada, tasks, data_root, workers=workers, pool=pool)

    return _run_subset("vety_train", train_raw), _run_subset("vety_test", test_raw)


def _materialize_wav_subset(
    sada: str,
    tasks: list[tuple[str, Any, str, str]],
    data_root: Path,
    *,
    workers: int,
    pool: Any | None = None,
) -> list[dict[str, Any]]:
    out_dir = spolecne.dataset_dir(sada, data_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not tasks:
        for stale_wav in out_dir.glob("*.wav"):
            stale_wav.unlink(missing_ok=True)
        spolecne.write_manifest(out_dir / "manifest.tsv", [])
        print(f"{sada}: 0 klipů, 0.000 h")
        return []
    if pool is not None:
        res = pool.map(_process_clip, tasks)
    else:
        with multiprocessing.Pool(processes=max(1, workers)) as owned_pool:
            res = owned_pool.map(_process_clip, tasks)
    rows = [r for r in res if r is not None]
    keep_files = {r["file"] for r in rows}
    for stale_wav in out_dir.glob("*.wav"):
        if stale_wav.name not in keep_files:
            stale_wav.unlink(missing_ok=True)
    manifest_path = out_dir / "manifest.tsv"
    spolecne.write_manifest(manifest_path, rows)
    hrs = spolecne.manifest_hours(rows)
    print(f"{sada}: {len(rows)} klipů, {hrs:.3f} h -> {manifest_path}")
    return rows


def prepare_diktaty_sets(
    data_root: Path,
    *,
    workers: int,
    pool: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    info = spolecne.collect_verified_diktaty(data_root, test_every=5)
    verified_min = float(info["verified_minutes"])
    print(f"Ověřené diktáty: {info['total_verified']} klipů ({verified_min:.1f} min)")
    if info["skipped_long"] > 0:
        print(
            f"Přeskočeno ověřených diktátů > {int(spolecne.MAX_DURATION_S)} s: "
            f"{info['skipped_long']}"
        )
    if info["skipped_short"] > 0:
        print(
            f"Přeskočeno ověřených diktátů < {spolecne.MIN_DURATION_S:.1f} s: "
            f"{info['skipped_short']}"
        )
    if verified_min < 10.0:
        print(
            f"Tip: zatím máš méně než 10 minut ověřených diktátů ({verified_min:.1f} min) "
            "— pro výraznější doladění spontánní řeči ověř alespoň 10 minut."
        )

    def _run_diktaty_subset(sada: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out_dir = spolecne.dataset_dir(sada, data_root)
        tasks = [
            ("wav_path", str(item["wav_path"]), str(item["text"]), str(out_dir / f"{item['stem']}.wav"))
            for item in items
        ]
        return _materialize_wav_subset(sada, tasks, data_root, workers=workers, pool=pool)

    return (
        _run_diktaty_subset("diktaty_train", info["train"]),
        _run_diktaty_subset("diktaty_test", info["test"]),
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Příprava tréninkových a testovacích WAV dat pro Mluvku.")
    parser.add_argument("--max-train", type=int, default=None, help="Maximální počet klipů pro cv_train (výchozí: vše).")
    parser.add_argument("--max-test", type=int, default=500, help="Maximální počet klipů pro cv_test (výchozí: 500).")
    parser.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 4))), help="Počet worker procesů.")
    parser.add_argument("--jen-vety", action="store_true", help="Připraví pouze vety_train/vety_test a diktaty_train/diktaty_test a nebude sahat na sady Common Voice.")
    parser.add_argument("--data", type=Path, default=None, help="Přepíše DATA adresář (jinak MLUVKA_DATA / %%LOCALAPPDATA%%\\Mluvka).")
    args = parser.parse_args()

    data_root = spolecne.data_dir(args.data)
    target_sets = spolecne.prepare_target_sets(args.jen_vety)
    run_cv = ("cv_train" in target_sets) or ("cv_test" in target_sets)

    train_shards: list[Path] = []
    test_shards: list[Path] = []
    if run_cv:
        cv_root = spolecne.cv_dir(data_root)
        train_shards = sorted(cv_root.glob("train-*.parquet"))
        test_shards = sorted(cv_root.glob("test-*.parquet"))

        if test_shards:
            pf0 = pq.ParquetFile(test_shards[0])
            print(f"Schema CV parquet ({test_shards[0].name}): {pf0.schema_arrow}")

    with multiprocessing.Pool(processes=max(1, args.workers)) as pool:
        if "cv_train" in target_sets:
            prepare_cv_set(
                "cv_train",
                train_shards,
                data_root,
                max_items=args.max_train,
                workers=args.workers,
                pool=pool,
            )
        if "cv_test" in target_sets:
            prepare_cv_set(
                "cv_test",
                test_shards,
                data_root,
                max_items=args.max_test,
                workers=args.workers,
                pool=pool,
            )
        if "vety_train" in target_sets or "vety_test" in target_sets:
            prepare_vety_sets(data_root, workers=args.workers, pool=pool)
        prepare_diktaty_sets(data_root, workers=args.workers, pool=pool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
