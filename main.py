"""
MiFly Market Intelligence - TopSellers
JHY International Ltda. / MiFly Chile

Servicio de autorizacion OAuth 2.0 contra la API de Mercado Libre.
Recibe el callback de MELI, intercambia el authorization code por un
access token y expone endpoints de consulta autenticados.

Las credenciales NUNCA van en el codigo: se leen de variables de
entorno configuradas en Railway.
"""

import os
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

# --- Configuracion (variables de entorno en Railway) ---
CLIENT_ID = os.getenv("MELI_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MELI_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("MELI_REDIRECT_URI", "")
AUTH_DOMAIN = os.getenv("MELI_AUTH_DOMAIN", "https://auth.mercadolibre.cl")

API_BASE = "https://api.mercadolibre.com"
TOKEN_URL = f"{API_BASE}/oauth/token"

app = FastAPI(
    title="MiFly Market Intelligence - TopSellers",
    description="Integracion OAuth 2.0 con Mercado Libre",
    version="1.0.0",
)

# Almacen en memoria. Se pierde en cada redeploy: para produccion,
# persistir en Postgres o Redis (ver README).
_store: dict = {}


def _require_config():
    faltantes = [
        nombre
        for nombre, valor in (
            ("MELI_CLIENT_ID", CLIENT_ID),
            ("MELI_CLIENT_SECRET", CLIENT_SECRET),
            ("MELI_REDIRECT_URI", REDIRECT_URI),
        )
        if not valor
    ]
    if faltantes:
        raise HTTPException(
            status_code=500,
            detail=f"Variables de entorno faltantes en Railway: {', '.join(faltantes)}",
        )


def _token_vigente() -> Optional[str]:
    token = _store.get("access_token")
    if not token:
        return None
    if time.time() >= _store.get("expires_at", 0):
        return None
    return token


@app.get("/")
def root():
    return {
        "servicio": "MiFly Market Intelligence - TopSellers",
        "version": app.version,
        "autorizado": _token_vigente() is not None,
        "redirect_uri_configurada": REDIRECT_URI or None,
        "endpoints": ["/health", "/oauth/login", "/oauth/callback", "/api/v1/me", "/docs"],
    }


@app.get("/health")
def health():
    """Healthcheck de Railway. No expone secretos."""
    return {
        "status": "ok",
        "config_completa": bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI),
        "token_vigente": _token_vigente() is not None,
    }


@app.get("/oauth/login")
def oauth_login():
    """Inicia el flujo: redirige al consentimiento de Mercado Libre."""
    _require_config()
    state = secrets.token_urlsafe(24)
    _store["state"] = state
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "scope": "offline_access read write",
    }
    return RedirectResponse(f"{AUTH_DOMAIN}/authorization?{urlencode(params)}")


@app.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """Callback registrado en Mercado Libre. Canjea el code por un token."""
    _require_config()

    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Mercado Libre devolvio un error: {error} - {error_description or ''}",
        )
    if not code:
        raise HTTPException(status_code=400, detail="Falta el parametro 'code'")

    esperado = _store.get("state")
    if esperado and state != esperado:
        raise HTTPException(status_code=400, detail="Parametro 'state' invalido")
    _store.pop("state", None)

    # Parametros en el BODY, no en la query (recomendacion oficial de MELI)
    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data=payload,
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
            },
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Fallo el canje del code ({resp.status_code}): {resp.text[:300]}",
        )

    data = resp.json()
    _store["access_token"] = data.get("access_token")
    _store["refresh_token"] = data.get("refresh_token")
    _store["user_id"] = data.get("user_id")
    _store["expires_at"] = time.time() + int(data.get("expires_in", 0))

    # El token nunca se devuelve al navegador.
    return HTMLResponse(
        "<html><body style='font-family:system-ui;padding:40px'>"
        "<h2>Autorizacion completada</h2>"
        f"<p>Usuario Mercado Libre: <b>{_store.get('user_id')}</b></p>"
        "<p>El access token quedo almacenado en el servicio. "
        "Ya puedes cerrar esta ventana.</p>"
        "</body></html>"
    )


@app.post("/oauth/refresh")
async def oauth_refresh():
    """Renueva el access token usando el refresh token."""
    _require_config()
    refresh = _store.get("refresh_token")
    if not refresh:
        raise HTTPException(status_code=400, detail="No hay refresh token almacenado")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": refresh,
            },
            headers={"accept": "application/json"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Fallo el refresh: {resp.text[:300]}")

    data = resp.json()
    _store["access_token"] = data.get("access_token")
    _store["refresh_token"] = data.get("refresh_token", refresh)
    _store["expires_at"] = time.time() + int(data.get("expires_in", 0))
    return {"status": "ok", "expires_in": data.get("expires_in")}


@app.get("/api/v1/me")
async def me():
    """Datos del usuario autorizado. Verifica que el token sirve."""
    token = _token_vigente()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Sin token vigente. Ejecuta primero /oauth/login",
        )

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{API_BASE}/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:300])

    d = resp.json()
    return {
        "id": d.get("id"),
        "nickname": d.get("nickname"),
        "site_id": d.get("site_id"),
        "pais": d.get("country_id"),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
