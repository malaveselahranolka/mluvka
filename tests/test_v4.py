"""Jednotkové a integrační testy pro TASK v4 (výběr modelu, beam_size a trenink/spolecne)."""

from __future__ import annotations

import ast
import json
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from mluvka import app as mluvka_app
from mluvka import asr, config, okno, trenink
from trenink import spolecne


def test_spolecne_has_no_heavy_ml_imports_at_module_level() -> None:
    src = Path(spolecne.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"torch", "pyarrow", "scipy", "transformers", "peft", "soundfile"})


def test_spolecne_clean_label_text() -> None:
    assert spolecne.clean_label_text(None) == ""
    assert spolecne.clean_label_text("  Ahoj\t\nsvěte\u200b!  ") == "Ahoj světe!"
    # Kombinovaná diakritika NFD se převede na NFC.
    assert spolecne.clean_label_text("C\u030cesky\u0301  text\r\ns   mezerami.") == "Český text s mezerami."


def test_spolecne_duration_filter_and_entries() -> None:
    assert spolecne.valid_duration(1.0) is True
    assert spolecne.valid_duration(30.0) is True
    assert spolecne.valid_duration(0.99) is False
    assert spolecne.valid_duration(30.01) is False
    assert spolecne.valid_duration(True) is False
    assert spolecne.valid_duration(math.nan) is False
    assert spolecne.valid_duration(math.inf) is False

    raw = [
        {"file": "000000.wav", "text": "  Krátký\tklip ", "seconds": 0.5},
        {"file": "000001.wav", "text": "   ", "seconds": 3.2},
        {"file": "000002.wav", "text": "Příliš\ndlouhý", "seconds": 31.0},
        {"file": "000003.wav", "text": "Dobrý\tden,  jak se  máte?", "seconds": 2.3456},
        {"file": "000004.wav", "text": "Hraniční klip", "seconds": 1.0},
    ]
    kept = spolecne.filter_entries(raw)
    assert len(kept) == 2
    assert kept[0] == {"file": "000003.wav", "text": "Dobrý den, jak se máte?", "seconds": 2.346}
    assert kept[1] == {"file": "000004.wav", "text": "Hraniční klip", "seconds": 1.0}


def test_spolecne_split_train_test() -> None:
    items = [f"veta-{i}" for i in range(1, 13)]
    train, test = spolecne.split_train_test(items, test_every=5)
    assert test == ["veta-5", "veta-10"]
    assert len(train) == 10
    assert "veta-5" not in train and "veta-10" not in train
    with pytest.raises(ValueError):
        spolecne.split_train_test(items, test_every=1)


def test_spolecne_manifest_roundtrip(tmp_path: Path) -> None:
    manifest = tmp_path / "data" / "cv_test" / "manifest.tsv"
    rows = [
        {"file": "000000.wav", "text": "První\tvěta   s diakritikou.", "seconds": 2.5},
        ("000001.wav", "Druhá věta.", 12.125),
    ]
    spolecne.write_manifest(manifest, rows)
    text = manifest.read_text(encoding="utf-8")
    assert text == "000000.wav\tPrvní věta s diakritikou.\t2.500\n000001.wav\tDruhá věta.\t12.125\n"

    loaded = spolecne.read_manifest(manifest, resolve_paths=True)
    assert len(loaded) == 2
    assert loaded[0]["file"] == "000000.wav"
    assert loaded[0]["text"] == "První věta s diakritikou."
    assert loaded[0]["seconds"] == pytest.approx(2.5)
    assert loaded[0]["wav_path"] == manifest.parent / "000000.wav"
    assert spolecne.manifest_hours(loaded) == pytest.approx((2.5 + 12.125) / 3600.0)


