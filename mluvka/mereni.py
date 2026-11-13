"""Stránka Model: setupy (model × beam × Haiku), jejich měření na tomto PC a podklady pro UI."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import subprocess
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from mluvka import config, hodnoceni, wer

N_CV = 100
N_KUSU_RYCHLOST = 3
TICHO_S = 0.4
BEAM_POVOLENE = (1, 5, 8)
_LOCK = threading.Lock()  # jen jedno měření najednou v rámci procesu


class Zastaveno(Exception):
    pass


# ---------- cesty a soubory ----------

def model_dir(data: Path | None = None) -> Path:
    return (data or config.data_dir()) / "model"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


# ---------- setupy ----------

def setup_id(model_soubor: str, beam: int, haiku: bool) -> str:
    return f"{model_soubor}|b{int(beam)}|{'ai' if haiku else 'raw'}"


def _setup(model_soubor: str, beam: int, haiku: bool, vlastni: bool) -> dict[str, Any]:
    return {"id": setup_id(model_soubor, beam, haiku), "model_soubor": model_soubor, "beam": int(beam), "haiku": bool(haiku), "vlastni": vlastni}


def modely_soubory(data: Path | None = None) -> list[str]:
    d = config.models_dir_path(data)
    return sorted(p.name for p in d.glob("*.bin") if p.is_file()) if d.is_dir() else []


def vlastni_setupy(data: Path | None = None) -> list[dict[str, Any]]:
    raw = _read_json(model_dir(data) / "setupy.json", [])
    out = []
    for s in raw if isinstance(raw, list) else []:
        try:
            out.append(_setup(str(s["model_soubor"]), int(s["beam"]), bool(s["haiku"]), True))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def vsechny_setupy(data: Path | None = None, mereni: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Vestavěné + vlastní + změřené setupy, jejichž model soubor existuje (bez duplicit)."""
    files = modely_soubory(data)
    out: dict[str, dict[str, Any]] = {}
    for f in files:
        s = _setup(f, 5, False, False); out[s["id"]] = s
    for f, beam, haiku in (("ggml-czechspeech-q8_0.bin", 5, True), ("ggml-czechspeech-q5_0.bin", 1, False)):
        if f in files:
            s = _setup(f, beam, haiku, False); out.setdefault(s["id"], s)
    for s in vlastni_setupy(data):
        if s["model_soubor"] in files:
            out.setdefault(s["id"], s)
    for m in mereni or []:
        if m.get("model") in files:
            s = _setup(m["model"], int(m.get("beam", 5)), bool(m.get("haiku")), False)
            out.setdefault(s["id"], s)
    return list(out.values())


def pridat_setup(data: Path | None, model_soubor: str, beam: Any, haiku: Any) -> dict[str, Any]:
    name = config.validate_model_soubor(model_soubor)
    if name not in modely_soubory(data):
        raise ValueError("Tenhle model ve složce models není.")
    try:
        beam_i = int(beam)
    except (TypeError, ValueError):
        raise ValueError("Beam musí být 1, 5 nebo 8.") from None
    if beam_i not in BEAM_POVOLENE or isinstance(beam, bool):
        raise ValueError("Beam musí být 1, 5 nebo 8.")
    new = _setup(name, beam_i, bool(haiku), True)
    if any(s["id"] == new["id"] for s in vsechny_setupy(data, nacti_mereni(data))):
        raise ValueError("Tohle nastavení už v tabulce je.")
    path = model_dir(data) / "setupy.json"
    raw = _read_json(path, [])
    raw = raw if isinstance(raw, list) else []
    raw.append({"model_soubor": name, "beam": beam_i, "haiku": bool(haiku)})
    _write_json(path, raw)
    return new


def odebrat_setup(data: Path | None, sid: str) -> None:
    path = model_dir(data) / "setupy.json"
    raw = _read_json(path, [])
    raw = raw if isinstance(raw, list) else []
    keep = [s for s in raw if isinstance(s, dict) and setup_id(str(s.get("model_soubor")), int(s.get("beam", 0) or 0), bool(s.get("haiku"))) != sid]
    if len(keep) == len(raw):
        raise ValueError("Odebrat jde jen vlastní nastavení.")
    _write_json(path, keep)


