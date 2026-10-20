"""Unit tests for InventoryStateManager: SQLite WAL, warmup suppression, and delta detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.collector import ShopifyProduct, ShopifyVariant
from src.state import DeltaType, InventoryStateManager


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_inventory.db"


def make_product(
    product_id: str,
    title: str,
    variant_id: str,
    price: float,
    available: bool,
) -> ShopifyProduct:
    return ShopifyProduct(
        id=product_id,
        title=title,
        handle=title.lower().replace(" ", "-"),
        url=f"https://test.com/products/{product_id}",
        image_url="https://test.com/img.jpg",
        variants=[
            ShopifyVariant(
                id=variant_id,
                product_id=product_id,
                title="Size 10",
                price=price,
                available=available,
            )
        ],
    )


def test_warmup_silence(temp_db: Path) -> None:
    """Warmup phase must populate the database without emitting any delta alerts."""
    manager = InventoryStateManager(db_path=temp_db)
    try:
        products = [
            make_product("p1", "Sneaker Alpha", "v1", 120.0, available=True),
            make_product("p2", "Sneaker Beta", "v2", 150.0, available=False),
        ]

        deltas = manager.detect_deltas(products, is_warmup=True)
        assert len(deltas) == 0

        # Verify items were saved in DB
        v1 = manager.get_variant("v1")
        assert v1 is not None
        assert v1["available"] is True
        assert v1["price"] == 120.0

        v2 = manager.get_variant("v2")
        assert v2 is not None
        assert v2["available"] is False
    finally:
        manager.close()


def test_detect_restock_transition(temp_db: Path) -> None:
    """Detect restock when available switches from False (0) to True (1)."""
    manager = InventoryStateManager(db_path=temp_db)
    try:
        # Initial seed: item is OOS
        initial = [make_product("p1", "Sneaker Alpha", "v1", 120.0, available=False)]
        manager.detect_deltas(initial, is_warmup=True)

        # Second scan: item is now in-stock
        updated = [make_product("p1", "Sneaker Alpha", "v1", 120.0, available=True)]
        deltas = manager.detect_deltas(updated, is_warmup=False)

        assert len(deltas) == 1
        delta = deltas[0]
        assert delta.delta_type == DeltaType.RESTOCK
        assert delta.variant_id == "v1"
        assert delta.product_title == "Sneaker Alpha"
        assert delta.new_price == 120.0
    finally:
        manager.close()


def test_detect_new_drop(temp_db: Path) -> None:
    """Detect new drop when an unrecorded variant arrives in-stock."""
    manager = InventoryStateManager(db_path=temp_db)
    try:
        # Initial seed with product 1
        initial = [make_product("p1", "Sneaker Alpha", "v1", 120.0, available=True)]
        manager.detect_deltas(initial, is_warmup=True)

        # Second scan introduces a brand new product 2
        updated = [
            make_product("p1", "Sneaker Alpha", "v1", 120.0, available=True),
            make_product("p2", "Sneaker Gamma", "v2", 200.0, available=True),
        ]
        deltas = manager.detect_deltas(updated, is_warmup=False)

        assert len(deltas) == 1
        delta = deltas[0]
        assert delta.delta_type == DeltaType.NEW_DROP
        assert delta.variant_id == "v2"
        assert delta.product_title == "Sneaker Gamma"
        assert delta.new_price == 200.0
    finally:
        manager.close()


def test_detect_price_drop(temp_db: Path) -> None:
    """Detect price drop when an available item's price decreases."""
    manager = InventoryStateManager(db_path=temp_db)
    try:
        # Initial seed: in-stock at $150
        initial = [make_product("p1", "Sneaker Alpha", "v1", 150.0, available=True)]
        manager.detect_deltas(initial, is_warmup=True)

        # Price drops to $120
        discounted = [make_product("p1", "Sneaker Alpha", "v1", 120.0, available=True)]
        deltas = manager.detect_deltas(discounted, is_warmup=False)

        assert len(deltas) == 1
        delta = deltas[0]
        assert delta.delta_type == DeltaType.PRICE_DROP
        assert delta.old_price == 150.0
        assert delta.new_price == 120.0
    finally:
        manager.close()


def test_no_delta_on_price_increase_or_same_state(temp_db: Path) -> None:
    """Ensure no false alarms on price increases or identical consecutive scans."""
    manager = InventoryStateManager(db_path=temp_db)
    try:
        initial = [make_product("p1", "Sneaker Alpha", "v1", 100.0, available=True)]
        manager.detect_deltas(initial, is_warmup=True)

        # Same state
        deltas = manager.detect_deltas(initial, is_warmup=False)
        assert len(deltas) == 0

        # Price increase (not a drop)
        higher_price = [make_product("p1", "Sneaker Alpha", "v1", 120.0, available=True)]
        deltas = manager.detect_deltas(higher_price, is_warmup=False)
        assert len(deltas) == 0
    finally:
        manager.close()
