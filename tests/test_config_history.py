"""Jednotkové testy pro mluvka.config a mluvka.history."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mluvka import config, history


def test_config_default_creation_and_reload_on_mtime(tmp_path: Path) -> None:
    data = tmp_path / "cfg_data"
    store = config.ConfigStore(data=data)
    assert store.path.exists()
    assert store.cfg["klavesa_vk"] == 163
    assert "Claude" in store.cfg["slovnik"]["slova"]
    assert store.reload_if_changed() is False

    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["zvuky"] = False
    raw["kanal_webhook"] = "https://hooks.slack.com/services/A/B/C"
    store.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    st = store.path.stat()
    os.utime(store.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    assert store.reload_if_changed() is True
    assert store.cfg["zvuky"] is False
    assert config.webhook_host(store.cfg) == "hooks.slack.com"


def test_history_append_prune_and_markdown_export(tmp_path: Path) -> None:
    data = tmp_path / "hist_data"
    now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
    old_ts = now - timedelta(days=35)
    keep_ts = now - timedelta(days=5)

    history.append_entry("Starý přepis", "starý", 2.0, 0.4, "notepad.exe", cas=old_ts, data=data)
    history.append_entry("Nový přepis", "nový", 3.5, 0.5, "Code.exe", cas=keep_ts, data=data)

    removed = history.prune_history(30, now=now, data=data)
    assert removed == 1
    entries = history.load_entries(data=data)
    assert len(entries) == 1
    assert entries[0]["text"] == "Nový přepis"
    assert entries[0]["aplikace"] == "Code.exe"

    history.append_entry("Nejnovější", "nejnovější", 1.2, 0.2, "msedge.exe", cas=now, data=data)
    md = history.export_markdown(data=data)
    content = md.read_text(encoding="utf-8")
    assert content.index("Nejnovější") < content.index("Nový přepis")


def test_config_store_set_value_preserves_disk_edits(tmp_path: Path) -> None:
    data = tmp_path / "cfg_merge"
    store = config.ConfigStore(data=data)
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["klavesa_vk"] = 164
    raw["slovnik"]["slova"] = ["VlastníSlovo"]
    store.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    st = store.path.stat()
    os.utime(store.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    store.set_value("zvuky", False)
    disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert disk["zvuky"] is False
    assert disk["klavesa_vk"] == 164
    assert disk["slovnik"]["slova"] == ["VlastníSlovo"]
