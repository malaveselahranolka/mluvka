"""Testy pro automatické učení oprav z cizích aplikací (bez UIA a bez spánku)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from mluvka import app as app_mod
from mluvka import config, history, okno, pamet, sledovani


class _FakeReader:
    """Injektovatelná náhrada za UIA čtečku."""

    def __init__(self, steps: list[tuple[int, bool, str | None]]) -> None:
        self._steps = list(steps)
        self._idx = 0
        self.read_calls = 0
        self.closed = threading.Event()
        self.elem = object()

    def init_thread(self) -> None:
        return None

    def close_thread(self) -> None:
        self.closed.set()

    def _current(self) -> tuple[int, bool, str | None]:
        pos = min(self._idx, len(self._steps) - 1)
        return self._steps[pos]

    def advance(self) -> None:
        if self._idx + 1 < len(self._steps):
            self._idx += 1

    def get_foreground_hwnd(self) -> int:
        return self._current()[0]

    def get_focused(self) -> Any | None:
        return self.elem

    def is_password(self, elem: Any) -> bool:
        return self._current()[1]

    def read_text(self, elem: Any) -> str | None:
        self.read_calls += 1
        val = self._current()[2]
        self.advance()
        return val


def test_najdi_opravy_single_word_fix() -> None:
    res = sledovani.najdi_opravy(
        "Napiš klódovi zprávu.",
        "Předchozí text.\nNapiš klódovi zprávu.",
        "Předchozí text.\nNapiš Claudovi zprávu.",
    )
    assert res == ("Napiš Claudovi zprávu.", [["klódovi", "Claudovi"]])


def test_najdi_opravy_ignores_text_before_and_after_span() -> None:
    assert (
        sledovani.najdi_opravy(
            "Napiš klódovi zprávu.",
            "Napiš klódovi zprávu.",
            "Dobrý den, Napiš klódovi zprávu. Jak se máš?",
        )
        is None
    )

    # Dopisování za i před se ignoruje, oprava uprostřed zůstane zachycena.
    res = sledovani.najdi_opravy(
        "Napiš klódovi zprávu.",
        "Napiš klódovi zprávu.",
        "Prosím napiš Claudovi zprávu, díky moc.",
    )
    assert res == ("napiš Claudovi zprávu,", [["klódovi", "Claudovi"]])


def test_najdi_opravy_ignores_major_rewrite_and_full_delete() -> None:
    # Přepsání více než 40 % slov úseku -> nic
    assert (
        sledovani.najdi_opravy(
            "Dnes půjdu na nákup a pak domů.",
            "Dnes půjdu na nákup a pak domů.",
            "Zítra pojedu do práce a pak domů.",
        )
        is None
    )
    # Smazání celého vloženého úseku -> nic
    assert (
        sledovani.najdi_opravy(
            "Napiš klódovi zprávu.",
            "Úvod. Napiš klódovi zprávu.",
            "Úvod.",
        )
        is None
    )


def test_najdi_opravy_filters_case_punct_and_dissimilar() -> None:
    # Pouze velikost písmen / interpunkce -> nic
    assert (
        sledovani.najdi_opravy(
            "Řekni ahoj všem lidem.",
            "Řekni ahoj všem lidem.",
            "Řekni Ahoj všem lidem!",
        )
        is None
    )
    # Nepodobná náhrada (ratio < 0.5) -> nic
    assert (
        sledovani.najdi_opravy(
            "Program Mluvka umí psát.",
            "Program Mluvka umí psát.",
            "Program pes umí psát.",
        )
        is None
    )


def test_najdi_opravy_uses_last_occurrence() -> None:
    pred = "Napiš klódovi zprávu. Mezitím něco. Napiš klódovi zprávu."
    po = "Napiš klódovi zprávu. Mezitím něco. Napiš Claudovi zprávu."
    res = sledovani.najdi_opravy("Napiš klódovi zprávu.", pred, po)
    assert res == ("Napiš Claudovi zprávu.", [["klódovi", "Claudovi"]])


def test_sledovac_missing_baseline_learns_nothing() -> None:
    done = threading.Event()
    learned: list[tuple[str, list[list[str]]]] = []

    class _TrackedReader(_FakeReader):
        def close_thread(self) -> None:
            super().close_thread()
            done.set()

    tracked = _TrackedReader([(100, False, "Zcela jiný text v okně.")])
    s = sledovani.Sledovac(
        reader_factory=lambda: tracked,
        initial_delay_s=0.0,
        baseline_retries=2,
        baseline_retry_interval_s=0.0,
        poll_interval_s=0.005,
        max_duration_s=0.05,
    )
    s.start("Napiš klódovi zprávu.", "e1", lambda u, d: learned.append((u, d)))
    assert done.wait(2.0)
    assert learned == []
    assert tracked.read_calls == 2


def test_sledovac_chromium_retry_then_learns() -> None:
    # 1. pokus vrátí prázdno (Electron teprve zapíná UIA), 2. pokus baseline, pak oprava, pak jiné okno
    steps = [
        (100, False, ""),
        (100, False, "Napiš klódovi zprávu."),
        (100, False, "Napiš Claudovi zprávu."),
        (200, False, "Cizí tajný text v jiném okně."),
    ]
    reader = _FakeReader(steps)
    got_ev = threading.Event()
    learned: list[tuple[str, list[list[str]]]] = []

    def cb(u: str, d: list[list[str]]) -> None:
        learned.append((u, d))
        got_ev.set()

    s = sledovani.Sledovac(
        reader_factory=lambda: reader,
        initial_delay_s=0.0,
        baseline_retries=3,
        baseline_retry_interval_s=0.005,
        poll_interval_s=0.005,
        max_duration_s=1.0,
    )
    s.start("Napiš klódovi zprávu.", "e1", cb)
    assert got_ev.wait(2.0)
    assert reader.closed.wait(2.0)
    assert learned == [("Napiš Claudovi zprávu.", [["klódovi", "Claudovi"]])]
    # Z cizího okna (hwnd=200) se vůbec nečetlo (zůstaly 3 čtení).
    assert reader.read_calls == 3


def test_sledovac_password_field_stops_without_reading() -> None:
    reader = _FakeReader([(100, True, "Napiš klódovi zprávu.")])
    s = sledovani.Sledovac(
        reader_factory=lambda: reader,
        initial_delay_s=0.0,
        baseline_retries=3,
        baseline_retry_interval_s=0.0,
        poll_interval_s=0.005,
        max_duration_s=0.2,
    )
    learned: list[Any] = []
    s.start("Napiš klódovi zprávu.", "e1", lambda u, d: learned.append((u, d)))
    assert reader.closed.wait(2.0)
    assert learned == []
    assert reader.read_calls == 0


def test_sledovac_foreground_change_uses_last_snapshot_before_switch() -> None:
    reader = _FakeReader(
        [
            (100, False, "Tajný úvod. Napiš klódovi zprávu."),
            (100, False, "Tajný úvod. Napiš Claudovi zprávu."),
            (999, False, "Napiš jiné cizí texty"),
        ]
    )
    got_ev = threading.Event()
    learned: list[tuple[str, list[list[str]]]] = []

    s = sledovani.Sledovac(
        reader_factory=lambda: reader,
        initial_delay_s=0.0,
        baseline_retries=1,
        poll_interval_s=0.005,
        max_duration_s=1.0,
    )
    s.start(
        "Napiš klódovi zprávu.",
        "e1",
        lambda u, d: (learned.append((u, d)), got_ev.set()),
    )
    assert got_ev.wait(2.0)
    assert reader.closed.wait(2.0)
    assert learned == [("Napiš Claudovi zprávu.", [["klódovi", "Claudovi"]])]


def test_sledovac_new_start_cancels_previous() -> None:
    gate1 = threading.Event()
    calls1: list[Any] = []
    calls2: list[tuple[str, list[list[str]]]] = []
    ev2 = threading.Event()

    class _SlowReader1(_FakeReader):
        def read_text(self, elem: Any) -> str | None:
            gate1.set()
            return super().read_text(elem)

    r1 = _SlowReader1(
        [
            (100, False, "Napiš klódovi zprávu."),
            (100, False, "Napiš Claudovi zprávu."),
        ]
    )
    r2 = _FakeReader(
        [
            (200, False, "Otevři kubernetis pod."),
            (200, False, "Otevři Kubernetes pod."),
            (300, False, "cizí"),
        ]
    )
    readers = iter([r1, r2])
    s = sledovani.Sledovac(
        reader_factory=lambda: next(readers),
        initial_delay_s=0.0,
        baseline_retries=1,
        poll_interval_s=0.02,
        max_duration_s=0.2,
    )
    s.start("Napiš klódovi zprávu.", "e1", lambda u, d: calls1.append((u, d)))
    assert gate1.wait(2.0)
    # Okamžitě začne nový diktát ještě před ukončením prvního sledování
    s.start("Otevři kubernetis pod.", "e2", lambda u, d: (calls2.append((u, d)), ev2.set()))
    assert r1.closed.wait(2.0)
    assert ev2.wait(2.0)
    assert calls1 == []
    assert calls2 == [("Otevři Kubernetes pod.", [["kubernetis", "Kubernetes"]])]


def test_app_on_learned_integration_and_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app_mod.setup_logging(tmp_path)
    try:
        store = config.ConfigStore(tmp_path)
        fake_overlay = MagicMock()
        fake_tray = MagicMock()
        fake_whisper = MagicMock()
        fake_whisper.transcribe.return_value = "Napiš klódovi zprávu."

        reader = _FakeReader(
            [
                (100, False, "Soukromý kontext aplikace. Napiš klódovi zprávu."),
                (100, False, "Soukromý kontext aplikace. Napiš Claudovi zprávu."),
                (200, False, "Okno 2"),
            ]
        )
        fast_sledovac = sledovani.Sledovac(
            reader_factory=lambda: reader,
            initial_delay_s=0.0,
            baseline_retries=1,
            poll_interval_s=0.005,
            max_duration_s=1.0,
        )

        with (
            patch("mluvka.overlay.Overlay", return_value=fake_overlay),
            patch("mluvka.tray.TrayController", return_value=fake_tray),
            patch("mluvka.asr.WhisperServer", return_value=fake_whisper),
            patch("mluvka.sledovani.Sledovac", return_value=fast_sledovac),
            patch("mluvka.output.paste_text", return_value=True),
        ):
            inst = app_mod.MluvkaApp(store, data=tmp_path)
            inst._model_loaded = True
            inst._whisper_ready.set()

            loud = np.full(16000, 0.2, dtype=np.float32)
            inst._handle_transcription_job("dictation", (loud, store.cfg, "Code.exe", "", None))
            assert reader.closed.wait(2.0)

            # Zápis do slovník.opravy, pamet_chyb a historie se štítkem opraveno
            entries = history.load_entries(tmp_path)
            assert len(entries) == 1
            assert entries[0]["text"] == "Napiš klódovi zprávu."
            assert entries[0]["opraveno"] == "Napiš Claudovi zprávu."

            pamet_entries = pamet.load_entries(tmp_path)
            assert len(pamet_entries) == 1
            assert pamet_entries[0]["zmeny"] == [["klódovi", "Claudovi"]]
            assert "Soukromý kontext aplikace" not in pamet_entries[0]["puvodni"]
            assert "Soukromý kontext aplikace" not in pamet_entries[0]["opraveno"]

            store.reload_if_changed()
            opravy_after = store.cfg["slovnik"]["opravy"]
            assert ["klódovi", "Claudovi"] in opravy_after
            count_before = len(opravy_after)

            # Duplicitní přidání nevytvoří duplikát
            assert store.add_opravy([["KLÓDOVI", "Claudovi"]]) == 0
            assert len(store.cfg["slovnik"]["opravy"]) == count_before

            fake_overlay.toast.assert_any_call("Naučeno: klódovi → Claudovi", kind="info")

            # Toast s více než 2 dvojicemi ukáže zbytek jako +N
            assert (
                inst._format_learned_toast([["a", "b"], ["c", "d"], ["e", "f"], ["g", "h"]])
                == "Naučeno: a → b, c → d +2"
            )

        # Cizí text aplikace nikdy nešel do logu
        for h in list(logging.getLogger("mluvka").handlers):
            h.flush()
        log_txt = config.log_file_path(tmp_path).read_text(encoding="utf-8")
        assert "Soukromý kontext aplikace" not in log_txt
    finally:
        for h in list(logging.getLogger("mluvka").handlers):
            h.flush()
            logging.getLogger("mluvka").removeHandler(h)
            h.close()


def test_app_skips_watcher_when_disabled_or_no_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for field in ("uceni_z_oprav", "historie"):
        data_dir = tmp_path / field
        data_dir.mkdir(parents=True, exist_ok=True)
        store = config.ConfigStore(data_dir)
        store.update_values({field: False})

        fake_sledovac = MagicMock()
        with (
            patch("mluvka.overlay.Overlay", return_value=MagicMock()),
            patch("mluvka.tray.TrayController", return_value=MagicMock()),
            patch("mluvka.asr.WhisperServer", return_value=MagicMock(transcribe=MagicMock(return_value="Napiš klódovi zprávu."))),
            patch("mluvka.sledovani.Sledovac", return_value=fake_sledovac),
            patch("mluvka.output.paste_text", return_value=True),
        ):
            inst = app_mod.MluvkaApp(store, data=data_dir)
            inst._model_loaded = True
            inst._whisper_ready.set()
            loud = np.full(16000, 0.2, dtype=np.float32)
            inst._handle_transcription_job("dictation", (loud, store.cfg, "Code.exe", "", None))
            fake_sledovac.start.assert_not_called()


def test_okno_validates_uceni_z_oprav_setting(tmp_path: Path) -> None:
    api = okno.Api(data=tmp_path)
    assert api.get_state()["config"]["uceni_z_oprav"] is True

    bad = api.save_settings({"uceni_z_oprav": "ano"})
    assert bad["ok"] is False

    res = api.save_settings({"uceni_z_oprav": False})
    assert res["ok"] is True
    assert res["config"]["uceni_z_oprav"] is False


def test_sledovac_element_disappears_uses_last_snapshot() -> None:
    reader = _FakeReader(
        [
            (100, False, "Napiš klódovi zprávu."),
            (100, False, "Napiš Claudovi zprávu."),
            (100, False, None),  # prvek zmizí (např. zavření dialogu/karty)
        ]
    )
    got_ev = threading.Event()
    learned: list[tuple[str, list[list[str]]]] = []
    s = sledovani.Sledovac(
        reader_factory=lambda: reader,
        initial_delay_s=0.0,
        baseline_retries=1,
        poll_interval_s=0.005,
        max_duration_s=1.0,
    )
    s.start("Napiš klódovi zprávu.", "e1", lambda u, d: (learned.append((u, d)), got_ev.set()))
    assert got_ev.wait(2.0)
    assert learned == [("Napiš Claudovi zprávu.", [["klódovi", "Claudovi"]])]


def test_sledovac_timeout_evaluates_last_snapshot() -> None:
    reader = _FakeReader(
        [
            (100, False, "Napiš klódovi zprávu."),
            (100, False, "Napiš Claudovi zprávu."),
        ]
    )
    got_ev = threading.Event()
    learned: list[tuple[str, list[list[str]]]] = []
    s = sledovani.Sledovac(
        reader_factory=lambda: reader,
        initial_delay_s=0.0,
        baseline_retries=1,
        poll_interval_s=0.005,
        max_duration_s=0.03,
    )
    s.start("Napiš klódovi zprávu.", "e1", lambda u, d: (learned.append((u, d)), got_ev.set()))
    assert got_ev.wait(2.0)
    assert learned == [("Napiš Claudovi zprávu.", [["klódovi", "Claudovi"]])]


def test_sledovac_reader_exception_never_crashes() -> None:
    done = threading.Event()

    class _BoomReader(_FakeReader):
        def get_focused(self) -> Any | None:
            raise RuntimeError("UIA selhalo")

        def close_thread(self) -> None:
            done.set()

    s = sledovani.Sledovac(
        reader_factory=lambda: _BoomReader([]),
        initial_delay_s=0.0,
        baseline_retries=1,
    )
    s.start("Napiš klódovi zprávu.", "e1", lambda _u, _d: None)
    assert done.wait(2.0)


def test_app_skips_watcher_when_paste_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = config.ConfigStore(tmp_path)
    fake_sledovac = MagicMock()
    with (
        patch("mluvka.overlay.Overlay", return_value=MagicMock()),
        patch("mluvka.tray.TrayController", return_value=MagicMock()),
        patch(
            "mluvka.asr.WhisperServer",
            return_value=MagicMock(transcribe=MagicMock(return_value="Napiš klódovi zprávu.")),
        ),
        patch("mluvka.sledovani.Sledovac", return_value=fake_sledovac),
        patch("mluvka.output.paste_text", return_value=False),
    ):
        inst = app_mod.MluvkaApp(store, data=tmp_path)
        inst._model_loaded = True
        inst._whisper_ready.set()
        loud = np.full(16000, 0.2, dtype=np.float32)
        inst._handle_transcription_job("dictation", (loud, store.cfg, "Code.exe", "", None))
        fake_sledovac.start.assert_not_called()


class _PatWrapper:
    def __init__(self, iface: Any, truthy: bool = True) -> None:
        self._iface = iface
        self._truthy = truthy
        self.queried: list[Any] = []

    def __bool__(self) -> bool:
        return self._truthy

    def QueryInterface(self, target: Any) -> Any:
        self.queried.append(target)
        return self._iface


def test_najdi_opravy_boundary_continuations_and_straddles() -> None:
    # Oprava posledního slova + okamžité dopisování za konec úseku
    res_end = sledovani.najdi_opravy(
        "Napiš zprávu pro Klóda",
        "Napiš zprávu pro Klóda",
        "Napiš zprávu pro Clauda a pošli to hned",
    )
    assert res_end == ("Napiš zprávu pro Clauda", [["Klóda", "Clauda"]])

    # Dopsání před začátek + oprava prvního slova
    res_start = sledovani.najdi_opravy(
        "Napiš zprávu pro Clauda",
        "Napiš zprávu pro Clauda",
        "Prosím Piš zprávu pro Clauda",
    )
    assert res_start == ("Piš zprávu pro Clauda", [["Napiš", "Piš"]])

    # Překryv na levé hranici (slovo před úsekem i na začátku úseku přepsána najednou)
    res_lstraddle = sledovani.najdi_opravy(
        "klódovi dlouhou zprávu.",
        "Řekl klódovi dlouhou zprávu.",
        "Napsal jinak Claudovi dlouhou zprávu.",
    )
    assert res_lstraddle == ("Claudovi dlouhou zprávu.", [["klódovi", "Claudovi"]])

    # Překryv na pravé hranici (slovo na konci úseku i slovo za ním přepsána najednou)
    res_rstraddle = sledovani.najdi_opravy(
        "Pošli zprávu klódovi",
        "Pošli zprávu klódovi hned",
        "Pošli zprávu Claudovi zítra ráno",
    )
    assert res_rstraddle == ("Pošli zprávu Claudovi", [["klódovi", "Claudovi"]])


def test_sledovac_stop_wait_concurrent_with_start_no_race() -> None:
    s = sledovani.Sledovac(
        reader_factory=lambda: _FakeReader([(100, False, "něco")]),
        initial_delay_s=0.05,
        poll_interval_s=0.05,
        max_duration_s=0.1,
    )
    for _ in range(15):
        s.start("Napiš klódovi zprávu.", "e1", lambda _u, _d: None)
        s.stop(wait=True, timeout=1.0)


def test_config_opravy_malformed_and_invalid_json_no_toast(tmp_path: Path) -> None:
    assert config.opravy_key_set({"slovnik": ["bad"]}) == set()
    assert config.opravy_key_set({"slovnik": {"opravy": 123}}) == set()

    store = config.ConfigStore(tmp_path)
    config.config_path(tmp_path).write_text("{broken json", encoding="utf-8")
    store.reload_if_changed()
    with pytest.raises(config.ConfigInvalidError):
        store.add_opravy([["klódovi", "Claudovi"]])


def test_uia_reader_patterns_fallback_and_com_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    import types

    class _FakeMod:
        class IUIAutomationTextPattern:
            pass

        class IUIAutomationValuePattern:
            pass

        class CUIAutomation:
            pass

        class IUIAutomation:
            pass

    co_calls: list[str] = []
    fake_uia = MagicMock()
    fake_comtypes = types.ModuleType("comtypes")
    fake_comtypes.CoInitialize = lambda: co_calls.append("init")
    fake_comtypes.CoUninitialize = lambda: co_calls.append("uninit")
    fake_client = types.ModuleType("comtypes.client")
    fake_client.GetModule = lambda dll: co_calls.append(f"mod:{dll}")
    fake_client.CreateObject = lambda cls, interface=None: (
        co_calls.append("create"),
        fake_uia,
    )[1]
    fake_gen = types.ModuleType("comtypes.gen")
    fake_gen.UIAutomationClient = _FakeMod
    fake_comtypes.client = fake_client
    fake_comtypes.gen = fake_gen

    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", fake_client)
    monkeypatch.setitem(sys.modules, "comtypes.gen", fake_gen)

    reader = sledovani.UiaReader()
    reader.init_thread()
    assert reader._uia is fake_uia
    assert co_calls == ["init", "mod:UIAutomationCore.dll", "create"]

    # Null TextPattern (falsy pointer) -> fallback na ValuePattern s QueryInterface
    val_iface = MagicMock()
    val_iface.CurrentValue = "Text přes ValuePattern"
    null_pat = _PatWrapper(None, truthy=False)
    val_pat = _PatWrapper(val_iface, truthy=True)

    elem = MagicMock()
    elem.GetCurrentPattern.side_effect = lambda pid: null_pat if pid == 10014 else val_pat
    assert reader.read_text(elem) == "Text přes ValuePattern"
    assert null_pat.queried == []
    assert val_pat.queried == [_FakeMod.IUIAutomationValuePattern]

    # TextPattern má přednost
    txt_iface = MagicMock()
    txt_iface.DocumentRange.GetText.return_value = "Text přes TextPattern"
    txt_pat = _PatWrapper(txt_iface, truthy=True)
    elem_txt = MagicMock()
    elem_txt.GetCurrentPattern.side_effect = lambda pid: txt_pat if pid == 10014 else val_pat
    assert reader.read_text(elem_txt) == "Text přes TextPattern"
    assert txt_pat.queried == [_FakeMod.IUIAutomationTextPattern]
    txt_iface.DocumentRange.GetText.assert_called_once_with(20000)

    # CurrentIsPassword selže -> bezpečně považováno za chráněné (True)
    bad_elem = MagicMock()
    type(bad_elem).CurrentIsPassword = property(lambda self: (_ for _ in ()).throw(OSError("COM")))
    assert reader.is_password(bad_elem) is True

    reader.close_thread()
    assert reader._uia is None
    assert co_calls[-1] == "uninit"

    # Selhání GetModule stále korektně uklidí COM při close_thread
    co_calls.clear()
    fake_client.GetModule = lambda _dll: (_ for _ in ()).throw(OSError("no typelib"))
    reader2 = sledovani.UiaReader()
    reader2.init_thread()
    assert reader2._uia is None
    reader2.close_thread()
    assert co_calls == ["init", "uninit"]
