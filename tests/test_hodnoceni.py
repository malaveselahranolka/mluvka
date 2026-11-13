from mluvka import hodnoceni as h


def test_znamka_boundaries_inclusive() -> None:
    assert h.znamka(0.0) == "A+"
    assert h.znamka(0.03) == "A+"
    assert h.znamka(0.0301) == "A"
    assert h.znamka(0.0648) == "B+"
    assert h.znamka(0.075) == "B+"
    assert h.znamka(0.1092) == "C+"
    assert h.znamka(0.30) == "F"
    assert h.znamka(0.31) == "F-"


def test_znamka_slovy_and_order() -> None:
    assert h.znamka_slovy("B+") == "B plus"
    assert h.znamka_slovy("F-") == "F mínus"
    assert h.znamka_slovy("A") == "A"
    assert h.poradi_znamky("A+") < h.poradi_znamky("B-") < h.poradi_znamky("F-")


def test_rychlost_boundaries() -> None:
    assert h.rychlost(1.3) == "instantni"
    assert h.rychlost(2.0) == "instantni"
    assert h.rychlost(3.4) == "rychla"
    assert h.rychlost(10.0) == "rychla"
    assert h.rychlost(10.1) == "pomala"
    assert [h.pocet_carek(k) for k in ("instantni", "rychla", "pomala")] == [9, 6, 3]


def test_prumer() -> None:
    assert abs(h.prumer(0.07, 0.05) - 0.06) < 1e-12
    assert h.prumer(None, 0.05) == 0.05
    assert h.prumer(None, None) is None


def _s(id_, haiku, prumer, s, znamka=None):
    return {"id": id_, "haiku": haiku, "prumer": prumer, "s_na_minutu": s, "znamka": znamka or (h.znamka(prumer) if prumer is not None else None)}


def test_doporucene_picks_and_merges() -> None:
    setupy = [
        _s("q8-b5-ai", True, 0.060, 5.1),
        _s("q8-b5", False, 0.065, 3.4),
        _s("q5-b1", False, 0.072, 1.3),
        _s("canary", False, 0.11, 1.0),  # C+: too inaccurate to count as "fastest"
        _s("nezmereno", False, None, None),
    ]
    rec = h.doporucene(setupy, ma_klic=True)
    assert [(r["klice"], r["setup_id"]) for r in rec] == [
        (["nejpresnejsi"], "q8-b5-ai"),
        (["nejrychlejsi"], "q5-b1"),
        (["offline"], "q8-b5"),
    ]
    # without a key, Haiku setups drop out and the most accurate merges with offline
    rec = h.doporucene(setupy, ma_klic=False)
    assert rec[0]["setup_id"] == "q8-b5" and rec[0]["klice"] == ["nejpresnejsi", "offline"]
    assert rec[0]["nazev"] == "Nejpřesnější a bez internetu"
    assert h.doporucene([], ma_klic=True) == []
