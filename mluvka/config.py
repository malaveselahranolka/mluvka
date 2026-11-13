"""Správa konfigurace a cest k datovému adresáři Mluvky."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("mluvka.config")

CONFIG_INVALID_MESSAGE = (
    "config.json obsahuje chybu. Oprav ho, nebo ho smaž a Mluvka vytvoří nový."
)


class ConfigInvalidError(RuntimeError):
    """Vyhozeno při pokusu o uložení konfigurace přes neplatný config.json."""


DEFAULT_CONFIG: dict[str, Any] = {
    "klavesa_vk": 163,
    "mikrofon": "",
    "zvuky": True,
    "ai_uprava": True,
    "claude_api_klic": "",
    "slovnik_do_promptu": True,
    "slovnik": {
        "slova": [
            "Claude",
            "Claude Code",
            "CLAUDE.md",
            "AGENTS.md",
            "Obsidian",
            "Antigravity",
            "Supabase",
        ],
        "opravy": [
            ["tvoho md", "CLAUDE.md"],
            ["agens md", "AGENTS.md"],
        ],
    },
    "zkratky": [],
    "kanal_webhook": "",
    "kanal_zapnuto": True,
    "historie": True,
    "historie_dny": 30,
    "autostart": False,
    "trenink_ukladat_zvuk": False,
    "darce_id": "",
    "model_soubor": "ggml-czechspeech-q5_0.bin",
    "beam_size": 5,
}

DEFAULT_MODEL_SOUBOR = "ggml-czechspeech-q5_0.bin"


def repo_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("MLUVKA_DATA", "").strip()
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Mluvka"
    return Path.home() / "AppData" / "Local" / "Mluvka"


def config_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "config.json"


def whisper_exe_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "bin" / "whisper" / "whisper-server.exe"


def models_dir_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "models"


def default_whisper_model_path(data: Path | None = None) -> Path:
    return models_dir_path(data) / DEFAULT_MODEL_SOUBOR


def validate_model_soubor(soubor: Any) -> str:
    """Ověří, že model_soubor je pouze bezpečný basename bez oddělovačů cest a '..'."""
    if not isinstance(soubor, str):
        raise ValueError("Název souboru modelu musí být text.")
    name = soubor.strip()
    if not name or name != soubor:
        raise ValueError("Název souboru modelu nesmí být prázdný ani mít okrajové mezery.")
    if (
        chr(0) in name
        or "/" in name
        or "\\" in name
        or ".." in name
        or ":" in name
        or name in (".", "..")
    ):
        raise ValueError("Název souboru modelu smí obsahovat jen název souboru bez cesty a '..'.")
    if Path(name).name != name:
        raise ValueError("Název souboru modelu musí být pouze basename.")
    return name


def _configured_model_soubor(data: Path | None = None) -> str:
    path = config_path(data)
    if not path.is_file():
        return DEFAULT_MODEL_SOUBOR
    raw_val: Any = None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw_val = loaded.get("model_soubor")
    except (OSError, ValueError):
        return DEFAULT_MODEL_SOUBOR
    if raw_val is None or raw_val == "":
        return DEFAULT_MODEL_SOUBOR
    return validate_model_soubor(raw_val)


def whisper_model_path(data: Path | None = None, model_soubor: str | None = None) -> Path:
    """Vrátí DATA/models/<model_soubor> pokud existuje, jinak výchozí CzechSpeech."""
    if model_soubor is not None:
        name = validate_model_soubor(model_soubor)
    else:
        name = _configured_model_soubor(data)
    candidate = models_dir_path(data) / name
    if candidate.is_file():
        return candidate
    return default_whisper_model_path(data)


def history_jsonl_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "historie.jsonl"


def history_md_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "historie.md"


def pamet_jsonl_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "pamet_chyb.jsonl"


def trenink_dir_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "trenink"


def trenink_vety_dir_path(data: Path | None = None) -> Path:
    return trenink_dir_path(data) / "vety"


def trenink_vety_tsv_path(data: Path | None = None) -> Path:
    return trenink_dir_path(data) / "vety.tsv"


def trenink_mereni_path(data: Path | None = None) -> Path:
    return trenink_dir_path(data) / "mereni.json"


def trenink_vysledky_path(data: Path | None = None) -> Path:
    return trenink_dir_path(data) / "vysledky.json"


def diktaty_dir_path(data: Path | None = None) -> Path:
    return trenink_dir_path(data) / "diktaty"


def log_file_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "mluvka.log"


def sounds_dir_path(data: Path | None = None) -> Path:
    return (data or data_dir()) / "zvuky"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _stat_stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return None


def _save_config_stamped(
    cfg: dict[str, Any],
    data: Path | None = None,
) -> tuple[Path, tuple[int, int, int] | None]:
    path = config_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        stamp = _stat_stamp(tmp_path)
        for attempt in range(5):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return path, stamp


def save_config(cfg: dict[str, Any], data: Path | None = None) -> Path:
    path, _stamp = _save_config_stamped(cfg, data=data)
    return path


def _try_load_config_status(data: Path | None = None) -> tuple[dict[str, Any] | None, str]:
    path = config_path(data)
    if not path.exists():
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        save_config(cfg, data=data)
        return cfg, "ok"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config.json must be a JSON object")
        return _deep_merge(DEFAULT_CONFIG, raw), "ok"
    except OSError as exc:
        log.warning("Přechodná chyba čtení config.json (%s).", exc)
        return None, "transient"
    except Exception as exc:
        log.warning("Chyba při načítání config.json (%s).", exc)
        return None, "invalid"


def _try_load_config(data: Path | None = None) -> tuple[dict[str, Any] | None, bool]:
    cfg, status = _try_load_config_status(data=data)
    return cfg, status == "ok"


def load_config(data: Path | None = None) -> dict[str, Any]:
    cfg, ok = _try_load_config(data=data)
    if ok and cfg is not None:
        return cfg
    return copy.deepcopy(DEFAULT_CONFIG)


def webhook_host(cfg: dict[str, Any]) -> str | None:
    url = str(cfg.get("kanal_webhook", "") or "").strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    return parts.hostname.lower()


class ConfigStore:
    """Vláknově bezpečné úložiště konfigurace s automatickým znovunačtením při změně souboru."""

    def __init__(self, data: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._data = data
        self._path = config_path(data)
        init_stamp = self._read_stamp()
        loaded, status = _try_load_config_status(data=data)
        if status == "ok" and loaded is not None:
            self.cfg: dict[str, Any] = loaded
            self._disk_valid = True
            self._stamp: tuple[int, int, int] | None = (
                init_stamp if init_stamp is not None else self._read_stamp()
            )
        elif status == "transient":
            self.cfg = copy.deepcopy(DEFAULT_CONFIG)
            self._disk_valid = True
            self._stamp = None
        else:
            self.cfg = copy.deepcopy(DEFAULT_CONFIG)
            self._disk_valid = False
            self._stamp = init_stamp

    @property
    def path(self) -> Path:
        return self._path

    @property
    def config_invalid(self) -> bool:
        with self._lock:
            return (not self._disk_valid) and self._path.exists()

    def _read_stamp(self) -> tuple[int, int, int] | None:
        return _stat_stamp(self._path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.cfg)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self.cfg.get(key, default))

    def _save_candidate(self, candidate: dict[str, Any]) -> None:
        if not self._disk_valid and self._path.exists():
            # Nechceme přepsat uživatelem rozbitý config.json výchozími hodnotami.
            log.warning("config.json má neplatný JSON; uložení přeskočeno.")
            raise ConfigInvalidError(CONFIG_INVALID_MESSAGE)
        _path, stamp = _save_config_stamped(candidate, data=self._data)
        self.cfg = copy.deepcopy(candidate)
        self._disk_valid = True
        self._stamp = stamp

    def set_value(self, key: str, value: Any) -> None:
        with self._lock:
            self.reload_if_changed()
            candidate = copy.deepcopy(self.cfg)
            candidate[key] = copy.deepcopy(value)
            self._save_candidate(candidate)

    def update_values(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.reload_if_changed()
            candidate = copy.deepcopy(self.cfg)
            for key, value in values.items():
                if isinstance(value, dict) and isinstance(candidate.get(key), dict):
                    candidate[key] = _deep_merge(candidate[key], value)
                else:
                    candidate[key] = copy.deepcopy(value)
            self._save_candidate(candidate)
            return copy.deepcopy(self.cfg)

    def reload_if_changed(self) -> bool:
        with self._lock:
            stamp = self._read_stamp()
            if stamp is None:
                loaded, status = _try_load_config_status(data=self._data)
                if status == "ok" and loaded is not None:
                    self.cfg = loaded
                    self._disk_valid = True
                    self._stamp = self._read_stamp()
                elif status == "transient":
                    self._stamp = None
                else:
                    self._disk_valid = False
                    self._stamp = self._read_stamp()
                return True
            if stamp != self._stamp:
                loaded, status = _try_load_config_status(data=self._data)
                if status == "ok" and loaded is not None:
                    self.cfg = loaded
                    self._disk_valid = True
                    # Uchováme razítko pořízené PŘED čtením, aby se souběžný zápis nepřehlédl.
                    self._stamp = stamp
                    return True
                if status == "transient":
                    return False
                self._stamp = stamp
                self._disk_valid = False
                return False
            return False

    def save(self) -> None:
        with self._lock:
            self._save_candidate(self.cfg)


def allowed_hosts(cfg: dict[str, Any]) -> list[str]:
    """Jediné vzdálené hosty, kam smí Mluvka: kanál (webhook) a Claude API, když je nastavený klíč."""
    hosts = [h for h in [webhook_host(cfg)] if h]
    if str(cfg.get("claude_api_klic", "") or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip():
        hosts.append("api.anthropic.com")
    return hosts
