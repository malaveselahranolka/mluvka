"""Vytváření a odstraňování zástupců aplikace Mluvka (Plocha + nabídka Start) a ikony .ico."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
from pathlib import Path

from PIL import Image

from mluvka import config, tray

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)]
SHORTCUT_NAME = "Mluvka.lnk"
SHORTCUT_DESCRIPTION = "Mluvka – diktování"

CSIDL_PROGRAMS = 0x0002
CSIDL_DESKTOPDIRECTORY = 0x0010

_CREATE_SHORTCUTS_PS = (
    "$ErrorActionPreference = 'Stop'\n"
    "$ws = New-Object -ComObject WScript.Shell\n"
    "foreach ($p in @($env:MLUVKA_LNK_DESKTOP, $env:MLUVKA_LNK_PROGRAMS)) {\n"
    "  if ($p) {\n"
    "    $lnk = $ws.CreateShortcut($p)\n"
    "    $lnk.TargetPath = $env:MLUVKA_LNK_TARGET\n"
    "    $lnk.Arguments = $env:MLUVKA_LNK_ARGS\n"
    "    $lnk.WorkingDirectory = $env:MLUVKA_LNK_WORKDIR\n"
    "    $lnk.IconLocation = $env:MLUVKA_LNK_ICON\n"
    "    $lnk.Description = $env:MLUVKA_LNK_DESC\n"
    "    $lnk.Save()\n"
    "  }\n"
    "}\n"
)

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.SHGetFolderPathW.argtypes = [wt.HWND, ctypes.c_int, wt.HANDLE, wt.DWORD, wt.LPWSTR]
shell32.SHGetFolderPathW.restype = ctypes.c_long


def _folder_path(csidl: int, fallback: Path) -> Path:
    buf = ctypes.create_unicode_buffer(wt.MAX_PATH)
    try:
        if shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return fallback


def desktop_dir() -> Path:
    return _folder_path(CSIDL_DESKTOPDIRECTORY, Path.home() / "Desktop")


def programs_dir() -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    fb = (
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if appdata
        else Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    )
    return _folder_path(CSIDL_PROGRAMS, fb)


def ico_path(data: Path | None = None) -> Path:
    return (data or config.data_dir()) / "mluvka.ico"


def ensure_ico(data: Path | None = None) -> Path:
    target = ico_path(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    img_64 = tray.kresli_ikonu("pripraveno")
    img_256 = (
        img_64.resize((256, 256), Image.Resampling.LANCZOS)
        if img_64.size != (256, 256)
        else img_64
    )
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        img_256.save(tmp, format="ICO", sizes=ICO_SIZES)
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return target


def shortcut_paths(
    desktop: Path | None = None,
    programs: Path | None = None,
) -> tuple[Path, Path]:
    d = (desktop or desktop_dir()) / SHORTCUT_NAME
    p = (programs or programs_dir()) / SHORTCUT_NAME
    return d, p


def build_powershell_call(
    *,
    desktop_lnk: Path,
    programs_lnk: Path,
    target_exe: Path,
    repo: Path,
    icon_file: Path,
) -> tuple[list[str], dict[str, str]]:
    cmd = ["powershell", "-NoProfile", "-Command", _CREATE_SHORTCUTS_PS]
    env = dict(os.environ)
    env["MLUVKA_LNK_DESKTOP"] = str(desktop_lnk)
    env["MLUVKA_LNK_PROGRAMS"] = str(programs_lnk)
    env["MLUVKA_LNK_TARGET"] = str(target_exe)
    env["MLUVKA_LNK_ARGS"] = "-m mluvka --okno"
    env["MLUVKA_LNK_WORKDIR"] = str(repo)
    env["MLUVKA_LNK_ICON"] = str(icon_file)
    env["MLUVKA_LNK_DESC"] = SHORTCUT_DESCRIPTION
    return cmd, env


def create_shortcuts(
    *,
    data: Path | None = None,
    repo: Path | None = None,
    desktop: Path | None = None,
    programs: Path | None = None,
) -> tuple[Path, Path, Path]:
    d = data or config.data_dir()
    r = repo or config.repo_dir()
    icon_file = ensure_ico(d)
    desktop_lnk, programs_lnk = shortcut_paths(desktop, programs)
    desktop_lnk.parent.mkdir(parents=True, exist_ok=True)
    programs_lnk.parent.mkdir(parents=True, exist_ok=True)
    target_exe = d / "venv" / "Scripts" / "pythonw.exe"

    cmd, env = build_powershell_call(
        desktop_lnk=desktop_lnk,
        programs_lnk=programs_lnk,
        target_exe=target_exe,
        repo=r,
        icon_file=icon_file,
    )
    subprocess.run(
        cmd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return desktop_lnk, programs_lnk, icon_file


def remove_shortcuts(
    *,
    desktop: Path | None = None,
    programs: Path | None = None,
) -> list[Path]:
    desktop_lnk, programs_lnk = shortcut_paths(desktop, programs)
    removed: list[Path] = []
    for path in (desktop_lnk, programs_lnk):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m mluvka.zastupci",
        description="Vytvoří nebo odebere zástupce Mluvky na Ploše a v nabídce Start.",
    )
    parser.add_argument("--odebrat", action="store_true", help="Odebere oba zástupce")
    args = parser.parse_args(argv)

    if args.odebrat:
        desktop_lnk, programs_lnk = shortcut_paths()
        removed = remove_shortcuts()
        for path in (desktop_lnk, programs_lnk):
            stav = "odebráno" if path in removed else "nenalezeno"
            print(f"{path} ({stav})")
        return 0

    desktop_lnk, programs_lnk, icon_file = create_shortcuts()
    print(f"Ikona: {icon_file}")
    print(f"Plocha: {desktop_lnk}")
    print(f"Start: {programs_lnk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
