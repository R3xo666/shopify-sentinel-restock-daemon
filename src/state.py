"""SQLite state repository with Write-Ahead Logging (WAL) and inventory delta detection."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from src.collector import ShopifyProduct
from src.utils import setup_logger

logger = setup_logger()


class DeltaType(str, Enum):
    """Types of actionable inventory transitions."""

    RESTOCK = "RESTOCK"
    NEW_DROP = "NEW_DROP"
    PRICE_DROP = "PRICE_DROP"


@dataclass(frozen=True)
class InventoryDelta:
    """Actionable change in inventory state to notify subscribers."""

    delta_type: DeltaType
    product_id: str
    product_title: str
    variant_id: str
    variant_title: str
    old_price: float | None
    new_price: float
    product_url: str
    image_url: str | None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class InventoryStateManager:
    """Manages persistent SQLite state for Shopify inventory variants."""

    def __init__(self, db_path: Path | str = "data/inventory_state.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=15.0,
                check_same_thread=False,
            )
            # Enable WAL mode for high-concurrency and crash resilience
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        return self._conn

    def _init_db(self) -> None:
        """Create variants table with indexes if it doesn't already exist."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS variants (
                    id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    product_title TEXT NOT NULL,
                    price REAL NOT NULL,
                    available INTEGER NOT NULL,
                    handle TEXT NOT NULL,
                    image_url TEXT,
                    updated_at TIMESTAMP NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_variants_product_id ON variants(product_id);"
            )

    def close(self) -> None:
        """Commit pending transactions and cleanly close SQLite database."""
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error as e:
                logger.error(f"Error closing database connection: {e}")
            finally:
                self._conn = None

    def get_variant(self, variant_id: str) -> dict[str, Any] | None:
        """Retrieve stored variant record by ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, product_id, title, product_title, price, available, handle, image_url, updated_at
            FROM variants WHERE id = ?
            """,
            (variant_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        return {
            "id": row[0],
            "product_id": row[1],
            "title": row[2],
            "product_title": row[3],
            "price": float(row[4]),
            "available": bool(row[5]),
            "handle": row[6],
            "image_url": row[7],
            "updated_at": row[8],
        }

    def detect_deltas(
        self,
        products: list[ShopifyProduct],
        is_warmup: bool = False,
    ) -> list[InventoryDelta]:
        """Detect RESTOCK, NEW_DROP, and PRICE_DROP transitions across products.

        When `is_warmup` is True, synchronizes existing state without emitting alert deltas.
        """
        deltas: list[InventoryDelta] = []
        conn = self._get_connection()
        now_iso = datetime.now(UTC).isoformat()

        with conn:
            for product in products:
                for variant in product.variants:
                    existing = self.get_variant(variant.id)

                    if existing is None:
                        # Variant never seen before
                        if not is_warmup and variant.available:
                            # New drop detected
                            deltas.append(
                                InventoryDelta(
                                    delta_type=DeltaType.NEW_DROP,
                                    product_id=product.id,
                                    product_title=product.title,
                                    variant_id=variant.id,
                                    variant_title=variant.title,
                                    old_price=None,
                                    new_price=variant.price,
                                    product_url=product.url,
                                    image_url=product.image_url,
                                )
                            )

                        conn.execute(
                            """
                            INSERT INTO variants (id, product_id, title, product_title, price, available, handle, image_url, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                variant.id,
                                product.id,
                                variant.title,
                                product.title,
                                variant.price,
                                1 if variant.available else 0,
                                product.handle,
                                product.image_url,
                                now_iso,
                            ),
                        )
                    else:
                        prev_available = existing["available"]
                        prev_price = existing["price"]

                        if not is_warmup:
                            # 1. Check RESTOCK: transitioned from 0 -> 1
                            if not prev_available and variant.available:
                                deltas.append(
                                    InventoryDelta(
                                        delta_type=DeltaType.RESTOCK,
                                        product_id=product.id,
                                        product_title=product.title,
                                        variant_id=variant.id,
                                        variant_title=variant.title,
                                        old_price=prev_price,
                                        new_price=variant.price,
                                        product_url=product.url,
                                        image_url=product.image_url,
                                    )
                                )

                            # 2. Check PRICE DROP: item is currently available and price strictly dropped
                            elif variant.available and variant.price < prev_price:
                                deltas.append(
                                    InventoryDelta(
                                        delta_type=DeltaType.PRICE_DROP,
                                        product_id=product.id,
                                        product_title=product.title,
                                        variant_id=variant.id,
                                        variant_title=variant.title,
                                        old_price=prev_price,
                                        new_price=variant.price,
                                        product_url=product.url,
                                        image_url=product.image_url,
                                    )
                                )

                        # Update existing variant record
                        conn.execute(
                            """
                            UPDATE variants
                            SET title = ?, product_title = ?, price = ?, available = ?, handle = ?, image_url = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                variant.title,
                                product.title,
                                variant.price,
                                1 if variant.available else 0,
                                product.handle,
                                product.image_url,
                                now_iso,
                                variant.id,
                            ),
                        )

        return deltas
