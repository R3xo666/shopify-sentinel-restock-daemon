# Shopify Sentinel

Shopify Sentinel monitors product variants on a public Shopify storefront and sends Discord webhook alerts for inventory changes. It tracks new variants, restocks, and price drops. Variant availability and prices are stored in SQLite so each polling cycle can be compared with the previous one.

The collector reads the store’s public product JSON endpoints, optionally limited to collection handles. Use it only for storefronts you are authorized to monitor, and set a reasonable polling interval.

## Requirements and setup

~~~bash
git clone https://github.com/R3xo666/shopify-sentinel-restock-daemon.git
cd shopify-sentinel-restock-daemon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
~~~

Set `DISCORD_WEBHOOK_URL` in `.env`. `PROXY_URL` is optional; `LOG_LEVEL` defaults to `INFO`. Do not commit `.env` or share the webhook URL.

## Configuration

Edit `config.yaml`:

| Setting | Meaning |
| --- | --- |
| `monitor.store_domain` | Store hostname, without a URL path |
| `monitor.collections` | Collection handles to monitor; an empty list scans the catalog |
| `monitor.poll_interval_seconds` | Delay between daemon cycles; default 15 |
| `monitor.request_timeout_seconds` | Request timeout; default 8 seconds |
| `monitor.max_retries` | Retry count for rate limits and transient errors; default 5 |
| `monitor.user_agents` | User-Agent values used for requests |
| `discord.bot_username` | Name shown on webhook messages |
| `discord.avatar_url` | Avatar shown on webhook messages |
| `discord.footer_text` | Footer shown on webhook messages |

The checked-in config targets `kith.com` and two collection handles. Change these values for your intended store.

## Run

A diagnostic pass fetches products and sends a test Discord message when a webhook is configured:

~~~bash
python3 main.py --test
~~~

Start the continuous monitor:

~~~bash
python3 main.py --daemon
~~~

The daemon performs a warm-up pass to seed its database without alerts, then reports new variants, restocks, and price drops on later cycles. SQLite state is stored in `data/inventory_state.db`.

### Command-line options

| Option | Description | Default |
| --- | --- | --- |
| `--daemon` | Run continuous monitoring | — |
| `--test` | Fetch the catalog and test alert formatting and delivery | — |
| `--config` | YAML settings file | `config.yaml` |
| `--env` | Environment file | `.env` |

If neither mode is selected, the program runs diagnostic mode. Use `--config` and `--env` to select other files.

## Service and tests

A systemd unit template is included as `shopify_monitor.service`; edit its paths and user before installing it on a host.

~~~bash
pytest -q
~~~