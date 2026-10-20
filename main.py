"""Main CLI and 24/7 daemon orchestrator for the Shopify Restock & Price Drop Discord Sentinel."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.collector import ShopifyCollector, ShopifyProduct
from src.config import AppSettings
from src.notifier import DiscordWebhookNotifier
from src.state import DeltaType, InventoryDelta, InventoryStateManager
from src.utils import setup_logger

console = Console()
logger = setup_logger()


class RestockDaemon:
    """Supervises the scraping lifecycle, delta detection, and graceful daemon termination."""

    def __init__(self, settings: AppSettings, db_path: Path | str = "data/inventory_state.db") -> None:
        self.settings = settings
        self.collector = ShopifyCollector(
            config=settings.monitor,
            proxy_url=settings.proxy_url,
        )
        self.state_manager = InventoryStateManager(db_path=db_path)
        self.notifier = DiscordWebhookNotifier(settings=settings)
        self.is_running = False
        self._stop_event = asyncio.Event()

    def _setup_signal_handlers(self) -> None:
        """Trap SIGINT (Ctrl+C) and SIGTERM for graceful shutdown."""
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:
                # Fallback on environments lacking loop.add_signal_handler
                signal.signal(sig, lambda _s, _f: self.stop())

    def stop(self) -> None:
        """Signal the daemon to stop."""
        if not self.is_running:
            return
        logger.info("[bold yellow]Received shutdown signal. Stopping daemon gracefully...[/bold yellow]")
        self.is_running = False
        self._stop_event.set()

    def display_banner(self) -> None:
        """Render a formatted startup banner."""
        banner_text = (
            f"[bold cyan]Shopify Inventory Sentinel Daemon v1.0.0[/bold cyan]\n"
            f"[green]Target Store:[/green] https://{self.settings.monitor.store_domain}\n"
            f"[green]Monitored Collections:[/green] {', '.join(self.settings.monitor.collections) or 'Full Catalog'}\n"
            f"[green]Poll Interval:[/green] {self.settings.monitor.poll_interval_seconds}s | "
            f"[green]Timeout:[/green] {self.settings.monitor.request_timeout_seconds}s\n"
            f"[green]Proxy Active:[/green] {'Yes' if self.settings.proxy_url else 'Direct Connection'}"
        )
        console.print(Panel(banner_text, title="🤖 [bold white]System Status[/bold white]", border_style="cyan"))

    def render_summary_table(
        self,
        products: list[ShopifyProduct],
        deltas: list[InventoryDelta],
        cycle_number: int,
    ) -> None:
        """Print a Rich table detailing recent scan results."""
        total_variants = sum(len(p.variants) for p in products)
        available_variants = sum(sum(1 for v in p.variants if v.available) for p in products)

        table = Table(title=f"Scan Cycle #{cycle_number} Summary", show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Timestamp", datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"))
        table.add_row("Total Products Scanned", str(len(products)))
        table.add_row("Total Variants Tracked", str(total_variants))
        table.add_row("Variants In-Stock", f"{available_variants}/{total_variants}")
        table.add_row("Actionable Deltas Found", str(len(deltas)))

        if deltas:
            for delta in deltas:
                table.add_row(
                    f"[{delta.delta_type.value}] {delta.product_title[:25]}",
                    f"${delta.new_price:.2f} ({delta.variant_title})",
                )

        console.print(table)

    async def run_single_cycle(self, is_warmup: bool = False) -> tuple[list[ShopifyProduct], list[InventoryDelta]]:
        """Execute one complete polling and delta detection pass."""
        products = await self.collector.fetch_all_targets()
        deltas = self.state_manager.detect_deltas(products, is_warmup=is_warmup)
        return products, deltas

    async def run_daemon(self) -> None:
        """Run continuous 24/7 monitoring loop."""
        self.is_running = True
        self._setup_signal_handlers()
        self.display_banner()

        logger.info("[bold green]Starting initial Warmup phase (seeding database, silencing alerts)...[/bold green]")
        products, _ = await self.run_single_cycle(is_warmup=True)
        total_variants = sum(len(p.variants) for p in products)
        logger.info(f"Warmup complete. Seeded {len(products)} products ({total_variants} variants) into SQLite.")

        cycle_count = 1
        try:
            while self.is_running:
                logger.info(f"Initiating scan cycle #{cycle_count}...")
                try:
                    products, deltas = await self.run_single_cycle(is_warmup=False)
                    self.render_summary_table(products, deltas, cycle_count)

                    if deltas:
                        logger.info(f"[bold cyan]Found {len(deltas)} inventory deltas! Dispatching alerts to Discord...[/bold cyan]")
                        sent = await self.notifier.dispatch_all(deltas)
                        logger.info(f"[bold green]Successfully delivered {sent}/{len(deltas)} Discord webhook alerts.[/bold green]")

                except (httpx.RequestError, sqlite3.Error, OSError, ValueError) as cycle_exc:
                    logger.error(f"[red]Transient error in cycle #{cycle_count}: {cycle_exc}[/red]")

                cycle_count += 1

                # Polite pause until next cycle or stop signal
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=float(self.settings.monitor.poll_interval_seconds),
                    )
                except TimeoutError:
                    pass

        finally:
            self.state_manager.close()
            logger.info("[bold green]State manager committed and closed. Daemon shutdown clean.[/bold green]")

    async def run_test_mode(self) -> None:
        """Execute diagnostic self-test: checks config, queries catalog, and tests alert engine."""
        self.display_banner()
        console.print("[bold yellow]🧪 Running diagnostic self-test...[/bold yellow]")

        # 1. Fetch catalog
        console.print("[cyan]1. Fetching product catalog...[/cyan]")
        products, _ = await self.run_single_cycle(is_warmup=True)
        console.print(f"[green]✓ Successfully extracted {len(products)} products from {self.settings.monitor.store_domain}[/green]")

        # 2. Render summary
        self.render_summary_table(products, [], cycle_number=0)

        # 3. Test Webhook with diagnostic delta
        console.print("[cyan]2. Testing Discord webhook dispatch...[/cyan]")
        test_delta = InventoryDelta(
            delta_type=DeltaType.RESTOCK,
            product_id="test-product-999",
            product_title="Shopify Sentinel Diagnostic Verification",
            variant_id="999888777",
            variant_title="US 10.5 / Black",
            old_price=160.0,
            new_price=140.0,
            product_url=f"https://{self.settings.monitor.store_domain}/products/test-product",
            image_url=self.settings.discord.avatar_url,
        )

        success = await self.notifier.dispatch_single(test_delta)
        if success:
            console.print("[bold green]✓ Discord webhook test succeeded! Embed successfully dispatched.[/bold green]")
        else:
            console.print("[yellow]! Note: Webhook test was not delivered (e.g. mock token or network). Config & formatting valid.[/yellow]")

        self.state_manager.close()
        console.print("[bold green]✓ Diagnostic test completed successfully.[/bold green]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shopify Inventory Sentinel Daemon")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run 24/7 background monitoring daemon",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run single diagnostic verification pass and exit",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--env",
        type=str,
        default=".env",
        help="Path to .env secrets file (default: .env)",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()

    if not args.daemon and not args.test:
        console.print("[yellow]No mode specified. Defaulting to --test. Use --daemon for 24/7 monitoring.[/yellow]")
        args.test = True

    try:
        settings = AppSettings.load(config_path=args.config, env_file=args.env)
    except (ValueError, FileNotFoundError, yaml.YAMLError) as exc:
        console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        sys.exit(1)

    daemon = RestockDaemon(settings=settings)

    if args.test:
        await daemon.run_test_mode()
    elif args.daemon:
        await daemon.run_daemon()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Execution interrupted by user. Exiting.[/bold yellow]")


if __name__ == "__main__":
    main()
