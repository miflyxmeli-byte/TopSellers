# MiFly Market Intelligence

Initial technical validation for Mercado Libre Chile (site `MLC`). This stage only verifies the official API capabilities required for category bestseller analysis.

## Requirements

- Python 3.12+
- Internet access for live API validation
- OAuth credentials only when an endpoint requires them

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

No real credentials belong in `.env.example` or source control.

## Run the validation script

```bash
source .venv/bin/activate
python scripts/test_mercadolibre_api.py
```

The script uses Mercado Libre Chile (`MLC`), checks the complete downloadable category tree and three technology-related categories, applies request timeouts, logs failures, and prints category and bestseller results. With `MELI_ACCESS_TOKEN` configured it enriches the documented `ITEM`, `PRODUCT`, and `USER_PRODUCT` highlight types. It also attempts the documented item review and seller resources when the preceding response supplies their IDs. Missing fields are printed as `-`. Results and limitations are documented in `docs/API_CAPABILITIES.md`.

## Tests

```bash
pytest
```

## Scope of this stage

Included: official API capability validation, live endpoint checks, and reproducible notes.

Deferred: dashboard, historical scoring, charts, brand system, price analysis, automation, PostgreSQL, and deployment.

## Railway OAuth service

`main.py` exposes the Mercado Libre OAuth flow used by the Railway `TopSellers`
service. Configure `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`, and
`MELI_REDIRECT_URI` as Railway variables; never commit their values.

Routes: `/health`, `/oauth/login`, `/oauth/callback`, `/oauth/refresh`,
`/api/v1/me`, `/api/v1/highlights/{category_id}`, and `/docs`.
Use `?enrich=true` on the highlights route to retrieve the documented detail
resource for each ranked `PRODUCT`, `ITEM`, or `USER_PRODUCT`. For catalog
products it also follows `buy_box_winner.item_id` and attempts the official item
and prices resources. `sold_quantity` is item-level cumulative sales, not total
sales for the catalog product or the quantity used to calculate the ranking.
When Mercado Libre exposes it, `product_sold_quantity` is reported separately.

## Historical snapshots

With `ENABLE_SNAPSHOT_SCHEDULER=true`, the service captures the categories in
`SNAPSHOT_CATEGORIES` every day at 06:00 America/Santiago. One snapshot per
category/day is enforced in PostgreSQL. History is available at
`GET /api/v1/categories/{category_id}/history`; protected manual execution uses
`POST /api/v1/snapshots/run?category_id=...` with the `X-Snapshot-Key` header.

Access and refresh tokens are encrypted by the application and persisted in
PostgreSQL. Configure `DATABASE_URL` and a stable, random
`TOKEN_ENCRYPTION_KEY`; changing the encryption key invalidates stored tokens.
Authenticated API calls automatically renew the access token five minutes before
expiration and persist Mercado Libre's rotated access/refresh token pair.
