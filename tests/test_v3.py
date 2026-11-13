"""Komplexní testy pro Task v3 (opravy vad, paměť chyb, import slovníku, Trénink, darovací balíček)."""

from __future__ import annotations

import json
import os
import wave
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from mluvka import (
    app as mluvka_app,
    asr,
    config,
    dar,
    history,
    llm,
    okno,
    pamet,
    slovnik_import,
    trenink,
    wer,
)


def _tone(seconds: float = 0.2, rate: int = 16000) -> np.ndarray:
    t = np.linspace(0.0, seconds, int(rate * seconds), endpoint=False, dtype=np.float32)
    return 0.1 * np.sin(2.0 * np.pi * 440.0 * t).astype(np.float32)


def _append(
    data: Path,
    txt: str,
    *,
    surovy: str | None = None,
    cas: datetime | None = None,
    entry_id: str | None = None,
    zvuk: str = "",
    aplikace: str = "notepad",
) -> dict:
    return history.append_entry(
        txt,
        txt if surovy is None else surovy,
        0.5,
        0.1,
        aplikace,
        cas=cas,
        entry_id=entry_id,
        zvuk=zvuk,
        data=data,
    )


def test_a1_history_clear_delete_prune_remove_export_and_wavs(tmp_path: Path) -> None:
    dikt_dir = config.diktaty_dir_path(tmp_path)
    dikt_dir.mkdir(parents=True, exist_ok=True)
    wav1 = dikt_dir / "e1.wav"
    wav2 = dikt_dir / "e2.wav"
    wav1.write_bytes(b"RIFF1")
    wav2.write_bytes(b"RIFF2")

    old_t = datetime.now().astimezone() - timedelta(days=40)
    new_t = datetime.now().astimezone() - timedelta(days=1)

    e1 = _append(tmp_path, "starý text", cas=old_t, entry_id="e1", zvuk="e1.wav")
    e2 = _append(tmp_path, "nový text", cas=new_t, entry_id="e2", zvuk="e2.wav")
    assert e1["id"] == "e1" and e2["id"] == "e2"

    md_path = config.history_md_path(tmp_path)
    md_path.write_text("# Export", encoding="utf-8")

    removed = history.prune_history(30, data=tmp_path)
    assert removed == 1
    assert not wav1.exists()
    assert wav2.exists()
    assert not md_path.exists()

    md_path.write_text("# Export 2", encoding="utf-8")
    assert history.delete_entry(entry_id="e2", data=tmp_path) is True
    assert not wav2.exists()
    assert not md_path.exists()

    wav3 = dikt_dir / "e3.wav"
    wav3.write_bytes(b"RIFF3")
    _append(tmp_path, "třetí", cas=new_t, entry_id="e3", zvuk="e3.wav")
    md_path.write_text("# Export 3", encoding="utf-8")
    assert history.clear_all(data=tmp_path) == 1
    assert not wav3.exists()
    assert not md_path.exists()
    assert history.load_entries(tmp_path) == []


def test_a2_history_unique_id_and_legacy_matching(tmp_path: Path) -> None:
    ts = datetime(2026, 11, 10, 10, 0, 0).astimezone()
    ts_str = ts.isoformat(timespec="seconds")
    a = _append(tmp_path, "stejný", cas=ts)
    b = _append(tmp_path, "stejný", cas=ts)
    assert a["id"] != b["id"] and len(a["id"]) == 32

    assert history.delete_entry(entry_id=a["id"], data=tmp_path) is True
    rem = history.load_entries(tmp_path)
    assert len(rem) == 1 and rem[0]["id"] == b["id"]

    jl = config.history_jsonl_path(tmp_path)
    jl.write_text(
        json.dumps({"cas": ts_str, "text": "legacy"}) + "\n"
        + json.dumps({"cas": ts_str, "text": "legacy"}) + "\n"
        + json.dumps({"cas": ts_str, "text": "jiný"}) + "\n",
        encoding="utf-8",
    )
    assert history.delete_entry(cas=ts_str, text="legacy", data=tmp_path) is True
    after = history.load_entries(tmp_path)
    assert len(after) == 2
    assert sum(1 for x in after if x["text"] == "legacy") == 1


