"""End-to-end policy provenance, shared car occupancy and conservative retirement."""

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
from app.ingestion.policies import NEW_TAIPEI_PUBLIC_CAR, NEW_TAIPEI_ROADSIDE
from app.main import create_app
from app.models import DataSource, ParkingLot, ParkingRate, ParkingRateSource, ParkingRealtime, ParkingRule
from app.repositories.parking import ParkingRepository
from app.services.compatibility import ParkingCompatibilityService

NOW = datetime(2026, 10, 10, 2, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures/new_taipei"


def sample(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="module")
def migrated():
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")


@pytest.fixture
async def session(migrated):
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        outer = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as db:
            yield db
        await outer.rollback()
    await engine.dispose()


async def api(db, parking_id, vehicle="LARGE_HEAVY", at=NOW, endpoint=""):
    app = create_app()
    app.state.clock = lambda: NOW

    async def override():
        yield db

    app.dependency_overrides[get_session] = override
    async with db.begin(), AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        detail = await client.get(
            f"/api/v1/parking/{parking_id}{endpoint}", params={"vehicle": vehicle, "at": at.isoformat()}
        )
        assert detail.status_code == 200
        return detail.json()


async def test_verified_car_policy_keeps_sources_counts_and_rates_in_their_zones(session):
    pipeline = ParkingIngestionPipeline(session, NewTaipeiParkingAdapter())
    assert (await pipeline.ingest(FeedSnapshot("static", sample("static_sample.json"), NOW, None))).failed == 0
    assert (await pipeline.ingest(FeedSnapshot("realtime", sample("realtime_sample.json"), NOW, None))).failed == 0
    async with session.begin():
        lot = (await session.scalars(select(ParkingLot).where(ParkingLot.external_id == "010152"))).one()
        parking_id = lot.id
    detail = await api(session, parking_id)
    by_type = {z["space_type"]: z for z in detail["zones"]}
    car, motor, heavy = (by_type[t] for t in ("CAR_SHARED", "MOTO_SHARED", "HEAVY_ONLY"))
    assert car["compatibility"]["status"] == "ALLOWED"
    assert motor["compatibility"]["status"] == heavy["compatibility"]["status"] == "UNKNOWN"
    assert car["availability"]["available"] == 44
    assert car["availability"]["total"] is None
    assert motor["availability"] is heavy["availability"] is None
    realtime = await api(session, parking_id, endpoint="/realtime")
    assert realtime["availability_summary"]["available"] is None
    assert realtime["availability_summary"]["coverage"] != "COMPLETE"
    assert car["rate_summary"] is None
    assert detail["entrances"] == []
    async with session.begin():
        source_codes = {s.id: s.code for s in (await session.scalars(select(DataSource))).all()}
        assert source_codes[car["compatibility"]["provenance"]["source_id"]] == NEW_TAIPEI_PUBLIC_CAR.code
        assert source_codes[car["availability"]["provenance"]["source_id"]] == "NEW_TAIPEI_REALTIME"
        assert car["compatibility"]["provenance"]["fetched_at"] is None
        assert car["compatibility"]["provenance"]["source_updated_at"] is None
        assert car["availability"]["provenance"]["fetched_at"] == "2026-10-10T02:00:00Z"
        policy_rule = (
            await session.scalars(
                select(ParkingRule).where(
                    ParkingRule.zone_id == car["zone_id"], ParkingRule.large_heavy_allowed.is_(True)
                )
            )
        ).one()
        scope = json.loads(policy_rule.notes)
        assert scope["policy_published_on"] == "2025-03-21"
        assert scope["scope_source"]["fetched_at"] == NOW.isoformat()
        rate_sources = (
            await session.scalars(
                select(ParkingRateSource.source_id).join(ParkingRate).where(ParkingRate.zone_id == car["zone_id"])
            )
        ).all()
        assert rate_sources and {source_codes[s] for s in rate_sources} == {"NEW_TAIPEI_STATIC"}
        assert (
            len((await session.scalars(select(ParkingRealtime).where(ParkingRealtime.zone_id == car["zone_id"]))).all())
            == 1
        )
    # API evaluations share the exact absolute instant and respect policy boundaries.
    old = await api(session, parking_id, at=NEW_TAIPEI_PUBLIC_CAR.effective_from - timedelta(microseconds=1))
    assert all(z["compatibility"]["status"] == "UNKNOWN" for z in old["zones"])
    normal = await api(session, parking_id, vehicle="NORMAL_HEAVY")
    assert next(z for z in normal["zones"] if z["space_type"] == "MOTO_SHARED")["compatibility"]["status"] == "ALLOWED"


async def test_scope_mismatch_retires_policy_and_reappearance_keeps_gap_unknown(session):
    pipeline = ParkingIngestionPipeline(session, NewTaipeiParkingAdapter())
    original = next(r for p in sample("static_sample.json")["pages"] for r in p if r["ID"] == "010152")
    for at, rec in [
        (NOW, original),
        (NOW + timedelta(hours=1), {**original, "ADDRESS": "unverified address"}),
        (NOW + timedelta(hours=2), original),
    ]:
        result = await pipeline.ingest(FeedSnapshot("static", [rec], at, None))
        assert result.failed == 0
    async with session.begin():
        lot = (await session.scalars(select(ParkingLot).where(ParkingLot.external_id == "010152"))).one()
        detail = await ParkingRepository(session).detail(lot.id)
        car = next(z for z in detail.zones if z.facts.space_type == "CAR_SHARED")
        for at, expected in [
            (NOW, "ALLOWED"),
            (NOW + timedelta(hours=1, minutes=30), "UNKNOWN"),
            (NOW + timedelta(hours=2), "ALLOWED"),
        ]:
            assert ParkingCompatibilityService().evaluate(detail.facts, car.facts, "LARGE_HEAVY", at).status == expected
        policy = (await session.scalars(select(DataSource).where(DataSource.code == NEW_TAIPEI_PUBLIC_CAR.code))).one()
        versions = (await session.scalars(select(ParkingRule).where(ParkingRule.source_id == policy.id))).all()
        assert len(versions) == 2
        assert json.loads(versions[0].notes)["facility"]["external_id"] == "010152"


async def test_roadside_policy_and_rate_have_independent_sources_and_retire_with_scope(session):
    pipeline = ParkingIngestionPipeline(session, NewTaipeiRoadsideAdapter())
    original = {
        **sample("roadside_sample.json")[0],
        "name": "機車停車位",
        "pay": "計次收費",
        "paycash": "20元/次",
        "day": "每天",
        "hour": "00:00-24:00",
        "memo": "",
    }  # synthetic policy case
    assert (await pipeline.ingest(FeedSnapshot("static", [original], NOW, None))).failed == 0
    async with session.begin():
        lot = (await session.scalars(select(ParkingLot).where(ParkingLot.external_id == original["id"]))).one()
        parking_id = lot.id
        rate = (await session.scalars(select(ParkingRate).where(ParkingRate.vehicle_type == "LARGE_HEAVY"))).one()
        evidence = (await session.scalars(select(ParkingRateSource).where(ParkingRateSource.rate_id == rate.id))).one()
        policy = await session.get(DataSource, rate.source_id)
        assert policy.code == NEW_TAIPEI_ROADSIDE.code
        assert evidence.source_id == policy.id and evidence.raw_payload["scope_evidence"]["id"] == original["id"]
        assert rate.effective_from == NOW
        assert rate.fetched_at is rate.source_updated_at is evidence.fetched_at is evidence.source_updated_at is None
        assert evidence.raw_payload["policy_published_on"] == "2026-06-29"
        assert evidence.raw_payload["scope_source"]["fetched_at"] == NOW.isoformat()
    detail = await api(session, parking_id)
    zone = detail["zones"][0]
    assert zone["compatibility"]["status"] == "ALLOWED"
    assert zone["rate_summary"]["comparison_hourly_rate_twd"] is None  # 30/4h entry fee is not a linear rate
    assert zone["availability"] is None
    assert (
        await pipeline.ingest(
            FeedSnapshot("static", [{**original, "memo": "夜間禁停"}], NOW + timedelta(hours=1), None)
        )
    ).failed == 0
    retired = await api(session, parking_id, at=NOW + timedelta(hours=1))
    assert retired["zones"][0]["compatibility"]["status"] == "UNKNOWN"
    async with session.begin():
        rates = (await session.scalars(select(ParkingRate).where(ParkingRate.vehicle_type == "LARGE_HEAVY"))).all()
        assert len(rates) == 1 and rates[0].effective_to == NOW + timedelta(hours=1)


@pytest.mark.parametrize("missing", ["car", "lot"])
async def test_disappearing_verified_car_scope_retires_policy_without_erasing_other_sources(session, missing):
    pipeline = ParkingIngestionPipeline(session, NewTaipeiParkingAdapter())
    rows = [r for page in sample("static_sample.json")["pages"] for r in page]
    await pipeline.ingest(FeedSnapshot("static", rows, NOW, None))
    async with session.begin():
        lot = (await session.scalars(select(ParkingLot).where(ParkingLot.external_id == "010152"))).one()
        detail = await ParkingRepository(session).detail(lot.id)
        car = next(z for z in detail.zones if z.facts.space_type == "CAR_SHARED")
        source = DataSource(code="TEST_OPERATOR", name="Operator", source_type="OPERATOR")
        session.add(source)
        await session.flush()
        operator = ParkingRule(
            parking_id=lot.id,
            zone_id=car.facts.zone_id,
            source_id=source.id,
            source_record_id="new_taipei:policy:operator-evidence",
            rule_kind="EXCEPTION",
            authority_priority=300,
            large_heavy_allowed=False,
            active=True,
        )
        session.add(operator)
        await session.flush()
        operator_id, parking_id = operator.id, lot.id
    changed = [({**r, "TOTALCAR": "0"} if r["ID"] == "010152" else r) for r in rows]
    if missing == "lot":
        changed = [r for r in rows if r["ID"] != "010152"]
    assert (await pipeline.ingest(FeedSnapshot("static", changed, NOW + timedelta(hours=1), None))).failed == 0
    async with session.begin():
        policy_id = await session.scalar(select(DataSource.id).where(DataSource.code == NEW_TAIPEI_PUBLIC_CAR.code))
        policy = (
            await session.scalars(
                select(ParkingRule).where(ParkingRule.parking_id == parking_id, ParkingRule.source_id == policy_id)
            )
        ).one()
        assert policy.effective_to == NOW + timedelta(hours=1)
        operator = await session.get(ParkingRule, operator_id)
        assert operator.active and operator.effective_to is None and operator.large_heavy_allowed is False


async def test_changed_policy_schedule_versions_both_rule_and_rate(session):
    pipeline = ParkingIngestionPipeline(session, NewTaipeiRoadsideAdapter())
    original = {
        **sample("roadside_sample.json")[0],
        "name": "機車停車位",
        "pay": "計次收費",
        "paycash": "20元/次",
        "day": "每天",
        "hour": "00:00-24:00",
        "memo": "",
    }
    await pipeline.ingest(FeedSnapshot("static", [original], NOW, None))
    assert (
        await pipeline.ingest(
            FeedSnapshot("static", [{**original, "hour": "07:00-20:00"}], NOW + timedelta(hours=1), None)
        )
    ).failed == 0
    async with session.begin():
        policy_id = await session.scalar(select(DataSource.id).where(DataSource.code == NEW_TAIPEI_ROADSIDE.code))
        rules = (
            await session.scalars(
                select(ParkingRule).where(ParkingRule.source_id == policy_id).order_by(ParkingRule.id)
            )
        ).all()
        rates = (
            await session.scalars(
                select(ParkingRate).where(ParkingRate.source_id == policy_id).order_by(ParkingRate.id)
            )
        ).all()
        assert len(rules) == len(rates) == 2
        assert rules[0].effective_to == rates[0].effective_to == NOW + timedelta(hours=1)
        assert rules[1].effective_from == rates[1].effective_from == NOW + timedelta(hours=1)
        assert "start_time" not in rules[0].schedule and rules[1].schedule["start_time"] == "07:00"


async def test_reimport_narrows_preexisting_policy_to_reviewed_scope_date(session):

    pipeline = ParkingIngestionPipeline(session, NewTaipeiParkingAdapter())
    original = next(r for p in sample("static_sample.json")["pages"] for r in p if r["ID"] == "010152")
    await pipeline.ingest(FeedSnapshot("static", [original], NOW, None))
    async with session.begin():
        policy_id = await session.scalar(select(DataSource.id).where(DataSource.code == NEW_TAIPEI_PUBLIC_CAR.code))
        row = (await session.scalars(select(ParkingRule).where(ParkingRule.source_id == policy_id))).one()
        row.effective_from = NEW_TAIPEI_PUBLIC_CAR.effective_from
        rule_id = row.id
    result = await pipeline.ingest(FeedSnapshot("static", [original], NOW + timedelta(seconds=1), None))
    assert result.failed == 0
    async with session.begin():
        row = await session.get(ParkingRule, rule_id)
        assert row.effective_from == NOW
        assert row.large_heavy_allowed is True and row.notes and row.active


@pytest.mark.parametrize("roadside", [False, True])
async def test_first_policy_scope_does_not_backdate_and_unchanged_scope_retains_start(session, roadside):
    if roadside:
        adapter = NewTaipeiRoadsideAdapter()
        record = {
            **sample("roadside_sample.json")[0],
            "name": "機車停車位",
            "pay": "計次收費",
            "paycash": "20元/次",
            "day": "每天",
            "hour": "00:00-24:00",
            "memo": "",
        }
    else:
        adapter = NewTaipeiParkingAdapter()
        record = next(r for p in sample("static_sample.json")["pages"] for r in p if r["ID"] == "010152")
    pipeline = ParkingIngestionPipeline(session, adapter)
    await pipeline.ingest(FeedSnapshot("static", [record], NOW, None))
    async with session.begin():
        parking_id = (await session.scalars(select(ParkingLot))).one().id
    old = await api(session, parking_id, at=NOW - timedelta(microseconds=1))
    assert all(z["compatibility"]["status"] == "UNKNOWN" for z in old["zones"])
    if roadside:
        rates = await api(session, parking_id, at=NOW - timedelta(microseconds=1), endpoint="/rates")
        assert rates["zones"][0]["rate_summary"] is None
    await pipeline.ingest(FeedSnapshot("static", [record], NOW + timedelta(hours=1), None))
    confirmed = await api(session, parking_id, at=NOW)
    assert any(z["compatibility"]["status"] == "ALLOWED" for z in confirmed["zones"])
    if roadside:
        rates = await api(session, parking_id, at=NOW, endpoint="/rates")
        assert rates["zones"][0]["rate_summary"] is not None


@pytest.mark.parametrize("change", ["matched", "mismatch", "car_missing", "lot_missing"])
async def test_reimport_narrows_closed_policy_history_even_when_scope_disappears(session, change):

    pipeline = ParkingIngestionPipeline(session, NewTaipeiParkingAdapter())
    rows = [r for page in sample("static_sample.json")["pages"] for r in page]
    await pipeline.ingest(FeedSnapshot("static", rows, NOW, None))
    async with session.begin():
        policy_id = await session.scalar(select(DataSource.id).where(DataSource.code == NEW_TAIPEI_PUBLIC_CAR.code))
        row = (await session.scalars(select(ParkingRule).where(ParkingRule.source_id == policy_id))).one()
        row.effective_from = NEW_TAIPEI_PUBLIC_CAR.effective_from
        parking_id, current_id = row.parking_id, row.id
        history = []
        for end, receipt in (
            ("2025-04-30T00:00:00+08:00", "2025-04-01T00:00:00+08:00"),
            ("2025-06-01T00:00:00+08:00", "2025-05-15T00:00:00+08:00"),
        ):
            closed = ParkingRule(
                parking_id=row.parking_id,
                zone_id=row.zone_id,
                source_id=row.source_id,
                source_record_id=row.source_record_id,
                large_heavy_allowed=True,
                rule_kind=row.rule_kind,
                authority_priority=row.authority_priority,
                active=True,
                effective_from=NEW_TAIPEI_PUBLIC_CAR.effective_from,
                effective_to=datetime.fromisoformat(end),
                notes=json.dumps({**json.loads(row.notes), "scope_verified_from": receipt}),
            )
            session.add(closed)
            await session.flush()
            history.append((closed.id, closed.notes, closed.effective_to, datetime.fromisoformat(receipt)))
    changes = {"mismatch": {"ADDRESS": "unverified address"}, "car_missing": {"TOTALCAR": "0"}}
    incoming = [
        ({**r, **changes.get(change, {})} if r["ID"] == "010152" else r)
        for r in rows
        if change != "lot_missing" or r["ID"] != "010152"
    ]
    result = await pipeline.ingest(FeedSnapshot("static", incoming, NOW + timedelta(seconds=1), None))
    assert result.failed == 0
    # No version may grant parking before the verified roster, including closed
    # rules and facilities that cannot produce a replacement normalized rule.
    detail = await api(session, parking_id, at=datetime.fromisoformat("2025-04-01T00:00:00+08:00"))
    assert all(z["compatibility"]["status"] == "UNKNOWN" for z in detail["zones"])
    floor = NOW
    async with session.begin():
        current = await session.get(ParkingRule, current_id)
        assert current.effective_from == floor
        for rule_id, notes, end, receipt in history:
            closed = await session.get(ParkingRule, rule_id)
            assert closed.notes == notes and closed.effective_to == end
            if end < datetime.fromisoformat("2025-05-12T00:00:00+08:00"):
                assert closed.active is False  # retain the unsupported interval as evidence
            else:
                assert closed.active is True and closed.effective_from == receipt


async def test_unverified_closed_policy_rate_retains_evidence_without_confirmed_price(session):
    record = {
        **sample("roadside_sample.json")[0],
        "name": "機車停車位",
        "pay": "計次收費",
        "paycash": "20元/次",
        "day": "每天",
        "hour": "00:00-24:00",
        "memo": "",
    }
    pipeline = ParkingIngestionPipeline(session, NewTaipeiRoadsideAdapter())
    await pipeline.ingest(FeedSnapshot("static", [record], NOW, None))
    async with session.begin():
        parking_id = (await session.scalars(select(ParkingLot))).one().id
        rate = (await session.scalars(select(ParkingRate).where(ParkingRate.vehicle_type == "LARGE_HEAVY"))).one()
        rate.effective_from = NEW_TAIPEI_ROADSIDE.effective_from
        rate.effective_to = datetime.fromisoformat("2026-07-05T00:00:00+08:00")
        rate_id, text, amount = rate.id, rate.raw_text, rate.base_amount
    await pipeline.ingest(FeedSnapshot("static", [{**record, "memo": "夜間禁停"}], NOW + timedelta(hours=1), None))
    old = await api(session, parking_id, endpoint="/rates", at=datetime.fromisoformat("2026-07-03T00:00:00+08:00"))
    assert old["zones"][0]["compatibility"]["status"] == "UNKNOWN"
    summary = old["zones"][0]["rate_summary"]
    assert summary["parse_status"] == "PARTIALLY_PARSED"
    assert summary["comparison_eligible"] is False and summary["comparison_hourly_rate_twd"] is None
    async with session.begin():
        rate = await session.get(ParkingRate, rate_id)
        assert rate.parse_status == "PARTIALLY_PARSED" and rate.raw_text == text and rate.base_amount == amount
        source = (await session.scalars(select(ParkingRateSource).where(ParkingRateSource.rate_id == rate_id))).one()
        assert source.raw_payload["scope_history_unverified"] is True
