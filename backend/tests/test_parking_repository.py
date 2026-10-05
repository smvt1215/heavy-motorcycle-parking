"""Live PostGIS and public API integration without touching non-test databases."""

import json
from datetime import timedelta
from pathlib import Path
from time import perf_counter

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import create_app
from app.models import (
    DataSource,
    ParkingEntrance,
    ParkingLot,
    ParkingRate,
    ParkingRateRule,
    ParkingRateSource,
    ParkingRealtime,
    ParkingRule,
    ParkingZone,
)
from app.repositories.parking import ParkingRepository
from tests.test_parking_api import BASE_FIELDS, NOW


@pytest.fixture(scope="module")
def migrated():
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")


@pytest.fixture
async def live(migrated):
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(bind=connection) as session:
            sources = [
                DataSource(code=f"m3-{index}", name=f"Source {index}", source_type=kind, freshness_seconds=60)
                for index, kind in enumerate(("GOVERNMENT", "OPERATOR", "MANUAL", "COMMUNITY"))
            ]
            session.add_all(sources)
            await session.flush()
            lots = [
                ParkingLot(name=f"M3 lot {index}", location=f"SRID=4326;POINT({lng} {lat})")
                for index, (lat, lng) in enumerate(((25.03, 121.56), (25.031, 121.56), (24.0, 120.0)))
            ]
            session.add_all(lots)
            await session.flush()
            zones = [
                ParkingZone(parking_id=lots[0].id, name=kind, space_type=kind, capacity=20)
                for kind in ("HEAVY_ONLY", "MOTO_SHARED", "CAR_SHARED", "LIGHT_MOTO_ONLY")
            ]
            unknown = ParkingZone(parking_id=lots[1].id, name="Unverified", space_type="HEAVY_ONLY")
            session.add_all([*zones, unknown])
            await session.flush()
            session.add_all(
                [
                    ParkingRule(
                        parking_id=lots[0].id,
                        zone_id=zone.id,
                        source_id=sources[0].id,
                        rule_kind="BASELINE",
                        authority_priority=10,
                        red_plate_allowed=permission,
                        yellow_plate_allowed=permission,
                        fetched_at=NOW,
                        confidence=0.5,
                    )
                    for zone, permission in zip(zones, (True, None, False, True), strict=True)
                ]
            )
            session.add(
                ParkingRule(
                    parking_id=lots[1].id,
                    zone_id=unknown.id,
                    source_id=sources[0].id,
                    rule_kind="BASELINE",
                    authority_priority=10,
                    red_plate_allowed=None,
                )
            )
            rate = ParkingRate(
                zone_id=zones[0].id,
                vehicle_type="RED",
                rate_type="HOURLY",
                parse_status="PARSED",
                base_amount=20,
                unit_minutes=60,
                daily_max_amount=100,
                source_id=sources[1].id,
                fetched_at=NOW,
            )
            session.add(rate)
            await session.flush()
            session.add_all(
                [
                    ParkingRateRule(rate_id=rate.id, day_type="WEEKDAY", amount=20, unit_minutes=60),
                    ParkingRateRule(rate_id=rate.id, day_type="WEEKEND", amount=40, unit_minutes=60),
                    ParkingRateSource(rate_id=rate.id, source_id=sources[3].id, fetched_at=NOW),
                    ParkingRealtime(
                        zone_id=zones[0].id,
                        source_id=sources[2].id,
                        status="AVAILABLE",
                        available_spaces=3,
                        total_spaces=20,
                        fetched_at=NOW - timedelta(seconds=5),
                    ),
                    ParkingRealtime(
                        zone_id=zones[0].id,
                        source_id=sources[2].id,
                        status="FULL",
                        available_spaces=0,
                        total_spaces=20,
                        fetched_at=NOW - timedelta(hours=1),
                    ),
                    ParkingRealtime(
                        zone_id=zones[1].id,
                        source_id=sources[2].id,
                        status="AVAILABLE",
                        available_spaces=999,
                        total_spaces=999,
                        fetched_at=NOW,
                    ),
                    ParkingEntrance(
                        parking_id=lots[0].id,
                        name="Entrance",
                        source_id=sources[3].id,
                        location="SRID=4326;POINT(121.561 25.031)",
                        heavy_motorcycle_access=None,
                    ),
                ]
            )
            await session.flush()
            app = create_app()
            app.state.clock = lambda: NOW

            async def override_session():
                yield session

            app.dependency_overrides[get_session] = override_session
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                yield client, session, lots, zones, sources
        await transaction.rollback()
    await engine.dispose()


async def test_live_gis_filters_radius_rounds_distance_and_batched_loading(live):
    _, session, lots, _, _ = live
    calls = []
    engine = session.bind.engine.sync_engine

    def track(connection, cursor, statement, parameters, context, executemany):
        calls.append(statement)

    event.listen(engine, "before_cursor_execute", track)
    try:
        results = await ParkingRepository(session).nearby(25.03, 121.56, 500)
    finally:
        event.remove(engine, "before_cursor_execute", track)
    assert [lot.facts.parking_id for lot in results] == [lot.id for lot in lots[:2]]
    assert results[0].distance_m == 0 and 100 <= results[1].distance_m <= 120
    assert len(calls) == 8
    assert "ST_DWithin" in calls[0] and "ST_Distance" in calls[0]
    assert results[0].zones[0].realtime.status == "AVAILABLE"
    assert results[0].zones[0].rates[0].supporting_sources
    assert results[0].entrances[0].lat != results[0].lat


