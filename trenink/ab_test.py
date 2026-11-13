"""A/B porovnání WER ggml modelů na cv_test, fleurs, vety_test a diktaty_test přes whisper-server.

Spouštět v aplikačním venv (`DATA/venv/Scripts/python.exe`, bez torch).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bench
from mluvka import asr, config, wer
from trenink import spolecne


def _resolve_model_arg(raw_path: str, data_root: Path) -> Path:
    p = Path(raw_path)
    if p.is_file():
        return p.resolve()
    in_trenink = spolecne.trenink_dir(data_root) / p
    if in_trenink.is_file():
        return in_trenink.resolve()
    in_data = data_root / p
    if in_data.is_file():
        return in_data.resolve()
    raise FileNotFoundError(f"Model neexistuje: {raw_path}")


def _load_dataset(
    sada: str,
    data_root: Path,
    limit: int | None,
) -> list[tuple[str, Any, str]]:
    if sada == "fleurs":
        clips = bench.load_fleurs(data_root)
        if limit is not None:
            clips = clips[:limit]
        return [
            (fname, bench.load_wav_16k_f32(wav_p), ref)
            for fname, wav_p, ref in clips
        ]

    if sada in ("cv_test", "vety_test", "diktaty_test", "fleurs_test"):
        manifest_path = spolecne.dataset_dir(sada, data_root) / "manifest.tsv"
        rows = spolecne.read_manifest(manifest_path, resolve_paths=True)
        if limit is not None:
            rows = rows[:limit]
        return [
            (row["file"], bench.load_wav_16k_f32(Path(row["wav_path"])), row["text"])
            for row in rows
        ]

    raise ValueError(f"Neznámá sada: {sada}")


def _append_vysledky(data_root: Path, new_rows: list[dict[str, Any]]) -> Path:
    out_path = config.trenink_vysledky_path(data_root)
    existing: list[dict[str, Any]] = []
    if out_path.is_file():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except (OSError, ValueError):
            existing = []
    existing.extend(new_rows)
    spolecne.atomic_write_text(
        out_path,
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
    )
    return out_path


def _format_markdown_report(
    datum_iso: str,
    beam: int,
    summary_rows: list[dict[str, Any]],
    worst_by_model: dict[str, tuple[str, list[dict[str, Any]]]],
) -> str:
    lines: list[str] = [
        f"# A/B test modelů ({datum_iso}, beam={beam})",
        "",
        "| Model | Sada | N | Beam | WER | Audio (s) | Čas (s) | RTF |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        wer_pct = f"{row['wer'] * 100.0:.2f} %"
        lines.append(
            f"| {row['model']} | {row['sada']} | {row['n']} | {row['beam']} | "
            f"{wer_pct} | {row['audio_s']:.1f} | {row['sekundy']:.2f} | {row['rtf']:.3f} |"
        )
    lines.append("")

    for _key, (model_label, bad_items) in worst_by_model.items():
        lines.append(f"## 10 nejhorších vět — {model_label}")
        lines.append("")
        if not bad_items:
            lines.append("Žádné chyby.")
            lines.append("")
            continue
        top10 = sorted(
            bad_items,
            key=lambda x: (float(x["wer"]), int(x["errors"])),
            reverse=True,
        )[:10]
        for idx, item in enumerate(top10, start=1):
            lines.append(
                f"{idx}. **[{item['sada']}] {item['id']}** "
                f"(WER {item['wer'] * 100.0:.1f} %, {item['errors']}/{item['ref_words']} chyb)"
            )
            lines.append(f"   - ref: {item['ref']}")
            lines.append(f"   - hyp: {item['hyp']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    default_beam = int(config.DEFAULT_CONFIG.get("beam_size", 5))
    parser = argparse.ArgumentParser(description="A/B WER srovnání ggml modelů přes lokální whisper-server.")
    parser.add_argument("--model", action="append", required=True, help="Cesta k ggml .bin modelu (lze opakovat).")
    parser.add_argument(
        "--sada",
        action="append",
        required=True,
        choices=("cv_test", "fleurs", "vety_test", "diktaty_test", "fleurs_test"),
        help="Testovací sada (lze opakovat).",
    )
    parser.add_argument("--n", type=int, default=None, help="Maximální počet klipů ze sady.")
    parser.add_argument("--beam", type=int, default=default_beam, help=f"Beam size (výchozí: {default_beam}).")
    parser.add_argument("--data", type=Path, default=None, help="Přepíše DATA adresář.")
    args = parser.parse_args()

    if int(args.beam) < 1:
        print("Chyba: --beam musí být alespoň 1.", file=sys.stderr)
        return 1
    if args.n is not None and int(args.n) < 1:
        print("Chyba: --n musí být alespoň 1.", file=sys.stderr)
        return 1

    data_root = spolecne.data_dir(args.data)
    model_paths = [_resolve_model_arg(m, data_root) for m in args.model]

    datasets: dict[str, list[tuple[str, Any, str]]] = {}
    for sada in dict.fromkeys(args.sada):
        clips = _load_dataset(sada, data_root, args.n)
        if not clips:
            print(f"Chyba: Sada '{sada}' je prázdná nebo neexistuje.", file=sys.stderr)
            return 1
        datasets[sada] = clips

    now = datetime.now().astimezone()
    datum_iso = now.isoformat(timespec="seconds")
    datum_file = now.strftime("%Y-%m-%d_%H%M%S")

    summary_rows: list[dict[str, Any]] = []
    vysledky_rows: list[dict[str, Any]] = []
    worst_by_model: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    seen_names: dict[str, int] = {}

    for model_path in model_paths:
        model_name = model_path.name
        seen_names[model_name] = seen_names.get(model_name, 0) + 1
        model_key = f"{model_path}#{seen_names[model_name]}"
        heading_label = model_name if seen_names[model_name] == 1 else f"{model_name} ({model_path.parent.name})"
        worst_list: list[dict[str, Any]] = []
        worst_by_model[model_key] = (heading_label, worst_list)
        print(f"\n=== Model: {model_name} (beam={args.beam}) ===")
        server = asr.WhisperServer(data_root, model_path=model_path)
        try:
            # start() spustí whisper-server na volném portu se standardními flagy a provede 1x warm-up.
            server.start(timeout_s=60.0)
            print(f"whisper-server připraven na portu {server.port}")

            for sada, clips in datasets.items():
                refs: list[str] = []
                hyps: list[str] = []
                audio_s = 0.0
                t0 = time.perf_counter()
                for clip_id, samples, ref in clips:
                    audio_s += len(samples) / float(asr.SAMPLE_RATE)
                    hyp = server.transcribe(samples, prompt="", beam_size=args.beam)
                    refs.append(ref)
                    hyps.append(hyp)
                    errs, ref_words, s_wer = wer.sentence_wer(ref, hyp)
                    if errs > 0:
                        worst_list.append(
                            {
                                "sada": sada,
                                "id": clip_id,
                                "ref": ref,
                                "hyp": hyp,
                                "errors": errs,
                                "ref_words": ref_words,
                                "wer": s_wer,
                            }
                        )
                elapsed_s = time.perf_counter() - t0
                total_wer = wer.compute_wer(refs, hyps)
                rtf = (elapsed_s / audio_s) if audio_s > 0 else 0.0
                print(
                    f"  {sada}: n={len(clips)}, WER={total_wer * 100.0:.2f} %, "
                    f"audio={audio_s:.1f} s, čas={elapsed_s:.2f} s, RTF={rtf:.3f}"
                )
                row_summary = {
                    "datum": datum_iso,
                    "model": model_name,
                    "sada": sada,
                    "n": len(clips),
                    "beam": int(args.beam),
                    "wer": round(float(total_wer), 4),
                    "sekundy": round(float(elapsed_s), 2),
                    "audio_s": round(float(audio_s), 2),
                    "rtf": round(float(rtf), 3),
                }
                summary_rows.append(row_summary)
                vysledky_rows.append(
                    {
                        "datum": datum_iso,
                        "model": model_name,
                        "sada": sada,
                        "n": len(clips),
                        "beam": int(args.beam),
                        "wer": round(float(total_wer), 4),
                        "sekundy": round(float(elapsed_s), 2),
                    }
                )
        except Exception as exc:
            print(f"Chyba při měření modelu {model_name}: {exc}", file=sys.stderr)
            return 1
        finally:
            server.close()

    vys_path = _append_vysledky(data_root, vysledky_rows)
    md_path = spolecne.trenink_dir(data_root) / f"ab_{datum_file}.md"
    spolecne.atomic_write_text(
        md_path,
        _format_markdown_report(datum_iso, int(args.beam), summary_rows, worst_by_model),
    )
    print(f"\nUloženo: {vys_path} a {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
