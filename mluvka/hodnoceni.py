"""Známka přesnosti (A+ … F-) a štítek rychlosti pro stránku Model. Čistá logika bez I/O."""

from __future__ import annotations

from typing import Any

# Horní meze průměrné chybovosti (WER, včetně) pro každou známku; nad poslední mezí je F-.
ZNAMKY: list[tuple[str, float]] = [
    ("A+", 0.03), ("A", 0.045), ("A-", 0.06),
    ("B+", 0.075), ("B", 0.09), ("B-", 0.105),
    ("C+", 0.12), ("C", 0.14), ("C-", 0.16),
    ("D+", 0.18), ("D", 0.21), ("D-", 0.24),
    ("F", 0.30),
]
NEJHORSI = "F-"

# Sekundy zpracování na 1 minutu řeči (včetně Haiku, je-li zapnuté).
RYCHLOST_MEZE = (("instantni", 2.0), ("rychla", 10.0))
RYCHLOST_NAZEV = {"instantni": "Instantní", "rychla": "Rychlá", "pomala": "Pomalá"}
RYCHLOST_CARKY = {"instantni": 9, "rychla": 6, "pomala": 3}

_POPISY = {
    "nejpresnejsi": "Nejpřesnější",
    "nejrychlejsi": "Nejrychlejší",
    "offline": "Bez internetu",
}


def znamka(prumer_wer: float) -> str:
    for z, mez in ZNAMKY:
        if prumer_wer <= mez + 1e-12:
            return z
    return NEJHORSI


def znamka_slovy(z: str) -> str:
    if z.endswith("+"):
        return f"{z[:-1]} plus"
    if z.endswith("-"):
        return f"{z[:-1]} mínus"
    return z


def poradi_znamky(z: str) -> int:
    """0 = A+, rostoucí = horší. Pro porovnání „B- nebo lepší“."""
    names = [n for n, _ in ZNAMKY] + [NEJHORSI]
    return names.index(z)


def rychlost(s_na_minutu: float) -> str:
    for klic, mez in RYCHLOST_MEZE:
        if s_na_minutu <= mez:
            return klic
    return "pomala"


def pocet_carek(klic: str) -> int:
    return RYCHLOST_CARKY[klic]


def prumer(cv: float | None, fleurs: float | None) -> float | None:
    vals = [v for v in (cv, fleurs) if v is not None]
    return sum(vals) / len(vals) if vals else None


def doporucene(setupy: list[dict[str, Any]], ma_klic: bool) -> list[dict[str, Any]]:
    """Doporučená nastavení ze změřených setupů.

    Každý setup: {id, haiku: bool, prumer: float|None, s_na_minutu: float|None, znamka: str|None}.
    Vrátí [{klice: [...], nazev, setup_id}] — shodné setupy sloučí do jedné karty.
    """
    zmerene = [s for s in setupy if s.get("prumer") is not None and (ma_klic or not s.get("haiku"))]
    volby: list[tuple[str, dict[str, Any]]] = []
    if zmerene:
        volby.append(("nejpresnejsi", min(zmerene, key=lambda s: s["prumer"])))
    rychle = [
        s for s in zmerene
        if s.get("s_na_minutu") is not None and s.get("znamka") and poradi_znamky(s["znamka"]) <= poradi_znamky("B-")
    ]
    if rychle:
        volby.append(("nejrychlejsi", min(rychle, key=lambda s: s["s_na_minutu"])))
    offline = [s for s in zmerene if not s.get("haiku")]
    if offline:
        volby.append(("offline", min(offline, key=lambda s: s["prumer"])))

    out: list[dict[str, Any]] = []
    for klic, s in volby:
        hit = next((o for o in out if o["setup_id"] == s["id"]), None)
        if hit:
            hit["klice"].append(klic)
        else:
            out.append({"klice": [klic], "setup_id": s["id"]})
    for o in out:
        names = [_POPISY[k] for k in o["klice"]]
        o["nazev"] = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " a " + names[-1].lower()
    return out
