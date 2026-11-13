"""Testy pro TASK v7: ukládání raw prepis, ověřování diktátů v UI/API a tréninkové sady diktaty_*."""

from __future__ import annotations

import ast
import json
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from mluvka import app as app_mod
from mluvka import config, history, hotkey, okno, trenink
from trenink import ab_test, spolecne


def _write_pcm16_wav(path: Path, seconds: float, sr: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"\x00\x00" * int(round(seconds * sr))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(frames)


def _speech_samples(seconds: float = 1.5, sr: int = 16000) -> np.ndarray:
    t = np.linspace(0.0, seconds, int(seconds * sr), endpoint=False, dtype=np.float32)
    return 0.2 * np.sin(2.0 * np.pi * 220.0 * t).astype(np.float32)


def test_app_saves_prepis_only_when_ukladat_zvuk_enabled(tmp_path: Path, monkeypatch) -> None:
    store = config.ConfigStore(tmp_path)
    store.update_values(
        {
            "historie": True,
            "ai_uprava": True,
            "trenink_ukladat_zvuk": False,
            "slovnik": {"slova": [], "opravy": [["klód", "Claude"]]},
            "zkratky": [],
        }
    )

    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.transcribe.return_value = "ehm tohle je klód test"
    fake_ai = MagicMock()
    fake_ai.upravit_text.return_value = "Tohle je uhlazený Claude test."
    fake_ai.proved_prikaz.return_value = "Výsledek příkazu"
    hook = hotkey.GlobalHotkeyHook(on_action=lambda a: None, klavesa_vk=hotkey.VK_RCONTROL)
    hook.start()
    try:
        with (
            patch.object(app_mod.overlay, "Overlay", return_value=fake_overlay),
            patch.object(app_mod.tray, "TrayController", return_value=fake_tray),
            patch.object(app_mod.asr, "WhisperServer", return_value=fake_whisper),
            patch.object(app_mod.llm, "ClaudeAI", return_value=fake_ai),
            patch.object(app_mod.hotkey, "GlobalHotkeyHook", return_value=hook),
        ):
            application = app_mod.MluvkaApp(store, data=tmp_path)
            application._whisper_ready.set()
            application._model_loaded = True
            monkeypatch.setattr(app_mod.llm, "is_available", lambda _data=None: True)
            monkeypatch.setattr(app_mod.output, "paste_text", lambda *_a, **_kw: True)
            monkeypatch.setattr(app_mod.channel, "send_async", lambda *_a, **_kw: None)

            samples = _speech_samples(1.2)

            # 1. Volba vypnuta -> neukládá WAV ani prepis
            cfg_off = store.snapshot()
            application._handle_transcription_job("dictate", (samples, cfg_off, "Code", "", ""))
            entries = history.load_entries(tmp_path)
            assert len(entries) == 1
            assert entries[0]["text"] == "Tohle je uhlazený Claude test."
            assert "prepis" not in entries[0]
            assert "zvuk" not in entries[0]
            diktaty_dir = config.diktaty_dir_path(tmp_path)
            assert not diktaty_dir.exists() or list(diktaty_dir.glob("*.wav")) == []

            # 2. Volba zapnuta -> uloží WAV a prepis po text.uprav, před Haiku
            store.update_values({"trenink_ukladat_zvuk": True})
            cfg_on = store.snapshot()
            application._handle_transcription_job("dictate", (samples, cfg_on, "Code", "", ""))
            entries = history.load_entries(tmp_path)
            assert len(entries) == 2
            e2 = entries[1]
            assert e2["text"] == "Tohle je uhlazený Claude test."
            assert e2["prepis"] == "Tohle je Claude test"
            assert e2["zvuk"] == f"{e2['id']}.wav"
            assert (diktaty_dir / e2["zvuk"]).is_file()

            # 3. Příkazový režim -> neukládá pro trénink ani do historie
            application._handle_transcription_job("command", (samples, cfg_on, "Code", "výběr", ""))
            entries_after_cmd = history.load_entries(tmp_path)
            assert len(entries_after_cmd) == 2

            # 4. Smazání záznamu odstraní záznam s prepis i WAV
            assert history.delete_entry(e2["id"], data=tmp_path) is True
            assert not (diktaty_dir / e2["zvuk"]).exists()
            assert len(history.load_entries(tmp_path)) == 1
    finally:
        hook.stop()


def test_okno_api_stateless_and_diktaty_crud_validation(tmp_path: Path, monkeypatch) -> None:
    store = config.ConfigStore(tmp_path)
    store.update_values({"trenink_ukladat_zvuk": True})
    api = okno.Api(data=tmp_path)
    assert vars(api) == {}

    tz = timezone(timedelta(hours=1))
    samples = _speech_samples(1.2)

    # Záznam 1 (starší, neověřený, WAV + prepis)
    id1 = "diktat001"
    wav1 = trenink.save_dictation_wav(id1, samples, data=tmp_path)
    history.append_entry(
        "Uhlazený první.",
        "ehm první surový",
        4.2,
        0.3,
        "Slack",
        cas=datetime(2026, 11, 10, 10, 0, tzinfo=tz),
        entry_id=id1,
        zvuk=wav1,
        prepis="ehm první text",
        data=tmp_path,
    )

    # Záznam 2 (novější, neověřený, WAV + prepis)
    id2 = "diktat002"
    wav2 = trenink.save_dictation_wav(id2, samples, data=tmp_path)
    history.append_entry(
        "Uhlazený druhý.",
        "druhý surový",
        8.5,
        0.4,
        "Code",
        cas=datetime(2026, 11, 12, 14, 30, tzinfo=tz),
        entry_id=id2,
        zvuk=wav2,
        prepis="druhý surový",
        data=tmp_path,
    )

    # Záznam 3: má WAV jméno v JSON, ale soubor na disku chybí -> nesmí být ve výpisu
    history.append_entry(
        "Bez souboru",
        "bez souboru",
        3.0,
        0.2,
        "Word",
        cas=datetime(2026, 11, 12, 15, 0, tzinfo=tz),
        entry_id="diktat_no_wav",
        zvuk="diktat_no_wav.wav",
        prepis="bez souboru",
        data=tmp_path,
    )

    # Záznam 4: má WAV na disku, ale bez pole prepis -> nesmí být ve výpisu
    wav4 = trenink.save_dictation_wav("diktat_no_prepis", samples, data=tmp_path)
    history.append_entry(
        "Jen starý WAV",
        "jen starý wav",
        2.0,
        0.2,
        "Word",
        cas=datetime(2026, 11, 12, 16, 0, tzinfo=tz),
        entry_id="diktat_no_prepis",
        zvuk=wav4,
        data=tmp_path,
    )

    listed = api.diktaty_k_overeni(0, 50)
    assert listed["ok"] is True
    assert listed["ukladat_zvuk"] is True
    assert listed["total"] == 2
    assert listed["verified_count"] == 0
    assert listed["verified_s"] == 0.0
    assert [item["id"] for item in listed["items"]] == [id2, id1]

    # Stránkování
    page1 = api.diktaty_k_overeni(0, 1)
    assert [item["id"] for item in page1["items"]] == [id2]
    assert page1["has_more"] is True
    page2 = api.diktaty_k_overeni(1, 1)
    assert [item["id"] for item in page2["items"]] == [id1]
    assert page2["has_more"] is False

    # Validace ID a textu pro overit_diktat
    assert api.overit_diktat("", "text")["ok"] is False
    assert api.overit_diktat("neexistuje", "text")["ok"] is False
    assert api.overit_diktat("diktat_no_wav", "text")["ok"] is False
    assert api.overit_diktat("diktat_no_prepis", "text")["ok"] is False
    assert api.overit_diktat(id1, "   ")["ok"] is False
    assert api.overit_diktat(id1, "a" * 5001)["ok"] is False
    assert api.overit_diktat(id1, 123)["ok"] is False  # type: ignore[arg-type]

    # Hraniční délka 5000 znaků projde
    res_max = api.overit_diktat(id1, "x" * 5000)
    assert res_max["ok"] is True

    res_ver = api.overit_diktat(id1, "  ehm první doslovně  ")
    assert res_ver["ok"] is True
    assert res_ver["verified_count"] == 1
    assert res_ver["total"] == 2
    assert res_ver["verified_s"] == 4.2
    assert res_ver["entry"]["overeno"] is True
    assert res_ver["entry"]["overeno_text"] == "ehm první doslovně"
    assert res_ver["entry"]["prepis"] == "ehm první text"

    # Přehrání validuje ID a volá winsound přes play_wav
    played: list[Path] = []
    monkeypatch.setattr(trenink, "play_wav", lambda p: played.append(p) or True)
    assert api.prehrat_diktat("neexistuje")["ok"] is False
    assert api.prehrat_diktat("diktat_no_wav")["ok"] is False
    assert api.prehrat_diktat(id2)["ok"] is True
    assert played == [config.diktaty_dir_path(tmp_path) / wav2]

    # Smazání nahrávky smaže WAV a vyčistí zvuk/prepis/overeno, ale ponechá záznam v historii
    assert api.smazat_diktat_zvuk("neexistuje")["ok"] is False
    del_res = api.smazat_diktat_zvuk(id1)
    assert del_res["ok"] is True
    assert del_res["verified_count"] == 0
    assert del_res["total"] == 1
    assert not (config.diktaty_dir_path(tmp_path) / wav1).exists()

    all_after = history.load_entries(tmp_path)
    e1_after = next(e for e in all_after if e.get("id") == id1)
    assert e1_after["text"] == "Uhlazený první."
    assert "zvuk" not in e1_after
    assert "prepis" not in e1_after
    assert "overeno" not in e1_after
    assert "overeno_text" not in e1_after

    # clear_all smaže vše včetně zbývajících prepis i WAVů
    history.clear_all(data=tmp_path)
    assert history.load_entries(tmp_path) == []
    assert list(config.diktaty_dir_path(tmp_path).glob("*.wav")) == []


def test_spolecne_diktaty_split_skip_long_and_oversample(tmp_path: Path) -> None:
    diktaty_root = spolecne.diktaty_dir(tmp_path)
    tz = timezone(timedelta(hours=1))
    base_dt = datetime(2026, 11, 1, 9, 0, tzinfo=tz)

    # 7 ověřených diktátů (vložené v promíchaném pořadí podle času)
    records = []
    for i in range(1, 8):
        uid = f"v{i}"
        wav_name = f"{uid}.wav"
        _write_pcm16_wav(diktaty_root / wav_name, seconds=2.0)
        records.append(
            {
                "id": uid,
                "cas": (base_dt + timedelta(hours=i)).isoformat(),
                "text": f"uhlazeno {i}",
                "prepis": f"surove {i}",
                "zvuk": wav_name,
                "overeno": True,
                "overeno_text": f"doslovně \t ehm  {i}",
            }
        )

    # Přidáme: neověřený, chybějící WAV, a > 30 s klip
    _write_pcm16_wav(diktaty_root / "unv.wav", seconds=2.0)
    _write_pcm16_wav(diktaty_root / "long1.wav", seconds=31.5)
    raw_entries = [
        records[4],  # v5 (5. podle data -> půjde do testu)
        records[0],
        {
            "id": "unv",
            "cas": (base_dt + timedelta(hours=20)).isoformat(),
            "text": "neověřeno",
            "prepis": "neověřeno",
            "zvuk": "unv.wav",
            "overeno": False,
        },
        {
            "id": "missing_wav",
            "cas": (base_dt + timedelta(hours=21)).isoformat(),
            "text": "chybí",
            "prepis": "chybí",
            "zvuk": "missing.wav",
            "overeno": True,
            "overeno_text": "chybí wav",
        },
        {
            "id": "long1",
            "cas": (base_dt + timedelta(hours=22)).isoformat(),
            "text": "dlouhý",
            "prepis": "dlouhý",
            "zvuk": "long1.wav",
            "overeno": True,
            "overeno_text": "dlouhý klip přes třicet sekund",
        },
        records[6],
        records[2],
        records[1],
        records[5],
        records[3],
    ]

    hist_path = spolecne.history_jsonl(tmp_path)
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in raw_entries) + "\n",
        encoding="utf-8",
    )

    info = spolecne.collect_verified_diktaty(tmp_path)
    assert info["total_verified"] == 7
    assert info["skipped_long"] == 1
    assert [r["id"] for r in info["train"]] == ["v1", "v2", "v3", "v4", "v6", "v7"]
    assert [r["id"] for r in info["test"]] == ["v5"]
    assert info["test"][0]["text"] == "doslovně ehm 5"
    assert abs(info["verified_minutes"] - (14.0 / 60.0)) < 1e-3

    # Test oversamplingu v spolecne.build_combined_training_entries (bez torch)
    d_train = spolecne.dataset_dir("diktaty_train", tmp_path)
    _write_pcm16_wav(d_train / "v1.wav", seconds=2.0)
    spolecne.write_manifest(
        d_train / "manifest.tsv",
        [{"file": "v1.wav", "text": "doslovně ehm 1", "seconds": 2.0}],
    )
    combined, cv_cnt, vety_cnt, diktaty_cnt = spolecne.build_combined_training_entries(
        tmp_path, vety_mult=5, diktaty_mult=3
    )
    assert cv_cnt == 0
    assert vety_cnt == 0
    assert diktaty_cnt == 1
    assert len(combined) == 3

    combined_zero, _, _, diktaty_cnt_zero = spolecne.build_combined_training_entries(
        tmp_path, vety_mult=5, diktaty_mult=0
    )
    assert diktaty_cnt_zero == 1
    assert len(combined_zero) == 0


