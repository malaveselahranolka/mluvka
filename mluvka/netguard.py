"""Síťová ochrana: povoluje pouze loopback a volitelně nakonfigurovaný webhook."""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
from collections.abc import Iterable
from typing import Any

log = logging.getLogger("mluvka.netguard")

_lock = threading.RLock()
_installed = False
_allowed_hosts: set[str] = set()
_allowed_ips: set[str] = set()

_orig_getaddrinfo = socket.getaddrinfo
_orig_gethostbyname = socket.gethostbyname
_orig_gethostbyname_ex = socket.gethostbyname_ex
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex


def _clean_host(host: str) -> str:
    h = host.strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if "%" in h:
        h = h.split("%", 1)[0]
    return h


def _is_loopback_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return False


def _is_allowed_host(host: str | bytes | None, *, allow_none: bool = False) -> bool:
    if host is None:
        return allow_none
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    cleaned = _clean_host(host)
    if not cleaned:
        return False
    if cleaned == "localhost" or _is_loopback_ip(cleaned):
        return True
    with _lock:
        return cleaned in _allowed_hosts or cleaned in _allowed_ips


def _block(host: Any) -> PermissionError:
    host_str = str(host)
    log.warning("Blokováno odchozí spojení: %s", host_str)
    return PermissionError(f"Síťové spojení zakázáno mimo loopback/webhook: {host_str}")


def _record_resolved_ips(host: str | bytes | None, results: list[Any]) -> None:
    if host is None:
        return
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    cleaned = _clean_host(host)
    with _lock:
        if cleaned not in _allowed_hosts:
            return
        for item in results:
            sockaddr = item[4]
            if isinstance(sockaddr, tuple) and sockaddr:
                ip_str = _clean_host(str(sockaddr[0]))
                if ip_str:
                    _allowed_ips.add(ip_str)


def _guarded_getaddrinfo(
    host: Any,
    port: Any,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[Any]:
    allow_none = bool(flags & socket.AI_PASSIVE)
    if not _is_allowed_host(host, allow_none=allow_none):
        raise _block(host)
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    _record_resolved_ips(host, res)
    return res


def _guarded_gethostbyname(hostname: str) -> str:
    if not _is_allowed_host(hostname):
        raise _block(hostname)
    ip = _orig_gethostbyname(hostname)
    with _lock:
        if _clean_host(hostname) in _allowed_hosts:
            _allowed_ips.add(_clean_host(ip))
    return ip


def _guarded_gethostbyname_ex(hostname: str) -> tuple[str, list[str], list[str]]:
    if not _is_allowed_host(hostname):
        raise _block(hostname)
    res = _orig_gethostbyname_ex(hostname)
    with _lock:
        if _clean_host(hostname) in _allowed_hosts:
            for ip in res[2]:
                _allowed_ips.add(_clean_host(ip))
    return res


def _check_sock_address(sock: socket.socket, address: Any) -> None:
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    if not isinstance(address, tuple) or not address:
        raise _block(address)
    host = address[0]
    if not _is_allowed_host(host):
        raise _block(host)


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    _check_sock_address(self, address)
    return _orig_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    _check_sock_address(self, address)
    return _orig_connect_ex(self, address)


def set_allowed_hosts(allowed_hosts: Iterable[str] = ()) -> None:
    cleaned: set[str] = set()
    for host in allowed_hosts:
        if host:
            ch = _clean_host(str(host))
            if ch:
                cleaned.add(ch)
    with _lock:
        _allowed_hosts.clear()
        _allowed_hosts.update(cleaned)
        _allowed_ips.clear()


def install(allowed_hosts: Iterable[str] = ()) -> None:
    """Nainstaluje síťovou ochranu na socket vrstvu."""
    global _installed
    set_allowed_hosts(allowed_hosts)
    with _lock:
        if not _installed:
            socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]
            socket.gethostbyname = _guarded_gethostbyname  # type: ignore[assignment]
            socket.gethostbyname_ex = _guarded_gethostbyname_ex  # type: ignore[assignment]
            socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
            socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
            _installed = True


def uninstall() -> None:
    """Obnoví původní socket funkce (pouze pro testy)."""
    global _installed
    with _lock:
        if _installed:
            socket.getaddrinfo = _orig_getaddrinfo  # type: ignore[assignment]
            socket.gethostbyname = _orig_gethostbyname  # type: ignore[assignment]
            socket.gethostbyname_ex = _orig_gethostbyname_ex  # type: ignore[assignment]
            socket.socket.connect = _orig_connect  # type: ignore[method-assign]
            socket.socket.connect_ex = _orig_connect_ex  # type: ignore[method-assign]
            _allowed_hosts.clear()
            _allowed_ips.clear()
            _installed = False