def test_a3_rewrite_retries_permission_error_and_app_run_handles_prune_error(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    ts = datetime(2026, 11, 10, 10, 0, 0).astimezone()
    _append(tmp_path, "a", cas=ts, entry_id="x1")
    _append(tmp_path, "b", cas=ts + timedelta(minutes=1), entry_id="x2")

    real_replace = os.replace
    attempts = {"n": 0}

    def flaky_replace(src, dst):
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(history.os, "replace", flaky_replace)
    monkeypatch.setattr(history.time, "sleep", lambda _s: None)
    assert history.delete_entry(entry_id="x1", data=tmp_path) is True
    assert attempts["n"] == 4
    assert [e["id"] for e in history.load_entries(tmp_path)] == ["x2"]

    store = config.ConfigStore(tmp_path)
    monkeypatch.setattr(mluvka_app.overlay, "Overlay", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.asr, "WhisperServer", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.hotkey, "GlobalHotkeyHook", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.tray, "TrayController", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.audio, "Recorder", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mluvka_app.audio, "ensure_sounds", lambda _d=None: None)
    app_inst = mluvka_app.MluvkaApp(store, data=tmp_path)

    monkeypatch.setattr(
        mluvka_app.history,
        "prune_history",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("disk lock")),
    )
    monkeypatch.setattr(mluvka_app.threading, "Thread", lambda **k: MagicMock(start=lambda: None, join=lambda timeout=None: None))
    app_inst._overlay.run = lambda: None
    with caplog.at_level("ERROR", logger="mluvka.app"):
        app_inst.run()
    assert any("promazávání staré historie" in rec.message for rec in caplog.records)


def test_a4_a5_config_store_invalid_and_two_stores_concurrent(tmp_path: Path, monkeypatch) -> None:
    s1 = config.ConfigStore(tmp_path)
    s2 = config.ConfigStore(tmp_path)

    before = dict(s1.cfg)
    s1.path.write_text("{broken", encoding="utf-8")
    assert s1.reload_if_changed() is False
    assert s1.config_invalid is True
    with pytest.raises(config.ConfigInvalidError):
        s1.save()
    with pytest.raises(config.ConfigInvalidError):
        s1.update_values({"zvuky": False})
    assert s1.cfg == before
    assert s1.path.read_text(encoding="utf-8") == "{broken"

    api = okno.Api(data=tmp_path)
    st = api.get_state()
    assert st["config_invalid"] is True
    res = api.save_settings({"zvuky": False})
    assert res["ok"] is False
    assert res["error"] == "config.json obsahuje chybu. Oprav ho, nebo ho smaž a Mluvka vytvoří nový."
    res_k = api.set_api_key("sk-test")
    assert res_k["ok"] is False and res_k["error"] == res["error"]

    s1.path.write_text(json.dumps(config.DEFAULT_CONFIG, ensure_ascii=False), encoding="utf-8")
    assert s1.reload_if_changed() is True
    assert s2.reload_if_changed() is True

    s1.set_value("klavesa_vk", 120)
    assert s2.reload_if_changed() is True
    assert s2.get("klavesa_vk") == 120

    s2.set_value("zvuky", False)
    assert s1.reload_if_changed() is True
    assert s1.get("zvuky") is False

    s1.set_value("historie_dny", 90)
    orig_status = config._try_load_config_status
    injected = {"done": False}

    def interleaved_status(data=None):
        res_st = orig_status(data=data)
        if not injected["done"]:
            injected["done"] = True
            s1.set_value("historie_dny", 7)
        return res_st

    monkeypatch.setattr(config, "_try_load_config_status", interleaved_status)
    assert s2.reload_if_changed() is True
    assert s2.get("historie_dny") == 90
    assert s2.reload_if_changed() is True
    assert s2.get("historie_dny") == 7


def test_a6_a7_a8_hardening_pause_and_timeouts(tmp_path: Path, monkeypatch) -> None:
    assert 19 not in okno.ALLOWED_VK
    api = okno.Api(data=tmp_path)
    res = api.save_settings({"klavesa_vk": 19})
    assert res["ok"] is False

    assert getattr(api, "_cfg_store", None) is None
    assert vars(api) == {}

    store = config.ConfigStore(tmp_path)
    store.set_value("claude_api_klic", "sk-ant-timeout-check")
    ai = llm.ClaudeAI(data=tmp_path)
    client = ai._get_client()
    t_val = client.timeout
    sec = getattr(t_val, "read", t_val)
    assert float(sec) == 30.0
    ai.close()


