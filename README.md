# MiFly Market Intelligence - TopSellers

Servicio de autorizacion OAuth 2.0 contra la API de Mercado Libre.
JHY International Ltda. / MiFly Chile.

## Stack

- FastAPI + Uvicorn + httpx (Python)
- Desplegado en Railway (Nixpacks)

## Endpoints

| Metodo | Ruta | Descripcion |
|---|---|---|
| GET | `/` | Estado del servicio |
| GET | `/health` | Healthcheck |
| GET | `/oauth/login` | Inicia el flujo, redirige al consentimiento de MELI |
| GET | `/oauth/callback` | Callback registrado en MELI, canjea el code por token |
| POST | `/oauth/refresh` | Renueva el access token |
| GET | `/api/v1/me` | Datos del usuario autorizado |
| GET | `/docs` | Documentacion interactiva (Swagger) |

## Variables de entorno

Se configuran en Railway, en la pestana Variables del servicio.
NUNCA se commitean: este repositorio es publico.

| Variable | Obligatoria | Descripcion |
|---|---|---|
| `MELI_CLIENT_ID` | si | App ID de la aplicacion en Mercado Libre |
| `MELI_CLIENT_SECRET` | si | Secret Key de la aplicacion |
| `MELI_REDIRECT_URI` | si | URL exacta registrada en MELI |
| `MELI_AUTH_DOMAIN` | no | Default `https://auth.mercadolibre.cl` (Chile) |

`PORT` la inyecta Railway automaticamente.

## Configuracion en Mercado Libre

En el panel de desarrolladores, la Redirect URI debe ser exactamente:

```
https://TU-DOMINIO.up.railway.app/oauth/callback
```

Debe coincidir caracter por caracter con `MELI_REDIRECT_URI`. Si no
coincide, MELI responde `invalid_grant`.

## Flujo

1. Abrir `/oauth/login` en el navegador.
2. Autorizar con un usuario **administrador** de la cuenta MELI.
   Si el usuario es operador o colaborador, el grant sale invalido.
3. MELI redirige a `/oauth/callback` con el `code`.
4. El servicio canjea el code por `access_token` y `refresh_token`.

El scope `offline_access` es el que habilita el refresh token.

## Limitacion actual

Los tokens se guardan en memoria y se pierden en cada redeploy o
reinicio del contenedor. Para produccion hay que persistirlos en
Postgres o Redis (Railway los ofrece como servicios del proyecto).

## Ejecucion local

```bash
pip install -r requirements.txt
export MELI_CLIENT_ID=... MELI_CLIENT_SECRET=... MELI_REDIRECT_URI=...
uvicorn main:app --reload
```