@pytest.mark.parametrize("suffix", ["", "/rates", "/realtime"])
async def test_live_specialized_common_schema_keeps_components_separate(live, suffix):
    client, _, lots, zones, sources = live
    response = await client.get(f"/api/v1/parking/{lots[0].id}{suffix}", params={"vehicle": "RED"})
    assert response.status_code == 200, response.text
    data = response.json()
    serialized = {zone["zone_id"]: zone for zone in data["zones"]}
    assert all(zone.keys() >= BASE_FIELDS for zone in serialized.values())
    assert serialized[zones[0].id]["compatibility"]["provenance"]["source_id"] == sources[0].id
    assert serialized[zones[0].id]["rate_summary"]["provenance"]["source_id"] == sources[1].id
    assert serialized[zones[0].id]["rate_summary"]["supporting_sources"][0]["source_id"] == sources[3].id
    assert serialized[zones[0].id]["availability"]["provenance"]["source_id"] == sources[2].id
    assert serialized[zones[2].id]["availability"] is None
    assert serialized[zones[1].id]["rate_summary"] is None
    if not suffix:
        assert data["entrances"][0]["provenance"]["source_id"] == sources[3].id
        assert data["entrances"][0]["heavy_motorcycle_access"] == "UNKNOWN"


async def test_live_nearby_optin_filters_returned_zones_and_numeric_unknown_isolation(live):
    client, _, lots, zones, _ = live
    query = {"lat": 25.03, "lng": 121.56, "vehicle": "RED"}
    confirmed = (await client.get("/api/v1/parking/nearby", params=query)).json()
    assert [item["id"] for item in confirmed["items"]] == [lots[0].id]
    first = confirmed["items"][0]
    assert [zone["zone_id"] for zone in first["zones"]] == [zones[0].id]
    assert first["availability_summary"]["available"] == 3
    included = (await client.get("/api/v1/parking/nearby", params={**query, "include_unknown": True})).json()
    assert [item["id"] for item in included["items"]] == [lot.id for lot in lots[:2]]
    assert included["items"][0]["ranking_score_bp"] == first["ranking_score_bp"]
    assert included["items"][0]["availability_summary"] == first["availability_summary"]
    assert [zone["zone_id"] for zone in included["items"][0]["zones"]] == [zone.id for zone in zones[:2]]


async def test_live_rate_rules_taipei_filters_and_pinned_realtime(live):
    client, _, lots, _, _ = live
    query = {"lat": 25.03, "lng": 121.56, "vehicle": "RED", "hourly_rate_max_twd": 30, "daily_max_required": True}
    weekday = await client.get("/api/v1/parking/nearby", params={**query, "at": "2026-10-05T12:00:00+08:00"})
    assert [item["id"] for item in weekday.json()["items"]] == [lots[0].id]
    weekend = await client.get("/api/v1/parking/nearby", params={**query, "at": "2026-10-04T12:00:00+08:00"})
    assert weekend.json()["items"] == []
    realtime = await client.get(
        f"/api/v1/parking/{lots[0].id}/realtime", params={"vehicle": "RED", "at": "2026-10-04T12:00:00+08:00"}
    )
    assert realtime.json()["zones"][0]["availability"]["freshness"]["status"] == "FRESH"
    assert realtime.json()["zones"][0]["rate_summary"]["comparison_hourly_rate_twd"] == 40


async def test_live_source_freshness_threshold_controls_available_only(live):
    client, session, _, _, sources = live
    params = {"lat": 25.03, "lng": 121.56, "vehicle": "RED", "available_only": True}
    assert len((await client.get("/api/v1/parking/nearby", params=params)).json()["items"]) == 1
    sources[2].freshness_seconds = 1
    await session.flush()
    assert (await client.get("/api/v1/parking/nearby", params=params)).json()["items"] == []


async def test_explain_spatial_index_on_representative_seed(live):
    client, session, _, _, _ = live
    # Distributed candidates let the planner choose the real GiST access path
    # without disabling sequential scans. Transaction rollback removes the seed.
    await session.execute(
        text("""
        INSERT INTO parking_lots (name, location)
        SELECT 'M3 EXPLAIN ' || n,
               ST_SetSRID(ST_MakePoint(120 + (n % 200) * 0.015, 23 + (n / 200) * 0.015),4326)::geography
        FROM generate_series(1,20000) AS n
    """)
    )
    await session.execute(text("ANALYZE parking_lots"))
    plan = (
        await session.execute(
            text("""
        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
        SELECT id, floor(ST_Distance(location, ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography)+0.5)::integer
        FROM parking_lots
        WHERE ST_DWithin(location, ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography,:radius)
    """),
            {"lat": 25.03, "lng": 121.56, "radius": 500},
        )
    ).scalar_one()[0]

    def index_names(node):
        return [node.get("Index Name")] + [name for child in node.get("Plans", []) for name in index_names(child)]

    assert "ix_parking_lots_location" in index_names(plan["Plan"]), plan
    assert plan["Plan"]["Actual Rows"] < 20000
    timings = []
    for _ in range(20):
        start = perf_counter()
        response = await client.get(
            "/api/v1/parking/nearby", params={"lat": 25.03, "lng": 121.56, "radius": 500, "vehicle": "RED"}
        )
        assert response.status_code == 200, response.text
        timings.append((perf_counter() - start) * 1000)
    print(
        "M3_QUERY_PROFILE="
        + json.dumps(
            {
                "seed_lots": 20000,
                "candidate_count": plan["Plan"]["Actual Rows"],
                "index": "ix_parking_lots_location",
                "planning_time_ms": plan["Planning Time"],
                "spatial_execution_time_ms": plan["Execution Time"],
                "api_samples": 20,
                "api_p95_ms": round(sorted(timings)[18], 3),
            }
        )
    )
