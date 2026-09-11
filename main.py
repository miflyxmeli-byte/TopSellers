"""OAuth service for MiFly Market Intelligence on Railway."""

import base64
import asyncio
import hashlib
import logging
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path as FilePath
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from cryptography.fernet import Fernet
from fastapi import FastAPI, Header, HTTPException, Path, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, UniqueConstraint, create_engine, delete, select, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

CLIENT_ID = os.getenv("MELI_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MELI_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("MELI_REDIRECT_URI", "")
AUTH_DOMAIN = os.getenv("MELI_AUTH_DOMAIN", "https://auth.mercadolibre.cl")
API_BASE = "https://api.mercadolibre.com"
TOKEN_URL = f"{API_BASE}/oauth/token"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///:memory:")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "local-development-only")
SNAPSHOT_CATEGORIES = tuple(filter(None, os.getenv("SNAPSHOT_CATEGORIES", "MLC1055").split(",")))
ENABLE_SNAPSHOT_SCHEDULER = os.getenv("ENABLE_SNAPSHOT_SCHEDULER", "false").lower() == "true"
SNAPSHOT_API_KEY = os.getenv("SNAPSHOT_API_KEY", "")
SANTIAGO_TZ = ZoneInfo("America/Santiago")
PROJECT_DIR = FilePath(__file__).resolve().parent
CATEGORY_LABELS = {
    "MLC1055": "Celulares y Smartphones",
    "MLC82067": "Tablets",
    "MLC3697": "Audífonos",
    "MLC172568": "Parlantes portátiles",
    "MLC180993": "Aspiradoras robot",
    "MLC4337": "Aspiradoras (incluye verticales)",
    "MLC4660": "Cámaras de acción",
    "MLC175541": "Estabilizadores",
    "MLC179485": "Drones",
    "MLC4597": "Secadores de pelo",
    "MLC178457": "Alisadores de pelo",
}
LOGGER = logging.getLogger("topsellers")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


class Base(DeclarativeBase):
    pass


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class RankingSnapshot(Base):
    __tablename__ = "ranking_snapshots"
    __table_args__ = (UniqueConstraint("category_id", "snapshot_date",
                                      name="uq_snapshot_category_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[str] = mapped_column(String(32), index=True)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_count: Mapped[int] = mapped_column(Integer)


class RankingEntry(Base):
    __tablename__ = "ranking_entries"
    __table_args__ = (UniqueConstraint("snapshot_id", "ranking", name="uq_snapshot_ranking"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ranking_snapshots.id", ondelete="CASCADE"), index=True)
    ranking: Mapped[int] = mapped_column(Integer)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    product_id: Mapped[str | None] = mapped_column(String(64))
    item_id: Mapped[str | None] = mapped_column(String(64))
    user_product_id: Mapped[str | None] = mapped_column(String(64))
    detail_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str | None] = mapped_column(String)
    brand: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    screen_size: Mapped[str | None] = mapped_column(String)
    ram: Mapped[str | None] = mapped_column(String)
    suction_pa: Mapped[float | None] = mapped_column(Float)
    suction_source: Mapped[str | None] = mapped_column(String(32))
    price: Mapped[float | None] = mapped_column(Float)
    currency_id: Mapped[str | None] = mapped_column(String(8))
    sold_quantity: Mapped[int | None] = mapped_column(Integer)
    product_sold_quantity: Mapped[int | None] = mapped_column(Integer)
    available_quantity: Mapped[int | None] = mapped_column(Integer)
    image: Mapped[str | None] = mapped_column(String)
    buy_box_winner_available: Mapped[bool] = mapped_column(Boolean, default=False)


engine_options = {"pool_pre_ping": True}
if DATABASE_URL == "sqlite:///:memory:":
    engine_options.update(connect_args={"check_same_thread": False}, poolclass=StaticPool)
engine = create_engine(DATABASE_URL, **engine_options)
Base.metadata.create_all(engine)
if not DATABASE_URL.startswith("sqlite"):
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE oauth_tokens ALTER COLUMN user_id TYPE BIGINT"))
        connection.execute(text("ALTER TABLE ranking_entries ADD COLUMN IF NOT EXISTS screen_size VARCHAR"))
        connection.execute(text("ALTER TABLE ranking_entries ADD COLUMN IF NOT EXISTS ram VARCHAR"))
        connection.execute(text("ALTER TABLE ranking_entries ADD COLUMN IF NOT EXISTS user_product_id VARCHAR(64)"))
        connection.execute(text("ALTER TABLE ranking_entries ADD COLUMN IF NOT EXISTS detail_restricted BOOLEAN DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE ranking_entries ADD COLUMN IF NOT EXISTS suction_pa DOUBLE PRECISION"))
        connection.execute(text("ALTER TABLE ranking_entries ADD COLUMN IF NOT EXISTS suction_source VARCHAR(32)"))
_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(TOKEN_ENCRYPTION_KEY.encode()).digest())
_cipher = Fernet(_fernet_key)

app = FastAPI(
    title="MiFly Market Intelligence - TopSellers",
    description="Integración OAuth 2.0 con Mercado Libre",
    version="1.0.0",
)

# OAuth handshake data is intentionally short-lived. Tokens are persisted in PostgreSQL.
_store: dict = {}
_refresh_lock = asyncio.Lock()
REFRESH_MARGIN_SECONDS = 300
_scheduler: AsyncIOScheduler | None = None
_usd_rate_cache: dict = {}


def _encrypt(value: str | None) -> str | None:
    return _cipher.encrypt(value.encode()).decode() if value else None


def _decrypt(value: str | None) -> str | None:
    return _cipher.decrypt(value.encode()).decode() if value else None


def _vacuum_group(category_id: str, title: str | None, model: str | None) -> str | None:
    if category_id not in {"MLC180993", "MLC4337"}:
        return None
    description = f"{title or ''} {model or ''}".lower()
    if category_id == "MLC180993" or re.search(r"\brobot(?:ica|izada|izado|ic)?s?\b", description):
        return "robot"
    return "other"


def _pressure_to_pa(value: str | None) -> float | None:
    match = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(kpa|pa)\b",
                      value or "", re.IGNORECASE)
    if not match:
        return None
    number, unit = match.groups()
    if unit.lower() == "pa" and re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
        amount = float(re.sub(r"[.,]", "", number))
    else:
        amount = float(number.replace(",", "."))
    return amount * 1000 if unit.lower() == "kpa" else amount


def _extract_suction_pa(attributes: list[dict], title: str | None) -> tuple[float | None, str | None]:
    for attribute in attributes:
        identity = f"{attribute.get('id', '')} {attribute.get('name', '')}".lower()
        if not any(term in identity for term in ("suction", "succión", "succion")):
            continue
        structured = attribute.get("value_struct") or {}
        unit = str(structured.get("unit", "")).lower()
        if structured.get("number") is not None and unit in {"pa", "kpa"}:
            amount = float(structured["number"])
            return (amount * 1000 if unit == "kpa" else amount), "attribute"
        parsed = _pressure_to_pa(attribute.get("value_name"))
        if parsed is not None:
            return parsed, "attribute"
    parsed = _pressure_to_pa(title)
    return (parsed, "title") if parsed is not None else (None, None)


def _token_record() -> OAuthToken | None:
    with Session(engine) as session:
        return session.scalar(select(OAuthToken).where(OAuthToken.id == 1))


def _save_tokens(data: dict, previous_refresh: str | None = None) -> None:
    with Session(engine) as session:
        record = session.get(OAuthToken, 1) or OAuthToken(id=1, access_token="", expires_at=0)
        record.access_token = _encrypt(data["access_token"]) or ""
        record.refresh_token = _encrypt(data.get("refresh_token") or previous_refresh)
        if data.get("user_id") is not None:
            record.user_id = data["user_id"]
        record.expires_at = time.time() + int(data.get("expires_in", 0))
        session.add(record)
        session.commit()


def _require_config() -> None:
    missing = [name for name, value in (
        ("MELI_CLIENT_ID", CLIENT_ID),
        ("MELI_CLIENT_SECRET", CLIENT_SECRET),
        ("MELI_REDIRECT_URI", REDIRECT_URI),
    ) if not value]
    if missing:
        raise HTTPException(500, f"Variables de entorno faltantes: {', '.join(missing)}")


def _valid_token() -> Optional[str]:
    record = _token_record()
    if not record or time.time() >= record.expires_at:
        return None
    return _decrypt(record.access_token)


async def _refresh_persisted_token(force: bool = False) -> dict:
    """Refresh once under concurrency and persist the rotated token pair."""
    _require_config()
    async with _refresh_lock:
        record = _token_record()
        if not record:
            raise HTTPException(401, "Sin autorización persistida. Ejecuta /oauth/login")
        if not force and time.time() < record.expires_at - REFRESH_MARGIN_SECONDS:
            return {"access_token": _decrypt(record.access_token),
                    "expires_in": int(record.expires_at - time.time()), "refreshed": False}
        refresh = _decrypt(record.refresh_token)
        if not refresh:
            raise HTTPException(401, "No hay refresh token; ejecuta /oauth/login")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET, "refresh_token": refresh,
            })
        if response.status_code != 200:
            raise HTTPException(401, "No fue posible renovar la autorización; ejecuta /oauth/login")
        data = response.json()
        if not data.get("access_token"):
            raise HTTPException(502, "Mercado Libre no devolvió un access token")
        _save_tokens(data, previous_refresh=refresh)
        return {"access_token": data["access_token"], "expires_in": data.get("expires_in"),
                "refreshed": True}


