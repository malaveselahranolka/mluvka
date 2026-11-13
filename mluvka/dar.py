"""Tvorba lokálního darovacího ZIP balíčku hlasových nahrávek (čistě offline)."""

from __future__ import annotations

import ctypes
import json
import os
import ctypes.wintypes as wt
import threading
import time
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from mluvka import config, history, trenink

CONSENT_TEXT = (
    "Daruji své hlasové nahrávky a jejich přepisy pro trénink a vyhodnocení otevřených modelů rozpoznávání české řeči.\n"
    "Beru na vědomí, že hlas je osobní údaj: balíček neobsahuje moje jméno ani e-mail, ale podle hlasu mě lze poznat.\n"
    "Balíček se zatím jen vytvoří v mém počítači. Kam a pod jakou licencí se data předají, se dozvím předem a rozhodnu sám.\n"
    "Souhlas mohu kdykoli odvolat. Podle identifikátoru dárce pak budou mé nahrávky z datasetu odstraněny."
)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _known_folder_documents() -> Path | None:
    if os.name != "nt":
        return None
    try:
        folder_uuid = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")
        guid = _GUID.from_buffer_copy(folder_uuid.bytes_le)
        ptr = ctypes.c_void_p()
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wt.DWORD, wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None
        hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ptr))
        try:
            if hr == 0 and ptr.value:
                return Path(ctypes.wstring_at(ptr.value))
        finally:
            if ptr.value:
                ole32.CoTaskMemFree(ptr)
    except Exception:
        pass
    try:
        buf = ctypes.create_unicode_buffer(260)
        shell32_fb = ctypes.WinDLL("shell32", use_last_error=True)
        shell32_fb.SHGetFolderPathW.argtypes = [wt.HWND, ctypes.c_int, wt.HANDLE, wt.DWORD, wt.LPWSTR]
        shell32_fb.SHGetFolderPathW.restype = ctypes.c_long
        if shell32_fb.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return None


def documents_dir() -> Path:
    override = os.environ.get("MLUVKA_DOCUMENTS", "").strip()
    if override:
        return Path(override)
    known = _known_folder_documents()
    if known is not None:
        return known
    return Path.home() / "Documents"


def ensure_darce_id(
    cfg_store: config.ConfigStore | None = None,
    *,
    data: Path | None = None,
) -> str:
    store = cfg_store or config.ConfigStore(data)
    existing = str(store.get("darce_id", "") or "").strip()
    if existing:
        return existing
    new_id = uuid.uuid4().hex
    store.set_value("darce_id", new_id)
    return new_id


def _unique_zip_path(target_dir: Path, day: date) -> Path:
    base_name = f"Mluvka-dar-{day.isoformat()}"
    candidate = target_dir / f"{base_name}.zip"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = target_dir / f"{base_name}-{suffix}.zip"
        if not candidate.exists():
            return candidate
        suffix += 1


def vytvor_balicek(
    data: Path | None = None,
    include_diktaty: bool = False,
    *,
    out_dir: Path | None = None,
    cfg_store: config.ConfigStore | None = None,
    today: date | None = None,
) -> Path:
    """Vytvoří darovací ZIP balíček v Dokumentech a vrátí jeho cestu."""
    vety_records = trenink.load_tsv(data)
    audio_items: list[tuple[str, Path, dict[str, Any]]] = []
    vety_count = 0
    diktaty_count = 0

    for code in sorted(vety_records.keys()):
        rec = vety_records[code]
        wav_path: Path = rec["wav_path"]
        try:
            delka_s, rate = trenink.wav_duration_and_rate(wav_path)
        except Exception:
            continue
        arcname = f"vety/{code}.wav"
        meta = {
            "file": arcname,
            "text": str(rec["sentence"]),
            "druh": "veta",
            "delka_s": round(delka_s, 2),
            "sample_rate": int(rate),
        }
        audio_items.append((arcname, wav_path, meta))
        vety_count += 1

    if include_diktaty:
        diktaty_dir = config.diktaty_dir_path(data)
        seen_names: set[str] = set()
        for entry in history.load_entries(data):
            zvuk = str(entry.get("zvuk", "") or "").strip()
            if not zvuk:
                continue
            wav_name = Path(zvuk).name
            if wav_name in seen_names:
                continue
            wav_path = diktaty_dir / wav_name
            if not wav_path.is_file():
                continue
            transcript = history.entry_text(entry).strip()
            if not transcript:
                continue
            try:
                delka_s, rate = trenink.wav_duration_and_rate(wav_path)
            except Exception:
                continue
            seen_names.add(wav_name)
            arcname = f"diktaty/{diktaty_count + 1:05d}.wav"  # pořadové číslo, ne id z historie
            meta = {
                "file": arcname,
                "text": transcript,
                "druh": "diktat",
                "delka_s": round(delka_s, 2),
                "sample_rate": int(rate),
            }
            audio_items.append((arcname, wav_path, meta))
            diktaty_count += 1

    if not audio_items:
        raise ValueError("Nejsou k dispozici žádné hlasové nahrávky pro vytvoření balíčku.")

    day = today or date.today()
    darce_id = ensure_darce_id(cfg_store, data=data)

    metadata_lines = [json.dumps(item[2], ensure_ascii=False) for item in audio_items]
    metadata_text = "\n".join(metadata_lines) + "\n"

    souhlas_text = (
        f"{CONSENT_TEXT}\n\n"
        f"Datum: {day.isoformat()}\n"
        f"Identifikátor dárce (darce_id): {darce_id}\n"
        f"Zahrnuto vzorových vět: {vety_count}\n"
        f"Zahrnuto běžných diktátů: {diktaty_count}\n"
    )

    readme_text = (
        "Mluvka — lokální darovací balíček hlasových nahrávek\n"
        "===================================================\n\n"
        "Obsah balíčku:\n"
        "- vety/*.wav: nahrané tréninkové věty (mono 16 kHz, 16-bit PCM WAV)\n"
        "- diktaty/*.wav: volitelně nahrané běžné diktáty (pouze pokud bylo zaškrtnuto)\n"
        "- metadata.jsonl: seznam souborů a přepisů (pole file, text, druh, delka_s, sample_rate)\n"
        "- souhlas.txt: text uděleného souhlasu, datum a pseudonymní identifikátor dárce\n\n"
        "Ochrana soukromí:\n"
        "- Balíček neobsahuje konfiguraci, API klíče, jméno, e-mail, logy, názvy aplikací ani přesné časy diktátů.\n"
        "- Balíček byl vytvořen pouze lokálně v tomto počítači a nic se automaticky neodeslalo.\n\n"
        "Licence:\n"
        "- zatím neurčeno, upřesní se před odesláním\n"
    )

    target_dir = out_dir or documents_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = _unique_zip_path(target_dir, day)
    tmp_zip = zip_path.with_name(f".{zip_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arcname, src_path, _meta in audio_items:
                zf.write(src_path, arcname=arcname)
            zf.writestr("metadata.jsonl", metadata_text.encode("utf-8"))
            zf.writestr("souhlas.txt", souhlas_text.encode("utf-8"))
            zf.writestr("README.txt", readme_text.encode("utf-8"))
        for attempt in range(5):
            try:
                os.replace(tmp_zip, zip_path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except Exception:
        tmp_zip.unlink(missing_ok=True)
        raise
    return zip_path
