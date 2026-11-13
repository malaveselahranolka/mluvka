"""Jednotkové testy síťové ochrany mluvka.netguard."""

from __future__ import annotations

import logging
import socket
from unittest.mock import patch

import pytest

from mluvka import netguard


def test_blocks_remote_hosts_and_logs_host_only(caplog: pytest.LogCaptureFixture) -> None:
    netguard.install([])
    with caplog.at_level(logging.WARNING, logger="mluvka.netguard"):
        with pytest.raises(PermissionError):
            socket.getaddrinfo("example.com", 80)
        with pytest.raises(PermissionError):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("example.com", 80))
            finally:
                s.close()
        with pytest.raises(PermissionError):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect_ex(("93.184.216.34", 80))
            finally:
                s.close()
    assert "example.com" in caplog.text
    assert "93.184.216.34" in caplog.text


def test_allows_loopback() -> None:
    netguard.install([])
    res = socket.getaddrinfo("127.0.0.1", 80, type=socket.SOCK_STREAM)
    assert res
    res_lh = socket.getaddrinfo("localhost", 80, type=socket.SOCK_STREAM)
    assert res_lh
    res_v6 = socket.getaddrinfo("::1", 80, type=socket.SOCK_STREAM)
    assert res_v6

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        cli.connect(("127.0.0.1", port))
    finally:
        cli.close()
        srv.close()


def test_allows_configured_webhook_host_and_resolved_ips() -> None:
    fake_res = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.42", 443))]
    with patch.object(netguard, "_orig_getaddrinfo", return_value=fake_res) as mock_gai:
        netguard.install(["hooks.slack.com"])
        res = socket.getaddrinfo("hooks.slack.com", 443, type=socket.SOCK_STREAM)
        assert res == fake_res
        mock_gai.assert_called_once()

        # Připojení na přeloženou IP adresu povoleného webhooku musí projít kontrolou netguardu
        with patch.object(netguard, "_orig_connect", return_value=None) as mock_conn:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("198.51.100.42", 443))
            finally:
                s.close()
            mock_conn.assert_called_once()

    # Jiný vzdálený host zůstává zablokován
    with pytest.raises(PermissionError):
        socket.getaddrinfo("example.com", 443)
