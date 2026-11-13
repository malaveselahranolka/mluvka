import json
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

from mluvka import mereni


def _wav(p: Path, seconds: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((np.ones(int(seconds * 16000)) * 1000).astype("<i2").tobytes())


def _data(tmp: Path) -> Path:
    (tmp / "models").mkdir(parents=True)
    for f in ("ggml-czechspeech-q5_0.bin", "ggml-czechspeech-q8_0.bin"):
        (tmp / "models" / f).write_bytes(b"lmgg" + b"0" * 100)
    fl = tmp / "bench" / "fleurs"
    refs = []
    for i in range(8):
        _wav(fl / f"{i}.wav", 20.0); refs.append(f"{i}.wav\tahoj světe číslo {i}")
    (fl / "refs.tsv").write_text("\n".join(refs) + "\n", encoding="utf-8")
    cv = tmp / "trenink" / "data" / "cv_test"
    rows = []
    for i in range(3):
        _wav(cv / f"{i:06d}.wav", 2.0); rows.append(f"{i:06d}.wav\tjedna dvě tři\t2.0")
    (cv / "manifest.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (tmp / "model").mkdir()
    (tmp / "model" / "stroj.json").write_text(json.dumps({"gpu": "Test GPU", "cpu": "c", "ram": "1 GB", "os": "x", "engine": "e"}), encoding="utf-8")
    return tmp


class FakeServer:
    instances: list["FakeServer"] = []

    def __init__(self, data, model_path=None, fail=False):
        self.closed = False; self.fail = fail; FakeServer.instances.append(self)

    def start(self, timeout_s=60.0):
        pass

    def transcribe(self, audio, cfg=None, beam_size=None):
        if self.fail:
            raise RuntimeError("boom")
        return "ahoj světe" if len(audio) > 16000 * 10 else "jedna dvě"

    def close(self):
        self.closed = True


class FakeAI:
    def __init__(self, data):
        pass

    def upravit_text(self, text, cfg=None):
        return text + " navíc"


def test_listing_grades_and_haiku_without_key(tmp_path) -> None:
    data = _data(tmp_path)
    (data / "model" / "mereni.json").write_text(json.dumps([
        {"setup_id": "ggml-czechspeech-q8_0.bin|b5|raw", "datum": "2026-11-13T10:00:00+01:00", "model": "ggml-czechspeech-q8_0.bin",
         "beam": 5, "haiku": False, "cv": {"wer": 0.0709, "n": 100}, "fleurs": {"wer": 0.0587, "n": 40}, "s_na_minutu": 3.1},
        {"setup_id": "ggml-czechspeech-q8_0.bin|b8|raw", "datum": "2026-11-13T10:05:00+01:00", "model": "ggml-czechspeech-q8_0.bin",
         "beam": 8, "haiku": False, "cv": None, "fleurs": {"wer": 0.06, "n": 40}, "s_na_minutu": 4.0},
    ]), encoding="utf-8")
    st = mereni.sestav_stav(data, {"model_soubor": "ggml-czechspeech-q8_0.bin", "beam_size": 5, "ai_uprava": True}, ma_klic=False)
    ids = [s["id"] for s in st["setupy"]]
    assert ids[0] == "ggml-czechspeech-q8_0.bin|b5|raw"  # active (no key → Haiku off) goes first
    assert st["setupy"][0]["znamka"] == "B+" and st["setupy"][0]["aktivni"] and st["setupy"][0]["rychlost"] == "rychla"
    assert "ggml-czechspeech-q8_0.bin|b8|raw" in ids  # measured experiment shows up as a row
    assert "ggml-czechspeech-q5_0.bin|b1|raw" in ids  # built-in
    assert next(s for s in st["setupy"] if s["haiku"])["pouzitelny"] is False
    assert mereni.nacti_mereni(tmp_path / "nic") == []


def test_active_adhoc_row_and_pridat_odebrat(tmp_path) -> None:
    data = _data(tmp_path)
    st = mereni.sestav_stav(data, {"model_soubor": "ggml-czechspeech-q5_0.bin", "beam_size": 8, "ai_uprava": False}, ma_klic=True)
    assert st["aktivni_id"] == "ggml-czechspeech-q5_0.bin|b8|raw"
    assert st["setupy"][0]["id"] == st["aktivni_id"]
    s = mereni.pridat_setup(data, "ggml-czechspeech-q5_0.bin", 8, True)
    assert s["vlastni"] and s["id"] == "ggml-czechspeech-q5_0.bin|b8|ai"
    with pytest.raises(ValueError, match="už v tabulce"):
        mereni.pridat_setup(data, "ggml-czechspeech-q5_0.bin", 8, True)
    with pytest.raises(ValueError):
        mereni.pridat_setup(data, "ggml-czechspeech-q5_0.bin", 3, False)
    with pytest.raises(ValueError):
        mereni.pridat_setup(data, "..\\evil.bin", 5, False)
    with pytest.raises(ValueError):
        mereni.odebrat_setup(data, "ggml-czechspeech-q8_0.bin|b5|raw")  # built-in
    mereni.odebrat_setup(data, s["id"])
    assert mereni.vlastni_setupy(data) == []


def test_zmer_setup_math_and_saves(tmp_path) -> None:
    data = _data(tmp_path)
    setup = mereni._setup("ggml-czechspeech-q8_0.bin", 5, True, False)
    seen = []
    row = mereni.zmer_setup(data, setup, {}, lambda d, t, f: seen.append((d, t)), server_factory=FakeServer, ai_factory=FakeAI,
                            stroj_info={"gpu": "x"})
    # cv refs "jedna dvě tři" vs hyp "jedna dvě navíc" → 1 substitution of 3 words
    assert abs(row["cv"]["wer"] - 1 / 3) < 1e-9 and row["cv"]["n"] == 3
    assert row["fleurs"]["n"] == 8
    assert row["s_na_minutu"] is not None and row["s_haiku_na_minutu"] is not None
    assert seen[-1][0] == seen[-1][1] == 3 + 8 + 2  # 8×20 s FLEURS → two ≥60 s pieces
    assert FakeServer.instances[-1].closed
    assert mereni.nacti_mereni(data)[-1]["setup_id"] == setup["id"]


def test_zmer_setup_closes_server_on_error_and_stop(tmp_path) -> None:
    data = _data(tmp_path)
    setup = mereni._setup("ggml-czechspeech-q8_0.bin", 5, False, False)
    with pytest.raises(RuntimeError):
        mereni.zmer_setup(data, setup, {}, server_factory=lambda d, model_path=None: FakeServer(d, fail=True), stroj_info={})
    assert FakeServer.instances[-1].closed
    ev = threading.Event(); ev.set()
    with pytest.raises(mereni.Zastaveno):
        mereni.zmer_setup(data, setup, {}, stop_event=ev, server_factory=FakeServer, stroj_info={})
    assert FakeServer.instances[-1].closed


def test_minutove_kusy_are_at_least_a_minute(tmp_path) -> None:
    data = _data(tmp_path)
    kusy = mereni.minutove_kusy(mereni.testovaci_sady(data)["fleurs"])
    assert len(kusy) == 2 and all(len(k) >= 60 * 16000 for k in kusy)
