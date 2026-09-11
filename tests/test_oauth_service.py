import base64
import asyncio
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
        session.execute(delete(main.ExchangeRate))
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
    async def fake_rate():
        return {"rate": 900.0, "date": "2026-09-10", "source": "test"}
    monkeypatch.setattr(main, "_usd_clp_rate", fake_rate)
    main._persist_snapshot("MLC1055", {"results": [
        {"ranking": 1, "type": "PRODUCT", "product_id": "MLC1",
         "title": "Teléfono", "brand": "Marca", "price": 90000,
         "currency_id": "CLP"}
    ]})

    payload = client.get("/api/v1/dashboard").json()

    assert payload["categories"][0]["name"] == "Celulares y Smartphones"
    assert payload["categories"][0]["result_count"] == 1
    assert payload["categories"][0]["results"][0]["title"] == "Teléfono"
    assert payload["categories"][0]["results"][0]["price_usd"] == 100.0
    assert payload["exchange_rate"]["clp_per_usd"] == 900.0


def test_vacuum_description_separates_robots_from_other_vacuums():
    assert main._vacuum_group("MLC180993", "Aspiradora inteligente", None) == "robot"
    assert main._vacuum_group("MLC4337", "Aspiradora Robot con mopa", None) == "robot"
    assert main._vacuum_group("MLC4337", "Aspiradora vertical inalámbrica", None) == "other"
    assert main._vacuum_group("MLC82067", "Tablet Robot Edition", None) is None


def test_restricted_user_product_keeps_its_public_ranking_id(monkeypatch):
    client = configured_client(monkeypatch)
    monkeypatch.setattr(main, "SNAPSHOT_CATEGORIES", ("MLC180993",))
    async def fake_rate():
        return None
    monkeypatch.setattr(main, "_usd_clp_rate", fake_rate)
    main._persist_snapshot("MLC180993", {"results": [{
        "ranking": 1, "type": "USER_PRODUCT", "user_product_id": "MLCU55760760",
        "detail_restricted": True,
    }]})

    row = client.get("/api/v1/dashboard").json()["categories"][0]["results"][0]

    assert row["user_product_id"] == "MLCU55760760"
    assert row["detail_restricted"] is True


def test_suction_pressure_is_normalized_to_pascals():
    assert main._pressure_to_pa("Potencia de succión 7000 Pa") == 7000
    assert main._pressure_to_pa("Presión de succión: 5,5 kPa") == 5500
    assert main._pressure_to_pa("Succión máxima 15.000pa") == 15000
    assert main._pressure_to_pa("Potencia 120 W") is None

    value, source = main._extract_suction_pa([{
        "id": "SUCTION_PRESSURE", "name": "Presión de succión",
        "value_struct": {"number": 6, "unit": "kPa"},
    }], "Modelo sin presión en el título")
    assert value == 6000
    assert source == "attribute"


def test_product_history_summarizes_positions(monkeypatch):
    client = configured_client(monkeypatch)
    now = datetime.now(main.SANTIAGO_TZ)
    with main.Session(main.engine) as session:
        first = main.RankingSnapshot(category_id="MLC1055", snapshot_date="2026-09-10",
                                     captured_at=now - timedelta(days=1), result_count=1)
        second = main.RankingSnapshot(category_id="MLC1055", snapshot_date="2026-09-11",
                                      captured_at=now, result_count=1)
        session.add_all([first, second])
        session.flush()
        session.add_all([
            main.RankingEntry(snapshot_id=first.id, ranking=5, resource_type="PRODUCT",
                              product_id="MLC123", title="Producto"),
            main.RankingEntry(snapshot_id=second.id, ranking=3, resource_type="PRODUCT",
                              product_id="MLC123", title="Producto", price=99990,
                              currency_id="CLP"),
        ])
        session.commit()

    payload = client.get("/api/v1/products/MLC123/history?category_id=MLC1055").json()

    assert payload["days_observed"] == 2
    assert payload["best_position"] == 3
    assert payload["current_position"] == 3
    assert [row["ranking"] for row in payload["observations"]] == [5, 3]


def test_product_history_returns_404_for_unknown_resource(monkeypatch):
    client = configured_client(monkeypatch)
    assert client.get("/api/v1/products/MLC999/history").status_code == 404


def test_exchange_rate_uses_930_when_all_sources_and_cache_fail(monkeypatch):
    configured_client(monkeypatch)
    main._usd_rate_cache.clear()

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, *args, **kwargs):
            raise RuntimeError("source unavailable")

    monkeypatch.setattr(main.httpx, "AsyncClient", FailingClient)
    rate = asyncio.run(main._usd_clp_rate())

    assert rate["rate"] == 930
    assert rate["fallback"] is True
    assert rate["source"] == "referencia MiFly"
