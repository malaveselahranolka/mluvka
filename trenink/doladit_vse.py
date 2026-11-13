"""Orchestrátor doladění modelu na hlase uživatele (bez importu torch).

Spouštět v aplikačním venv (`DATA/venv/Scripts/python.exe`).
Spouští podprocesy v `venv-train` a aplikačním `venv` a zapisuje průběžný stav
do `DATA/trenink/doladeni/stav.json`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mluvka import config
from trenink import spolecne

ERR_CHYBI_PROSTREDI = (
    "Chybí tréninkové prostředí. Postup je v README v sekci Doladění modelu."
)
ERR_LOW_VRAM = (
    "Grafická karta nemá dost volné paměti. Zavři programy, které ji používají, a zkus to znovu."
)

_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TOTAL_STEPS_RE = re.compile(r"\btotal_steps\s*=\s*(\d+)\b")
_KROK_LINE_RE = re.compile(
    r"\bKrok\s+(\d+)\s*/\s*(\d+)\b"
    r"(?:.*?loss\s*=\s*([-+]?\d+(?:[.,]\d+)?))?"
    r"(?:.*?([-+]?\d+(?:[.,]\d+)?)\s*s/krok)?"
    r"(?:.*?ETA\s+([^|\r\n]+))?",
    re.IGNORECASE,
)
_ETA_H_RE = re.compile(r"(\d+)\s*h\b")
_ETA_MIN_RE = re.compile(r"(\d+)\s*(?:min|m)\b")
_ETA_S_RE = re.compile(r"(\d+)\s*s\b")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def default_run_name(now: datetime | None = None) -> str:
    dt = now or datetime.now().astimezone()
    return f"osobni-{dt.strftime('%Y%m%d-%H%M')}"


def validate_run_name(name: str) -> str:
    clean = str(name or "").strip()
    if not clean or ".." in clean or not _RUN_NAME_RE.match(clean):
        raise ValueError(f"Neplatný název běhu: {name!r}")
    return clean


def parse_eta_seconds(raw_eta: str | None) -> int | None:
    if not raw_eta:
        return None
    text = str(raw_eta).strip()
    if not text:
        return None
    total = 0
    matched = False
    for regex, mult in ((_ETA_H_RE, 3600), (_ETA_MIN_RE, 60), (_ETA_S_RE, 1)):
        m = regex.search(text)
        if m:
            total += int(m.group(1)) * mult
            matched = True
    return total if matched else None


def parse_total_steps(line: str) -> int | None:
    m = _TOTAL_STEPS_RE.search(line)
    if not m:
        return None
    val = int(m.group(1))
    return val if val > 0 else None


def parse_log_line(line: str) -> dict[str, Any] | None:
    """Rozparsuje řádek `Krok a/b | loss=x | s/krok | ETA ...` z doladit.py."""
    m = _KROK_LINE_RE.search(line)
    if not m:
        return None
    krok = int(m.group(1))
    celkem = int(m.group(2))
    loss_raw = m.group(3)
    s_krok_raw = m.group(4)
    eta_raw = m.group(5)

    loss = float(loss_raw.replace(",", ".")) if loss_raw is not None else None
    s_krok = float(s_krok_raw.replace(",", ".")) if s_krok_raw is not None else None
    eta_s = parse_eta_seconds(eta_raw)
    if eta_s is None and s_krok is not None and celkem >= krok:
        eta_s = int(round(s_krok * max(0, celkem - krok)))

    return {
        "krok": krok,
        "celkem": celkem,
        "loss": loss,
        "s_krok": s_krok,
        "eta_s": eta_s,
    }


def evaluate_verdict(pred: dict[str, Any], po: dict[str, Any]) -> bool:
    """True pokud vety_test WER klesl alespoň o 10 % relativně a fleurs se nezhoršil o více než 1.0 pt."""
    try:
        pred_vety = float(pred["vety_test"])
        po_vety = float(po["vety_test"])
        pred_fleurs = float(pred["fleurs"])
        po_fleurs = float(po["fleurs"])
    except (KeyError, TypeError, ValueError):
        return False

    if not all(math.isfinite(x) for x in (pred_vety, po_vety, pred_fleurs, po_fleurs)):
        return False
    if pred_vety <= 0.0:
        return False

    rel_gain = (pred_vety - po_vety) / pred_vety
    fleurs_delta = po_fleurs - pred_fleurs
    return (rel_gain >= 0.10 - 1e-9) and (fleurs_delta <= 0.01 + 1e-9)


def build_vysledek(
    rows: list[dict[str, Any]],
    *,
    pred_model_name: str,
    po_model_name: str,
    start_index: int = 0,
) -> dict[str, Any]:
    """Vytáhne čerstvé řádky obou modelů z `vysledky.json` a vyhodnotí verdikt."""
    candidate_rows = rows[max(0, start_index):]
    if not candidate_rows:
        candidate_rows = rows

    found: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidate_rows:
        if not isinstance(row, dict):
            continue
        model = str(row.get("model", "") or "")
        sada = str(row.get("sada", "") or "")
        if model in (pred_model_name, po_model_name) and sada in ("vety_test", "fleurs"):
            found[(model, sada)] = row

    required = [
        (pred_model_name, "vety_test"),
        (pred_model_name, "fleurs"),
        (po_model_name, "vety_test"),
        (po_model_name, "fleurs"),
    ]
    missing = [pair for pair in required if pair not in found]
    if missing:
        raise RuntimeError(f"Ve vysledky.json chybí výsledky A/B měření: {missing}")

    pred_vety = found[(pred_model_name, "vety_test")]
    pred_fleurs = found[(pred_model_name, "fleurs")]
    po_vety = found[(po_model_name, "vety_test")]
    po_fleurs = found[(po_model_name, "fleurs")]

    pred = {
        "vety_test": float(pred_vety["wer"]),
        "fleurs": float(pred_fleurs["wer"]),
    }
    po = {
        "vety_test": float(po_vety["wer"]),
        "fleurs": float(po_fleurs["wer"]),
    }
    lepsi = evaluate_verdict(pred, po)
    n_vety = int(po_vety.get("n") or pred_vety.get("n") or 0)
    return {
        "pred": pred,
        "po": po,
        "n_vety": n_vety,
        "lepsi": lepsi,
        "model_soubor": po_model_name if lepsi else None,
    }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(src, tmp)
        for attempt in range(5):
            try:
                os.replace(tmp, dst)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _read_vysledky_rows(data_root: Path) -> list[dict[str, Any]]:
    path = spolecne.vysledky_path(data_root)
    if not path.is_file():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, list) else []
    except (OSError, ValueError):
        return []


def _cleanup_run_intermediates(run_dir: Path, run_name: str) -> None:
    shutil.rmtree(run_dir / "merged", ignore_errors=True)
    shutil.rmtree(run_dir / "_f16_tmp", ignore_errors=True)
    (run_dir / f"ggml-{run_name}-f16.bin").unlink(missing_ok=True)
    (run_dir / f"ggml-{run_name}-q5_0.bin").unlink(missing_ok=True)


def _kill_proc_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _resolve_app_python(data_root: Path) -> Path:
    venv_py = data_root / "venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        return venv_py
    cur = Path(sys.executable)
    if cur.name.lower() == "pythonw.exe":
        alt = cur.with_name("python.exe")
        if alt.is_file():
            return alt
    return cur


class _StopRequested(Exception):
    pass


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Kompletní orchestrátor doladění modelu Mluvky.")
    parser.add_argument("--name", default=None, help="Název běhu (výchozí: osobni-YYYYMMDD-HHMM).")
    parser.add_argument("--cv-limit", type=int, default=1000, help="Maximální počet CV klipů (výchozí: 1000).")
    parser.add_argument("--max-steps", type=int, default=None, help="Volitelný limit kroků optimalizátoru pro testy.")
    parser.add_argument("--data", type=Path, default=None, help="Přepíše DATA adresář.")
    args = parser.parse_args()

    data_root = spolecne.data_dir(args.data)

    try:
        raw_name = (
            args.name
            if args.name is not None
            else spolecne.unique_run_name(default_run_name(), data_root)
        )
        run_name = validate_run_name(raw_name)
    except ValueError as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 1

    if int(args.cv_limit) < 0:
        print("Chyba: --cv-limit nesmí být záporné.", file=sys.stderr)
        return 1
    stav_path = spolecne.doladeni_stav_path(data_root)
    log_tail: deque[str] = deque(maxlen=8)

    zacatek = _now_iso()
    if stav_path.is_file():
        try:
            prev = json.loads(stav_path.read_text(encoding="utf-8"))
            if (
                isinstance(prev, dict)
                and prev.get("pid") == os.getpid()
                and prev.get("name") == run_name
                and prev.get("zacatek")
            ):
                zacatek = str(prev["zacatek"])
        except (OSError, ValueError):
            pass

    state: dict[str, Any] = {
        "stav": "bezi",
        "faze": "priprava",
        "krok": 0,
        "celkem": 0,
        "loss": None,
        "eta_s": None,
        "pid": os.getpid(),
        "name": run_name,
        "zacatek": zacatek,
        "konec": None,
        "log_tail": [],
        "vysledek": None,
        "chyba": None,
    }

    def save_state() -> None:
        state["log_tail"] = list(log_tail)
        spolecne.atomic_write_text(
            stav_path,
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )

    save_state()

    stop_flag = False
    active_proc: subprocess.Popen[str] | None = None

    def _on_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop_flag
        stop_flag = True
        if active_proc is not None:
            _kill_proc_tree(active_proc)

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _on_signal)
            except (OSError, ValueError):
                pass

    venv_train_py = data_root / "venv-train" / "Scripts" / "python.exe"
    base_model_file = spolecne.base_model_dir(data_root) / "model.safetensors"
    if not venv_train_py.is_file() or not base_model_file.is_file():
        log_tail.append(ERR_CHYBI_PROSTREDI)
        state["stav"] = "chyba"
        state["chyba"] = ERR_CHYBI_PROSTREDI
        state["konec"] = _now_iso()
        save_state()
        print(ERR_CHYBI_PROSTREDI, file=sys.stderr)
        return 1

    app_py = _resolve_app_python(data_root)
    run_dir = spolecne.runs_dir(data_root) / run_name
    models_out_dir = spolecne.models_dir(data_root)
    target_model_name = f"ggml-{run_name}-q8_0.bin"
    copied_model_path = models_out_dir / target_model_name
    copied_by_us = False

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["MLUVKA_DATA"] = str(data_root)

    def run_step(
        faze: str,
        cmd: list[str],
        *,
        parse_training: bool = False,
    ) -> int:
        nonlocal active_proc
        if stop_flag:
            raise _StopRequested()
        state["faze"] = faze
        save_state()

        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env,
            creationflags=CREATE_NO_WINDOW,
        )
        active_proc = proc
        last_log_save = 0.0
        dirty_log = False
        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                stripped = line.strip()
                if stripped:
                    print(stripped, flush=True)
                    log_tail.append(stripped)
                    important_change = False
                    if parse_training:
                        total = parse_total_steps(stripped)
                        if total is not None:
                            state["celkem"] = total
                            important_change = True
                        parsed = parse_log_line(stripped)
                        if parsed is not None:
                            state["krok"] = parsed["krok"]
                            state["celkem"] = parsed["celkem"]
                            if parsed["loss"] is not None:
                                state["loss"] = round(float(parsed["loss"]), 4)
                            if parsed["eta_s"] is not None:
                                state["eta_s"] = int(parsed["eta_s"])
                            important_change = True
                    now_m = time.monotonic()
                    if important_change or (now_m - last_log_save) >= 0.5:
                        save_state()
                        last_log_save = now_m
                        dirty_log = False
                    else:
                        dirty_log = True
                if stop_flag:
                    _kill_proc_tree(proc)
                    raise _StopRequested()
            rc = proc.wait()
            if dirty_log:
                save_state()
            if stop_flag:
                raise _StopRequested()
            return int(rc)
        finally:
            active_proc = None
            if proc.poll() is None:
                _kill_proc_tree(proc)

    try:
        # 1. Příprava vět uživatele
        rc = run_step(
            "priprava",
            [
                str(venv_train_py),
                str(REPO_ROOT / "trenink" / "priprav_data.py"),
                "--jen-vety",
                "--data",
                str(data_root),
            ],
        )
        if rc != 0:
            state["stav"] = "chyba"
            state["chyba"] = "Příprava nahrávek selhala."
            state["konec"] = _now_iso()
            save_state()
            return rc or 1

        # 2. LoRA doladění
        train_cmd = [
            str(venv_train_py),
            str(REPO_ROOT / "trenink" / "doladit.py"),
            "--name",
            run_name,
            "--cv-limit",
            str(int(args.cv_limit)),
            "--data",
            str(data_root),
        ]
        if args.max_steps is not None:
            train_cmd.extend(["--max-steps", str(int(args.max_steps))])

        rc = run_step("doladeni", train_cmd, parse_training=True)
        if rc == 2:
            state["stav"] = "chyba"
            state["chyba"] = ERR_LOW_VRAM
            state["konec"] = _now_iso()
            save_state()
            return 2
        if rc != 0:
            state["stav"] = "chyba"
            state["chyba"] = "Doladění modelu selhalo."
            state["konec"] = _now_iso()
            save_state()
            return rc or 1

        # 3. Převod do ggml q8_0 a zkopírování do DATA/models/
        rc = run_step(
            "prevod",
            [
                str(venv_train_py),
                str(REPO_ROOT / "trenink" / "do_ggml.py"),
                "--adapter",
                str(run_dir / "adapter"),
                "--name",
                run_name,
                "--q8",
                "--only-q8",
                "--data",
                str(data_root),
            ],
        )
        if rc != 0:
            state["stav"] = "chyba"
            state["chyba"] = "Převod modelu do formátu ggml selhal."
            state["konec"] = _now_iso()
            save_state()
            return rc or 1

        q8_in_run = run_dir / target_model_name
        if not q8_in_run.is_file():
            state["stav"] = "chyba"
            state["chyba"] = "Chybí převedený model q8_0."
            state["konec"] = _now_iso()
            save_state()
            return 1

        # 4. Kontrola aktivního modelu a A/B měření přímo nad modelem v runs/
        cfg = config.load_config(data_root)
        beam = max(1, int(cfg.get("beam_size", 5) or 5))
        active_model_path = config.whisper_model_path(data_root)
        if not active_model_path.is_file():
            state["stav"] = "chyba"
            state["chyba"] = f"Chybí aktivní model pro srovnání ({active_model_path.name})."
            state["konec"] = _now_iso()
            save_state()
            return 1
        if copied_model_path.resolve() == active_model_path.resolve():
            state["stav"] = "chyba"
            state["chyba"] = "Název nového modelu koliduje s právě aktivním modelem."
            state["konec"] = _now_iso()
            save_state()
            return 1

        prev_vysledky_len = len(_read_vysledky_rows(data_root))
        rc = run_step(
            "mereni",
            [
                str(app_py),
                str(REPO_ROOT / "trenink" / "ab_test.py"),
                "--model",
                str(active_model_path),
                "--model",
                str(q8_in_run),
                "--sada",
                "vety_test",
                "--sada",
                "fleurs",
                "--beam",
                str(beam),
                "--data",
                str(data_root),
            ],
        )
        if rc != 0:
            state["stav"] = "chyba"
            state["chyba"] = "Srovnávací měření přesnosti selhalo."
            state["konec"] = _now_iso()
            save_state()
            return rc or 1

        # 5. Vyhodnocení výsledku — do DATA/models/ kopírujeme jen model, který je lepší
        vysledky_rows = _read_vysledky_rows(data_root)
        vysledek = build_vysledek(
            vysledky_rows,
            pred_model_name=active_model_path.name,
            po_model_name=copied_model_path.name,
            start_index=prev_vysledky_len,
        )
        if vysledek["lepsi"]:
            _atomic_copy(q8_in_run, copied_model_path)
            copied_by_us = True

        # 6. Úklid těžkých mezivýsledků (merged, f16, nepoužité q5_0)
        _cleanup_run_intermediates(run_dir, run_name)

        if state["celkem"]:
            state["krok"] = max(int(state["krok"] or 0), int(state["celkem"]))
        state["eta_s"] = 0
        state["vysledek"] = vysledek
        state["chyba"] = None
        state["stav"] = "hotovo"
        state["konec"] = _now_iso()
        save_state()
        return 0
    except _StopRequested:
        if copied_by_us:
            copied_model_path.unlink(missing_ok=True)
        state["stav"] = "zastaveno"
        state["chyba"] = None
        state["konec"] = _now_iso()
        save_state()
        return 0
    except Exception as exc:
        if copied_by_us:
            copied_model_path.unlink(missing_ok=True)
        log_tail.append(f"Chyba: {exc}")
        state["stav"] = "chyba"
        state["chyba"] = f"Doladění selhalo ({type(exc).__name__})."
        state["konec"] = _now_iso()
        save_state()
        return 1
    finally:
        if run_dir.exists():
            _cleanup_run_intermediates(run_dir, run_name)


if __name__ == "__main__":
    raise SystemExit(main())
