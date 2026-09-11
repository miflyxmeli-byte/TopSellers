"""OAuth service for MiFly Market Intelligence on Railway."""

import base64
import asyncio
import hashlib
import os
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import BigInteger, Float, Integer, String, create_engine, select, text
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


engine_options = {"pool_pre_ping": True}
if DATABASE_URL == "sqlite:///:memory:":
    engine_options.update(connect_args={"check_same_thread": False}, poolclass=StaticPool)
engine = create_engine(DATABASE_URL, **engine_options)
Base.metadata.create_all(engine)
if not DATABASE_URL.startswith("sqlite"):
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE oauth_tokens ALTER COLUMN user_id TYPE BIGINT"))
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


def _encrypt(value: str | None) -> str | None:
    return _cipher.encrypt(value.encode()).decode() if value else None


def _decrypt(value: str | None) -> str | None:
    return _cipher.decrypt(value.encode()).decode() if value else None


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


@app.get("/")
def root():
    return {
        "service": "MiFly Market Intelligence - TopSellers",
        "version": app.version,
        "authorized": _valid_token() is not None,
        "redirect_uri_configured": REDIRECT_URI or None,
        "endpoints": ["/health", "/oauth/login", "/oauth/callback", "/oauth/refresh",
                      "/api/v1/me", "/api/v1/highlights/{category_id}", "/docs"],
    }


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
            rows.append({
                "ranking": entry.get("position"),
                "type": resource_type,
                "product_id": resource_id if resource_type == "PRODUCT" else detail.get("catalog_product_id"),
                "item_id": resource_id if resource_type == "ITEM" else winner_item_id or detail.get("item_id"),
                "title": detail.get("name") or detail.get("title"),
                "brand": attributes.get("BRAND"),
                "model": attributes.get("MODEL"),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