async def _access_token() -> str:
    record = _token_record()
    if not record:
        raise HTTPException(401, "Sin autorización persistida. Ejecuta /oauth/login")
    if time.time() < record.expires_at - REFRESH_MARGIN_SECONDS:
        return _decrypt(record.access_token) or ""
    return (await _refresh_persisted_token())["access_token"]


@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse((PROJECT_DIR / "static" / "dashboard.html").read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {"status": "ok", "config_complete": bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI),
            "persistent_storage": not DATABASE_URL.startswith("sqlite"),
            "token_valid": _valid_token() is not None}


@app.get("/oauth/login")
def oauth_login():
    _require_config()
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    _store["state"] = state
    _store["code_verifier"] = code_verifier
    params = {"response_type": "code", "client_id": CLIENT_ID,
              "redirect_uri": REDIRECT_URI, "state": state,
              "code_challenge": code_challenge, "code_challenge_method": "S256"}
    return RedirectResponse(f"{AUTH_DOMAIN}/authorization?{urlencode(params)}")


@app.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(code: Optional[str] = Query(None), state: Optional[str] = Query(None),
                         error: Optional[str] = Query(None),
                         error_description: Optional[str] = Query(None)):
    _require_config()
    if error:
        raise HTTPException(400, f"Mercado Libre: {error} - {error_description or ''}")
    if not code:
        raise HTTPException(400, "Falta el parámetro 'code'")
    expected = _store.get("state")
    if not expected or not state or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "Parámetro 'state' inválido o expirado")
    _store.pop("state", None)
    code_verifier = _store.pop("code_verifier", None)
    if not code_verifier:
        raise HTTPException(400, "Verificador PKCE ausente o expirado; reinicia /oauth/login")
    payload = {"grant_type": "authorization_code", "client_id": CLIENT_ID,
               "client_secret": CLIENT_SECRET, "code": code, "redirect_uri": REDIRECT_URI,
               "code_verifier": code_verifier}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(TOKEN_URL, data=payload,
                                     headers={"accept": "application/json",
                                              "content-type": "application/x-www-form-urlencoded"})
    if response.status_code != 200:
        raise HTTPException(502, f"Falló el canje del código ({response.status_code})")
    data = response.json()
    if not data.get("access_token"):
        raise HTTPException(502, "Mercado Libre no devolvió un access token")
    _save_tokens(data)
    return HTMLResponse("<html><body><h2>Autorización completada</h2>"
                        f"<p>Usuario Mercado Libre: <b>{data.get('user_id')}</b></p>"
                        "<p>Ya puedes cerrar esta ventana.</p></body></html>")