def test_ab_test_diktaty_test_and_doladit_flag(tmp_path: Path) -> None:
    d_test = spolecne.dataset_dir("diktaty_test", tmp_path)
    _write_pcm16_wav(d_test / "v5.wav", seconds=1.5)
    spolecne.write_manifest(
        d_test / "manifest.tsv",
        [{"file": "v5.wav", "text": "doslovně pět", "seconds": 1.5}],
    )
    loaded = ab_test._load_dataset("diktaty_test", tmp_path, limit=None)
    assert len(loaded) == 1
    assert loaded[0][0] == "v5.wav"
    assert loaded[0][2] == "doslovně pět"

    # Ověř AST v doladit.py a ab_test.py bez importu torch
    doladit_src = Path("trenink/doladit.py").read_text(encoding="utf-8")
    assert "--diktaty-mult" in doladit_src
    ast.parse(doladit_src)
    ab_src = Path("trenink/ab_test.py").read_text(encoding="utf-8")
    assert '"diktaty_test"' in ab_src
    ast.parse(ab_src)


def test_app_prepis_preserves_spoken_words_without_snippet_or_command_expansion(tmp_path: Path, monkeypatch) -> None:
    store = config.ConfigStore(tmp_path)
    store.update_values(
        {
            "historie": True,
            "ai_uprava": False,
            "trenink_ukladat_zvuk": True,
            "slovnik": {"slova": [], "opravy": [["klód", "Claude"]]},
            "zkratky": [["můj podpis", "S pozdravem,\nAdam Novák"]],
        }
    )

    fake_overlay = MagicMock()
    fake_tray = MagicMock()
    fake_whisper = MagicMock()
    fake_whisper.transcribe.return_value = "můj podpis klód"
    fake_ai = MagicMock()
    hook = hotkey.GlobalHotkeyHook(on_action=lambda a: None, klavesa_vk=hotkey.VK_RCONTROL)
    hook.start()
    try:
        with (
            patch.object(app_mod.overlay, "Overlay", return_value=fake_overlay),
            patch.object(app_mod.tray, "TrayController", return_value=fake_tray),
            patch.object(app_mod.asr, "WhisperServer", return_value=fake_whisper),
            patch.object(app_mod.llm, "ClaudeAI", return_value=fake_ai),
            patch.object(app_mod.hotkey, "GlobalHotkeyHook", return_value=hook),
        ):
            application = app_mod.MluvkaApp(store, data=tmp_path)
            application._whisper_ready.set()
            application._model_loaded = True
            monkeypatch.setattr(app_mod.output, "paste_text", lambda *_a, **_kw: True)
            monkeypatch.setattr(app_mod.channel, "send_async", lambda *_a, **_kw: None)

            samples = _speech_samples(1.2)
            cfg = store.snapshot()
            application._handle_transcription_job("dictate", (samples, cfg, "Code", "", ""))

            # Přesná zkratka bez dalších slov: text se vloží celý, prepis zůstane doslovný
            fake_whisper.transcribe.return_value = "můj podpis"
            application._handle_transcription_job("dictate", (samples, cfg, "Code", "", ""))

            # Hlasový příkaz nový řádek: text zalomí, prepis zůstane mluvený
            fake_whisper.transcribe.return_value = "první nový řádek druhý"
            application._handle_transcription_job("dictate", (samples, cfg, "Code", "", ""))

            entries = history.load_entries(tmp_path)
            assert len(entries) == 3
            assert entries[0]["text"] == "S pozdravem,\nAdam Novák Claude"
            assert entries[0]["prepis"] == "můj podpis Claude"
            assert entries[1]["text"] == "S pozdravem,\nAdam Novák"
            assert entries[1]["prepis"] == "můj podpis"
            assert entries[2]["text"] == "první\nDruhý"
            assert entries[2]["prepis"] == "první nový řádek druhý"
    finally:
        hook.stop()