def test_b_history_edit_pamet_suggestions_and_claude_examples(tmp_path: Path, monkeypatch) -> None:
    ts = datetime(2026, 11, 12, 9, 0, 0).astimezone()
    e = _append(tmp_path, "otevři klód kód prosím", surovy="otevři klód kód prosím", cas=ts)
    api = okno.Api(data=tmp_path)
    res1 = api.edit_history(entry_id=e["id"], new_text="otevři Claude Code prosím")
    assert res1["ok"] is True
    assert res1["entry"]["text"] == "otevři klód kód prosím"
    assert res1["entry"]["surovy"] == "otevři klód kód prosím"
    assert res1["entry"]["opraveno"] == "otevři Claude Code prosím"
    assert res1["zmeny"] == [["klód kód", "Claude Code"]]
    assert res1["suggestions"] == [["klód kód", "Claude Code"]]

    added = api.add_oprava("klód kód", "Claude Code")
    assert added["ok"] is True and added["added"] is True
    e2 = _append(tmp_path, "spusť klód kód", cas=ts + timedelta(minutes=5))
    res2 = api.edit_history(entry_id=e2["id"], new_text="spusť Claude Code")
    assert res2["suggestions"] == []

    assert pamet.extract_changes("jedna dva tři čtyři pět konec", "alfa beta gama delta epsilon konec") == []

    long_src = "A" * 260
    long_dst = "B" * 260
    for idx in range(7):
        pamet.add(f"m{idx}", f"{idx} {long_src}", f"{idx} {long_dst}", data=tmp_path)
    top_items = pamet.top(20, data=tmp_path)
    assert any(it["z"] == "klód kód" and it["count"] == 2 for it in top_items)

    captured: dict[str, str] = {}
    ai = llm.ClaudeAI(data=tmp_path)

    def fake_chat(sys_p: str, user_p: str, timeout: float = 30.0) -> str:
        captured["sys"] = sys_p
        captured["user"] = user_p
        return "hotovo"

    monkeypatch.setattr(ai, "chat", fake_chat)
    cfg = dict(config.DEFAULT_CONFIG)
    examples = pamet.recent_examples(5, data=tmp_path)
    assert len(examples) == 5
    out = ai.upravit_text("test text", cfg, priklady=examples)
    assert out == "hotovo"
    assert "<priklady_oprav>" in llm.CLEANUP_SYSTEM_PROMPT
    assert "<priklady_oprav>" in captured["user"]
    assert captured["user"].index("<priklady_oprav>") < captured["user"].index("<text>")
    for src_s, dst_s in examples:
        assert len(src_s) <= 200 and len(dst_s) <= 200
    assert ("1 " + "A" * 198) not in captured["user"]
    ai.close()

    # P2#1: add_oprava odmítne prázdný cílový text po ořezání
    assert api.add_oprava("klód", "   ")["ok"] is False

    # P2#2: no-op úprava nezapíše prázdnou položku a nevytlačí příklady v recent_examples
    pam_before = len(pamet.load_entries(tmp_path))
    noop = api.edit_history(entry_id=e2["id"], new_text="spusť Claude Code")
    assert noop["ok"] is True and noop["zmeny"] == []
    assert len(pamet.load_entries(tmp_path)) == pam_before
    pamet.add("noop1", "stejné", "stejné", data=tmp_path)
    assert len(pamet.recent_examples(5, data=tmp_path)) == 5

    assert pamet.clear(data=tmp_path) >= 7
    assert pamet.top(20, data=tmp_path) == []


