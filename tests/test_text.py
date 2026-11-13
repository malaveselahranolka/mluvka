"""Jednotkové testy pro mluvka.text.uprav."""

from __future__ import annotations

from mluvka.text import uprav


def test_preserves_diacritics_and_normalises_whitespace() -> None:
    raw = "  Příliš   žluťoučký\t\tkůň\n úpěl  ďábelské ódy.  "
    assert uprav(raw, {}) == "Příliš žluťoučký kůň úpěl ďábelské ódy."


def test_empty_and_whitespace() -> None:
    assert uprav("", {}) == ""
    assert uprav("   \t\n  ", {}) == ""


def test_newline_command_eats_spaces_and_single_punct_and_capitalises() -> None:
    assert uprav("Ahoj nový řádek jak se máš", {}) == "Ahoj\nJak se máš"
    assert uprav("Ahoj Nový Řádek, jak se máš?", {}) == "Ahoj\nJak se máš?"
    assert uprav("Ahoj nový řádek. další řádek", {}) == "Ahoj\nDalší řádek"


def test_paragraph_command() -> None:
    assert uprav("První odstavec. Nový odstavec druhý odstavec.", {}) == (
        "První odstavec.\n\nDruhý odstavec."
    )


def test_trailing_newline_command_preserved_for_output() -> None:
    assert uprav("Konec řádku nový řádek", {}) == "Konec řádku\n"


def test_fillers_removed_and_sentence_start_recapitalised() -> None:
    assert uprav("Ehm, dobrý den.", {}) == "Dobrý den."
    assert uprav("Ehm, hmmm, dobrý den.", {}) == "Dobrý den."
    assert uprav("Dobrý den. Ehm, jak se máte?", {}) == "Dobrý den. Jak se máte?"
    assert uprav("To je hm dobré, eee a ééé taky mmm fajn.", {}) == "To je dobré, a taky fajn."


def test_short_single_double_letters_not_removed_as_fillers() -> None:
    text = "Délka je 5 mm a 10 m, vitamín E nebo é a dvojité ee."
    assert uprav(text, {}) == text


def test_keeps_jako_proste_vlastne() -> None:
    text = "To je jako prostě vlastně úplně v pohodě."
    assert uprav(text, {}) == text


def test_fillers_never_match_inside_words_with_diacritics() -> None:
    text = "Škoda, že neehm a hmlovina či beee nejsou výplně."
    assert uprav(text, {}) == text


def test_dictionary_corrections_case_insensitive_unicode_boundaries() -> None:
    cfg = {
        "slovnik": {
            "opravy": [
                ["tvoho md", "CLAUDE.md"],
                ["agens md", "AGENTS.md"],
                ["škoda", "ŠKODA"],
            ]
        }
    }
    assert uprav("Otevři TVOHO MD a agens md, ne tvoho mdx.", cfg) == (
        "Otevři CLAUDE.md a AGENTS.md, ne tvoho mdx."
    )
    assert uprav("To je škoda a neškodná věc.", cfg) == "To je ŠKODA a neškodná věc."


def test_snippets_longest_trigger_first_and_exact_with_punct() -> None:
    cfg = {
        "zkratky": [
            ["podpis", "Adam"],
            ["můj pracovní podpis", "S pozdravem,\nAdam Novák"],
        ]
    }
    assert uprav("Můj pracovní podpis.", cfg) == "S pozdravem,\nAdam Novák"
    assert uprav("můj pracovní podpis!", cfg) == "S pozdravem,\nAdam Novák"
    assert uprav("Přidej můj pracovní podpis na konec.", cfg) == (
        "Přidej S pozdravem,\nAdam Novák na konec."
    )


def test_tidy_removes_space_before_punctuation() -> None:
    assert uprav("Ahoj , jak se máš ? Dobře ! Ano : jedna ; dvě .", {}) == (
        "Ahoj, jak se máš? Dobře! Ano: jedna; dvě."
    )


def test_snippet_with_trailing_newline_command_preserves_newline() -> None:
    cfg = {"zkratky": [["podpis", "Adam"]]}
    assert uprav("podpis nový řádek", cfg) == "Adam\n"


def test_filler_recap_does_not_uppercase_later_abbreviations() -> None:
    assert uprav("Ehm, je to např. v pořádku.", {}) == "Je to např. v pořádku."
    assert uprav("Ehm, 1. ledna.", {}) == "1. ledna."
    assert uprav("Ehm, hmmm, v pořádku. A dál 2. února.", {}) == "V pořádku. A dál 2. února."
    assert uprav("První věta. Ehm, druhá např. v pořádku.", {}) == "První věta. Druhá např. v pořádku."