def test_stop_playback_on_delete_prune_and_validation(tmp_path: Path, monkeypatch) -> None:
    import pytest

    store = config.ConfigStore(tmp_path)
    store.update_values({"trenink_ukladat_zvuk": True})
    api = okno.Api(data=tmp_path)
    samples = _speech_samples(1.2)

    with pytest.raises(ValueError):
        trenink.save_dictation_wav("..", samples, data=tmp_path)
    with pytest.raises(ValueError):
        trenink.save_dictation_wav("abc", np.array([], dtype=np.float32), data=tmp_path)

    stops: list[bool] = []
    monkeypatch.setattr(trenink, "stop_playback", lambda: stops.append(True))

    now = datetime.now(timezone.utc)
    wav_a = trenink.save_dictation_wav("a1", samples, data=tmp_path)
    history.append_entry("Starý", "starý", 2.0, 0.2, "Code", cas=now - timedelta(days=40), entry_id="a1", zvuk=wav_a, prepis="starý", data=tmp_path)

    wav_b = trenink.save_dictation_wav("b1", samples, data=tmp_path)
    history.append_entry("Nový", "nový", 2.0, 0.2, "Code", cas=now, entry_id="b1", zvuk=wav_b, prepis="nový", data=tmp_path)

    assert api.smazat_diktat_zvuk("b1")["ok"] is True
    assert len(stops) == 1

    # Prune starší než 30 dnů smaže i záznam se starým prepis a jeho WAV
    removed = history.prune_history(max_days=30, now=now, data=tmp_path)
    assert removed == 1
    assert not (config.diktaty_dir_path(tmp_path) / wav_a).exists()

    assert api.clear_history()["ok"] is True
    assert len(stops) == 2

    # SADA_LABELS má český popisek pro diktaty_test
    assert trenink.SADA_LABELS["diktaty_test"] == "Vlastní diktáty"


def test_spolecne_unique_stem_loop(tmp_path: Path) -> None:
    d_dir = spolecne.diktaty_dir(tmp_path)
    _write_pcm16_wav(d_dir / "w1.wav", 2.0)
    _write_pcm16_wav(d_dir / "w2.wav", 2.0)
    _write_pcm16_wav(d_dir / "w3.wav", 2.0)
    entries = [
        {"id": "dup-2", "cas": "2026-11-10T10:00:00+01:00", "zvuk": "w1.wav", "overeno": True, "overeno_text": "první"},
        {"id": "dup", "cas": "2026-11-10T10:01:00+01:00", "zvuk": "w2.wav", "overeno": True, "overeno_text": "druhý"},
        {"id": "dup", "cas": "2026-11-10T10:02:00+01:00", "zvuk": "w3.wav", "overeno": True, "overeno_text": "třetí"},
    ]
    res = spolecne.select_and_split_diktaty(entries, d_dir)
    stems = [r["stem"] for r in res["train"] + res["test"]]
    assert len(stems) == 3
    assert len(set(stems)) == 3

