"""Testy pro Task v6 — Doladění z okna a orchestraci pipeline."""

from __future__ import annotations

import ast
import json
import math
import os
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

import pytest

from mluvka import config, doladeni, okno, trenink
from trenink import doladit_vse, spolecne


def _write_silence_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"\x00\x00" * max(1, int(round(seconds * rate)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)


def _create_recorded_vety(data_dir: Path, count: int) -> None:
    vety_dir = config.trenink_vety_dir_path(data_dir)
    lines: list[str] = []
    for i in range(1, count + 1):
        code = f"{i:03d}"
        _write_silence_wav(vety_dir / f"{code}.wav", seconds=2.0)
        lines.append(f"{code}\tTestovací věta číslo {i}.\t2.00\t2026-11-13T09:00:00+01:00")
    tsv_path = config.trenink_vety_tsv_path(data_dir)
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_cv_manifest(data_dir: Path, count: int) -> None:
    manifest = spolecne.dataset_dir("cv_train", data_dir) / "manifest.tsv"
    rows = [(f"{i:06d}.wav", f"Běžná česká věta {i}.", 2.5) for i in range(count)]
    spolecne.write_manifest(manifest, rows)


def test_no_ml_imports_in_torch_free_modules() -> None:
    forbidden = {"torch", "transformers", "peft", "scipy", "pyarrow", "accelerate", "datasets"}
    for rel in ("trenink/spolecne.py", "trenink/doladit_vse.py", "mluvka/doladeni.py"):
        tree = ast.parse(Path(rel).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not (imported & forbidden), f"{rel} nesmí importovat ML balíky: {imported & forbidden}"


def test_spolecne_jen_vety_and_cv_limit_helpers(tmp_path: Path) -> None:
    assert spolecne.prepare_target_sets(True) == ("vety_train", "vety_test")
    assert spolecne.prepare_target_sets(False) == ("cv_train", "cv_test", "vety_train", "vety_test")

    rows = [{"file": f"{i:03d}.wav", "text": f"Věta {i}", "seconds": 2.0} for i in range(20)]
    assert len(spolecne.select_cv_entries(rows, cv_limit=None)) == 20
    assert spolecne.select_cv_entries(rows, cv_limit=0) == []
    assert len(spolecne.select_cv_entries(rows, cv_limit=50)) == 20

    s1 = spolecne.select_cv_entries(rows, cv_limit=6, seed=42)
    s2 = spolecne.select_cv_entries(rows, cv_limit=6, seed=42)
    s3 = spolecne.select_cv_entries(rows, cv_limit=6, seed=99)
    assert len(s1) == 6
    assert s1 == s2
    assert s1 != s3
    with pytest.raises(ValueError):
        spolecne.select_cv_entries(rows, cv_limit=-1)

    _write_cv_manifest(tmp_path, 30)
    spolecne.write_manifest(
        spolecne.dataset_dir("vety_train", tmp_path) / "manifest.tsv",
        [("000001.wav", "Moje první věta.", 2.0), ("000002.wav", "Moje druhá věta.", 2.0)],
    )
    combined_all, cv_all, vety_cnt = spolecne.build_training_entries(tmp_path, vety_mult=5, cv_limit=None)
    assert cv_all == 30
    assert vety_cnt == 2
    assert len(combined_all) == 30 + 2 * 5

    combined_lim, cv_lim, _ = spolecne.build_training_entries(tmp_path, vety_mult=5, cv_limit=10, seed=7)
    assert cv_lim == 10
    assert len(combined_lim) == 10 + 2 * 5

    combined_zero, cv_zero, _ = spolecne.build_training_entries(tmp_path, vety_mult=3, cv_limit=0)
    assert cv_zero == 0
    assert len(combined_zero) == 2 * 3


def test_doladit_vse_parsers_and_verdict() -> None:
    assert doladit_vse.parse_total_steps("Plán: total_steps=87, warmup=4, batch=4") == 87
    assert doladit_vse.parse_total_steps("nějaký jiný řádek") is None

    p1 = doladit_vse.parse_log_line("Krok 40/87 | loss=0.0521 | 14.31 s/krok | ETA 11 min 12 s | lr=5.66e-05")
    assert p1 == {
        "krok": 40,
        "celkem": 87,
        "loss": pytest.approx(0.0521),
        "s_krok": pytest.approx(14.31),
        "eta_s": 11 * 60 + 12,
    }

    p2 = doladit_vse.parse_log_line("Krok 5/100 | loss=1,2340 | 10.00 s/krok | ETA 1 h 05 min 03 s")
    assert p2 is not None
    assert p2["krok"] == 5
    assert p2["celkem"] == 100
    assert p2["loss"] == pytest.approx(1.234)
    assert p2["eta_s"] == 3600 + 5 * 60 + 3

    p3 = doladit_vse.parse_log_line("Krok 4/10 | loss=0.2500 | 12.50 s/krok")
    assert p3 is not None
    assert p3["eta_s"] == 75

    # Formát z doladit._format_eta ("11m 12s", "1h 05m 03s", "?")
    p_short = doladit_vse.parse_log_line("Krok 40/87 | loss=0.0521 | 14.31 s/krok | ETA 11m 12s | lr=5.66e-05")
    assert p_short is not None
    assert p_short["eta_s"] == 11 * 60 + 12
    p_hour = doladit_vse.parse_log_line("Krok 1/300 | loss=0.9000 | 14.00 s/krok | ETA 1h 05m 03s | lr=1.00e-04")
    assert p_hour is not None
    assert p_hour["eta_s"] == 3600 + 5 * 60 + 3
    assert doladit_vse.parse_eta_seconds("?") is None
    assert doladit_vse.parse_eta_seconds("11m 12s") == 11 * 60 + 12

    # Verdikt: >= 10 % relativní pokles vety_test a zároveň fleurs nejvýše o 1.0 pt horší
    assert doladit_vse.evaluate_verdict({"vety_test": 0.20, "fleurs": 0.06}, {"vety_test": 0.18, "fleurs": 0.07}) is True
    assert doladit_vse.evaluate_verdict({"vety_test": 0.20, "fleurs": 0.06}, {"vety_test": 0.181, "fleurs": 0.06}) is False
    assert doladit_vse.evaluate_verdict({"vety_test": 0.20, "fleurs": 0.06}, {"vety_test": 0.15, "fleurs": 0.071}) is False
    assert doladit_vse.evaluate_verdict({"vety_test": 0.0, "fleurs": 0.05}, {"vety_test": 0.0, "fleurs": 0.05}) is False

    rows = [
        {"datum": "2026-11-12", "model": "ggml-czechspeech-q8_0.bin", "sada": "vety_test", "n": 12, "wer": 0.30},
        {"datum": "2026-11-13", "model": "ggml-czechspeech-q8_0.bin", "sada": "vety_test", "n": 12, "wer": 0.20},
        {"datum": "2026-11-13", "model": "ggml-czechspeech-q8_0.bin", "sada": "fleurs", "n": 40, "wer": 0.06},
        {"datum": "2026-11-13", "model": "ggml-osobni-20261113-0915-q8_0.bin", "sada": "vety_test", "n": 12, "wer": 0.12},
        {"datum": "2026-11-13", "model": "ggml-osobni-20261113-0915-q8_0.bin", "sada": "fleurs", "n": 40, "wer": 0.065},
    ]
    res_good = doladit_vse.build_vysledek(
        rows,
        pred_model_name="ggml-czechspeech-q8_0.bin",
        po_model_name="ggml-osobni-20261113-0915-q8_0.bin",
        start_index=1,
    )
    assert res_good["lepsi"] is True
    assert res_good["n_vety"] == 12
    assert res_good["model_soubor"] == "ggml-osobni-20261113-0915-q8_0.bin"

    rows_bad = [
        {"model": "ggml-czechspeech-q8_0.bin", "sada": "vety_test", "n": 12, "wer": 0.20},
        {"model": "ggml-czechspeech-q8_0.bin", "sada": "fleurs", "n": 40, "wer": 0.06},
        {"model": "ggml-osobni-20261113-0915-q8_0.bin", "sada": "vety_test", "n": 12, "wer": 0.19},
        {"model": "ggml-osobni-20261113-0915-q8_0.bin", "sada": "fleurs", "n": 40, "wer": 0.06},
    ]
    res_bad = doladit_vse.build_vysledek(
        rows_bad,
        pred_model_name="ggml-czechspeech-q8_0.bin",
        po_model_name="ggml-osobni-20261113-0915-q8_0.bin",
    )
    assert res_bad["lepsi"] is False
    assert res_bad["model_soubor"] is None


def test_nazev_modelu_osobni_and_backcompat(tmp_path: Path) -> None:
    assert trenink._nazev_modelu("ggml-osobni-20261113-0915-q8_0.bin", False) == "Doladěný na tvém hlase (13. 11.)"
    assert trenink._nazev_modelu("ggml-osobni-20261113-0915-2-q8_0.bin", False) == "Doladěný na tvém hlase (13. 11.)"
    assert trenink._nazev_modelu("ggml-osobni-20260305-1402-q8_0.bin", False) == "Doladěný na tvém hlase (5. 3.)"
    assert trenink._nazev_modelu("ggml-osobni-q5_0.bin", False) == "ggml-osobni-q5_0.bin"
    assert trenink._nazev_modelu(config.DEFAULT_MODEL_SOUBOR, True) == "CzechSpeech q5_0"

    assert spolecne.unique_run_name("osobni-20261113-0915", tmp_path) == "osobni-20261113-0915"
    (spolecne.models_dir(tmp_path)).mkdir(parents=True, exist_ok=True)
    (spolecne.models_dir(tmp_path) / "ggml-osobni-20261113-0915-q8_0.bin").write_bytes(b"lmgg")
    assert spolecne.unique_run_name("osobni-20261113-0915", tmp_path) == "osobni-20261113-0915-2"


def test_doladeni_idle_counts_and_estimate(tmp_path: Path) -> None:
    _create_recorded_vety(tmp_path, 55)
    _write_cv_manifest(tmp_path, 1500)
    stav = doladeni.doladeni_stav(tmp_path)
    # 55 nahraných -> každá 5. do testu (11) -> 44 ve vety_train; cv omezeno na 1000
    expected_min = math.ceil((44 * 5 + 1000) / 16 * 14.5 / 60) + 8
    assert stav == {
        "stav": "nic",
        "nahrano": 55,
        "potreba": 50,
        "odhad_min": expected_min,
    }


def test_doladeni_stav_roundtrip_and_dead_pid(tmp_path: Path) -> None:
    payload = {
        "stav": "hotovo",
        "faze": "mereni",
        "krok": 87,
        "celkem": 87,
        "loss": 0.048,
        "eta_s": 0,
        "pid": 1234,
        "name": "osobni-20261113-0915",
        "zacatek": "2026-11-13T09:15:00+01:00",
        "konec": "2026-11-13T09:38:00+01:00",
        "log_tail": ["Krok 87/87 | loss=0.0480"],
        "vysledek": {
            "pred": {"vety_test": 0.084, "fleurs": 0.059},
            "po": {"vety_test": 0.051, "fleurs": 0.061},
            "n_vety": 12,
            "lepsi": True,
            "model_soubor": "ggml-osobni-20261113-0915-q8_0.bin",
        },
        "chyba": None,
    }
    stav_path = spolecne.doladeni_stav_path(tmp_path)
    spolecne.atomic_write_text(stav_path, json.dumps(payload, ensure_ascii=False, indent=2))
    assert doladeni.doladeni_stav(tmp_path) == payload

    running_payload = {
        **payload,
        "stav": "bezi",
        "faze": "doladeni",
        "konec": None,
        "vysledek": None,
        "pid": 999999,
    }
    spolecne.atomic_write_text(stav_path, json.dumps(running_payload, ensure_ascii=False, indent=2))
    res_dead = doladeni.doladeni_stav(tmp_path, pid_alive_fn=lambda _pid: False)
    assert res_dead["stav"] == "chyba"
    assert res_dead["chyba"] == "Doladění neočekávaně skončilo."
    on_disk = json.loads(stav_path.read_text(encoding="utf-8"))
    assert on_disk["stav"] == "chyba"
    assert on_disk["chyba"] == "Doladění neočekávaně skončilo."


def test_doladeni_spustit_zastavit_and_pouzit(tmp_path: Path) -> None:
    # 1. Málo vět (< 50)
    _create_recorded_vety(tmp_path, 49)
    res_few = doladeni.doladeni_spustit(tmp_path)
    assert res_few["ok"] is False
    assert "50" in res_few["error"]

    # 2. Dost vět, ale běží měření (zmerit)
    _create_recorded_vety(tmp_path, 50)
    res_meas = doladeni.doladeni_spustit(tmp_path, mereni_bezi=True)
    assert res_meas["ok"] is False

    # 3. Úspěšné odpojené spuštění + zápis úvodního stav.json před návratem
    launched: dict[str, Any] = {}

    class FakeProc:
        pid = 42424

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeProc:
        launched["cmd"] = cmd
        launched["kwargs"] = kwargs
        return FakeProc()

    res_ok = doladeni.doladeni_spustit(
        tmp_path,
        popen_fn=fake_popen,
        name="osobni-20261113-0915",
    )
    assert res_ok["ok"] is True
    assert res_ok["stav"] == "bezi"
    assert res_ok["pid"] == 42424
    flags = launched["kwargs"]["creationflags"]
    expected_flags = doladeni.DETACHED_PROCESS | doladeni.CREATE_NEW_PROCESS_GROUP | doladeni.CREATE_NO_WINDOW
    assert flags == expected_flags
    stav_path = spolecne.doladeni_stav_path(tmp_path)
    assert stav_path.is_file()
    assert json.loads(stav_path.read_text(encoding="utf-8"))["pid"] == 42424

    # 4. Odmítnutí druhého spuštění, když proces stále žije
    res_dup = doladeni.doladeni_spustit(tmp_path, pid_alive_fn=lambda pid: pid == 42424)
    assert res_dup["ok"] is False

    # 5. Zastavení přes taskkill /PID <pid> /T /F + stav=zastaveno
    killed_cmds: list[list[str]] = []
    res_stop = doladeni.doladeni_zastavit(
        tmp_path,
        run_fn=lambda cmd, **kw: killed_cmds.append(cmd),
        pid_alive_fn=lambda _pid: False,
    )
    assert res_stop["ok"] is True
    assert res_stop["stav"] == "zastaveno"
    assert killed_cmds and killed_cmds[0] == ["taskkill", "/PID", "42424", "/T", "/F"]
    assert json.loads(stav_path.read_text(encoding="utf-8"))["stav"] == "zastaveno"

    # 6. Použití doladěného modelu povoleno jedině když lepsi=True a soubor existuje v DATA/models
    models_dir = spolecne.models_dir(tmp_path)
    models_dir.mkdir(parents=True, exist_ok=True)
    model_name = "ggml-osobni-20261113-0915-q8_0.bin"

    # Stav zastaveno -> odmítne
    assert doladeni.doladeni_pouzit(tmp_path)["ok"] is False

    # Hotovo, ale lepsi=False -> odmítne
    hotovo_bad = {
        "stav": "hotovo",
        "faze": "mereni",
        "krok": 5,
        "celkem": 5,
        "loss": 0.1,
        "eta_s": 0,
        "pid": 42424,
        "name": "osobni-20261113-0915",
        "zacatek": "2026-11-13T09:15:00+01:00",
        "konec": "2026-11-13T09:17:00+01:00",
        "log_tail": [],
        "vysledek": {
            "pred": {"vety_test": 0.10, "fleurs": 0.06},
            "po": {"vety_test": 0.10, "fleurs": 0.06},
            "n_vety": 10,
            "lepsi": False,
            "model_soubor": model_name,
        },
        "chyba": None,
    }
    spolecne.atomic_write_text(stav_path, json.dumps(hotovo_bad))
    assert doladeni.doladeni_pouzit(tmp_path)["ok"] is False

    # Hotovo + lepsi=True, ale chybí .bin v models -> odmítne
    hotovo_good = dict(hotovo_bad)
    hotovo_good["vysledek"] = dict(hotovo_bad["vysledek"], lepsi=True, po={"vety_test": 0.07, "fleurs": 0.06})
    spolecne.atomic_write_text(stav_path, json.dumps(hotovo_good))
    assert doladeni.doladeni_pouzit(tmp_path)["ok"] is False

    # Hotovo + lepsi=True + .bin existuje -> nastaví model_soubor v config.json
    (models_dir / model_name).write_bytes(b"lmgg" + b"\x00" * 1024)
    (models_dir / config.DEFAULT_MODEL_SOUBOR).write_bytes(b"lmgg" + b"\x00" * 1024)
    cfg_store = config.ConfigStore(tmp_path)
    cfg_store.set_value("model_soubor", config.DEFAULT_MODEL_SOUBOR)
    res_use = doladeni.doladeni_pouzit(tmp_path, cfg_store=cfg_store)
    assert res_use["ok"] is True
    assert res_use["aktivni"] == model_name
    assert config.load_config(tmp_path)["model_soubor"] == model_name


def test_okno_api_stateless_and_doladeni_methods() -> None:
    api = okno.Api()
    assert vars(api) == {}
    for name in ("doladeni_stav", "doladeni_spustit", "doladeni_zastavit", "doladeni_pouzit", "vybrat_model"):
        assert callable(getattr(api, name))


def test_doladit_vse_missing_venv_train_writes_stav(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "trenink/doladit_vse.py", "--name", "osobni-20261113-0915", "--data", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert proc.returncode != 0
    stav = json.loads(spolecne.doladeni_stav_path(tmp_path).read_text(encoding="utf-8"))
    assert stav["stav"] == "chyba"
    assert stav["chyba"] == doladit_vse.ERR_CHYBI_PROSTREDI


def test_doladeni_corrupt_stav_stop_race_and_zmerit_mutex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stav_path = spolecne.doladeni_stav_path(tmp_path)
    spolecne.atomic_write_text(stav_path, "{neplatny json")
    _create_recorded_vety(tmp_path, 55)

    corrupt_state = doladeni.doladeni_stav(tmp_path)
    assert corrupt_state["stav"] == "chyba"
    assert corrupt_state["chyba"] == doladeni.ERR_SOUBOR_STAVU

    refuse_start = doladeni.doladeni_spustit(tmp_path)
    assert refuse_start["ok"] is False
    assert refuse_start["error"] == doladeni.ERR_SOUBOR_STAVU

    # Během aktivního běhu doladění odmítne okno.Api().zmerit()
    spolecne.atomic_write_text(
        stav_path,
        json.dumps({"stav": "bezi", "faze": "doladeni", "pid": os.getpid(), "name": "osobni-20261113-1010"}),
    )
    monkeypatch.setenv("MLUVKA_DATA", str(tmp_path))
    res_meas = okno.Api().zmerit()
    assert res_meas["ok"] is False
    assert doladeni.is_process_alive(os.getpid()) is True
    assert doladeni.is_process_alive(99999999) is False

    # Race při zastavení: pokud běh právě dopsal stav=hotovo, doladeni_zastavit jej nepřepíše na zastaveno
    def fake_run_race(*_a: Any, **_kw: Any) -> None:
        spolecne.atomic_write_text(
            stav_path,
            json.dumps({"stav": "hotovo", "faze": "mereni", "pid": os.getpid(), "vysledek": {"lepsi": True}}),
        )

    res_race = doladeni.doladeni_zastavit(
        tmp_path,
        run_fn=fake_run_race,
        pid_alive_fn=lambda _p: False,
    )
    assert res_race["ok"] is True
    assert res_race["stav"] == "hotovo"
    assert json.loads(stav_path.read_text(encoding="utf-8"))["stav"] == "hotovo"
