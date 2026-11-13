"""Sdílené pytest fixtury — všechny jednotkové testy používají dočasný adresář."""

from __future__ import annotations

from pathlib import Path

import pytest

from mluvka import netguard


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "mluvka_data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MLUVKA_DATA", str(data))
    yield data
    netguard.uninstall()
