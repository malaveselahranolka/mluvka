"""Backend pro spuštění, sledování, zastavení a aktivaci doladění modelu z okna."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mluvka import config, trenink
from trenink import spolecne

POTREBA_VET = 50
ERR_NEOČEKAVANE = "Doladění neočekávaně skončilo."
ERR_SOUBOR_STAVU = "Stav doladění nelze ověřit (poškozený nebo zamčený soubor)."

_DOLADENI_LOCK = threading.RLock()
_KERNEL32: Any = None
_REPO_ROOT = Path(__file__).resolve().parent.parent
DOLADIT_VSE_SCRIPT = _REPO_ROOT / "trenink" / "doladit_vse.py"

DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compute_estimate_min(vety_train_count: int, cv_train_count: int) -> int:
    """Odhadne dobu doladění v minutách podle velikosti tréninkové dávky."""
    vety = max(0, int(vety_train_count))
    cv = max(0, int(cv_train_count))
    steps = (vety * 5 + min(1000, cv)) / 16.0
    return int(math.ceil(steps * 14.5 / 60.0)) + 8


def count_cv_train(data: Path | None = None) -> int:
    """Spočítá platné záznamy v `DATA/trenink/data/cv_train/manifest.tsv`."""
    data_root = spolecne.data_dir(data)
    manifest = spolecne.dataset_dir("cv_train", data_root) / "manifest.tsv"
    if not manifest.is_file():
        return 0
    return len(spolecne.read_manifest(manifest, resolve_paths=False))


def _default_run_name(now: datetime | None = None) -> str:
    dt = now or datetime.now().astimezone()
    return f"osobni-{dt.strftime('%Y%m%d-%H%M')}"


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


def _read_stav_file(stav_path: Path) -> tuple[dict[str, Any] | None, bool]:
    """Načte `stav.json`; vrátí `(data, soubor_existuje)` s krátkým opakováním při přechodné chybě."""
    if not stav_path.exists():
        return None, False
    for attempt in range(5):
        try:
            loaded = json.loads(stav_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded, True
            return None, True
        except FileNotFoundError:
            return None, False
        except (PermissionError, OSError, ValueError):
            if attempt < 4:
                time.sleep(0.03)
    return None, True


def _write_stav_file(stav_path: Path, payload: dict[str, Any]) -> None:
    spolecne.atomic_write_text(
        stav_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def is_process_alive(pid: Any) -> bool:
    """Ověří, zda proces s daným PID stále běží."""
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False

    if os.name != "nt":
        try:
            os.kill(pid_int, 0)
            return True
        except OSError:
            return False

    import ctypes
    from ctypes import wintypes

    global _KERNEL32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    ERROR_ACCESS_DENIED = 5
    STILL_ACTIVE = 259

    if _KERNEL32 is None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        _KERNEL32 = k32

    handle = _KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid_int)
    if not handle:
        err = ctypes.get_last_error()
        return err == ERROR_ACCESS_DENIED
    try:
        wait_res = _KERNEL32.WaitForSingleObject(handle, 0)
        if wait_res == WAIT_TIMEOUT:
            return True
        if wait_res == WAIT_OBJECT_0:
            return False
        exit_code = wintypes.DWORD()
        if _KERNEL32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return int(exit_code.value) == STILL_ACTIVE
        return False
    finally:
        _KERNEL32.CloseHandle(handle)


def doladeni_stav(
    data: Path | None = None,
    *,
    pid_alive_fn: Callable[[Any], bool] | None = None,
) -> dict[str, Any]:
    """Vrátí stav ze `stav.json`, nebo vypočtený klidový stav `nic`."""
    data_root = spolecne.data_dir(data)
    stav_path = spolecne.doladeni_stav_path(data_root)
    alive_check = pid_alive_fn or is_process_alive

    with _DOLADENI_LOCK:
        state, exists = _read_stav_file(stav_path)
        if state is None:
            if exists:
                return {
                    "stav": "chyba",
                    "faze": None,
                    "krok": 0,
                    "celkem": 0,
                    "loss": None,
                    "eta_s": None,
                    "pid": None,
                    "name": None,
                    "zacatek": None,
                    "konec": None,
                    "log_tail": [],
                    "vysledek": None,
                    "chyba": ERR_SOUBOR_STAVU,
                }
            nahrano = len(trenink.load_tsv(data_root))
            vety_train = nahrano - (nahrano // 5)
            cv_count = count_cv_train(data_root)
            return {
                "stav": "nic",
                "nahrano": nahrano,
                "potreba": POTREBA_VET,
                "odhad_min": compute_estimate_min(vety_train, cv_count),
            }

        if state.get("stav") == "bezi":
            pid = state.get("pid")
            if not alive_check(pid):
                # Znovu načíst před zápisem, aby se nepřepsal právě zapsaný stav hotovo/zastaveno.
                latest, latest_exists = _read_stav_file(stav_path)
                if latest is not None and (latest.get("stav") != "bezi" or latest.get("pid") != pid):
                    return latest
                if latest is None and not latest_exists:
                    nahrano = len(trenink.load_tsv(data_root))
                    vety_train = nahrano - (nahrano // 5)
                    return {
                        "stav": "nic",
                        "nahrano": nahrano,
                        "potreba": POTREBA_VET,
                        "odhad_min": compute_estimate_min(vety_train, count_cv_train(data_root)),
                    }
                target = dict(latest if latest is not None else state)
                target["stav"] = "chyba"
                target["chyba"] = ERR_NEOČEKAVANE
                if not target.get("konec"):
                    target["konec"] = _now_iso()
                _write_stav_file(stav_path, target)
                return target

        return state


def doladeni_spustit(
    data: Path | None = None,
    *,
    mereni_bezi: bool = False,
    popen_fn: Callable[..., Any] | None = None,
    pid_alive_fn: Callable[[Any], bool] | None = None,
    name: str | None = None,
    cv_limit: int = 1000,
    max_steps: int | None = None,
) -> dict[str, Any]:
    """Spustí orchestrátor `doladit_vse.py` jako oddělený proces a zapíše úvodní `stav.json`."""
    data_root = spolecne.data_dir(data)
    stav_path = spolecne.doladeni_stav_path(data_root)
    popen = popen_fn or subprocess.Popen

    with _DOLADENI_LOCK:
        raw_state, raw_exists = _read_stav_file(stav_path)
        if raw_state is None and raw_exists:
            return {"ok": False, "error": ERR_SOUBOR_STAVU}
        cur = doladeni_stav(data_root, pid_alive_fn=pid_alive_fn)
        if cur.get("stav") == "bezi":
            return {"ok": False, "error": "Doladění už běží."}
        if mereni_bezi:
            return {"ok": False, "error": "Nejdřív počkej, až skončí měření přesnosti."}

        nahrano = len(trenink.load_tsv(data_root))
        if nahrano < POTREBA_VET:
            return {
                "ok": False,
                "error": f"Pro doladění je potřeba alespoň {POTREBA_VET} nahraných vět (máš {nahrano}).",
            }

        run_name = name or spolecne.unique_run_name(_default_run_name(), data_root)
        app_py = _resolve_app_python(data_root)
        cmd = [
            str(app_py),
            str(DOLADIT_VSE_SCRIPT),
            "--name",
            run_name,
            "--cv-limit",
            str(int(cv_limit)),
            "--data",
            str(data_root),
        ]
        if max_steps is not None:
            cmd.extend(["--max-steps", str(int(max_steps))])

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["MLUVKA_DATA"] = str(data_root)

        creationflags = (
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            if os.name == "nt"
            else 0
        )

        proc = popen(
            cmd,
            cwd=str(_REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            env=env,
        )

        initial_state: dict[str, Any] = {
            "stav": "bezi",
            "faze": "priprava",
            "krok": 0,
            "celkem": 0,
            "loss": None,
            "eta_s": None,
            "pid": int(proc.pid),
            "name": run_name,
            "zacatek": _now_iso(),
            "konec": None,
            "log_tail": [],
            "vysledek": None,
            "chyba": None,
        }

        existing, _ = _read_stav_file(stav_path)
        if existing is not None and (
            existing.get("pid") == initial_state["pid"]
            or (
                existing.get("name") == run_name
                and existing.get("stav") in {"chyba", "hotovo", "zastaveno"}
            )
        ):
            return {"ok": True, **existing}

        _write_stav_file(stav_path, initial_state)
        return {"ok": True, **initial_state}


def doladeni_zastavit(
    data: Path | None = None,
    *,
    run_fn: Callable[..., Any] | None = None,
    pid_alive_fn: Callable[[Any], bool] | None = None,
) -> dict[str, Any]:
    """Ukončí běžící strom procesů doladění přes `taskkill /PID <pid> /T /F` a zapíše `stav=zastaveno`."""
    data_root = spolecne.data_dir(data)
    stav_path = spolecne.doladeni_stav_path(data_root)
    runner = run_fn or subprocess.run
    alive_check = pid_alive_fn or is_process_alive

    with _DOLADENI_LOCK:
        state, exists = _read_stav_file(stav_path)
        if state is None:
            if exists:
                return {"ok": False, "error": ERR_SOUBOR_STAVU}
            return {"ok": True, **doladeni_stav(data_root, pid_alive_fn=pid_alive_fn)}

        if state.get("stav") == "bezi":
            pid = state.get("pid")
            if pid:
                try:
                    pid_int = int(pid)
                except (TypeError, ValueError):
                    pid_int = 0
                if pid_int > 0:
                    if os.name == "nt":
                        runner(
                            ["taskkill", "/PID", str(pid_int), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW,
                        )
                    else:
                        try:
                            os.kill(pid_int, 15)
                        except OSError:
                            pass
                    deadline = time.monotonic() + 1.5
                    while time.monotonic() < deadline and alive_check(pid_int):
                        time.sleep(0.05)

            latest, _ = _read_stav_file(stav_path)
            if latest is not None and latest.get("stav") in {"hotovo", "chyba", "zastaveno"}:
                return {"ok": True, **latest}
            state = dict(latest if latest is not None else state)
            state["stav"] = "zastaveno"
            state["chyba"] = None
            state["konec"] = _now_iso()
            _write_stav_file(stav_path, state)

        return {"ok": True, **state}


def select_model(
    data: Path | None,
    cfg_store: config.ConfigStore,
    soubor: str,
) -> dict[str, Any]:
    """Sdílená validovaná cesta pro přepnutí `model_soubor` v config.json."""
    cfg_store.reload_if_changed()
    if cfg_store.config_invalid:
        raise config.ConfigInvalidError(config.CONFIG_INVALID_MESSAGE)
    name = config.validate_model_soubor(soubor)
    available = trenink.list_models(data)
    if not any(m["soubor"] == name for m in available):
        return {"ok": False, "error": f"Model „{name}“ není mezi dostupnými modely."}
    cfg_store.set_value("model_soubor", name)
    info = trenink.get_models_state(data, selected_soubor=name, beam=cfg_store.get("beam_size"))
    cfg = cfg_store.snapshot()
    cfg.pop("claude_api_klic", None)
    return {"ok": True, **info, "config": cfg}


def doladeni_pouzit(
    data: Path | None = None,
    *,
    cfg_store: config.ConfigStore | None = None,
) -> dict[str, Any]:
    """Aktivuje doladěný model, pouze pokud je `vysledek.lepsi` a soubor existuje v DATA/models."""
    data_root = spolecne.data_dir(data)
    with _DOLADENI_LOCK:
        state = doladeni_stav(data_root)
        if state.get("stav") != "hotovo":
            return {"ok": False, "error": "Doladění ještě není hotové."}
        vysledek = state.get("vysledek")
        if not isinstance(vysledek, dict) or not bool(vysledek.get("lepsi")):
            return {"ok": False, "error": "Doladěný model nepřinesl potřebné zlepšení."}

        raw_soubor = vysledek.get("model_soubor")
        try:
            name = config.validate_model_soubor(raw_soubor)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        model_path = spolecne.models_dir(data_root) / name
        if not model_path.is_file():
            return {"ok": False, "error": f"Model „{name}“ v složce models chybí."}

        store = cfg_store or config.ConfigStore(data_root)
        return select_model(data_root, store, name)