def test_c_slovnik_import_parser_and_api(tmp_path: Path, monkeypatch) -> None:
    expected_prompt = (
        "Znáš mě z našich konverzací. Připrav mi slovník pro diktovací aplikaci Mluvka, která převádí moji češtinu na text.\n"
        "Vypiš slova, která často používám a která by český model rozpoznávání řeči mohl přepsat špatně: jména lidí, firem a míst, názvy produktů, aplikací a nástrojů, odborné výrazy, anglická slova a zkratky.\n"
        "U slov, kde čekáš konkrétní zkomoleninu, přidej opravu ve tvaru [jak to model nejspíš napíše, jak to má být], například [\"klód kód\", \"Claude Code\"].\n"
        "Odpověz POUZE jako JSON v tomto tvaru, bez dalšího textu:\n"
        "{\"slova\": [\"…\"], \"opravy\": [[\"…\", \"…\"]]}\n"
        "Nejvýš 150 slov a 50 oprav. Nevymýšlej si, uváděj jen to, co ode mě opravdu znáš."
    )
    assert slovnik_import.SLOVNIK_PROMPT == expected_prompt

    raw = (
        "Jasně, tady máš návrh slovníku:\n"
        "```json\n"
        "{\n"
        '  "slova": [" Anthropic ", "anthropic", "", "Pytest", "Docker"],\n'
        '  "opravy": [["klód", "Claude"], ["KLÓD", "claude"], ["Stejné", "Stejné"], ["claude", "Claude"], ["", "X"]]\n'
        "}\n"
        "```\n"
        "Doufám, že pomůže!"
    )
    parsed = slovnik_import.parse(raw)
    assert parsed["slova"] == ["Anthropic", "Pytest", "Docker"]
    assert parsed["opravy"] == [["klód", "Claude"], ["claude", "Claude"]]

    embedded = 'Tady: {"slova": ["a{b}c", "Mluvka"], "opravy": []} a pak {ještě něco}'
    assert slovnik_import.parse(embedded) == {"slova": ["a{b}c", "Mluvka"], "opravy": []}

    with pytest.raises(ValueError):
        slovnik_import.parse("úplný nesmysl bez jsonu")
    with pytest.raises(ValueError):
        slovnik_import.parse('{"slova": [123], "opravy": []}')
    with pytest.raises(ValueError):
        slovnik_import.parse(json.dumps({"slova": ["x" * 61], "opravy": []}))
    with pytest.raises(ValueError):
        slovnik_import.parse(json.dumps({"slova": [f"w{i}" for i in range(301)], "opravy": []}))
    with pytest.raises(ValueError):
        slovnik_import.parse(json.dumps({"slova": [], "opravy": [[f"a{i}", f"b{i}"] for i in range(301)]}))

    clip_calls: list[str] = []

    def fake_set_clip(txt: str) -> int:
        clip_calls.append(txt)
        return 1

    monkeypatch.setattr(okno.output, "set_clipboard_text", fake_set_clip)
    store = config.ConfigStore(tmp_path)
    store.update_values({"slovnik": {"slova": ["Docker"], "opravy": [["klód", "Claude"]]}})
    api = okno.Api(data=tmp_path)
    assert api.copy_slovnik_prompt()["ok"] is True
    assert clip_calls == [slovnik_import.SLOVNIK_PROMPT]

    bad_prev = api.preview_slovnik_import("nesmysl")
    assert bad_prev["ok"] is False
    assert bad_prev["error"] == "Odpověď se nepodařilo přečíst. Zkontroluj, že je to JSON ve tvaru z promptu."

    prev = api.preview_slovnik_import(raw)
    assert prev["ok"] is True
    assert prev["slova"] == ["Anthropic", "Pytest"]
    assert prev["opravy"] == [["claude", "Claude"]]
    assert prev["existing_count"] == 2

    applied = api.apply_slovnik_import({"slova": prev["slova"], "opravy": prev["opravy"]})
    assert applied["ok"] is True
    assert applied["added_slova"] == 2
    assert applied["added_opravy"] == 1
    assert set(applied["config"]["slovnik"]["slova"]) == {"Docker", "Anthropic", "Pytest"}