def test_config_defaults_and_model_soubor_validation_and_fallback(tmp_path: Path) -> None:
    assert config.DEFAULT_CONFIG["model_soubor"] == "ggml-czechspeech-q5_0.bin"
    assert config.DEFAULT_CONFIG["beam_size"] == 5

    # Starý config.json bez klíčů beam_size / model_soubor dostane výchozí hodnoty.
    cfg_file = config.config_path(tmp_path)
    cfg_file.write_text(json.dumps({"zvuky": False}), encoding="utf-8")
    store = config.ConfigStore(tmp_path)
    assert store.cfg["beam_size"] == 5
    assert store.cfg["model_soubor"] == "ggml-czechspeech-q5_0.bin"

    # whisper_model_path na prázdném adresáři nevytvoří config.json a vrátí výchozí model.
    empty_data = tmp_path / "clean_data"
    default_p = empty_data / "models" / "ggml-czechspeech-q5_0.bin"
    assert config.whisper_model_path(empty_data) == default_p
    assert not config.config_path(empty_data).exists()

    # Path traversal / oddělovače cest musí být odmítnuty.
    for bad in ("../evil.bin", "sub/model.bin", "sub\\model.bin", "a..b.bin", "C:x.bin", ".", "..", "", " x.bin", 42):
        with pytest.raises(ValueError):
            config.validate_model_soubor(bad)
        if isinstance(bad, str):
            with pytest.raises(ValueError):
                config.whisper_model_path(tmp_path, model_soubor=bad)

    cfg_file.write_text(json.dumps({"model_soubor": "../hack.bin"}), encoding="utf-8")
    with pytest.raises(ValueError):
        config.whisper_model_path(tmp_path)

    # Fallback na výchozí CzechSpeech když zvolený soubor neexistuje; jinak vrátí zvolený soubor.
    models_dir = config.models_dir_path(tmp_path)
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "ggml-czechspeech-q5_0.bin").write_bytes(b"lmggdefault")
    cfg_file.write_text(json.dumps({"model_soubor": "ggml-moje-q5_0.bin"}), encoding="utf-8")
    assert config.whisper_model_path(tmp_path) == models_dir / "ggml-czechspeech-q5_0.bin"

    custom = models_dir / "ggml-moje-q5_0.bin"
    custom.write_bytes(b"lmggcustom")
    assert config.whisper_model_path(tmp_path) == custom


