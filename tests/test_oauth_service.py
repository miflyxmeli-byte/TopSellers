import base64
import hashlib
from datetime import datetime, timedelta
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
        session.execute(delete(main.RankingEntry))
        session.execute(delete(main.RankingSnapshot))
        session.execute(delete(main.OAuthToken))
        session.commit()
    return TestClient(main.app)


def test_health_reports_complete_configuration(monkeypatch):
    client = configured_client(monkeypatch)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "config_complete": True,
                               "persistent_storage": False, "token_valid": False}


def test_root_serves_dashboard(monkeypatch):
    client = configured_client(monkeypatch)
    response = client.get("/")
    assert response.status_code == 200
    assert "MiFly Market Intelligence" in response.text
    assert "Top Sellers" in response.text


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


def test_snapshot_is_persisted_once_per_category_and_day(monkeypatch):
    client = configured_client(monkeypatch)
    payload = {"results": [{"ranking": 1, "type": "PRODUCT", "product_id": "MLC1",
                            "title": "Example", "brand": "Brand"}]}
    first, first_created = main._persist_snapshot("MLC1055", payload)
    second, second_created = main._persist_snapshot("MLC1055", payload)
    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    history = client.get("/api/v1/categories/MLC1055/history").json()
    assert history["count"] == 1
    assert history["snapshots"][0]["results"][0]["product_id"] == "MLC1"


def test_movements_requires_two_daily_snapshots(monkeypatch):
    client = configured_client(monkeypatch)
    main._persist_snapshot("MLC1055", {"results": [
        {"ranking": 1, "type": "PRODUCT", "product_id": "MLC1", "title": "Example"}
    ]})

    response = client.get("/api/v1/categories/MLC1055/movements")

    assert response.status_code == 200
    assert response.json()["comparable"] is False


def test_movements_reports_up_new_and_dropped_products(monkeypatch):
    client = configured_client(monkeypatch)
    now = datetime.now(main.SANTIAGO_TZ)
    with main.Session(main.engine) as session:
        previous = main.RankingSnapshot(
            category_id="MLC1055", snapshot_date="2026-09-09",
            captured_at=now - timedelta(days=1), result_count=2,
        )
        current = main.RankingSnapshot(
            category_id="MLC1055", snapshot_date="2026-09-10",
            captured_at=now, result_count=2,
        )
        session.add_all([previous, current])
        session.flush()
        session.add_all([
            main.RankingEntry(snapshot_id=previous.id, ranking=2, resource_type="PRODUCT",
                              product_id="MLC-UP", title="Sube"),
            main.RankingEntry(snapshot_id=previous.id, ranking=1, resource_type="PRODUCT",
                              product_id="MLC-OUT", title="Sale"),
            main.RankingEntry(snapshot_id=current.id, ranking=1, resource_type="PRODUCT",
                              product_id="MLC-UP", title="Sube"),
            main.RankingEntry(snapshot_id=current.id, ranking=2, resource_type="PRODUCT",
                              product_id="MLC-NEW", title="Nuevo"),
        ])
        session.commit()

    payload = client.get("/api/v1/categories/MLC1055/movements").json()

    assert payload["comparable"] is True
    assert payload["movements"][0]["status"] == "up"
    assert payload["movements"][0]["movement"] == 1
    assert payload["movements"][1]["status"] == "new"
    assert payload["dropped"][0]["product_id"] == "MLC-OUT"


def test_dashboard_returns_latest_snapshot(monkeypatch):
    client = configured_client(monkeypatch)
    monkeypatch.setattr(main, "SNAPSHOT_CATEGORIES", ("MLC1055",))
    main._persist_snapshot("MLC1055", {"results": [
        {"ranking": 1, "type": "PRODUCT", "product_id": "MLC1",
         "title": "Teléfono", "brand": "Marca"}
    ]})

    payload = client.get("/api/v1/dashboard").json()

    assert payload["categories"][0]["name"] == "Celulares y Smartphones"
    assert payload["categories"][0]["result_count"] == 1
    assert payload["categories"][0]["results"][0]["title"] == "Teléfono"