def test_d_wer_trenink_recording_and_measurement_mocked(tmp_path: Path) -> None:
    assert wer.normalise_for_wer("Ahoj, světe! 123?") == ["ahoj", "světe", "123"]
    assert wer.levenshtein_words(["ahoj", "světe"], ["ahoj", "bramboro"]) == 1
    assert wer.compute_wer(["dobrý den světe"], ["dobrý noc světe"]) == pytest.approx(1 / 3)
    diff = wer.diff_words("dobrý den světe", "Dobrý, noc světe!")
    assert diff == [
        {"word": "Dobrý,", "ok": True},
        {"word": "noc", "ok": False},
        {"word": "světe!", "ok": True},
    ]

    sentences = trenink.load_sentences()
    assert len(sentences) >= 90

    trenink.save_sentence_recording(1, _tone(0.3), data=tmp_path)
    trenink.save_sentence_recording(1, _tone(0.5), data=tmp_path)
    trenink.save_sentence_recording(2, _tone(0.25), data=tmp_path)
    recs = trenink.load_tsv(tmp_path)
    assert recs["001"]["seconds"] == pytest.approx(0.5, abs=0.02)
    assert recs["001"]["sentence"] == sentences[0]
    with wave.open(str(recs["001"]["wav_path"]), "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 16000)

    seen_prompts: list[str] = []

    class _FakeServer:
        def __init__(self, data=None):
            self.closed = False

        def start(self, timeout_s=60.0):
            pass

        def transcribe(self, samples, prompt=""):
            seen_prompts.append(prompt)
            return sentences[0] if len(seen_prompts) == 1 else "úplně chybný přepis"

        def close(self):
            self.closed = True

    prog: list[tuple[int, int]] = []
    report = trenink.measure(
        tmp_path,
        on_progress=lambda d, t: prog.append((d, t)),
        server_factory=lambda d: _FakeServer(d),
    )
    assert seen_prompts == ["", ""]
    assert prog == [(1, 2), (2, 2)]
    assert report["n"] == 2
    assert 0.0 < report["wer"] < 2.0
    assert len(report["worst"]) == 1
    assert config.trenink_mereni_path(tmp_path).is_file()
    loaded_meas = trenink.load_measurement(tmp_path)
    assert loaded_meas is not None and loaded_meas["n"] == 2


def test_d_dictation_wav_opt_in_and_cleanup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mluvka_app.overlay, "Overlay", MagicMock)
    monkeypatch.setattr(mluvka_app.asr, "WhisperServer", MagicMock)
    monkeypatch.setattr(mluvka_app.hotkey, "GlobalHotkeyHook", MagicMock)
    monkeypatch.setattr(mluvka_app.tray, "TrayController", MagicMock)
    monkeypatch.setattr(mluvka_app.audio, "Recorder", MagicMock)
    monkeypatch.setattr(mluvka_app.output, "paste_text", lambda _t, **k: True)

    def _run_job(save_audio: bool) -> None:
        store = config.ConfigStore(tmp_path)
        cfg = store.update_values({
            "trenink_ukladat_zvuk": save_audio,
            "historie": True,
            "ai_uprava": False,
            "kanal_zapnuto": False,
        })
        app_inst = mluvka_app.MluvkaApp(store, data=tmp_path)
        app_inst._whisper_ready.set()
        app_inst._model_loaded = True
        app_inst._whisper.transcribe = MagicMock(return_value="test diktátu")
        app_inst._handle_transcription_job(
            "dictate", (_tone(0.3), cfg, "notepad", "", None)
        )

    _run_job(False)
    entries_off = history.load_entries(tmp_path)
    assert len(entries_off) == 1
    assert "zvuk" not in entries_off[0]
    assert not config.diktaty_dir_path(tmp_path).exists()

    _run_job(True)
    entries_on = history.load_entries(tmp_path)
    assert len(entries_on) == 2
    saved_entry = entries_on[1]
    assert saved_entry.get("zvuk") == f"{saved_entry['id']}.wav"
    wav_file = config.diktaty_dir_path(tmp_path) / saved_entry["zvuk"]
    assert wav_file.is_file()

    assert history.delete_entry(entry_id=saved_entry["id"], data=tmp_path) is True
    assert not wav_file.exists()


def test_d_live_whisper_server_smoke(tmp_path: Path) -> None:
    local_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Mluvka"
    exe = Path(os.environ.get("MLUVKA_WHISPER_EXE", str(local_root / "bin" / "whisper" / "whisper-server.exe")))
    model_env = os.environ.get("MLUVKA_WHISPER_MODEL", "").strip()
    model = Path(model_env) if model_env else config.whisper_model_path(local_root)
    if not exe.is_file() or not model.is_file():
        pytest.skip("whisper-server nebo model není nainstalován")

    trenink.save_sentence_recording(1, _tone(0.6), data=tmp_path)
    report = trenink.measure(
        tmp_path,
        server_factory=lambda d: asr.WhisperServer(d, exe_path=exe, model_path=model),
    )
    assert report["n"] == 1