# ---------- měření (soubor) ----------

def nacti_mereni(data: Path | None = None) -> list[dict[str, Any]]:
    # Jen měření z téhle stránky: stejné sady i nápověda jako v aplikaci, aby šly známky porovnat.
    raw = _read_json(model_dir(data) / "mereni.json", [])
    return raw if isinstance(raw, list) else []


def _pridej_mereni(data: Path | None, row: dict[str, Any]) -> None:
    rows = nacti_mereni(data)
    rows.append(row)
    _write_json(model_dir(data) / "mereni.json", rows)


def posledni_mereni(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Nejnovější měření pro každý setup; rychlost doplní z nejnovějšího měření, které ji má."""
    by: dict[str, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: str(r.get("datum", ""))):
        sid = r.get("setup_id")
        if not sid:
            continue
        prev = by.get(sid)
        merged = dict(r)
        if prev and merged.get("s_na_minutu") is None:
            merged["s_na_minutu"] = prev.get("s_na_minutu")
            merged["s_haiku_na_minutu"] = prev.get("s_haiku_na_minutu")
        by[sid] = merged
    return by


# ---------- počítač ----------

class _MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def stroj(data: Path | None = None) -> dict[str, str]:
    path = model_dir(data) / "stroj.json"
    cached = _read_json(path, None)
    if isinstance(cached, dict) and cached.get("gpu"):
        return cached
    gpu = "Bez grafické karty NVIDIA"
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True,
                           text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
        if line:
            name, mem = [x.strip() for x in line.split(",", 1)]
            gb = round(int(re.sub(r"\D", "", mem) or 0) / 1024)
            gpu = f"{name}, {gb} GB"
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    cpu = platform.processor() or "Neznámý procesor"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            cpu = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    ram = ""
    try:
        ms = _MEMSTATUS(); ms.dwLength = ctypes.sizeof(_MEMSTATUS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            ram = f"{round(ms.ullTotalPhys / 1024**3)} GB"
    except (AttributeError, OSError):
        pass
    info = {"gpu": gpu, "cpu": cpu, "ram": ram or "neznámá", "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "engine": "whisper.cpp b5454, CUDA 12.4"}
    try:
        _write_json(path, info)
    except OSError:
        pass
    return info


# ---------- informace o modelech ----------

def info_modelu(data: Path | None, soubor: str) -> dict[str, Any]:
    from mluvka import trenink  # lokálně: trenink importuje hodně věcí
    p = config.models_dir_path(data) / soubor
    m = re.search(r"(q\d_\d|f16|f32)", soubor)
    licence = "CC BY-NC 4.0" if re.search(r"czechspeech|cv1|osobni", soubor) else "neznámá"
    return {"soubor": soubor, "nazev": trenink._nazev_modelu(soubor, soubor == config.DEFAULT_MODEL_SOUBOR),
            "velikost_mb": round(p.stat().st_size / 1e6, 1) if p.is_file() else None,
            "kvantizace": m.group(1) if m else "?", "licence": licence}


# ---------- sestavení stavu pro UI ----------

def aktivni_setup(data: Path | None, cfg: dict[str, Any], ma_klic: bool) -> dict[str, Any]:
    try:
        model = config.whisper_model_path(data, model_soubor=cfg.get("model_soubor") or None).name
    except ValueError:
        model = config.DEFAULT_MODEL_SOUBOR
    try:
        beam = max(1, int(cfg.get("beam_size", 5)))
    except (TypeError, ValueError):
        beam = 5
    return _setup(model, beam, bool(cfg.get("ai_uprava", True)) and ma_klic, False)


def sestav_stav(data: Path | None, cfg: dict[str, Any], ma_klic: bool) -> dict[str, Any]:
    rows = nacti_mereni(data)
    last = posledni_mereni(rows)
    setupy = vsechny_setupy(data, rows)
    akt = aktivni_setup(data, cfg, ma_klic)
    if not any(s["id"] == akt["id"] for s in setupy):
        setupy.append(akt)
    nazvy = {f: info_modelu(data, f)["nazev"] for f in modely_soubory(data)}
    out = []
    for s in setupy:
        m = last.get(s["id"])
        pr = hodnoceni.prumer((m.get("cv") or {}).get("wer") if m else None, (m.get("fleurs") or {}).get("wer") if m else None) if m else None
        spm = m.get("s_na_minutu") if m else None
        rych = hodnoceni.rychlost(spm) if spm is not None else None
        out.append({**s, "nazev_modelu": nazvy.get(s["model_soubor"], s["model_soubor"]), "mereni": m, "prumer": pr,
                    "znamka": hodnoceni.znamka(pr) if pr is not None else None,
                    "s_na_minutu": spm, "rychlost": rych, "carky": hodnoceni.pocet_carek(rych) if rych else None,
                    "aktivni": s["id"] == akt["id"], "pouzitelny": ma_klic or not s["haiku"]})
    out.sort(key=lambda s: (not s["aktivni"], s["prumer"] is None, s["prumer"] or 0.0, s["id"]))
    return {
        "setupy": out, "aktivni_id": akt["id"], "doporucene": hodnoceni.doporucene(out, ma_klic),
        "stroj": stroj(data), "modely": [info_modelu(data, f) for f in modely_soubory(data)],
        "surova": sorted(rows, key=lambda r: str(r.get("datum", "")), reverse=True), "ma_klic": ma_klic,
        "beam_povolene": list(BEAM_POVOLENE),
    }


# ---------- samotné měření ----------

def _load_wav(p: Path) -> np.ndarray:
    with wave.open(str(p), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0


def testovaci_sady(data: Path | None = None) -> dict[str, list[tuple[Path, str]]]:
    root = data or config.data_dir()
    sady: dict[str, list[tuple[Path, str]]] = {}
    man = root / "trenink" / "data" / "cv_test" / "manifest.tsv"
    if man.is_file():
        rows = [l.split("\t") for l in man.read_text(encoding="utf-8").splitlines() if l.strip()]
        sady["cv"] = [(man.parent / r[0], r[1]) for r in rows[:N_CV] if len(r) >= 2]
    refs = root / "bench" / "fleurs" / "refs.tsv"
    if refs.is_file():
        rows = [l.split("\t", 1) for l in refs.read_text(encoding="utf-8").splitlines() if l.strip()]
        sady["fleurs"] = [(refs.parent / r[0], r[1].strip()) for r in rows if len(r) == 2]
    return sady


def minutove_kusy(fleurs: list[tuple[Path, str]], pocet: int = N_KUSU_RYCHLOST) -> list[np.ndarray]:
    """Spojí po sobě jdoucí FLEURS klipy (s 0,4 s ticha) do kusů dlouhých aspoň 60 s."""
    ticho = np.zeros(int(TICHO_S * 16000), np.float32)
    kusy, cur = [], []
    for p, _ in fleurs:
        cur += [_load_wav(p), ticho]
        if sum(len(x) for x in cur) >= 60 * 16000:
            kusy.append(np.concatenate(cur)); cur = []
            if len(kusy) == pocet:
                break
    return kusy


def zmer_setup(
    data: Path | None,
    setup: dict[str, Any],
    cfg: dict[str, Any],
    progress_cb: Callable[[int, int, str], None] = lambda *_: None,
    stop_event: threading.Event | None = None,
    *,
    server_factory: Callable[..., Any] | None = None,
    ai_factory: Callable[..., Any] | None = None,
    stroj_info: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Změří setup: WER na Common Voice (100) + FLEURS (40) a rychlost na 3 minutových kusech. Uloží do mereni.json."""
    from mluvka import asr, llm
    stop_event = stop_event or threading.Event()
    sady = testovaci_sady(data)
    if "fleurs" not in sady:
        raise ValueError("Chybí testovací nahrávky FLEURS (složka bench/fleurs).")
    ai = None
    if setup["haiku"]:
        ai = (ai_factory or llm.ClaudeAI)(data)
        if not llm.is_available(data) and ai_factory is None:
            raise ValueError("Chybí API klíč pro Claude.")
    kusy = minutove_kusy(sady["fleurs"])
    total = sum(len(v) for v in sady.values()) + len(kusy)
    done = 0
    model_path = config.models_dir_path(data) / config.validate_model_soubor(setup["model_soubor"])
    server = (server_factory or asr.WhisperServer)(data, model_path=model_path)
    try:
        server.start(timeout_s=90.0)
        vysledky: dict[str, Any] = {"cv": None, "fleurs": None}
        for sada, items in sady.items():
            refs, hyps = [], []
            for p, ref in items:
                if stop_event.is_set():
                    raise Zastaveno()
                hyp = server.transcribe(_load_wav(p), cfg=cfg, beam_size=setup["beam"])
                if ai is not None:
                    hyp = ai.upravit_text(hyp, cfg)
                refs.append(ref); hyps.append(hyp)
                done += 1; progress_cb(done, total, "presnost")
            vysledky[sada] = {"wer": wer.compute_wer(refs, hyps), "n": len(items)}
        t_asr = t_ai = 0.0
        minut = 0.0
        for kus in kusy:
            if stop_event.is_set():
                raise Zastaveno()
            t0 = time.perf_counter()
            text = server.transcribe(kus, cfg=cfg, beam_size=setup["beam"])
            t1 = time.perf_counter()
            if ai is not None:
                ai.upravit_text(text, cfg)
            t2 = time.perf_counter()
            t_asr += t1 - t0; t_ai += t2 - t1; minut += len(kus) / 16000 / 60
            done += 1; progress_cb(done, total, "rychlost")
    finally:
        try:
            server.close()
        except Exception:
            pass
    row = {
        "setup_id": setup["id"], "datum": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": setup["model_soubor"], "beam": setup["beam"], "haiku": setup["haiku"],
        "cv": vysledky["cv"], "fleurs": vysledky["fleurs"],
        "s_na_minutu": round((t_asr + t_ai) / minut, 2) if minut else None,
        "s_haiku_na_minutu": round(t_ai / minut, 2) if (minut and ai is not None) else None,
        "stroj": stroj_info if stroj_info is not None else stroj(data), "zdroj": "mluvka",
    }
    _pridej_mereni(data, row)
    return row


# ---------- běh na pozadí pro okno ----------

class Mereni:
    """Stav jednoho běžícího měření v procesu okna (jen jedno najednou)."""

    def __init__(self) -> None:
        self.stav: dict[str, Any] | None = None
        self._stop = threading.Event()

    def bezi(self) -> bool:
        return bool(self.stav and self.stav.get("bezi"))

    def spustit(self, data: Path | None, setup: dict[str, Any], cfg: dict[str, Any], on_done: Callable[[], None] = lambda: None) -> None:
        if not _LOCK.acquire(blocking=False):
            raise RuntimeError("Probíhá jiné měření.")
        self._stop = threading.Event()
        self.stav = {"bezi": True, "setup_id": setup["id"], "hotovo": 0, "celkem": 0, "faze": "start", "chyba": ""}

        def run() -> None:
            try:
                zmer_setup(data, setup, cfg, self._progress, self._stop)
                self.stav.update(bezi=False, vysledek="ok")
            except Zastaveno:
                self.stav.update(bezi=False, vysledek="zastaveno")
            except Exception as exc:  # chyba se ukáže v řádku tabulky
                self.stav.update(bezi=False, vysledek="chyba", chyba=str(exc) or type(exc).__name__)
            finally:
                _LOCK.release()
                on_done()

        threading.Thread(target=run, name="mluvka-mereni", daemon=True).start()

    def _progress(self, done: int, total: int, faze: str) -> None:
        if self.stav is not None:
            self.stav.update(hotovo=done, celkem=total, faze=faze)

    def zastavit(self) -> None:
        self._stop.set()
