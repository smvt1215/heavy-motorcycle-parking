"""API acceptance with both city adapters, real sample provenance and DEV identity."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.db import get_session
from app.ingestion.contracts import FeedSnapshot
from app.ingestion.new_taipei import NewTaipeiParkingAdapter
from app.ingestion.new_taipei_roadside import NewTaipeiRoadsideAdapter
from app.ingestion.pipeline import ParkingIngestionPipeline
from app.ingestion.policies import NEW_TAIPEI_PUBLIC_CAR
from app.ingestion.taipei import TaipeiParkingAdapter
from app.main import create_app
from app.models import ParkingLot

NOW = datetime(2026, 10, 10, 2, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"
BASE = {"zone_id", "name", "space_type", "capacity", "compatibility", "rate_summary", "availability"}


@pytest.fixture(scope="module")
def migrated():
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")


@pytest.fixture
async def accepted(migrated):
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        outer = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as db:
            for adapter, city, static, realtime in [
                (TaipeiParkingAdapter(), "taipei", "alldesc_sample.json", "allavailable_sample.json"),
                (NewTaipeiParkingAdapter(), "new_taipei", "static_sample.json", "realtime_sample.json"),
                (NewTaipeiRoadsideAdapter(), "new_taipei", "roadside_sample.json", "roadside_sample.json"),
            ]:
                for kind, filename in [("static", static), ("realtime", realtime)]:
                    payload = json.loads((FIXTURES / city / filename).read_text())
                    result = await ParkingIngestionPipeline(db, adapter).ingest(
                        FeedSnapshot(kind, payload, NOW, adapter.source_updated_at(payload))
                    )
                    # Taipei fixtures intentionally contain invalid counts/coordinates.
                    expected_failures = 3 if city == "taipei" else 0
                    assert result.failed == expected_failures and result.normalized > 0
            async with db.begin():
                lots = {lot.external_id: lot.id for lot in (await db.scalars(select(ParkingLot))).all()}
            app = create_app()
            clock = [NOW]
            app.state.clock = lambda: clock[0]

            async def override():
                yield db

            app.dependency_overrides[get_session] = override
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                yield client, lots, clock
        await outer.rollback()
    await engine.dispose()


async def get(client, path, **params):
    response = await client.get(f"/api/v1/parking/{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def query(client, parking_id, **extra):
    detail = await get(client, parking_id, vehicle="LARGE_HEAVY")
    return {**detail["location"], "radius": 5000, "vehicle": "LARGE_HEAVY", **extra}


@pytest.mark.parametrize("vehicle", ["NORMAL_HEAVY", "LARGE_HEAVY"])
async def test_common_zone_contract_and_exact_instant_across_city_selected_endpoints(accepted, vehicle):
    client, lots, _ = accepted
    for external_id in ("010152", "TPE0003"):
        results = [
            await get(client, f"{lots[external_id]}{suffix}", vehicle=vehicle, at=NOW.isoformat())
            for suffix in ("", "/rates", "/realtime")
        ]
        assert all(r["vehicle"] == vehicle and r["evaluation_at"] == "2026-10-10T02:00:00Z" for r in results)
        zones = [{z["zone_id"]: {k: z[k] for k in BASE} for z in r["zones"]} for r in results]
        assert zones[0] == zones[1] == zones[2]
    detail = results[0]  # Taipei entrance provenance retains its independent static source.
    assert detail["entrances"][0]["provenance"]["source_name"] == "臺北市停車場資訊 V2"
    assert detail["entrances"][0]["provenance"]["source_url"].endswith("TCMSV_alldesc.json")
    ntpc = await get(client, lots["010152"], vehicle=vehicle)
    car = next(z for z in ntpc["zones"] if z["space_type"] == "CAR_SHARED")
    if vehicle == "LARGE_HEAVY":
        assert car["compatibility"]["provenance"]["source_url"] == NEW_TAIPEI_PUBLIC_CAR.url
        assert car["compatibility"]["provenance"]["source_name"] == NEW_TAIPEI_PUBLIC_CAR.name
    assert car["availability"]["provenance"]["source_url"].endswith("E09B35A5-A738-48CC-B0F5-570B67AD9C78/json")
    assert car["rate_summary"] is None


async def test_nearby_unknown_shared_availability_and_price_filters_do_not_cross_zones(accepted):
    client, lots, _ = accepted
    params = await query(client, lots["010152"])
    confirmed = await get(client, "nearby", **params)
    included = await get(client, "nearby", **{**params, "include_unknown": True})
    assert lots["010152"] in {i["id"] for i in confirmed["items"]}
    assert lots["010056"] not in {i["id"] for i in confirmed["items"]}
    assert lots["010056"] in {i["id"] for i in included["items"]}
    assert all(i["compatibility"]["status"] == "ALLOWED" for i in confirmed["items"])
    available = await get(client, "nearby", **{**params, "available_only": True, "include_unknown": True})
    item = next(i for i in available["items"] if i["id"] == lots["010152"])
    assert len(item["zones"]) == 1 and item["zones"][0]["space_type"] == "CAR_SHARED"
    assert item["zones"][0]["availability"]["available"] == 44
    assert item["availability_summary"]["available"] is item["availability_summary"]["total"] is None
    normal = await get(client, "nearby", **{**params, "vehicle": "NORMAL_HEAVY", "available_only": True})
    assert lots["010152"] not in {i["id"] for i in normal["items"]}
    price = await get(client, "nearby", **{**params, "hourly_rate_max_twd": 50})
    assert lots["010152"] not in {i["id"] for i in price["items"]}


async def test_new_class_cursor_keeps_time_and_rejects_changed_vehicle_and_legacy_cursor(accepted):
    client, lots, clock = accepted
    params = await query(client, lots["010152"], vehicle="NORMAL_HEAVY", include_unknown=True, limit=1)
    first = await get(client, "nearby", **params)
    assert first["sort_version"] == 1
    cursor = first["page"]["next_cursor"]
    assert cursor
    clock[0] += timedelta(seconds=1)
    second = await get(client, "nearby", **{**params, "cursor": cursor})
    assert second["evaluation_at"] == first["evaluation_at"]
    assert second["items"][0]["id"] != first["items"][0]["id"]
    for changed in [{**params, "vehicle": "LARGE_HEAVY", "cursor": cursor}, {**params, "cursor": "old-yellow-cursor"}]:
        response = await client.get("/api/v1/parking/nearby", params=changed)
        assert response.status_code == 400 and response.json()["error"]["code"]


async def test_dev_login_preferences_favorites_reports_keep_explicit_query_vehicle(accepted):
    client, lots, _ = accepted
    login = await client.post("/api/v1/auth/dev-session", json={"subject": "integration-acceptance"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    changed = await client.put("/api/v1/me/vehicle", json={"vehicle": "NORMAL_HEAVY"}, headers=headers)
    assert changed.status_code == 200 and changed.json()["preferred_vehicle"] == "NORMAL_HEAVY"
    parking_id = lots["010152"]
    for suffix in ("", "/rates", "/realtime"):
        assert (await client.get(f"/api/v1/parking/{parking_id}{suffix}", headers=headers)).status_code == 422
        response = await client.get(
            f"/api/v1/parking/{parking_id}{suffix}", params={"vehicle": "LARGE_HEAVY"}, headers=headers
        )
        assert response.status_code == 200 and response.json()["vehicle"] == "LARGE_HEAVY"
    saved = await client.post("/api/v1/favorites", json={"parking_id": parking_id}, headers=headers)
    assert saved.status_code == 201
    assert (await client.get("/api/v1/favorites", headers=headers)).json()["items"][0]["parking_id"] == parking_id
    reported = await client.post(
        "/api/v1/reports", json={"parking_id": parking_id, "report_type": "PARKING_NOT_ALLOWED"}, headers=headers
    )
    assert reported.status_code == 201 and reported.json()["status"] == "PENDING"
    detail = await get(client, parking_id, vehicle="LARGE_HEAVY")
    assert next(z for z in detail["zones"] if z["space_type"] == "CAR_SHARED")["compatibility"]["status"] == "ALLOWED"
    assert (await client.put("/api/v1/me/vehicle", json={"vehicle": "RED"}, headers=headers)).status_code == 422
