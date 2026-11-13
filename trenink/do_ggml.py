"""Sloučení LoRA adaptéru, převod do ggml f16 a kvantizace na q5_0 (volitelně q8_0).

Spouštět v tréninkovém venv (`DATA/venv-train/Scripts/python.exe`).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import torch
from peft import PeftModel
from transformers import WhisperForConditionalGeneration

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trenink import spolecne

CONVERT_URL = "https://raw.githubusercontent.com/ggml-org/whisper.cpp/v1.7.5/models/convert-h5-to-ggml.py"
MEL_FILTERS_URL = "https://raw.githubusercontent.com/openai/whisper/v20240930/whisper/assets/mel_filters.npz"
GGML_MAGIC = b"lmgg"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_once(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        digest = _sha256_file(dest)
        print(f"Nástroj z cache: {dest} (sha256={digest})")
        return dest

    tmp = dest.with_name(f".{dest.name}.tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "Mluvka-Trainer/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            payload = resp.read()
        if not payload:
            raise RuntimeError(f"Stažený soubor z {url} je prázdný.")
        tmp.write_bytes(payload)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    digest = _sha256_file(dest)
    print(f"Staženo: {dest} (sha256={digest})")
    return dest


def ensure_tools(data_root: Path) -> tuple[Path, Path]:
    tools = spolecne.tools_dir(data_root)
    convert_py = _download_once(CONVERT_URL, tools / "convert-h5-to-ggml.py")
    mel_npz = _download_once(
        MEL_FILTERS_URL,
        tools / "whisper" / "whisper" / "assets" / "mel_filters.npz",
    )
    if not mel_npz.is_file():
        raise FileNotFoundError(f"Chybí {mel_npz}")
    return convert_py, tools / "whisper"


def _merge_adapter(base_dir: Path, adapter_dir: Path, merged_dir: Path) -> Path:
    if not adapter_dir.is_dir():
        raise FileNotFoundError(f"Adresář adaptéru neexistuje: {adapter_dir}")
    print(f"Slučuji LoRA adaptér {adapter_dir} se základem {base_dir} (fp16)...")
    base_model = WhisperForConditionalGeneration.from_pretrained(
        str(base_dir),
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
    )
    peft_model = PeftModel.from_pretrained(base_model, str(adapter_dir))
    merged = peft_model.merge_and_unload()
    merged.config.forced_decoder_ids = None

    shutil.rmtree(merged_dir, ignore_errors=True)
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(merged_dir), safe_serialization=True)

    for item in base_dir.iterdir():
        if not item.is_file():
            continue
        name = item.name
        if name.startswith("model.safetensors") or ".part" in name:
            continue
        target = merged_dir / name
        if not target.exists():
            shutil.copy2(item, target)
    print(f"Sloučený HF model uložen -> {merged_dir}")
    return merged_dir


def verify_ggml_magic(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError(f"Výstupní ggml soubor je neplatný nebo prázdný: {path}")
    with path.open("rb") as f:
        head = f.read(4)
    if head != GGML_MAGIC:
        raise RuntimeError(
            f"Neplatná ggml hlavička v {path.name}: {head!r} (očekáváno {GGML_MAGIC!r})"
        )
    print(f"Ověřeno ggml magic ({head!r}): {path} ({path.stat().st_size / (1024**2):.1f} MB)")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Převod HF Whisper modelu / LoRA adaptéru do ggml.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--adapter", type=Path, help="Cesta k LoRA adaptéru (např. runs/smoke/adapter).")
    group.add_argument("--base-only", action="store_true", help="Převede jen základní model (kontrola převodu).")
    parser.add_argument("--name", default=None, help="Název běhu pro výstupní soubory ggml-<name>-*.bin.")
    parser.add_argument("--q8", action="store_true", help="Vytvoří také q8_0 kvantizaci.")
    parser.add_argument("--only-q8", action="store_true", help="Vytvoří pouze q8_0 kvantizaci bez q5_0.")
    parser.add_argument("--data", type=Path, default=None, help="Přepíše DATA adresář.")
    args = parser.parse_args()

    data_root = spolecne.data_dir(args.data)
    base_dir = spolecne.base_model_dir(data_root)
    quantize_exe = spolecne.whisper_quantize_exe(data_root)
    if not (base_dir / "model.safetensors").is_file():
        print(f"Chybí základní model: {base_dir / 'model.safetensors'}", file=sys.stderr)
        return 1
    if not quantize_exe.is_file():
        print(f"Chybí whisper-quantize.exe: {quantize_exe}", file=sys.stderr)
        return 1

    if args.base_only:
        run_name = args.name or "base"
        run_dir = spolecne.runs_dir(data_root) / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        hf_input_dir = base_dir
    else:
        adapter_arg = args.adapter
        if not adapter_arg.is_absolute():
            candidate = spolecne.trenink_dir(data_root) / adapter_arg
            adapter_dir = candidate if candidate.exists() else adapter_arg.resolve()
        else:
            adapter_dir = adapter_arg
        inferred = adapter_dir.parent.name if adapter_dir.name == "adapter" else adapter_dir.name
        run_name = args.name or inferred or "osobni"
        run_dir = spolecne.runs_dir(data_root) / run_name
        hf_input_dir = _merge_adapter(base_dir, adapter_dir, run_dir / "merged")

    convert_py, whisper_tools_dir = ensure_tools(data_root)

    f16_tmp_dir = run_dir / "_f16_tmp"
    shutil.rmtree(f16_tmp_dir, ignore_errors=True)
    f16_tmp_dir.mkdir(parents=True, exist_ok=True)

    cmd_convert = [
        sys.executable,
        str(convert_py),
        str(hf_input_dir),
        str(whisper_tools_dir),
        str(f16_tmp_dir),
    ]
    print("Spouštím převod do f16 ggml...")
    subprocess.run(cmd_convert, check=True)

    raw_f16 = f16_tmp_dir / "ggml-model.bin"
    if not raw_f16.is_file():
        bins = list(f16_tmp_dir.glob("*.bin"))
        if not bins:
            raise FileNotFoundError(f"convert-h5-to-ggml.py nevytvořil žádný .bin v {f16_tmp_dir}")
        raw_f16 = bins[0]

    f16_bin = run_dir / f"ggml-{run_name}-f16.bin"
    shutil.move(str(raw_f16), str(f16_bin))
    shutil.rmtree(f16_tmp_dir, ignore_errors=True)
    verify_ggml_magic(f16_bin)

    if not args.only_q8:
        q5_bin = run_dir / f"ggml-{run_name}-q5_0.bin"
        print(f"Kvantizuji q5_0 -> {q5_bin.name}...")
        subprocess.run([str(quantize_exe), str(f16_bin), str(q5_bin), "q5_0"], check=True)
        verify_ggml_magic(q5_bin)

    if args.q8 or args.only_q8:
        q8_bin = run_dir / f"ggml-{run_name}-q8_0.bin"
        print(f"Kvantizuji q8_0 -> {q8_bin.name}...")
        subprocess.run([str(quantize_exe), str(f16_bin), str(q8_bin), "q8_0"], check=True)
        verify_ggml_magic(q8_bin)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
