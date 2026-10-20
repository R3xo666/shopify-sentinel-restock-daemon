"""Unit tests for OWASP security protections (SSRF, mention injection) and Discord notifier."""

from __future__ import annotations

import httpx
import pytest

from src.config import AppSettings, DiscordConfig, MonitorConfig, validate_target_domain
from src.notifier import DiscordWebhookNotifier
from src.state import DeltaType, InventoryDelta
from src.utils import mask_sensitive_strings, sanitize_discord_text


# --- SSRF Tests (CWE-918) ---
def test_ssrf_rejects_non_https() -> None:
    with pytest.raises(ValueError, match="SSRF Violation"):
        validate_target_domain("http://insecure-store.com")


def test_ssrf_rejects_localhost_and_internal() -> None:
    with pytest.raises(ValueError, match="SSRF Violation"):
        validate_target_domain("localhost")

    with pytest.raises(ValueError, match="SSRF Violation"):
        validate_target_domain("http://localhost:8080")

    with pytest.raises(ValueError, match="SSRF Violation"):
        validate_target_domain("metadata.google.internal")


def test_ssrf_rejects_private_and_metadata_ips() -> None:
    # Loopback
    with pytest.raises(ValueError, match="SSRF Violation"):
        validate_target_domain("127.0.0.1")

    # Cloud metadata IP
    with pytest.raises(ValueError, match="SSRF Violation"):
        validate_target_domain("169.254.169.254")

    # RFC 1918 private IPs
    with pytest.raises(ValueError, match="SSRF Violation"):
        validate_target_domain("192.168.1.1")

    with pytest.raises(ValueError, match="SSRF Violation"):
        validate_target_domain("10.0.0.5")


def test_ssrf_allows_valid_domains() -> None:
    assert validate_target_domain("kith.com") == "kith.com"
    assert validate_target_domain("https://shop.example.com") == "shop.example.com"


# --- Mention Sanitization Tests (CWE-116) ---
def test_mention_sanitization() -> None:
    malicious_title = "Limited Sneaker @everyone and @here <@&999888> [Fake Link](https://attacker.com)"
    sanitized = sanitize_discord_text(malicious_title)
    assert "@everyone" not in sanitized
    assert "@here" not in sanitized
    assert "@\u200beveryone" in sanitized
    assert "@\u200bhere" in sanitized
    assert "<@&999888>" not in sanitized
    assert "\\[Fake Link\\]" in sanitized


# --- Token Masking Tests (CWE-522) ---
def test_token_and_proxy_masking() -> None:
    raw_log = "Dispatching to https://discord.com/api/webhooks/123456789/SuperSecretToken1234"
    masked = mask_sensitive_strings(raw_log)
    assert "SuperSecretToken1234" not in masked
    assert "https://discord.com/api/webhooks/123456789/********" in masked

    proxy_log = "Connected via http://user:super_secret_pass@proxy.com:8080"
    masked_proxy = mask_sensitive_strings(proxy_log)
    assert "super_secret_pass" not in masked_proxy
    assert "://user:********@" in masked_proxy


# --- Notifier Embed Tests ---
@pytest.fixture
def test_settings() -> AppSettings:
    return AppSettings(
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/123/token_abc",
        PROXY_URL=None,
        LOG_LEVEL="INFO",
        monitor=MonitorConfig(store_domain="kith.com"),
        discord=DiscordConfig(
            bot_username="Shopify Sentinel",
            avatar_url="https://icon.png",
            footer_text="Shopify Sentinel Daemon",
        ),
    )


def test_embed_colors_and_checkout_url(test_settings: AppSettings) -> None:
    notifier = DiscordWebhookNotifier(settings=test_settings)

    restock_delta = InventoryDelta(
        delta_type=DeltaType.RESTOCK,
        product_id="prod_1",
        product_title="Air Jordan 1",
        variant_id="var_999",
        variant_title="US 10",
        old_price=180.0,
        new_price=180.0,
        product_url="https://kith.com/products/aj1",
        image_url="https://kith.com/aj1.jpg",
    )

    embed = notifier._build_embed(restock_delta)

    # Check WCAG Color: #00E676 -> 0x00E676 (58998)
    assert embed["color"] == 0x00E676
    assert "RESTOCK ALERT" in embed["title"]

    # Verify One-Click Checkout URL exists in fields
    quick_buy_field = next(f for f in embed["fields"] if f["name"] == "⚡ Quick Checkout")
    assert "https://kith.com/cart/var_999:1" in quick_buy_field["value"]


@pytest.mark.asyncio
async def test_notifier_dispatch_success(test_settings: AppSettings) -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == test_settings.discord_webhook_url
        return httpx.Response(204)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        notifier = DiscordWebhookNotifier(settings=test_settings, client=client)
        delta = InventoryDelta(
            delta_type=DeltaType.NEW_DROP,
            product_id="prod_2",
            product_title="Dunk Low",
            variant_id="var_888",
            variant_title="US 9",
            old_price=None,
            new_price=110.0,
            product_url="https://kith.com/products/dunk-low",
            image_url=None,
        )
        success = await notifier.dispatch_single(delta)
        assert success is True
