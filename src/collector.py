"""Resilient asynchronous Shopify catalog collector with HTTP/2 and 429 full-jitter backoff."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from src.config import MonitorConfig
from src.utils import calculate_exponential_backoff, setup_logger

logger = setup_logger()


@dataclass(frozen=True)
class ShopifyVariant:
    """Normalized Shopify product variant record."""

    id: str
    product_id: str
    title: str
    price: float
    available: bool
    sku: str | None = None


@dataclass(frozen=True)
class ShopifyProduct:
    """Normalized Shopify product record."""

    id: str
    title: str
    handle: str
    url: str
    image_url: str | None
    variants: list[ShopifyVariant] = field(default_factory=list)


class ShopifyCollector:
    """Asynchronous client for extracting Shopify product and variant inventory."""

    def __init__(
        self,
        config: MonitorConfig,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.proxy_url = proxy_url
        self._custom_client = client
        self._ua_index = 0

    def _get_next_user_agent(self) -> str:
        """Rotate User-Agent strings cyclically."""
        if not self.config.user_agents:
            return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ua = self.config.user_agents[self._ua_index % len(self.config.user_agents)]
        self._ua_index += 1
        return ua

    def _build_headers(self) -> dict[str, str]:
        """Generate browser-like headers for catalog requests."""
        return {
            "User-Agent": self._get_next_user_agent(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    async def _create_client(self) -> httpx.AsyncClient:
        """Initialize HTTP/2 client with optional proxy and timeouts."""
        if self._custom_client is not None:
            return self._custom_client

        transport = None
        if self.proxy_url:
            transport = httpx.AsyncHTTPTransport(proxy=self.proxy_url, http2=True)

        return httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(self.config.request_timeout_seconds),
            transport=transport,
            follow_redirects=True,
        )

    def _parse_raw_products(self, raw_products: list[dict[str, Any]]) -> list[ShopifyProduct]:
        """Convert raw Shopify JSON dictionaries into normalized dataclass models."""
        products: list[ShopifyProduct] = []

        for p_data in raw_products:
            p_id = str(p_data.get("id", ""))
            p_title = str(p_data.get("title", "Untitled Product")).strip()
            p_handle = str(p_data.get("handle", "")).strip()
            product_url = f"https://{self.config.store_domain}/products/{quote(p_handle)}"

            images = p_data.get("images", [])
            image_url = None
            if images and isinstance(images, list) and isinstance(images[0], dict):
                image_url = images[0].get("src")

            variants: list[ShopifyVariant] = []
            for v_data in p_data.get("variants", []):
                v_id = str(v_data.get("id", ""))
                v_title = str(v_data.get("title", "Default Title")).strip()
                try:
                    price = float(v_data.get("price", 0.0))
                except (ValueError, TypeError):
                    price = 0.0

                is_available = bool(v_data.get("available", False))
                sku = v_data.get("sku")

                variants.append(
                    ShopifyVariant(
                        id=v_id,
                        product_id=p_id,
                        title=v_title,
                        price=price,
                        available=is_available,
                        sku=str(sku) if sku else None,
                    )
                )

            products.append(
                ShopifyProduct(
                    id=p_id,
                    title=p_title,
                    handle=p_handle,
                    url=product_url,
                    image_url=image_url,
                    variants=variants,
                )
            )

        return products

    async def fetch_page(
        self,
        client: httpx.AsyncClient,
        endpoint_url: str,
        page: int = 1,
    ) -> list[ShopifyProduct]:
        """Fetch a single page of products with exponential backoff on HTTP 429 and network errors."""
        url = f"{endpoint_url}?limit=250&page={page}"

        for attempt in range(self.config.max_retries):
            try:
                headers = self._build_headers()
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    payload = response.json()
                    raw_products = payload.get("products", [])
                    return self._parse_raw_products(raw_products)

                if response.status_code == 429:
                    retry_after_hdr = response.headers.get("Retry-After")
                    if retry_after_hdr and retry_after_hdr.isdigit():
                        jitter = secrets.SystemRandom().uniform(0.5, 2.0)
                        backoff = float(retry_after_hdr) + jitter
                    else:
                        backoff = calculate_exponential_backoff(attempt)

                    logger.warning(
                        f"[yellow]HTTP 429 Rate Limited on {self.config.store_domain}. "
                        f"Retrying in {backoff:.2f}s (Attempt {attempt + 1}/{self.config.max_retries})...[/yellow]"
                    )
                    await asyncio.sleep(backoff)
                    continue

                if response.status_code in {500, 502, 503, 504}:
                    backoff = calculate_exponential_backoff(attempt)
                    logger.warning(
                        f"[yellow]Shopify upstream error {response.status_code}. "
                        f"Retrying in {backoff:.2f}s (Attempt {attempt + 1}/{self.config.max_retries})...[/yellow]"
                    )
                    await asyncio.sleep(backoff)
                    continue

                logger.error(f"Unexpected status code {response.status_code} fetching {url}")
                return []

            except (httpx.RequestError, httpx.TimeoutException) as exc:
                backoff = calculate_exponential_backoff(attempt)
                logger.warning(
                    f"[yellow]Network error: {exc}. Retrying in {backoff:.2f}s "
                    f"(Attempt {attempt + 1}/{self.config.max_retries})...[/yellow]"
                )
                await asyncio.sleep(backoff)

        logger.error(f"[red]Exhausted {self.config.max_retries} retries fetching {url}[/red]")
        return []

    async def fetch_collection_or_catalog(
        self,
        collection_handle: str | None = None,
    ) -> list[ShopifyProduct]:
        """Paginate through catalog or collection products."""
        if collection_handle:
            clean_handle = quote(collection_handle.strip("/"))
            endpoint = f"https://{self.config.store_domain}/collections/{clean_handle}/products.json"
        else:
            endpoint = f"https://{self.config.store_domain}/products.json"

        all_products: list[ShopifyProduct] = []
        page = 1

        client = await self._create_client()
        should_close = self._custom_client is None

        try:
            while True:
                products = await self.fetch_page(client, endpoint, page=page)
                if not products:
                    break
                all_products.extend(products)
                if len(products) < 250:
                    # Last page reached
                    break
                page += 1
                await asyncio.sleep(0.5)  # Brief polite pacing between pagination requests
        finally:
            if should_close:
                await client.aclose()

        return all_products

    async def fetch_all_targets(self) -> list[ShopifyProduct]:
        """Fetch all configured collections or main catalog, deduplicating products by ID."""
        combined_products: dict[str, ShopifyProduct] = {}

        if self.config.collections:
            for handle in self.config.collections:
                try:
                    items = await self.fetch_collection_or_catalog(handle)
                    for item in items:
                        combined_products[item.id] = item
                except (httpx.RequestError, ValueError, KeyError) as exc:
                    logger.error(f"Failed fetching collection '{handle}': {exc}")
        else:
            try:
                items = await self.fetch_collection_or_catalog(None)
                for item in items:
                    combined_products[item.id] = item
            except (httpx.RequestError, ValueError, KeyError) as exc:
                logger.error(f"Failed fetching catalog: {exc}")

        return list(combined_products.values())
