# Shopify Sentinel

Shopify Sentinel monitors product variants on a public Shopify storefront and sends Discord webhook alerts when a variant comes back in stock or its price drops. It keeps the previous variant state in SQLite so each polling cycle can be compared with the last one.

The storefront, collections, polling interval, and retry limit are set in `config.yaml`. Use the tool only with storefronts you are authorized to monitor, and keep polling intervals respectful.

## Setup

~~~bash
git clone https://github.com/R3xo666/shopify-sentinel-restock-daemon.git
cd shopify-sentinel-restock-daemon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
~~~

Set `DISCORD_WEBHOOK_URL` in `.env`. `PROXY_URL` is optional. Do not commit `.env` or share the webhook URL.

Edit `config.yaml` with the storefront domain and collection handles to monitor.

## Run

Run one diagnostic pass:

~~~bash
python3 main.py --test
~~~

When a webhook URL is configured, diagnostic mode sends a test Discord message.

Start continuous monitoring:

~~~bash
python3 main.py --daemon
~~~

The daemon performs an initial warm-up pass to seed its SQLite state without sending alerts, then checks for restocks and price drops on each polling cycle. `--config` and `--env` can point to alternate configuration files.

## Tests

~~~bash
pytest -q
~~~