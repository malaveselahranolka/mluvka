"""LoRA doladění modelu CzechSpeech (Whisper) v bfloat16 na RTX 4050 (6 GB VRAM).

Spouštět v tréninkovém venv (`DATA/venv-train/Scripts/python.exe`).
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import torch
from peft import LoraConfig, get_peft_model
from torch.utils.data import DataLoader, Dataset
from transformers import (
    WhisperFeatureExtractor,
    WhisperForConditionalGeneration,
    WhisperTokenizer,
    get_linear_schedule_with_warmup,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trenink import spolecne

MIN_FREE_VRAM_GB = 3.5


def _format_eta(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        return "?"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return f"{h:d}h {m:02d}m {sec:02d}s"
    return f"{m:d}m {sec:02d}s"


def _load_wav_mono_f32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != spolecne.TARGET_SAMPLE_RATE:
            raise ValueError(f"{path} není 16 kHz mono PCM16 WAV.")
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


class ManifestWhisperDataset(Dataset):
    """Dataset z manifest.tsv s extrakcí 128 mel kanálů (padded na 3000)."""

    def __init__(
        self,
        entries: list[dict[str, Any]],
        feature_extractor: WhisperFeatureExtractor,
        tokenizer: WhisperTokenizer,
        *,
        decoder_start_token_id: int,
        max_target_positions: int,
    ) -> None:
        self.entries = entries
        self.feature_extractor = feature_extractor
        self.tokenizer = tokenizer
        self.decoder_start_token_id = int(decoder_start_token_id)
        self.max_target_positions = int(max_target_positions)

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, list[int]]:
        row = self.entries[idx]
        audio = _load_wav_mono_f32(Path(row["wav_path"]))
        feats = self.feature_extractor(
            audio,
            sampling_rate=spolecne.TARGET_SAMPLE_RATE,
            return_tensors="pt",
        ).input_features[0]

        token_ids: list[int] = list(self.tokenizer(str(row["text"])).input_ids)
        if token_ids and token_ids[0] == self.decoder_start_token_id:
            token_ids = token_ids[1:]
        if len(token_ids) > self.max_target_positions:
            eos = self.tokenizer.eos_token_id
            if eos is not None and self.max_target_positions >= 1:
                token_ids = token_ids[: self.max_target_positions - 1] + [int(eos)]
            else:
                token_ids = token_ids[: self.max_target_positions]
        return feats, token_ids


def _collate_batch(batch: list[tuple[torch.Tensor, list[int]]]) -> dict[str, torch.Tensor]:
    input_features = torch.stack([item[0] for item in batch], dim=0)
    max_len = max(len(item[1]) for item in batch)
    labels = torch.full((len(batch), max_len), -100, dtype=torch.long)
    for row_i, (_feats, ids) in enumerate(batch):
        labels[row_i, : len(ids)] = torch.tensor(ids, dtype=torch.long)
    return {
        "input_features": input_features,
        "labels": labels,
    }


def _build_training_entries(
    data_root: Path,
    vety_mult: int = 5,
    diktaty_mult: int = 3,
    cv_limit: int | None = None,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], int, int, int]:
    return spolecne.build_combined_training_entries(
        data_root,
        vety_mult=vety_mult,
        diktaty_mult=diktaty_mult,
        cv_limit=cv_limit,
        seed=seed,
    )


def _check_vram_before_start() -> tuple[float, float]:
    if not torch.cuda.is_available():
        print(
            "Chyba: CUDA GPU není dostupná — LoRA trénink vyžaduje NVIDIA GPU.",
            file=sys.stderr,
        )
        sys.exit(2)
    free_b, total_b = torch.cuda.mem_get_info()
    free_gb = free_b / float(1024**3)
    total_gb = total_b / float(1024**3)
    if free_gb < MIN_FREE_VRAM_GB:
        print(
            f"Chyba: Na GPU je volných jen {free_gb:.2f} GB z {total_gb:.2f} GB "
            f"(minimum pro trénink je {MIN_FREE_VRAM_GB:.1f} GB). "
            "Paměť pravděpodobně drží běžící Mluvka (whisper-server.exe) — ukonči ji a spusť trénink znovu.",
            file=sys.stderr,
        )
        sys.exit(2)
    return free_gb, total_gb


class _ResetAccumForOOM(Exception):
    pass


def _is_cuda_oom(exc: BaseException) -> bool:
    if isinstance(exc, torch.cuda.OutOfMemoryError):
        return True
    msg = str(exc).lower()
    return "out of memory" in msg or "cuda error: out of memory" in msg


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="LoRA doladění modelu CzechSpeech na vlastních a CV datech.")
    parser.add_argument("--name", default="osobni", help="Název běhu (uloží se do runs/<name>).")
    parser.add_argument("--batch", type=int, default=4, help="Velikost micro-batche (výchozí: 4, při OOM spadne na 2).")
    parser.add_argument("--accum", type=int, default=4, help="Počet kroků akumulace gradientů (výchozí: 4).")
    parser.add_argument("--epochs", type=int, default=1, help="Počet epoch (výchozí: 1).")
    parser.add_argument("--max-steps", type=int, default=None, help="Volitelný limit kroků optimalizátoru.")
    parser.add_argument("--lr", type=float, default=1e-4, help="Počáteční learning rate (výchozí: 1e-4).")
    parser.add_argument("--vety-mult", type=int, default=5, help="Převzorkování vety_train (výchozí: 5).")
    parser.add_argument("--diktaty-mult", type=int, default=3, help="Převzorkování diktaty_train (výchozí: 3).")
    parser.add_argument("--cv-limit", type=int, default=None, help="Maximální počet náhodně vybraných klipů z cv_train (výchozí: vše, 0 = žádný).")
    parser.add_argument("--seed", type=int, default=42, help="Náhodné semínko.")
    parser.add_argument("--data", type=Path, default=None, help="Přepíše DATA adresář.")
    args = parser.parse_args()

    if args.cv_limit is not None and args.cv_limit < 0:
        print("Chyba: --cv-limit nesmí být záporné.", file=sys.stderr)
        return 1
    if args.vety_mult < 0 or args.diktaty_mult < 0:
        print("Chyba: --vety-mult a --diktaty-mult nesmí být záporné.", file=sys.stderr)
        return 1

    free_gb, total_gb = _check_vram_before_start()
    data_root = spolecne.data_dir(args.data)
    base_dir = spolecne.base_model_dir(data_root)
    if not (base_dir / "model.safetensors").is_file():
        print(f"Chyba: Chybí základní HF model v {base_dir}", file=sys.stderr)
        return 1

    entries, cv_cnt, vety_cnt, diktaty_cnt = _build_training_entries(
        data_root,
        vety_mult=args.vety_mult,
        diktaty_mult=args.diktaty_mult,
        cv_limit=args.cv_limit,
        seed=args.seed,
    )
    if not entries:
        print("Chyba: V manifestech cv_train, vety_train ani diktaty_train nejsou žádná data.", file=sys.stderr)
        return 1

    run_dir = spolecne.runs_dir(data_root) / args.name
    adapter_dir = run_dir / "adapter"
    log_path = run_dir / "train.log"
    run_dir.mkdir(parents=True, exist_ok=True)

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)
    if hasattr(torch.backends.cuda, "matmul"):
        torch.backends.cuda.matmul.allow_tf32 = True
    torch.cuda.reset_peak_memory_stats()

    log_file = log_path.open("a", encoding="utf-8", buffering=1)
    model: Any = None

    def log_line(msg: str) -> None:
        print(msg, flush=True)
        log_file.write(msg + "\n")
        log_file.flush()

    def save_adapter(peft_model: Any, tag: str) -> None:
        adapter_dir.mkdir(parents=True, exist_ok=True)
        peft_model.save_pretrained(str(adapter_dir))
        log_line(f"Adaptér uložen ({tag}) -> {adapter_dir}")

    try:
        log_line(
            f"Start tréninku '{args.name}': volná VRAM {free_gb:.2f}/{total_gb:.2f} GB | "
            f"cv_train={cv_cnt}, vety_train={vety_cnt} (x{args.vety_mult}), "
            f"diktaty_train={diktaty_cnt} (x{args.diktaty_mult}) -> celkem {len(entries)} položek"
        )

        feature_extractor = WhisperFeatureExtractor.from_pretrained(str(base_dir))
        tokenizer = WhisperTokenizer.from_pretrained(
            str(base_dir),
            language="czech",
            task="transcribe",
            predict_timestamps=False,
        )

        base_model = WhisperForConditionalGeneration.from_pretrained(
            str(base_dir),
            torch_dtype=torch.bfloat16,
        )
        base_model.config.forced_decoder_ids = None
        base_model.config.use_cache = False
        base_model.config.apply_spec_augment = True
        base_model.config.mask_time_prob = 0.05
        base_model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        base_model.enable_input_require_grads()

        lora_cfg = LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            bias="none",
            target_modules=["q_proj", "k_proj", "v_proj", "out_proj", "fc1", "fc2"],
        )
        model = get_peft_model(base_model, lora_cfg, autocast_adapter_dtype=True)
        model.to("cuda")
        model.train()

        trainable_params = [p for p in model.parameters() if p.requires_grad]
        n_train = sum(p.numel() for p in trainable_params)
        n_all = sum(p.numel() for p in model.parameters())
        log_line(
            f"LoRA parametry: {n_train:,} trénovatelných z {n_all:,} ({100.0 * n_train / max(1, n_all):.2f} %)"
        )

        dataset = ManifestWhisperDataset(
            entries,
            feature_extractor,
            tokenizer,
            decoder_start_token_id=int(model.config.decoder_start_token_id),
            max_target_positions=int(getattr(model.config, "max_target_positions", 448)),
        )

        current_batch = max(1, int(args.batch))
        accum_steps = max(1, int(args.accum))

        if args.max_steps is not None:
            total_steps = max(1, int(args.max_steps))
        else:
            batches_per_epoch = math.ceil(len(dataset) / current_batch)
            steps_per_epoch = max(1, math.ceil(batches_per_epoch / accum_steps))
            total_steps = steps_per_epoch * max(1, int(args.epochs))

        warmup_steps = int(round(0.05 * total_steps))
        optimizer = torch.optim.AdamW(trainable_params, lr=float(args.lr))
        scheduler = get_linear_schedule_with_warmup(
            optimizer,
            num_warmup_steps=warmup_steps,
            num_training_steps=total_steps,
        )

        log_line(
            f"Plán: total_steps={total_steps}, warmup={warmup_steps}, batch={current_batch}, "
            f"accum={accum_steps}, lr={args.lr}"
        )

        opt_step = 0
        micro_in_accum = 0
        accum_loss = 0.0
        step_times: list[float] = []
        t_step_start = time.monotonic()
        epoch = 0

        def log_step(step_loss_val: float, avg_step_s: float) -> None:
            eta_s = avg_step_s * max(0, total_steps - opt_step)
            cur_lr = float(scheduler.get_last_lr()[0])
            log_line(
                f"Krok {opt_step}/{total_steps} | loss={step_loss_val:.4f} | "
                f"{avg_step_s:.2f} s/krok | ETA {_format_eta(eta_s)} | lr={cur_lr:.2e}"
            )

        def _make_loader(bs: int, ep: int) -> DataLoader:
            gen = torch.Generator()
            gen.manual_seed(args.seed + ep)
            return DataLoader(
                dataset,
                batch_size=bs,
                shuffle=True,
                collate_fn=_collate_batch,
                generator=gen,
                num_workers=0,
            )

        loader_bs = max(1, int(args.batch))

        def _forward_backward(
            batch_dict: dict[str, torch.Tensor],
            loss_scale: float,
            *,
            can_retry_in_place: bool,
        ) -> float:
            nonlocal current_batch
            try:
                feats = batch_dict["input_features"].to("cuda", dtype=torch.bfloat16)
                labels = batch_dict["labels"].to("cuda")
                with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                    out = model(input_features=feats, labels=labels)
                    raw_loss = out.loss
                    scaled_loss = raw_loss / loss_scale
                scaled_loss.backward()
                return float(raw_loss.detach().item())
            except RuntimeError as exc:
                if not _is_cuda_oom(exc) or current_batch <= 1:
                    raise
                bs_actual = int(batch_dict["input_features"].shape[0])
                new_bs = 2 if current_batch >= 4 else 1
                if bs_actual <= new_bs:
                    new_bs = 1
                if bs_actual <= new_bs:
                    raise
                optimizer.zero_grad(set_to_none=True)
                torch.cuda.empty_cache()
                current_batch = new_bs
                if not can_retry_in_place:
                    raise _ResetAccumForOOM()
                log_line(f"Varování: CUDA OOM — snižuji micro-batch na {current_batch} a opakuji dávku.")
                part_losses: list[float] = []
                for start in range(0, bs_actual, new_bs):
                    sub = {
                        "input_features": batch_dict["input_features"][start : start + new_bs],
                        "labels": batch_dict["labels"][start : start + new_bs],
                    }
                    part_weight = float(sub["input_features"].shape[0]) / float(bs_actual)
                    sub_loss_val = _forward_backward(
                        sub,
                        loss_scale / part_weight,
                        can_retry_in_place=(start == 0),
                    )
                    part_losses.append(sub_loss_val * part_weight)
                return sum(part_losses)

        optimizer.zero_grad(set_to_none=True)

        while opt_step < total_steps and (args.max_steps is not None or epoch < max(1, int(args.epochs))):
            epoch += 1
            loader = _make_loader(loader_bs, epoch)
            for batch_dict in loader:
                if opt_step >= total_steps:
                    break
                bs_now = int(batch_dict["input_features"].shape[0])
                if bs_now > current_batch:
                    sub_chunks = [
                        {
                            "input_features": batch_dict["input_features"][i : i + current_batch],
                            "labels": batch_dict["labels"][i : i + current_batch],
                        }
                        for i in range(0, bs_now, current_batch)
                    ]
                else:
                    sub_chunks = [batch_dict]

                try:
                    for chunk_idx, chunk in enumerate(sub_chunks):
                        if opt_step >= total_steps:
                            break
                        chunk_scale = float(accum_steps * len(sub_chunks))
                        loss_val = _forward_backward(
                            chunk,
                            chunk_scale,
                            can_retry_in_place=(micro_in_accum == 0 and chunk_idx == 0),
                        )
                        accum_loss += loss_val / float(len(sub_chunks))
                    micro_in_accum += 1
                except _ResetAccumForOOM:
                    micro_in_accum = 0
                    accum_loss = 0.0
                    log_line(f"Varování: CUDA OOM — snižuji micro-batch na {current_batch} a restartuji akumulaci kroku.")
                    continue

                if micro_in_accum >= accum_steps:
                    torch.nn.utils.clip_grad_norm_(trainable_params, 1.0)
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)

                    opt_step += 1
                    step_dur = time.monotonic() - t_step_start
                    t_step_start = time.monotonic()
                    step_times.append(step_dur)
                    if len(step_times) > 20:
                        step_times.pop(0)
                    avg_s = sum(step_times) / len(step_times)
                    step_loss = accum_loss / float(micro_in_accum)
                    accum_loss = 0.0
                    micro_in_accum = 0

                    log_step(step_loss, avg_s)
                    if opt_step % 500 == 0 and opt_step < total_steps:
                        save_adapter(model, f"krok {opt_step}")

        if args.max_steps is None and micro_in_accum > 0 and opt_step < total_steps:
            scale_fix = float(accum_steps) / float(micro_in_accum)
            if scale_fix != 1.0:
                for p in trainable_params:
                    if p.grad is not None:
                        p.grad.mul_(scale_fix)
            torch.nn.utils.clip_grad_norm_(trainable_params, 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)

            opt_step += 1
            step_dur = time.monotonic() - t_step_start
            t_step_start = time.monotonic()
            step_times.append(step_dur)
            avg_s = sum(step_times) / len(step_times)
            step_loss = accum_loss / float(micro_in_accum)
            accum_loss = 0.0
            micro_in_accum = 0
            log_step(step_loss, avg_s)

        save_adapter(model, "konec")
        peak_gb = torch.cuda.max_memory_allocated() / float(1024**3)
        avg_final = (sum(step_times) / len(step_times)) if step_times else 0.0
        log_line(f"Trénink dokončen: kroky={opt_step}, průměr={avg_final:.2f} s/krok, VRAM špička={peak_gb:.2f} GB")
        return 0
    except KeyboardInterrupt:
        log_line("\nPřerušeno uživatelem (Ctrl+C) — ukládám adaptér...")
        if model is not None:
            save_adapter(model, "ctrl-c")
        peak_gb = torch.cuda.max_memory_allocated() / float(1024**3)
        log_line(f"Uloženo po přerušení, VRAM špička={peak_gb:.2f} GB")
        return 130
    except Exception as exc:
        if _is_cuda_oom(exc):
            log_line(f"Chyba VRAM (CUDA OOM): {exc}")
            return 2
        log_line(f"Chyba tréninku: {exc}")
        return 1
    finally:
        log_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