@app.post("/oauth/refresh")
async def oauth_refresh():
    result = await _refresh_persisted_token(force=True)
    return {"status": "ok", "expires_in": result.get("expires_in")}


@app.get("/api/v1/me")
async def me():
    token = await _access_token()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{API_BASE}/users/me",
                                    headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise HTTPException(response.status_code, "Mercado Libre rechazó la consulta")
    data = response.json()
    return {key: data.get(key) for key in ("id", "nickname", "site_id", "country_id")}


@app.get("/api/v1/highlights/{category_id}")
async def highlights(category_id: str = Path(pattern=r"^MLC\d+$"), enrich: bool = False):
    """Return Mercado Libre's official current bestseller ranking for an MLC leaf category."""
    token = await _access_token()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{API_BASE}/highlights/MLC/category/{category_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise HTTPException(response.status_code, "Mercado Libre rechazó la consulta de highlights")
    payload = response.json()
    if not enrich:
        return payload

    rows = []
    async with httpx.AsyncClient(timeout=20) as client:
        for entry in payload.get("content", []):
            resource_type = entry.get("type")
            resource_id = entry.get("id")
            path = {
                "PRODUCT": f"/products/{resource_id}",
                "ITEM": f"/items/{resource_id}",
                "USER_PRODUCT": f"/user-products/{resource_id}",
            }.get(resource_type)
            detail = {}
            detail_status = None
            if path:
                detail_response = await client.get(
                    f"{API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}
                )
                detail_status = detail_response.status_code
                if detail_response.status_code == 200:
                    detail = detail_response.json()
            buy_box = detail.get("buy_box_winner") or {}
            winner_item_id = buy_box.get("item_id")
            if resource_type == "PRODUCT" and not winner_item_id:
                offers_response = await client.get(
                    f"{API_BASE}/products/{resource_id}/items",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if offers_response.status_code == 200:
                    offers = offers_response.json().get("results") or []
                    priced_offers = [offer for offer in offers if offer.get("price") is not None]
                    if priced_offers:
                        # Without a published buy-box winner, expose the lowest current offer.
                        buy_box = min(priced_offers, key=lambda offer: offer["price"])
                        winner_item_id = buy_box.get("item_id")
            item_detail: dict = {}
            item_status = None
            price_detail: dict = {}
            price_status = None
            if winner_item_id:
                item_response = await client.get(
                    f"{API_BASE}/items/{winner_item_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                item_status = item_response.status_code
                if item_response.status_code == 200:
                    item_detail = item_response.json()
                price_response = await client.get(
                    f"{API_BASE}/items/{winner_item_id}/prices",
                    headers={"Authorization": f"Bearer {token}"},
                )
                price_status = price_response.status_code
                if price_response.status_code == 200:
                    price_detail = price_response.json()
            prices = price_detail.get("prices") or []
            marketplace_prices = [
                price for price in prices
                if isinstance(price, dict)
                and (
                    "channel_marketplace" in (price.get("conditions") or {}).get("context_restrictions", [])
                    or not (price.get("conditions") or {}).get("context_restrictions")
                )
            ]
            promotional = next((p for p in marketplace_prices if p.get("type") == "promotion"), None)
            standard = next((p for p in marketplace_prices if p.get("type") == "standard"), None)
            effective_price = promotional or standard or {}
            attributes = {
                attribute.get("id"): attribute.get("value_name") or attribute.get("value_id")
                for attribute in detail.get("attributes", [])
                if isinstance(attribute, dict) and attribute.get("id")
            }
            pictures = detail.get("pictures") or []
            image = pictures[0].get("secure_url") or pictures[0].get("url") \
                if pictures and isinstance(pictures[0], dict) else detail.get("thumbnail")
            suction_pa, suction_source = _extract_suction_pa(
                detail.get("attributes") or [], detail.get("name") or detail.get("title")
            )
            rows.append({
                "ranking": entry.get("position"),
                "type": resource_type,
                "product_id": resource_id if resource_type == "PRODUCT" else detail.get("catalog_product_id"),
                "item_id": resource_id if resource_type == "ITEM" else winner_item_id or detail.get("item_id"),
                "user_product_id": resource_id if resource_type == "USER_PRODUCT" else None,
                "detail_restricted": resource_type == "USER_PRODUCT" and detail_status == 403,
                "title": detail.get("name") or detail.get("title"),
                "brand": attributes.get("BRAND"),
                "model": attributes.get("MODEL"),
                "screen_size": attributes.get("DISPLAY_SIZE") or attributes.get("SCREEN_SIZE"),
                "ram": attributes.get("RAM_MEMORY") or attributes.get("RAM"),
                "suction_pa": suction_pa,
                "suction_source": suction_source,
                "gtin": attributes.get("GTIN") or attributes.get("EAN"),
                "price": effective_price.get("amount", buy_box.get("price", item_detail.get("price"))),
                "regular_price": effective_price.get("regular_amount"),
                "currency_id": effective_price.get("currency_id", buy_box.get("currency_id", item_detail.get("currency_id"))),
                "available_quantity": buy_box.get("available_quantity", item_detail.get("available_quantity")),
                "sold_quantity": item_detail.get("sold_quantity"),
                "product_sold_quantity": detail.get("sold_quantity"),
                "buy_box_winner_available": bool(buy_box),
                "seller_id": buy_box.get("seller_id", detail.get("seller_id")),
                "permalink": item_detail.get("permalink") or detail.get("permalink"),
                "image": image,
                "detail_status": detail_status,
                "item_status": item_status,
                "price_status": price_status,
            })
    return {"query_data": payload.get("query_data"), "count": len(rows), "results": rows}


def _persist_snapshot(category_id: str, payload: dict) -> tuple[RankingSnapshot, bool]:
    captured_at = datetime.now(SANTIAGO_TZ)
    snapshot_date = captured_at.date().isoformat()
    with Session(engine) as session:
        existing = session.scalar(select(RankingSnapshot).where(
            RankingSnapshot.category_id == category_id,
            RankingSnapshot.snapshot_date == snapshot_date,
        ))
        if existing:
            session.execute(delete(RankingEntry).where(RankingEntry.snapshot_id == existing.id))
            rows = payload.get("results") or []
            existing.captured_at = captured_at
            existing.result_count = len(rows)
            snapshot = existing
            created = False
        else:
            rows = payload.get("results") or []
            snapshot = RankingSnapshot(category_id=category_id, snapshot_date=snapshot_date,
                                       captured_at=captured_at, result_count=len(rows))
            session.add(snapshot)
            session.flush()
            created = True
        for row in rows:
            session.add(RankingEntry(
                snapshot_id=snapshot.id, ranking=row.get("ranking"), resource_type=row.get("type"),
                product_id=row.get("product_id"), item_id=row.get("item_id"), title=row.get("title"),
                user_product_id=row.get("user_product_id"),
                detail_restricted=bool(row.get("detail_restricted")),
                brand=row.get("brand"), model=row.get("model"),
                screen_size=row.get("screen_size"), ram=row.get("ram"),
                suction_pa=row.get("suction_pa"), suction_source=row.get("suction_source"),
                price=row.get("price"),
                currency_id=row.get("currency_id"), sold_quantity=row.get("sold_quantity"),
                product_sold_quantity=row.get("product_sold_quantity"),
                available_quantity=row.get("available_quantity"), image=row.get("image"),
                buy_box_winner_available=bool(row.get("buy_box_winner_available")),
            ))
        session.commit()
        session.refresh(snapshot)
        return snapshot, created


async def capture_snapshot(category_id: str) -> dict:
    payload = await highlights(category_id=category_id, enrich=True)
    snapshot, created = _persist_snapshot(category_id, payload)
    return {"snapshot_id": snapshot.id, "category_id": category_id,
            "snapshot_date": snapshot.snapshot_date, "result_count": snapshot.result_count,
            "created": created}


@app.post("/api/v1/snapshots/run")
async def run_snapshot(category_id: str = Query(pattern=r"^MLC\d+$"),
                       x_snapshot_key: str | None = Header(None)):
    if not SNAPSHOT_API_KEY or not x_snapshot_key or not secrets.compare_digest(
        x_snapshot_key, SNAPSHOT_API_KEY
    ):
        raise HTTPException(401, "Snapshot API key inválida")
    return await capture_snapshot(category_id)


@app.get("/api/v1/categories/{category_id}/history")
def category_history(category_id: str = Path(pattern=r"^MLC\d+$"), limit: int = Query(30, ge=1, le=365)):
    with Session(engine) as session:
        snapshots = session.scalars(select(RankingSnapshot).where(
            RankingSnapshot.category_id == category_id
        ).order_by(RankingSnapshot.captured_at.desc()).limit(limit)).all()
        result = []
        for snapshot in snapshots:
            entries = session.scalars(select(RankingEntry).where(
                RankingEntry.snapshot_id == snapshot.id
            ).order_by(RankingEntry.ranking)).all()
            result.append({
                "snapshot_id": snapshot.id, "snapshot_date": snapshot.snapshot_date,
                "captured_at": snapshot.captured_at, "result_count": snapshot.result_count,
                "results": [{"ranking": entry.ranking, "type": entry.resource_type,
                        "product_id": entry.product_id, "item_id": entry.item_id,
                        "user_product_id": entry.user_product_id,
                        "detail_restricted": entry.detail_restricted,
                             "title": entry.title, "brand": entry.brand, "model": entry.model,
                             "price": entry.price, "currency_id": entry.currency_id,
                             "sold_quantity": entry.sold_quantity,
                             "product_sold_quantity": entry.product_sold_quantity,
                             "available_quantity": entry.available_quantity,
                             "image": entry.image} for entry in entries],
            })
        return {"category_id": category_id, "count": len(result), "snapshots": result}


async def _usd_clp_rate() -> dict | None:
    cached_at = _usd_rate_cache.get("cached_at", 0)
    if time.time() - cached_at < 21600 and _usd_rate_cache.get("rate"):
        return _usd_rate_cache
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get("https://mindicador.cl/api/dolar")
        response.raise_for_status()
        latest = response.json()["serie"][0]
        _usd_rate_cache.update(rate=float(latest["valor"]), date=latest.get("fecha"),
                               cached_at=time.time(), source="mindicador.cl")
        return _usd_rate_cache
    except Exception:
        LOGGER.warning("USD/CLP exchange rate unavailable", exc_info=True)
        return _usd_rate_cache or None


@app.get("/api/v1/dashboard")
async def dashboard_data():
    """Return the latest stored snapshot for each configured business category."""
    categories = []
    fx = await _usd_clp_rate()
    with Session(engine) as session:
        for category_id in SNAPSHOT_CATEGORIES:
            snapshots = session.scalars(select(RankingSnapshot).where(
                RankingSnapshot.category_id == category_id
            ).order_by(RankingSnapshot.captured_at.desc()).limit(2)).all()
            snapshot = snapshots[0] if snapshots else None
            previous_positions = {}
            if len(snapshots) > 1:
                previous_entries = session.scalars(select(RankingEntry).where(
                    RankingEntry.snapshot_id == snapshots[1].id
                )).all()
                previous_positions = {(entry.product_id or entry.item_id or entry.user_product_id): entry.ranking
                                      for entry in previous_entries
                                      if entry.product_id or entry.item_id or entry.user_product_id}
            rows = []
            if snapshot:
                entries = session.scalars(select(RankingEntry).where(
                    RankingEntry.snapshot_id == snapshot.id
                ).order_by(RankingEntry.ranking)).all()
                rows = [{
                    "ranking": entry.ranking,
                    "previous_position": previous_positions.get(entry.product_id or entry.item_id or entry.user_product_id),
                    "movement": (previous_positions.get(entry.product_id or entry.item_id or entry.user_product_id) - entry.ranking)
                    if previous_positions.get(entry.product_id or entry.item_id or entry.user_product_id) is not None else None,
                    "movement_status": "new" if previous_positions and
                    previous_positions.get(entry.product_id or entry.item_id or entry.user_product_id) is None else "pending",
                    "product_id": entry.product_id,
                    "item_id": entry.item_id, "user_product_id": entry.user_product_id,
                    "detail_restricted": entry.detail_restricted, "title": entry.title,
                    "brand": entry.brand, "model": entry.model, "price": entry.price,
                    "vacuum_group": _vacuum_group(category_id, entry.title, entry.model),
                    "source_category_id": category_id,
                    "source_category_name": CATEGORY_LABELS.get(category_id, category_id),
                    "screen_size": entry.screen_size, "ram": entry.ram,
                    "suction_pa": entry.suction_pa, "suction_source": entry.suction_source,
                    "currency_id": entry.currency_id,
                    "sold_quantity": entry.sold_quantity,
                    "product_sold_quantity": entry.product_sold_quantity,
                    "available_quantity": entry.available_quantity, "image": entry.image,
                    "price_usd": round(entry.price / fx["rate"], 2)
                    if entry.price is not None and entry.currency_id == "CLP" and fx else None,
                } for entry in entries]
            categories.append({
                "category_id": category_id,
                "name": CATEGORY_LABELS.get(category_id, category_id),
                "snapshot_date": snapshot.snapshot_date if snapshot else None,
                "previous_date": snapshots[1].snapshot_date if len(snapshots) > 1 else None,
                "captured_at": snapshot.captured_at if snapshot else None,
                "result_count": snapshot.result_count if snapshot else 0,
                "results": rows,
            })
        vacuum_sources = [category for category in categories
                          if category["category_id"] in {"MLC180993", "MLC4337"}]
        if vacuum_sources:
            categories.insert(5, {
                "category_id": "VACUUM_ALL", "name": "Aspiradoras · vista combinada",
                "snapshot_date": max((source["snapshot_date"] for source in vacuum_sources
                                      if source["snapshot_date"]), default=None),
                "captured_at": None,
                "previous_date": None,
                "result_count": sum(source["result_count"] for source in vacuum_sources),
                "results": [row for source in vacuum_sources for row in source["results"]],
            })
    return {"generated_at": datetime.now(SANTIAGO_TZ),
            "exchange_rate": {"clp_per_usd": fx["rate"], "date": fx.get("date"),
                              "source": fx.get("source")} if fx else None,
            "categories": categories}


@app.get("/api/v1/categories/{category_id}/movements")
def category_movements(category_id: str = Path(pattern=r"^MLC\d+$")):
    """Compare the latest two daily snapshots; positive movement means a rise."""
    with Session(engine) as session:
        snapshots = session.scalars(select(RankingSnapshot).where(
            RankingSnapshot.category_id == category_id
        ).order_by(RankingSnapshot.captured_at.desc()).limit(2)).all()
        if len(snapshots) < 2:
            return {"category_id": category_id, "comparable": False,
                    "message": "Se necesitan al menos dos snapshots diarios"}

        def entries(snapshot_id: int) -> dict[str, RankingEntry]:
            values = session.scalars(select(RankingEntry).where(
                RankingEntry.snapshot_id == snapshot_id
            )).all()
            return {(entry.product_id or entry.item_id or f"rank:{entry.ranking}"): entry
                    for entry in values}

        current, previous = snapshots[0], snapshots[1]
        current_entries, previous_entries = entries(current.id), entries(previous.id)
        rows = []
        for key, entry in current_entries.items():
            old = previous_entries.get(key)
            rows.append({
                "product_id": entry.product_id, "item_id": entry.item_id,
                "title": entry.title, "brand": entry.brand,
                "current_position": entry.ranking,
                "previous_position": old.ranking if old else None,
                "movement": old.ranking - entry.ranking if old else None,
                "status": "new" if old is None else (
                    "up" if old.ranking > entry.ranking else
                    "down" if old.ranking < entry.ranking else "unchanged"
                ),
            })
        dropped = [{"product_id": entry.product_id, "item_id": entry.item_id,
                    "title": entry.title, "previous_position": entry.ranking,
                    "status": "dropped"}
                   for key, entry in previous_entries.items() if key not in current_entries]
        return {"category_id": category_id, "comparable": True,
                "current_date": current.snapshot_date, "previous_date": previous.snapshot_date,
                "movements": sorted(rows, key=lambda row: row["current_position"]),
                "dropped": dropped}


async def _scheduled_snapshots() -> None:
    for category_id in SNAPSHOT_CATEGORIES:
        try:
            await capture_snapshot(category_id)
        except Exception:
            LOGGER.exception("Scheduled snapshot failed for %s", category_id)


@app.on_event("startup")
async def start_snapshot_scheduler() -> None:
    global _scheduler
    if not ENABLE_SNAPSHOT_SCHEDULER or _scheduler:
        return
    _scheduler = AsyncIOScheduler(timezone=SANTIAGO_TZ)
    _scheduler.add_job(_scheduled_snapshots, CronTrigger(hour=6, minute=0, timezone=SANTIAGO_TZ),
                       id="daily_mlc_snapshots", replace_existing=True, max_instances=1,
                       coalesce=True, misfire_grace_time=3600)
    _scheduler.start()


@app.on_event("shutdown")
async def stop_snapshot_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
