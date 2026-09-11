import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import delete

import main


def configured_client(monkeypatch):
    monkeypatch.setattr(main, "CLIENT_ID", "test-client")
    monkeypatch.setattr(main, "CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(main, "REDIRECT_URI", "https://example.test/oauth/callback")
    main._store.clear()
    with main.Session(main.engine) as session:
        session.execute(delete(main.OAuthToken))
        session.commit()
    return TestClient(main.app)


def test_health_reports_complete_configuration(monkeypatch):
    client = configured_client(monkeypatch)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "config_complete": True,
                               "persistent_storage": False, "token_valid": False}


def test_login_redirect_contains_state_without_secret(monkeypatch):
    client = configured_client(monkeypatch)
    response = client.get("/oauth/login", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert "client_id=test-client" in location
    assert "state=" in location
    assert "scope=" not in location
    assert "test-secret" not in location
    assert main._store.get("state")
    verifier = main._store.get("code_verifier")
    assert verifier
    query = parse_qs(urlparse(location).query)
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert query["code_challenge"] == [expected_challenge]
    assert query["code_challenge_method"] == ["S256"]


def test_callback_rejects_missing_or_wrong_state(monkeypatch):
    client = configured_client(monkeypatch)
    main._store["state"] = "expected"
    response = client.get("/oauth/callback?code=temporary&state=wrong")
    assert response.status_code == 400
    assert response.json()["detail"] == "Parámetro 'state' inválido o expirado"


def test_me_requires_valid_token(monkeypatch):
    client = configured_client(monkeypatch)
    response = client.get("/api/v1/me")
    assert response.status_code == 401


def test_highlights_requires_token_and_chilean_category(monkeypatch):
    client = configured_client(monkeypatch)
    assert client.get("/api/v1/highlights/MLC1055").status_code == 401
    assert client.get("/api/v1/highlights/MLA1055").status_code == 422


def test_tokens_are_encrypted_and_survive_memory_clear(monkeypatch):
    configured_client(monkeypatch)
    main._save_tokens({"access_token": "access-secret", "refresh_token": "refresh-secret",
                       "user_id": 3_247_182_976, "expires_in": 3600})
    main._store.clear()
    record = main._token_record()
    assert record.access_token != "access-secret"
    assert record.refresh_token != "refresh-secret"
    assert record.user_id == 3_247_182_976
    assert main._valid_token() == "access-secret"


def test_access_token_refreshes_inside_margin(monkeypatch):
    configured_client(monkeypatch)
    main._save_tokens({"access_token": "expiring", "refresh_token": "refresh-secret",
                       "user_id": 3_247_182_976, "expires_in": 60})
    called = []

    async def fake_refresh(force=False):
        called.append(force)
        return {"access_token": "renewed", "expires_in": 21600, "refreshed": True}

    monkeypatch.setattr(main, "_refresh_persisted_token", fake_refresh)
    import asyncio
    assert asyncio.run(main._access_token()) == "renewed"
    assert called == [False]
