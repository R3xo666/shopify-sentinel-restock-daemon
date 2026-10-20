"""Discord webhook alert dispatcher with WCAG-compliant embeds and mention sanitization."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import httpx

from src.config import AppSettings
from src.state import DeltaType, InventoryDelta
from src.utils import sanitize_discord_text, setup_logger

logger = setup_logger()

# WCAG 2.2 AA Compliant High-Contrast Hex to Decimal Colors
COLOR_PALETTE = {
    DeltaType.RESTOCK: 0x00E676,     # Bright Emerald Green (#00E676)
    DeltaType.NEW_DROP: 0x2979FF,    # Cobalt Blue (#2979FF)
    DeltaType.PRICE_DROP: 0xFFAB00,  # Amber Gold (#FFAB00)
}

EMOJI_MAP = {
    DeltaType.RESTOCK: "🔄 **RESTOCK ALERT**",
    DeltaType.NEW_DROP: "🚀 **NEW DROP ALERT**",
    DeltaType.PRICE_DROP: "📉 **PRICE DROP ALERT**",
}


class DiscordWebhookNotifier:
    """Dispatches formatted inventory notifications to a Discord channel webhook."""

    def __init__(
        self,
        settings: AppSettings,
        client: httpx.AsyncClient | None = None,
        rate_limit_delay: float = 1.5,
    ) -> None:
        self.settings = settings
        self.webhook_url = settings.discord_webhook_url
        self.rate_limit_delay = rate_limit_delay
        self._custom_client = client

    def _build_embed(self, delta: InventoryDelta) -> dict[str, Any]:
        """Build a Discord embed dictionary adhering to WCAG 2.2 visual contrast and structure."""
        color = COLOR_PALETTE.get(delta.delta_type, 0x2979FF)
        header_text = EMOJI_MAP.get(delta.delta_type, "🔔 **INVENTORY UPDATE**")[:256]

        sanitized_product_title = sanitize_discord_text(delta.product_title, max_len=200)
        sanitized_variant_title = sanitize_discord_text(delta.variant_title, max_len=200)

        parsed_url = urlparse(delta.product_url)
        domain = parsed_url.hostname or self.settings.monitor.store_domain

        # One-Click Instant Cart Checkout Link
        quick_cart_url = f"https://{domain}/cart/{delta.variant_id}:1"

        fields = [
            {
                "name": "🏷️ Product",
                "value": f"[{sanitized_product_title}]({delta.product_url})"[:1024],
                "inline": False,
            },
            {
                "name": "📏 Variant / Size",
                "value": f"`{sanitized_variant_title}`"[:1024],
                "inline": True,
            },
        ]

        if delta.delta_type == DeltaType.PRICE_DROP and delta.old_price is not None:
            price_field = f"~~${delta.old_price:.2f}~~ ➔ **${delta.new_price:.2f}**"
        else:
            price_field = f"**${delta.new_price:.2f}**"

        fields.append(
            {
                "name": "💰 Price",
                "value": price_field[:1024],
                "inline": True,
            }
        )

        fields.append(
            {
                "name": "⚡ Quick Checkout",
                "value": f"[One-Click Buy Link]({quick_cart_url})"[:1024],
                "inline": False,
            }
        )

        footer_text = self.settings.discord.footer_text[:2048]

        embed: dict[str, Any] = {
            "title": header_text,
            "url": delta.product_url,
            "color": color,
            "fields": fields,
            "timestamp": delta.timestamp.isoformat(),
            "footer": {
                "text": footer_text,
                "icon_url": self.settings.discord.avatar_url,
            },
        }

        if delta.image_url:
            embed["thumbnail"] = {"url": delta.image_url}

        return embed

    async def _send_payload(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> bool:
        """Send JSON payload to the Discord webhook with retry on Discord 429."""
        try:
            response = await client.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )

            if response.status_code in {200, 204}:
                return True

            if response.status_code == 429:
                retry_data = response.json() if response.text else {}
                retry_after = float(retry_data.get("retry_after", 2.0))
                logger.warning(f"Discord webhook rate-limited. Sleeping {retry_after:.2f}s...")
                await asyncio.sleep(retry_after)
                # Retry once
                second_resp = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10.0,
                )
                return second_resp.status_code in {200, 204}

            logger.error(f"Failed to deliver Discord webhook: HTTP {response.status_code} - {response.text}")
            return False

        except httpx.RequestError as exc:
            logger.error(f"Network error delivering Discord webhook: {exc}")
            return False

    async def dispatch_single(self, delta: InventoryDelta) -> bool:
        """Dispatch a single inventory delta to Discord."""
        embed = self._build_embed(delta)
        payload = {
            "username": self.settings.discord.bot_username[:80],
            "avatar_url": self.settings.discord.avatar_url,
            "embeds": [embed],
        }

        should_close = self._custom_client is None
        client = self._custom_client or httpx.AsyncClient()

        try:
            return await self._send_payload(client, payload)
        finally:
            if should_close:
                await client.aclose()

    async def dispatch_all(self, deltas: list[InventoryDelta]) -> int:
        """Dispatch a batch of inventory deltas with leaky-bucket rate limiting."""
        if not deltas:
            return 0

        success_count = 0
        should_close = self._custom_client is None
        client = self._custom_client or httpx.AsyncClient()

        try:
            for idx, delta in enumerate(deltas):
                embed = self._build_embed(delta)
                payload = {
                    "username": self.settings.discord.bot_username[:80],
                    "avatar_url": self.settings.discord.avatar_url,
                    "embeds": [embed],
                }

                ok = await self._send_payload(client, payload)
                if ok:
                    success_count += 1

                # Polite delay between multiple webhook pushes to avoid Discord bucket exhaustion
                if idx < len(deltas) - 1:
                    await asyncio.sleep(self.rate_limit_delay)

        finally:
            if should_close:
                await client.aclose()

        return success_count
