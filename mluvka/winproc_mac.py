"""Správa podprocesů a zjišťování aktivní aplikace na macOS (Quartz / Cocoa)."""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import threading
from pathlib import Path
from typing import IO, Any

_PROCS_LOCK = threading.Lock()
_ACTIVE_PROCS: list[subprocess.Popen[bytes]] = []
_ATEXIT_REGISTERED = False


def _cleanup_children() -> None:
    with _PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
        _ACTIVE_PROCS.clear()
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def _track_proc(proc: subprocess.Popen[bytes]) -> None:
    global _ATEXIT_REGISTERED
    with _PROCS_LOCK:
        if not _ATEXIT_REGISTERED:
            atexit.register(_cleanup_children)
            _ATEXIT_REGISTERED = True
        _ACTIVE_PROCS[:] = [p for p in _ACTIVE_PROCS if p.poll() is None]
        _ACTIVE_PROCS.append(proc)


class JobObject:
    """No-op obdoba Win32 JobObject pro macOS (děti hlídá atexit + terminate_process)."""

    def assign(self, pid: int) -> None:
        return None

    def close(self) -> None:
        return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_hidden_process(
    cmd: list[str],
    log_path: Path,
    job: JobObject | None = None,
    cwd: Path | None = None,
) -> tuple[subprocess.Popen[bytes], IO[str]]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(cwd) if cwd else None,
        )
    except Exception:
        log_file.close()
        raise
    _track_proc(proc)
    if job is not None:
        job.assign(proc.pid)
    return proc, log_file


def terminate_process(
    proc: subprocess.Popen[bytes] | None,
    log_file: IO[str] | None = None,
    timeout: float = 5.0,
) -> None:
    try:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
    finally:
        if proc is not None:
            with _PROCS_LOCK:
                _ACTIVE_PROCS[:] = [p for p in _ACTIVE_PROCS if p is not proc]
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass


def _cocoa_nsworkspace() -> Any:
    try:
        import Cocoa
        if hasattr(Cocoa, "NSWorkspace"):
            return Cocoa.NSWorkspace
    except ImportError:
        pass
    from AppKit import NSWorkspace

    return NSWorkspace


def foreground_process_name() -> str:
    try:
        ws_cls = _cocoa_nsworkspace()
        app = ws_cls.sharedWorkspace().frontmostApplication()
        if app is None:
            return ""
        exe_url = app.executableURL()
        if exe_url is not None:
            path = exe_url.path()
            if path:
                return Path(str(path)).name
        loc = app.localizedName()
        return str(loc) if loc else ""
    except Exception:
        return ""


def process_name_stem(name: str) -> str:
    """Normalizuje název procesu bez koncovky .exe pro porovnání na Windows i macOS."""
    low = str(name or "").strip().lower()
    if low.endswith(".exe"):
        return low[:-4]
    return low


def acquire_instance_lock(name: str, data: Path | None = None) -> Any | None:
    """Získá exkluzivní POSIX zámek souboru; vrátí otevřený deskriptor nebo None pokud už běží."""
    import fcntl
    from mluvka import config

    lock_dir = data or config.data_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f".{name}.lock"
    f = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    try:
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
    except OSError:
        pass
    return f


def release_instance_lock(handle: Any | None) -> None:
    if handle is None:
        return
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass


def is_instance_locked(name: str, data: Path | None = None) -> bool:
    import fcntl
    from mluvka import config

    lock_dir = data or config.data_dir()
    lock_path = lock_dir / f".{name}.lock"
    if not lock_path.exists():
        return False
    try:
        f = lock_path.open("r+", encoding="utf-8")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        f.close()

def read_instance_lock_pid(name: str, data: Path | None = None) -> int | None:
    from mluvka import config

    lock_dir = data or config.data_dir()
    lock_path = lock_dir / f".{name}.lock"
    if not lock_path.is_file():
        return None
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
        pid = int(raw)
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def activate_pid(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        import Cocoa
        cls = getattr(Cocoa, "NSRunningApplication", None)
        if cls is None:
            from AppKit import NSRunningApplication as cls  # type: ignore[no-redef]
            import AppKit as _ak
            opts = int(getattr(_ak, "NSApplicationActivateIgnoringOtherApps", 2))
        else:
            opts = int(getattr(Cocoa, "NSApplicationActivateIgnoringOtherApps", 2))
        app = cls.runningApplicationWithProcessIdentifier_(int(pid))
        if app is not None:
            return bool(app.activateWithOptions_(opts))
    except Exception:
        return False
    return False