def test_e_dar_package_privacy_consents_and_folder_guard(tmp_path: Path, monkeypatch) -> None:
    docs_dir = tmp_path / "Dokumenty"
    data_dir = tmp_path / "Data"
    monkeypatch.setenv("MLUVKA_DOCUMENTS", str(docs_dir))
    monkeypatch.setenv("MLUVKA_DATA", str(data_dir))

    store = config.ConfigStore(data_dir)
    store.update_values({
        "claude_api_klic": "sk-ant-TAJNY-KLIC-9999",
        "kanal_webhook": "https://hooks.slack.com/services/SECRET",
    })
    config.log_file_path(data_dir).write_text("SECRET LOG LINE", encoding="utf-8")

    sentences = trenink.load_sentences()
    trenink.save_sentence_recording(1, _tone(0.3), data=data_dir)

    dikt_wav = trenink.save_dictation_wav("d1", _tone(0.25), data=data_dir)
    ts = datetime(2026, 11, 12, 12, 34, 56).astimezone()
    _append(
        data_dir,
        "surový diktát",
        surovy="surový diktát",
        cas=ts,
        entry_id="d1",
        zvuk=dikt_wav,
        aplikace="TajnáApp",
    )
    history.update_text("d1", "opravený diktát pro dar", data=data_dir)

    api = okno.Api(data=data_dir)
    bad = api.vytvor_dar(False, [True, True, False])
    assert bad["ok"] is False

    r1 = api.vytvor_dar(False, [True, True, True])
    assert r1["ok"] is True
    p1 = Path(r1["path"])
    assert p1.parent == docs_dir and p1.exists()

    with zipfile.ZipFile(p1, "r") as zf:
        names = set(zf.namelist())
        assert "vety/001.wav" in names
        assert not any(n.startswith("diktaty/") for n in names)
        assert {"metadata.jsonl", "souhlas.txt", "README.txt"}.issubset(names)

        meta_rows = [json.loads(line) for line in zf.read("metadata.jsonl").decode("utf-8").splitlines() if line.strip()]
        assert len(meta_rows) == 1
        assert set(meta_rows[0].keys()) == {"file", "text", "druh", "delka_s", "sample_rate"}
        assert meta_rows[0]["file"] == "vety/001.wav"
        assert meta_rows[0]["text"] == sentences[0]
        assert meta_rows[0]["druh"] == "veta"
        assert meta_rows[0]["sample_rate"] == 16000

        souhlas = zf.read("souhlas.txt").decode("utf-8")
        assert dar.CONSENT_TEXT in souhlas
        readme = zf.read("README.txt").decode("utf-8")
        assert "zatím neurčeno, upřesní se před odesláním" in readme
        blob = souhlas + readme + zf.read("metadata.jsonl").decode("utf-8")
        assert "sk-ant-TAJNY-KLIC-9999" not in blob
        assert "TajnáApp" not in blob
        assert "SECRET LOG LINE" not in blob

    r2 = api.vytvor_dar(True, [True, True, True])
    assert r2["ok"] is True
    p2 = Path(r2["path"])
    assert p2.name.endswith("-2.zip")
    with zipfile.ZipFile(p2, "r") as zf:
        names2 = set(zf.namelist())
        assert "diktaty/00001.wav" in names2 and f"diktaty/{dikt_wav}" not in names2
        meta2 = [json.loads(line) for line in zf.read("metadata.jsonl").decode("utf-8").splitlines() if line.strip()]
        d_rows = [m for m in meta2 if m["druh"] == "diktat"]
        assert len(d_rows) == 1
        assert set(d_rows[0].keys()) == {"file", "text", "druh", "delka_s", "sample_rate"}
        assert d_rows[0]["text"] == "opravený diktát pro dar"
        raw_meta = zf.read("metadata.jsonl").decode("utf-8")
        assert "12:34:56" not in raw_meta
        assert "TajnáApp" not in raw_meta

    store.reload_if_changed()
    id1 = store.get("darce_id")
    assert isinstance(id1, str) and len(id1) == 32
    r3 = api.vytvor_dar(False, [True, True, True])
    assert Path(r3["path"]).name.endswith("-3.zip")
    store.reload_if_changed()
    assert store.get("darce_id") == id1

    opened: list[str] = []
    monkeypatch.setattr(okno.os, "startfile", opened.append, raising=False)
    assert api.otevrit_slozku(str(p1))["ok"] is True
    assert api.otevrit_slozku(r"C:\Windows\System32")["ok"] is False
    assert api.otevrit_slozku(str(docs_dir / ".." / "Windows"))["ok"] is False