def test_vysledky_json_parsing_for_model_card(tmp_path: Path) -> None:
    vys = config.trenink_vysledky_path(tmp_path)
    vys.parent.mkdir(parents=True, exist_ok=True)
    vys.write_text(
        json.dumps(
            [
                {
                    "datum": "2026-11-12T22:00:00+01:00",
                    "model": "ggml-czechspeech-q5_0.bin",
                    "sada": "cv_test",
                    "n": 100,
                    "beam": 5,
                    "wer": 0.089,
                    "sekundy": 10.0,
                },
                {
                    "datum": "2026-11-13T01:00:00+01:00",
                    "model": "ggml-czechspeech-q5_0.bin",
                    "sada": "cv_test",
                    "n": 500,
                    "beam": 5,
                    "wer": 0.071,
                    "sekundy": 22.0,
                },
                {
                    "datum": "2026-11-13T01:05:00+01:00",
                    "model": "ggml-czechspeech-q5_0.bin",
                    "sada": "fleurs",
                    "n": 40,
                    "beam": 5,
                    "wer": 0.064,
                    "sekundy": 5.5,
                },
                {
                    "datum": "2026-10-25T02:30:00+02:00",
                    "model": "ggml-czechspeech-q5_0.bin",
                    "sada": "fleurs",
                    "n": 40,
                    "beam": 5,
                    "wer": 0.099,
                    "sekundy": 5.5,
                },
                {
                    "datum": "2026-10-25T02:15:00+01:00",
                    "model": "ggml-czechspeech-q5_0.bin",
                    "sada": "fleurs",
                    "n": 40,
                    "beam": 5,
                    "wer": 0.064,
                    "sekundy": 5.5,
                },
                "neplatna-polozka",
                {"model": "ggml-czechspeech-q5_0.bin", "sada": "fleurs", "wer": "nan"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    parsed = trenink.parse_vysledky_latest(tmp_path)
    assert parsed["ggml-czechspeech-q5_0.bin"]["cv_test"]["wer"] == pytest.approx(0.071)
    # 02:15+01:00 (01:15 UTC) je novější než 02:30+02:00 (00:30 UTC) přes změnu letního času.
    assert parsed["ggml-czechspeech-q5_0.bin"]["fleurs"]["wer"] == pytest.approx(0.064)
    assert trenink.format_model_wer(parsed["ggml-czechspeech-q5_0.bin"]) == "Common Voice: 7,1 % · FLEURS: 6,4 %"


def test_api_modely_and_vybrat_model(tmp_path: Path) -> None:
    models_dir = config.models_dir_path(tmp_path)
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "ggml-czechspeech-q5_0.bin").write_bytes(b"lmgg0000")
    (models_dir / "ggml-osobni-q5_0.bin").write_bytes(b"lmgg1111")
    (models_dir / "nebrat.txt").write_text("ignorovat", encoding="utf-8")

    config.trenink_vysledky_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    config.config_path(tmp_path).write_text(
        json.dumps({"claude_api_klic": "sk-ant-secret-key", "model_soubor": "ggml-czechspeech-q5_0.bin"}),
        encoding="utf-8",
    )
    config.trenink_vysledky_path(tmp_path).write_text(
        json.dumps(
            [
                {"datum": "2026-11-13T02:00:00+01:00", "model": "ggml-osobni-q5_0.bin", "sada": "cv_test", "n": 50, "beam": 5, "wer": 0.058, "sekundy": 4.0},
                {"datum": "2026-11-13T02:05:00+01:00", "model": "ggml-osobni-q5_0.bin", "sada": "fleurs", "n": 40, "beam": 5, "wer": 0.059, "sekundy": 3.5},
            ]
        ),
        encoding="utf-8",
    )

    api = okno.Api(data=tmp_path)
    assert vars(api) == {}

    res = api.modely()
    assert res["ok"] is True
    assert res["aktivni"] == "ggml-czechspeech-q5_0.bin"
    assert [m["soubor"] for m in res["models"]] == ["ggml-czechspeech-q5_0.bin", "ggml-osobni-q5_0.bin"]
    assert res["models"][0]["nazev"] == "CzechSpeech q5_0"
    assert res["models"][0]["vybrany"] is True
    assert res["models"][1]["nazev"] == "ggml-osobni-q5_0.bin"
    assert res["models"][1]["wer_text"] == "Common Voice: 5,8 % · FLEURS: 5,9 %"

    bad_path = api.vybrat_model("../ggml-osobni-q5_0.bin")
    assert bad_path["ok"] is False

    missing = api.vybrat_model("neexistuje.bin")
    assert missing["ok"] is False

    picked = api.vybrat_model("ggml-osobni-q5_0.bin")
    assert picked["ok"] is True
    assert "claude_api_klic" not in picked["config"]
    assert picked["aktivni"] == "ggml-osobni-q5_0.bin"
    assert picked["models"][1]["vybrany"] is True
    disk_cfg = json.loads(config.config_path(tmp_path).read_text(encoding="utf-8"))
    assert disk_cfg["model_soubor"] == "ggml-osobni-q5_0.bin"


def test_asr_beam_size_sent_only_when_gt_1(tmp_path: Path) -> None:
    srv = asr.WhisperServer(tmp_path)
    srv._port = 12345
    captured_payloads: list[bytes] = []

    def fake_open(req, timeout=60.0):
        captured_payloads.append(req.data)
        resp_mock = MagicMock()
        resp_mock.read.return_value = json.dumps({"text": "Test"}).encode("utf-8")
        cm = MagicMock()
        cm.__enter__.return_value = resp_mock
        return cm

    speech = np.full(16000, 0.2, dtype=np.float32)
    with patch.object(srv._opener, "open", side_effect=fake_open):
        # 1) Přímé _infer_chunk (výchozí greedy=1): beam_size se neposílá.
        srv._infer_chunk(speech, prompt="")
        assert b'name="beam_size"' not in captured_payloads[-1]

        # 2) transcribe s beam_size=1: beam_size se neposílá.
        srv.transcribe(speech, cfg={"beam_size": 1})
        assert b'name="beam_size"' not in captured_payloads[-1]
        assert b'name="audio_ctx"\r\n\r\n1500' in captured_payloads[-1]

        # 3) transcribe s beam_size=5: pošle beam_size=5.
        srv.transcribe(speech, cfg={"beam_size": 5})
        assert b'name="beam_size"\r\n\r\n5' in captured_payloads[-1]

        # 4) transcribe bez cfg použije výchozí beam_size=5.
        srv.transcribe(speech)
        assert b'name="beam_size"\r\n\r\n5' in captured_payloads[-1]

        # 5) Explicitní beam_size parametr má přednost.
        srv.transcribe(speech, cfg={"beam_size": 5}, beam_size=1)
        assert b'name="beam_size"' not in captured_payloads[-1]
        srv.transcribe(speech, cfg={"beam_size": 1}, beam_size=3)
        assert b'name="beam_size"\r\n\r\n3' in captured_payloads[-1]


def test_app_model_reload_on_config_poll_idle_and_deferred_when_busy(tmp_path: Path, monkeypatch) -> None:
    models_dir = config.models_dir_path(tmp_path)
    models_dir.mkdir(parents=True, exist_ok=True)
    default_bin = models_dir / "ggml-czechspeech-q5_0.bin"
    custom_bin = models_dir / "ggml-osobni-q5_0.bin"
    default_bin.write_bytes(b"lmgg0")
    custom_bin.write_bytes(b"lmgg1")

    monkeypatch.delenv("MLUVKA_WHISPER_MODEL", raising=False)
    store = config.ConfigStore(tmp_path)

    servers: list[MagicMock] = []

    def make_server(data=None, *, exe_path=None, model_path=None):
        m = MagicMock()
        m.port = 18000 + len(servers)
        m.model_path = model_path or config.whisper_model_path(data)
        servers.append(m)
        return m

    monkeypatch.setattr(mluvka_app.overlay, "Overlay", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.hotkey, "GlobalHotkeyHook", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.tray, "TrayController", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.audio, "Recorder", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.asr, "WhisperServer", make_server)

    inst = mluvka_app.MluvkaApp(store, data=tmp_path)
    inst._whisper_ready.set()
    inst._model_loaded = True
    assert len(servers) == 1
    initial_srv = servers[0]

    # Změna modelu během nahrávání nesmí okamžitě restartovat server.
    with inst._state_lock:
        inst._recording = True
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["model_soubor"] = "ggml-osobni-q5_0.bin"
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    st = store.path.stat()
    os.utime(store.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    inst._refresh_config()
    inst._maybe_schedule_model_reload()
    assert inst._work_queue.empty()
    initial_srv.close.assert_not_called()

    # Jakmile nahrávání skončí (přes _cancel_recording do klidu), naplánuje se reload_model.
    inst._cancel_recording()
    kind, payload = inst._work_queue.get_nowait()
    assert kind == "reload_model"
    assert Path(payload) == custom_bin

    # Worker reload provede: zavře starý server a nastartuje nový s novým modelem.
    inst._work_queue.put((kind, payload))
    inst._work_queue.put(None)
    inst._worker_loop()

    initial_srv.close.assert_called_once()
    assert len(servers) == 2
    assert servers[1].model_path == custom_bin
    servers[1].start.assert_called_once()
    assert inst._active_model_path == custom_bin

    # Test přepnutí A -> B -> A ještě během probíhajícího reloadu B.
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["model_soubor"] = "ggml-czechspeech-q5_0.bin"
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    st = store.path.stat()
    os.utime(store.path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    with inst._state_lock:
        inst._reloading_model = True
        inst._active_model_path = default_bin
    inst._refresh_config()
    assert inst._pending_model_path == default_bin
    with inst._state_lock:
        inst._reloading_model = False
        inst._active_model_path = custom_bin
    inst._maybe_schedule_model_reload()
    kind2, payload2 = inst._work_queue.get_nowait()
    assert kind2 == "reload_model"
    assert Path(payload2) == default_bin


def test_app_init_fallback_when_config_has_invalid_model_soubor(tmp_path: Path, monkeypatch) -> None:
    models_dir = config.models_dir_path(tmp_path)
    models_dir.mkdir(parents=True, exist_ok=True)
    default_bin = models_dir / "ggml-czechspeech-q5_0.bin"
    default_bin.write_bytes(b"lmgg0")
    config.config_path(tmp_path).write_text(json.dumps({"model_soubor": "../hack.bin"}), encoding="utf-8")
    monkeypatch.delenv("MLUVKA_WHISPER_MODEL", raising=False)

    created_paths: list[Path | None] = []

    def make_server(data=None, *, exe_path=None, model_path=None):
        created_paths.append(model_path)
        m = MagicMock()
        m.model_path = model_path
        return m

    monkeypatch.setattr(mluvka_app.overlay, "Overlay", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.hotkey, "GlobalHotkeyHook", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.tray, "TrayController", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.audio, "Recorder", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.asr, "WhisperServer", make_server)

    store = config.ConfigStore(tmp_path)
    inst = mluvka_app.MluvkaApp(store, data=tmp_path)
    assert inst._active_model_path == default_bin
    assert created_paths == [default_bin]


def test_vyradit_z_cv_drops_number_words_and_single_words() -> None:
    from trenink import spolecne
    assert spolecne.vyradit_z_cv("sedm")
    assert spolecne.vyradit_z_cv("Stálo to dvacet pět korun.")
    assert spolecne.vyradit_z_cv("Bylo jich sto.")
    assert not spolecne.vyradit_z_cv("Stojí to za to, viď?")
    assert not spolecne.vyradit_z_cv("Mladinkého Václava vedli poručníci od hradu ke hradu.")


def test_vysledky_prefer_app_beam_over_newer_experiment(tmp_path) -> None:
    import json
    from mluvka import config, trenink
    p = config.trenink_vysledky_path(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([
        {"datum": "2026-11-13T07:42:00+01:00", "model": "m.bin", "sada": "cv_test", "n": 500, "beam": 5, "wer": 0.0709},
        {"datum": "2026-11-13T08:30:00+01:00", "model": "m.bin", "sada": "cv_test", "n": 500, "beam": 8, "wer": 0.0721},
    ]), encoding="utf-8")
    assert trenink.parse_vysledky_latest(tmp_path, 5)["m.bin"]["cv_test"]["wer"] == 0.0709
    assert trenink.parse_vysledky_latest(tmp_path)["m.bin"]["cv_test"]["wer"] == 0.0721
