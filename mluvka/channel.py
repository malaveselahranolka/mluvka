"""Volitelný odchozí webhook (kanál) pro hotové přepisy."""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from typing import Any, Callable
from urllib.parse import urlsplit

log = logging.getLogger("mluvka.channel")

_TEAMS_SUFFIXES = (".logic.azure.com", ".powerplatform.com", ".webhook.office.com")


def is_valid_webhook_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    try:
        parts = urlsplit(u)
    except ValueError:
        return False
    return parts.scheme.lower() == "https" and bool(parts.hostname)


def build_payload(url: str, text: str) -> dict[str, Any]:
    host = (urlsplit(url).hostname or "").lower()
    if host in ("discord.com", "discordapp.com") or host.endswith(
        (".discord.com", ".discordapp.com")
    ):
        return {"content": text[:2000]}
    if host == "hooks.slack.com":
        return {"text": text}
    if any(host.endswith(suffix) for suffix in _TEAMS_SUFFIXES):
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                    },
                }
            ],
        }
    return {"text": text}


def send_sync(url: str, text: str, timeout_s: float = 5.0) -> None:
    payload = json.dumps(build_payload(url, text), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Mluvka/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        resp.read()


def send_async(
    url: str,
    text: str,
    on_error: Callable[[], None] | None = None,
    timeout_s: float = 5.0,
) -> threading.Thread | None:
    if not is_valid_webhook_url(url):
        return None
    host = (urlsplit(url).hostname or "").lower()

    def _run() -> None:
        try:
            send_sync(url, text, timeout_s=timeout_s)
        except Exception as exc:
            log.warning("Kanál (%s): odeslání selhalo (%s)", host, type(exc).__name__)
            if on_error is not None:
                on_error()

    t = threading.Thread(target=_run, name="mluvka-channel", daemon=True)
    t.start()
    return t
