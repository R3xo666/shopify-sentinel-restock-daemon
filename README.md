<div align="center">

# 👟 Shopify Sentinel
### High-Velocity Autonomous Restock & Price Drop Discord Daemon

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![AsyncIO & HTTP/2](https://img.shields.io/badge/Networking-AsyncIO%20%26%20HTTP%2F2-00599C?style=for-the-badge&logo=httpx&logoColor=white)](https://www.python-httpx.org/)
[![SQLite WAL](https://img.shields.io/badge/Database-SQLite%20WAL-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![OWASP Defended](https://img.shields.io/badge/Security-OWASP%20CWE--918%20%7C%20CWE--116-10B981?style=for-the-badge&logo=shield&logoColor=white)](#-security--defensive-engineering)
[![Tests Passed](https://img.shields.io/badge/Pytest-16%2F16%20Passed%20(100%25)-brightgreen?style=for-the-badge&logo=pytest&logoColor=white)](#-automated-verification-suite)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)

<p align="center">
  <b>Sub-100ms Shopify catalog poller with idempotent state tracking, 429 jitter backoff, and WCAG-compliant 1-click checkout Discord embeds.</b><br>
  Engineered by <a href="https://r3xo666.github.io/"><b>Aditya Kumar (R3xo666)</b></a>
</p>

[Key Features](#-key-features) • [System Architecture](#-system-architecture) • [Discord Embeds](#-discord-alert-preview) • [Quickstart](#-quickstart) • [Security](#-security--defensive-engineering) • [Systemd Deployment](#-linux-vps-systemd-daemon)

---

</div>

## 📌 Overview

**Shopify Sentinel** is a lightweight, non-blocking Python 24/7 background daemon engineered for e-commerce resellers, monitors, and communities. Instead of running brittle HTML parsers or resource-hungry headless browsers (Playwright/Puppeteer taking 500MB+ RAM), Sentinel communicates directly with Shopify's high-speed native JSON endpoints. 

It tracks variant-level inventory changes in an atomic **SQLite WAL state machine**, completely suppresses initial boot alert storms, and transmits rich, WCAG 2.2 AA compliant Discord webhook embeds equipped with **One-Click Instant Checkout URLs** (`/cart/{variant_id}:1`).

```text
 ╭────────────────────────────── 🤖 System Status ──────────────────────────────╮
 │ Shopify Inventory Sentinel Daemon v1.0.0                                     │
 │ Target Store: https://kith.com                                               │
 │ Monitored Collections: mens-footwear, apparel                                │
 │ Poll Interval: 15s | Timeout: 8.0s                                           │
 │ Memory Footprint: ~26 MB RAM | Engine: HTTP/2 Multiplexed                    │
 ╰──────────────────────────────────────────────────────────────────────────────╯
```

---

## 🚀 Key Features

* **⚡ Sub-100ms Native Extraction:** Queries `/products.json?limit=250` and `/collections/{handle}/products.json` directly. Eliminates HTML parsing fragility and scraper bans.
* **🛡️ Zero Startup Spam (Warmup Seeding):** On the first scan, Sentinel silently seeds SQLite with existing stock without spamming alerts. Only delta transitions trigger dispatches.
* **🎯 Three Actionable Delta Triggers:**
  * `RESTOCK`: Out-of-Stock variant (`available: 0`) flips to In-Stock (`available: 1`).
  * `NEW_DROP`: Brand new product or variant discovered after initial warmup.
  * `PRICE_DROP`: In-stock item price decreases (`new_price < old_price`). Price increases are silently ignored.
* **🛒 1-Click Instant Buy URLs:** Alerts generate direct checkout hyperlinks (`https://{domain}/cart/{variant_id}:1`) allowing instant buying before hype drops sell out.
* **🔄 Anti-Ban & 429 Backoff:** Enforces exponential backoff with randomized Full Jitter (`min(60, 2^attempt + jitter)`) and cyclically rotates User-Agent browser headers.
* **🔒 Enterprise OWASP Hardening:**
  * **CWE-918 (SSRF Defense):** Validates target store domains against RFC 1918 private subnets, loopbacks, and cloud metadata IPs (`169.254.169.254`).
  * **CWE-116 (Discord Mention Sanitization):** Strips and neutralizes `@everyone` / `@here` exploit attempts inside product titles.
  * **CWE-522 (Credential Masking):** Discord webhook tokens and proxy passwords are systematically masked in logs.
* **🪶 Ultra-Low Memory (< 30MB RAM):** Perfect for $5 budget Linux VPS instances running multiple background micro-services.

---

## 🏛️ System Architecture

Built following **Ousterhout's Deep Module Pattern**—small, robust interfaces concealing complex internal machinery:

```mermaid
flowchart TD
    subgraph Config Layer
        ENV[".env (Webhook, Proxies, Secrets)"]
        CONF["config.yaml (Domains, Collections, Intervals)"]
    end

    subgraph Core Engine [Deep Modules]
        Collector["ShopifyCollector (HTTP/2 Async, UA Rotation, Jitter Backoff)"]
        StateMgr["InventoryStateManager (SQLite WAL, Atomic Delta Detector)"]
        Notifier["DiscordWebhookNotifier (Leaky-Bucket Rate Limiter, WCAG Builder)"]
        Daemon["RestockDaemon (Orchestrator, SIGINT/SIGTERM Trap)"]
    end

    ENV --> Collector
    CONF --> Collector
    Collector -->|"Normalized Products"| StateMgr
    StateMgr -->|"Actionable Deltas"| Notifier
    Notifier -->|"Webhook POST"| Discord["Discord Channel"]
    Daemon -. "Lifecycle Supervision" .-> Collector
```

---

## 🎨 Discord Alert Preview

Alerts are designed adhering to **WCAG 2.2 AA accessibility standards** with high visual contrast color coding and screen-reader friendly markdown:

| Trigger Type | Embed Color | Visual Format | Action Item |
| :--- | :---: | :--- | :--- |
| **🔄 RESTOCK** | `#00E676` (Emerald) | `[Product Name](URL)` • `Size / Variant` • `$Price` | `[⚡ One-Click Buy Link]` |
| **🚀 NEW DROP** | `#2979FF` (Cobalt) | `[Product Name](URL)` • `Size / Variant` • `$Price` | `[⚡ One-Click Buy Link]` |
| **📉 PRICE DROP** | `#FFAB00` (Amber) | `[Product Name](URL)` • `~~$Old~~ ➔ $New` | `[⚡ One-Click Buy Link]` |

```text
 ┌─────────────────────────────────────────────────────────────┐
 │  🔄 RESTOCK ALERT                                           │
 │  https://kith.com/products/retro-runner-90s                 │
 │                                                             │
 │  🏷️ Product: Retro Runner 90s                               │
 │  📏 Variant: US 9.5 / Black                                 │
 │  💰 Price: $140.00                                          │
 │  ⚡ Quick Checkout: [One-Click Buy Link](https://...)       │
 │                                                             │
 │  Shopify Inventory Sentinel • 2026-10-21 02:15:15 UTC       │
 └─────────────────────────────────────────────────────────────┘
```

---

## 📂 Repository Structure

```text
shopify-sentinel-restock-daemon/
├── .env.example              # Template with webhook secrets & proxy settings
├── .gitignore                # Production ignore list for databases, caches, logs
├── config.yaml               # Store configuration, collections, poll frequency
├── pytest.ini                # Pytest async loop scope configuration
├── requirements.txt          # Pinned production dependencies
├── shopify_monitor.service   # Ready-to-use Linux systemd daemon definition
├── main.py                   # Main CLI entrypoint (--test, --daemon, Rich UI)
├── README.md                 # Complete system documentation
├── src/
│   ├── __init__.py
│   ├── config.py             # Pydantic v2 configuration models & SSRF validation
│   ├── collector.py          # Resilient AsyncIO HTTP/2 Shopify catalog poller
│   ├── state.py              # SQLite persistent inventory state tracker (WAL mode)
│   ├── notifier.py           # Discord webhook dispatcher with rate-limit queue
│   └── utils.py              # Rich logger, exponential backoff jitter & text sanitizers
└── tests/
    ├── __init__.py
    ├── test_collector.py     # Mocked Shopify responses & 429 backoff tests
    ├── test_state.py         # Restock & price drop delta transition tests
    └── test_security_and_notifier.py # SSRF validation, mention sanitization, and embeds
```

---

## ⚡ Quickstart

### 1. Clone & Set Up Environment
```bash
git clone https://github.com/R3xo666/shopify-sentinel-restock-daemon.git
cd shopify-sentinel-restock-daemon

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install pinned dependencies
pip install -r requirements.txt
```

### 2. Configure Credentials
```bash
cp .env.example .env
```
Edit `.env` with your Discord webhook URL:
```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
PROXY_URL=                  # Optional: http://user:pass@proxy-ip:port
LOG_LEVEL=INFO
```

### 3. Customize Monitored Stores (`config.yaml`)
```yaml
monitor:
  store_domain: "kith.com" # Any public Shopify storefront
  collections:
    - "mens-footwear"
    - "apparel"
  poll_interval_seconds: 15
  request_timeout_seconds: 8
  max_retries: 5
```

### 4. Run Self-Test Diagnostic
```bash
python main.py --test
```

### 5. Launch 24/7 Monitoring Loop
```bash
python main.py --daemon
```

---

## 🛡️ Security & Defensive Engineering

| Threat / Vulnerability | OWASP Category | Defensive Implementation |
| :--- | :---: | :--- |
| **Server-Side Request Forgery** | **CWE-918** | Strict scheme whitelisting (`https://` only) + DNS pre-resolution checking against RFC 1918 subnets, loopbacks, and AWS/GCP metadata (`169.254.169.254`). |
| **Discord Mention Abuse** | **CWE-116** | Zero-width space insertion (`@\u200beveryone`, `@\u200bhere`) and bracket escaping to prevent server raid / phishing embeds. |
| **Credential & Token Leakage** | **CWE-522** | Regex-powered `SensitiveDataFilter` masks tokens in URLs (`/webhooks/123/***/`) and proxy connection strings across all loggers. |
| **Database Corruption on Crash** | **CWE-399** | SQLite Write-Ahead Logging (WAL) mode + `PRAGMA synchronous = NORMAL`. Signal handlers trap `SIGINT`/`SIGTERM` to safely commit in-memory buffers. |

---

## 🧪 Automated Verification Suite

Sentinel includes a comprehensive test suite covering network mocking, rate limiting, and security invariants:

```bash
pytest -v
```

```text
============================= 16 passed in 22.52s ==============================
✓ test_collector_success_parsing
✓ test_collector_429_backoff_retry
✓ test_collector_exhaust_retries
✓ test_ssrf_rejects_non_https
✓ test_ssrf_rejects_localhost_and_internal
✓ test_ssrf_rejects_private_and_metadata_ips
✓ test_ssrf_allows_valid_domains
✓ test_mention_sanitization
✓ test_token_and_proxy_masking
✓ test_embed_colors_and_checkout_url
✓ test_notifier_dispatch_success
✓ test_warmup_silence
✓ test_detect_restock_transition
✓ test_detect_new_drop
✓ test_detect_price_drop
✓ test_no_delta_on_price_increase_or_same_state
```

---

## 🐧 Linux VPS (systemd Daemon)

To keep Sentinel running indefinitely in the background on Ubuntu/Debian/Fedora VPS:

```bash
# 1. Copy directory to /opt
sudo cp -r . /opt/shopify-sentinel-restock-daemon
cd /opt/shopify-sentinel-restock-daemon

# 2. Setup production virtual environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Install and start systemd service
sudo cp shopify_monitor.service /etc/systemd/system/shopify_sentinel.service
sudo systemctl daemon-reload
sudo systemctl enable shopify_sentinel
sudo systemctl start shopify_sentinel

# 4. Stream real-time logs
sudo journalctl -u shopify_sentinel -f
```

---

## 📜 License & Acknowledgments

Distributed under the **MIT License**. Built with engineering rigors for speed, resilience, and zero maintenance overhead.

Crafted by **[Aditya Kumar (R3xo666)](https://github.com/R3xo666)** • [Portfolio](https://r3xo666.github.io/) • [Discord](https://discord.com) (`dattebayoo.bat`)
