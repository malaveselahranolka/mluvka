"""Jednotkové testy webhook formátování v mluvka.channel."""

from __future__ import annotations

from mluvka import channel


def test_discord_payload_and_truncation() -> None:
    long_text = "a" * 2500
    for host in ("https://discord.com/api/webhooks/1/x", "https://discordapp.com/api/webhooks/1/x"):
        payload = channel.build_payload(host, long_text)
        assert list(payload.keys()) == ["content"]
        assert len(payload["content"]) == 2000


def test_slack_payload() -> None:
    payload = channel.build_payload("https://hooks.slack.com/services/T/B/X", "Ahoj")
    assert payload == {"text": "Ahoj"}


def test_teams_adaptive_card_payloads() -> None:
    for url in (
        "https://prod.westeurope.logic.azure.com/workflows/1",
        "https://env.api.powerplatform.com/powerautomate/1",
        "https://outlook.webhook.office.com/webhookb2/1",
    ):
        payload = channel.build_payload(url, "Test zpráva")
        assert payload["type"] == "message"
        card = payload["attachments"][0]["content"]
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.4"
        assert card["body"] == [{"type": "TextBlock", "text": "Test zpráva", "wrap": True}]


def test_generic_webhook_payload_and_url_validation() -> None:
    assert channel.build_payload("https://example.org/hook", "Ahoj") == {"text": "Ahoj"}
    assert channel.is_valid_webhook_url("https://discord.com/api/webhooks/1/x") is True
    assert channel.is_valid_webhook_url("http://discord.com/api/webhooks/1/x") is False
    assert channel.is_valid_webhook_url("") is False
