"""Unit tests for ShopifyCollector: mock responses, pagination, and 429 backoff handling."""

from __future__ import annotations

import httpx
import pytest

from src.collector import ShopifyCollector
from src.config import MonitorConfig

MOCK_STORE_DOMAIN = "test-store.myshopify.com"

SAMPLE_SHOPIFY_PAGE_1 = {
    "products": [
        {
            "id": 1001,
            "title": "Retro Runner 90s",
            "handle": "retro-runner-90s",
            "images": [{"src": "https://cdn.shopify.com/s/files/1/shoe.jpg"}],
            "variants": [
                {
                    "id": 2001,
                    "title": "US 9 / Black",
                    "price": "140.00",
                    "available": True,
                    "sku": "RR-9-BLK",
                },
                {
                    "id": 2002,
                    "title": "US 10 / Black",
                    "price": "140.00",
                    "available": False,
                    "sku": "RR-10-BLK",
                },
            ],
        }
    ]
}


@pytest.fixture
def monitor_config() -> MonitorConfig:
    return MonitorConfig(
        store_domain=MOCK_STORE_DOMAIN,
        collections=["footwear"],
        poll_interval_seconds=10,
        request_timeout_seconds=5.0,
        max_retries=3,
    )


@pytest.mark.asyncio
async def test_collector_success_parsing(monitor_config: MonitorConfig) -> None:
    """Test successful JSON parsing into normalized Product and Variant models."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_SHOPIFY_PAGE_1)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        collector = ShopifyCollector(config=monitor_config, client=client)
        products = await collector.fetch_collection_or_catalog("footwear")

        assert len(products) == 1
        product = products[0]
        assert product.id == "1001"
        assert product.title == "Retro Runner 90s"
        assert product.handle == "retro-runner-90s"
        assert product.url == f"https://{MOCK_STORE_DOMAIN}/products/retro-runner-90s"
        assert product.image_url == "https://cdn.shopify.com/s/files/1/shoe.jpg"

        assert len(product.variants) == 2
        v1, v2 = product.variants
        assert v1.id == "2001"
        assert v1.price == 140.0
        assert v1.available is True
        assert v2.id == "2002"
        assert v2.available is False


@pytest.mark.asyncio
async def test_collector_429_backoff_retry(monitor_config: MonitorConfig) -> None:
    """Test that HTTP 429 triggers retry and succeeds on subsequent attempt."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=SAMPLE_SHOPIFY_PAGE_1)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        collector = ShopifyCollector(config=monitor_config, client=client)
        products = await collector.fetch_page(
            client=client,
            endpoint_url=f"https://{MOCK_STORE_DOMAIN}/products.json",
            page=1,
        )

        assert call_count == 2
        assert len(products) == 1
        assert products[0].id == "1001"


@pytest.mark.asyncio
async def test_collector_exhaust_retries(monitor_config: MonitorConfig) -> None:
    """Test that collector returns empty list gracefully when all retries fail."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        collector = ShopifyCollector(config=monitor_config, client=client)
        products = await collector.fetch_page(
            client=client,
            endpoint_url=f"https://{MOCK_STORE_DOMAIN}/products.json",
            page=1,
        )

        assert products == []
