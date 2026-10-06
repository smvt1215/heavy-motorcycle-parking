"""Live PostGIS tests for raw durability, idempotence and API provenance."""

import asyncio
import base64
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.ingestion.cli import parser, run
from app.ingestion.contracts import FeedSnapshot
from app.ingestion.downloader import ParkingDownloader
from app.ingestion.pipeline import ParkingIngestionPipeline
from app.ingestion.taipei import TaipeiParkingAdapter
from app.models import (
    DataSource,
    ParkingEntrance,
    ParkingLot,
    ParkingRate,
    ParkingRateSource,
    ParkingRealtime,
    ParkingRule,
    ParkingZone,
    RawImportBatch,
    RawParkingRecord,
)
from app.repositories.parking import ParkingRepository
from app.services.compatibility import ParkingCompatibilityService
from app.services.rates import resolve_rates
from app.services.realtime import is_available_only, resolve_availability

NOW = datetime(2026, 10, 5, 16, 2, 1, tzinfo=UTC)
SOURCE_AT = NOW.replace(second=0)
STAMP = "Tue Oct 06 00:02:00 CST 2026"


def row(external_id="M4-TEST"):
    return {
        "id": external_id,
        "name": "測試停車場",
        "area": "松山區",
        "address": "民生東路5段84號",
        "tw97x": "306457.778",
        "tw97y": "2772361.517",
        "totalcar": 10,
        "totalmotor": 5,
        "totallargemotor": "3",
        "payex": "小型車每小時30元；大型重機依現場公告",
        "FareInfo": {
            "FareRule": [
                {
                    "ParkingType": "CM",
                    "RateType": "1",
                    "ChargeableSTime": "00",
                    "ChargeableETime": "24",
                    "ParkingRates": 30,
                    "CUnit": "",
                },
            ]
        },
        "EntranceCoord": {"EntrancecoordInfo": [{"Xcod": "25.0585", "Ycod": "121.5596", "Address": "入口"}]},
    }


def snapshot(rows=None, *, kind="static", fetched_at=NOW, stamp=STAMP):
    payload = {"data": {"UPDATETIME": stamp, "park": [row()] if rows is None else rows}}
    return FeedSnapshot(kind, payload, fetched_at, TaipeiParkingAdapter().source_updated_at(payload))


@pytest.fixture(scope="module")
def migrated():
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")


@pytest.fixture
async def live(migrated):
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        outer = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            yield session, ParkingIngestionPipeline(session, TaipeiParkingAdapter())
        await outer.rollback()
    await engine.dispose()


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


async def test_repeat_import_preserves_normalized_ids_and_complete_raw_envelopes(live):
    session, pipeline = live
    first = await pipeline.ingest(snapshot())
    async with session.begin():
        lots = (await session.scalars(select(ParkingLot))).all()
        ids = {
            model: (await session.scalars(select(model.id))).all()
            for model in (ParkingLot, ParkingZone, ParkingRule, ParkingRate, ParkingEntrance, ParkingRateSource)
        }
        original_lot_id = lots[0].id
    second = await pipeline.ingest(snapshot())
    assert first.status == second.status == "SUCCEEDED"
    async with session.begin():
        for model, original in ids.items():
            assert (await session.scalars(select(model.id))).all() == original
        assert await count(session, RawImportBatch) == 2
        assert await count(session, RawParkingRecord) == 2
        batch = await session.get(RawImportBatch, first.batch_id)
        assert batch.raw_payload == snapshot().payload
        assert batch.source_updated_at == SOURCE_AT and batch.fetched_at == NOW
        raw = (await session.scalars(select(RawParkingRecord).where(RawParkingRecord.batch_id == first.batch_id))).one()
        assert raw.payload == row() and raw.status == "NORMALIZED"
        assert raw.raw_rate_text == row()["payex"]
        lot = await ParkingRepository(session).detail(original_lot_id)
        compat = ParkingCompatibilityService()
        results = {zone.facts.space_type: compat.evaluate(lot.facts, zone.facts, "YELLOW", NOW) for zone in lot.zones}
        assert results["HEAVY_ONLY"].status == "ALLOWED"
        assert results["CAR_SHARED"].status == results["MOTO_SHARED"].status == "UNKNOWN"
        assert all(result.provenance[0].fetched_at == NOW for result in results.values())
        assert lot.entrances[0].access == "UNKNOWN"
        assert lot.lat != lot.entrances[0].lat  # Projected center must not become the rounded entrance.
        assert all(rate.parse_status != "PARSED" for zone in lot.zones for rate in zone.rates)


async def test_partial_records_are_observable_and_do_not_abort_valid_records(live):
    session, pipeline = live
    invalid = row("M4-INVALID")
    invalid["totalcar"] = True
    result = await pipeline.ingest(snapshot([row(), invalid, 42]))
    assert (result.status, result.total, result.normalized, result.failed) == ("PARTIALLY_SUCCEEDED", 3, 1, 2)
    async with session.begin():
        assert await count(session, ParkingLot) == 1
        records = (await session.scalars(select(RawParkingRecord).order_by(RawParkingRecord.id))).all()
        assert records[1].status == records[2].status == "INVALID"
        assert records[1].error_code and records[2].error_code
        assert records[1].payload["totalcar"] is True
        assert records[2].payload == {"_value": 42}


async def test_conflicting_duplicate_lot_ids_never_choose_one_permission(live):
    session, pipeline = live
    first, second = row(), row()
    second["totallargemotor"] = 0
    result = await pipeline.ingest(snapshot([first, second]))
    assert result.status == "FAILED" and result.failed == 2
    async with session.begin():
        assert await count(session, ParkingLot) == 0
        records = (await session.scalars(select(RawParkingRecord))).all()
        assert {r.error_code for r in records} == {"DUPLICATE_ID_CONFLICT"}


async def test_identical_duplicates_are_retained_only_as_raw_evidence(live):
    session, pipeline = live
    result = await pipeline.ingest(snapshot([row(), row()]))
    assert result.normalized == 2 and result.failed == 0
    async with session.begin():
        assert await count(session, ParkingLot) == 1
        assert await count(session, RawParkingRecord) == 2


async def test_realtime_has_independent_source_and_no_cross_category_counts(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    feed = snapshot(
        [{"id": "M4-TEST", "availablecar": 8, "availablemotor": -9, "availableheavymotor": 2}], kind="realtime"
    )
    result = await pipeline.ingest(feed)
    repeated = await pipeline.ingest(feed)
    assert result.status == repeated.status == "SUCCEEDED"
    async with session.begin():
        rows = (
            await session.execute(
                select(ParkingRealtime, ParkingZone, DataSource)
                .join(ParkingZone, ParkingRealtime.zone_id == ParkingZone.id)
                .join(DataSource, ParkingRealtime.source_id == DataSource.id)
            )
        ).all()
        assert len(rows) == 3
        categories = {z.external_id.rsplit(":", 1)[-1]: rt for rt, z, _ in rows}
        assert categories["heavy"].available_spaces == 2
        assert categories["heavy"].total_spaces == 3
        assert categories["car"].available_spaces == 8
        assert categories["motor"].status == "UNKNOWN" and categories["motor"].total_spaces is None
        assert all(source.code == "TAIPEI_REALTIME_V2" for _, _, source in rows)
        assert all(rt.fetched_at == NOW and rt.source_updated_at == SOURCE_AT for rt, _, _ in rows)


async def test_static_capacity_at_another_instant_cannot_confirm_realtime_total(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    result = await pipeline.ingest(
        snapshot(
            [{"id": "M4-TEST", "availablecar": 20, "availableheavymotor": 4}],
            kind="realtime",
            fetched_at=NOW + timedelta(minutes=1),
            stamp="Tue Oct 06 00:03:00 CST 2026",
        )
    )
    assert result.status == "SUCCEEDED"
    async with session.begin():
        rows = (await session.scalars(select(ParkingRealtime))).all()
        assert all(r.total_spaces is None for r in rows)


async def test_invalid_realtime_count_rolls_back_all_category_updates(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    result = await pipeline.ingest(
        snapshot([{"id": "M4-TEST", "availablecar": 2, "availablemotor": 1, "availableheavymotor": 4}], kind="realtime")
    )
    assert result.status == "FAILED" and result.failed == 1
    async with session.begin():
        assert await count(session, ParkingRealtime) == 0
        raw = (await session.scalars(select(RawParkingRecord).where(RawParkingRecord.record_type == "realtime"))).one()
        assert raw.error_code == "COUNT_EXCEEDS_CAPACITY" and raw.payload["availableheavymotor"] == 4


async def test_older_static_snapshot_cannot_replace_newer_permission(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    changed = row()
    changed["totallargemotor"] = "0"
    result = await pipeline.ingest(
        snapshot([changed], fetched_at=NOW - timedelta(minutes=2), stamp="Tue Oct 06 00:00:00 CST 2026")
    )
    assert result.status == "FAILED"
    async with session.begin():
        assert (await session.scalars(select(ParkingRule).where(ParkingRule.yellow_plate_allowed.is_(True)))).one()


async def test_removed_zone_permission_becomes_unknown_not_space_type_default(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    changed = row()
    changed["totallargemotor"] = "0"
    await pipeline.ingest(snapshot([changed], fetched_at=NOW + timedelta(minutes=1)))
    async with session.begin():
        lot_id = (await session.scalars(select(ParkingLot.id))).one()
        lot = await ParkingRepository(session).detail(lot_id)
        zone = next(z for z in lot.zones if z.facts.space_type == "HEAVY_ONLY")
        result = ParkingCompatibilityService().evaluate(lot.facts, zone.facts, "YELLOW", NOW + timedelta(minutes=1))
        assert result.status == "UNKNOWN" and zone.capacity is None
        assert result.rule_ids


async def test_record_integrity_error_is_isolated_by_savepoint(live):
    session, pipeline = live
    original = pipeline.adapter.normalize_static

    def normalize(record):
        normalized = original(record)
        if record["id"] == "M4-BAD-DB":
            return replace(normalized, zones=(replace(normalized.zones[0], capacity=-1),))
        return normalized

    pipeline.adapter.normalize_static = normalize
    result = await pipeline.ingest(snapshot([row("M4-BAD-DB"), row()]))
    assert (result.normalized, result.failed) == (1, 1)
    async with session.begin():
        assert await count(session, ParkingLot) == 1
        failed = (await session.scalars(select(RawParkingRecord).where(RawParkingRecord.status == "INVALID"))).one()
        assert failed.error_code == "INTEGRITY_ERROR"


async def test_fatal_writer_error_retains_raw_and_rolls_back_normalized_batch(live):
    session, pipeline = live
    original = pipeline.writer.write_lot

    async def write(lot, feed, source_id):
        if lot.external_id == "M4-FATAL":
            raise RuntimeError("simulated storage failure")
        await original(lot, feed, source_id)

    pipeline.writer.write_lot = write
    with pytest.raises(RuntimeError, match="simulated"):
        await pipeline.ingest(snapshot([row(), row("M4-FATAL")]))
    async with session.begin():
        assert await count(session, ParkingLot) == 0
        batch = (await session.scalars(select(RawImportBatch))).one()
        assert batch.status == "FAILED" and batch.normalized_records == 0 and batch.failed_records == 2
        assert batch.raw_payload == snapshot([row(), row("M4-FATAL")]).payload
        assert {r.error_code for r in (await session.scalars(select(RawParkingRecord))).all()} == {"BATCH_ABORTED"}


async def test_bad_envelope_is_retained_and_failed(live):
    session, pipeline = live
    broken = {"data": {"park": "schema changed"}}
    result = await pipeline.ingest(FeedSnapshot("static", broken, NOW, None))
    assert result.status == "FAILED"
    async with session.begin():
        batch = await session.get(RawImportBatch, result.batch_id)
        assert batch.raw_payload == broken and batch.error_summary["errors"]


async def test_download_failure_is_observable_without_normalizing(live):
    session, pipeline = live
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        result = await pipeline.download_and_ingest("static", ParkingDownloader(client))
    assert result.status == "FAILED"
    async with session.begin():
        batch = await session.get(RawImportBatch, result.batch_id)
        assert batch.error_summary["errors"] == {"HTTP_ERROR": 1}


async def test_cache_failure_keeps_committed_database_result(live):
    session, pipeline = live

    class BrokenCache:
        async def set(self, *args, **kwargs):
            raise OSError("simulated cache outage")

    pipeline.cache = BrokenCache()
    result = await pipeline.ingest(snapshot())
    assert result.status == "SUCCEEDED"
    async with session.begin():
        assert await count(session, ParkingLot) == 1
        batch = await session.get(RawImportBatch, result.batch_id)
        assert batch.error_summary["cache"] == "UNAVAILABLE"


async def test_unknown_or_zero_capacity_does_not_gain_heavy_permission(live):
    session, pipeline = live
    generic = copy.deepcopy(row())
    generic["totallargemotor"] = None
    await pipeline.ingest(snapshot([generic]))
    async with session.begin():
        lot_id = (await session.scalars(select(ParkingLot.id))).one()
        lot = await ParkingRepository(session).detail(lot_id)
        assert all(
            ParkingCompatibilityService().evaluate(lot.facts, z.facts, "RED", NOW).status == "UNKNOWN"
            for z in lot.zones
        )


async def test_unlabelled_parsed_price_never_confirms_selected_vehicle(live):
    session, pipeline = live
    unlabelled = row()
    unlabelled["payex"], unlabelled["FareInfo"] = "每小時30元", {}
    await pipeline.ingest(snapshot([unlabelled]))
    async with session.begin():
        lot_id = (await session.scalars(select(ParkingLot.id))).one()
        lot = await ParkingRepository(session).detail(lot_id)
        heavy = next(z for z in lot.zones if z.facts.space_type == "HEAVY_ONLY")
        assert heavy.rates[0].parse_status == "PARSED" and heavy.rates[0].vehicle is None
        summary, _ = resolve_rates(heavy.rates, "YELLOW", NOW)
        assert summary is None


async def test_missing_realtime_sentinel_supersedes_previous_positive_count(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    await pipeline.ingest(snapshot([{"id": "M4-TEST", "availableheavymotor": 2}], kind="realtime"))
    await pipeline.ingest(
        snapshot(
            [{"id": "M4-TEST", "availableheavymotor": -9}], kind="realtime", fetched_at=NOW + timedelta(seconds=10)
        )
    )
    async with session.begin():
        lot_id = (await session.scalars(select(ParkingLot.id))).one()
        lot = await ParkingRepository(session).detail(lot_id)
        heavy = next(z for z in lot.zones if z.facts.space_type == "HEAVY_ONLY")
        assert heavy.realtime.status == "UNKNOWN" and heavy.realtime.available is None


async def test_replay_newer_source_with_older_fetch_is_rejected(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    result = await pipeline.ingest(
        snapshot(fetched_at=NOW - timedelta(seconds=10), stamp="Tue Oct 06 00:03:00 CST 2026")
    )
    assert result.status == "FAILED"
    async with session.begin():
        batch = await session.get(RawImportBatch, result.batch_id)
        assert batch.error_summary["errors"] == {"STALE_SNAPSHOT": 1}


async def test_static_upsert_preserves_other_sources_facts(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    async with session.begin():
        lot_id = (await session.scalars(select(ParkingLot.id))).one()
        heavy_id = (
            await session.scalars(select(ParkingZone.id).where(ParkingZone.external_id == "M4-TEST:heavy"))
        ).one()
        other = DataSource(code="M4-OPERATOR", name="Operator", source_type="OPERATOR")
        session.add(other)
        await session.flush()
        rule = ParkingRule(
            parking_id=lot_id,
            zone_id=heavy_id,
            source_id=other.id,
            rule_kind="EXCEPTION",
            authority_priority=500,
            yellow_plate_allowed=False,
        )
        entry = ParkingEntrance(
            parking_id=lot_id, source_id=other.id, name="Operator entrance", heavy_motorcycle_access=True
        )
        session.add_all([rule, entry])
        await session.flush()
        rule_id, entry_id = rule.id, entry.id
    await pipeline.ingest(snapshot(fetched_at=NOW + timedelta(seconds=10)))
    async with session.begin():
        assert (await session.get(ParkingRule, rule_id)).yellow_plate_allowed is False
        assert (await session.get(ParkingEntrance, entry_id)).heavy_motorcycle_access is True


async def test_overlapping_workers_keep_one_lot_and_latest_source_facts(migrated):
    engine = create_async_engine(settings.database_url)
    original, changed = row("M4-CONCURRENT"), row("M4-CONCURRENT")
    changed["totallargemotor"] = "4"

    async def ingest(feed):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await ParkingIngestionPipeline(session, TaipeiParkingAdapter()).ingest(feed)

    results = []
    try:
        results = await asyncio.gather(
            ingest(snapshot([original])),
            ingest(snapshot([changed], fetched_at=NOW + timedelta(minutes=1), stamp="Tue Oct 06 00:03:00 CST 2026")),
        )
        async with AsyncSession(engine) as session, session.begin():
            lots = (await session.scalars(select(ParkingLot).where(ParkingLot.external_id == "M4-CONCURRENT"))).all()
            assert len(lots) == 1
            zones = (await session.scalars(select(ParkingZone).where(ParkingZone.parking_id == lots[0].id))).all()
            assert len(zones) == 3
            assert next(z for z in zones if z.external_id.endswith(":heavy")).capacity == 4
    finally:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(delete(ParkingLot).where(ParkingLot.external_id == "M4-CONCURRENT"))
            batch_ids = [r.batch_id for r in results]
            await session.execute(delete(RawParkingRecord).where(RawParkingRecord.batch_id.in_(batch_ids)))
            await session.execute(delete(RawImportBatch).where(RawImportBatch.id.in_(batch_ids)))
        await engine.dispose()


@pytest.mark.parametrize("value", ["\x00", "\ud800"])
async def test_jsonb_unsupported_unicode_keeps_reversible_raw_evidence(live, value):
    session, pipeline = live
    broken = row()
    broken["payex"] = "原始" + value
    feed = snapshot([broken])
    result = await pipeline.ingest(feed)
    assert result.status == "FAILED" and result.failed == 1
    async with session.begin():
        batch = await session.get(RawImportBatch, result.batch_id)
        assert json.loads(base64.b64decode(batch.raw_payload["data"])) == feed.payload
        raw = (await session.scalars(select(RawParkingRecord))).one()
        assert json.loads(base64.b64decode(raw.payload["data"])) == broken
        assert raw.error_code == "UNSUPPORTED_JSON_VALUE" and raw.raw_rate_text is None


async def test_long_entrance_name_fails_only_its_lot_record(live):
    session, pipeline = live
    broken = row("M4-LONG-ENTRANCE")
    broken["EntranceCoord"]["EntrancecoordInfo"][0]["Address"] = "x" * 201
    result = await pipeline.ingest(snapshot([broken, row()]))
    assert result.normalized == result.failed == 1 and result.status == "PARTIALLY_SUCCEEDED"
    async with session.begin():
        assert await count(session, ParkingLot) == 1


async def test_disappeared_lot_becomes_unknown_and_retires_rates(live):
    session, pipeline = live
    await pipeline.ingest(snapshot([row("M4-A"), row("M4-B")]))
    await pipeline.ingest(snapshot([row("M4-A")], fetched_at=NOW + timedelta(minutes=1)))
    async with session.begin():
        lot_id = (await session.scalars(select(ParkingLot.id).where(ParkingLot.external_id == "M4-B"))).one()
        lot = await ParkingRepository(session).detail(lot_id)
        assert all(
            ParkingCompatibilityService().evaluate(lot.facts, z.facts, "YELLOW", NOW).status == "UNKNOWN"
            for z in lot.zones
        )
        rates = (
            await session.scalars(select(ParkingRate).join(ParkingZone).where(ParkingZone.parking_id == lot_id))
        ).all()
        assert rates and all(rate.effective_to == NOW + timedelta(minutes=1) for rate in rates)


async def test_failed_static_record_is_not_treated_as_a_deleted_lot(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    bad = row()
    bad["tw97x"] = None
    await pipeline.ingest(snapshot([bad], fetched_at=NOW + timedelta(minutes=1)))
    async with session.begin():
        heavy = (await session.scalars(select(ParkingRule).where(ParkingRule.yellow_plate_allowed.is_(True)))).one()
        assert heavy.fetched_at == NOW


async def test_tombstoned_zone_cannot_receive_positive_realtime(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    changed = row()
    changed["totallargemotor"] = "0"
    await pipeline.ingest(snapshot([changed], fetched_at=NOW + timedelta(seconds=10)))
    await pipeline.ingest(
        snapshot([{"id": "M4-TEST", "availableheavymotor": 2}], kind="realtime", fetched_at=NOW + timedelta(seconds=20))
    )
    async with session.begin():
        realtime = (
            await session.scalars(
                select(ParkingRealtime).join(ParkingZone).where(ParkingZone.external_id == "M4-TEST:heavy")
            )
        ).one()
        assert realtime.status == "UNKNOWN" and realtime.available_spaces is None


async def test_reappearing_entrance_clears_absence_note(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    removed = row()
    removed["EntranceCoord"] = {}
    await pipeline.ingest(snapshot([removed], fetched_at=NOW + timedelta(seconds=10)))
    await pipeline.ingest(snapshot(fetched_at=NOW + timedelta(seconds=20)))
    async with session.begin():
        entrance = (await session.scalars(select(ParkingEntrance))).one()
        assert entrance.notes is None and entrance.location is not None


async def test_frozen_source_data_never_becomes_fresh_from_repeated_downloads(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    await pipeline.ingest(
        snapshot([{"id": "M4-TEST", "availableheavymotor": 2}], kind="realtime", fetched_at=NOW + timedelta(hours=1))
    )
    async with session.begin():
        lot_id = (await session.scalars(select(ParkingLot.id))).one()
        lot = await ParkingRepository(session).detail(lot_id)
        heavy = next(z for z in lot.zones if z.facts.space_type == "HEAVY_ONLY")
        availability = resolve_availability(heavy.realtime, NOW + timedelta(hours=1))
        assert availability["status"] == "AVAILABLE" and availability["freshness"]["status"] == "STALE"
        assert availability["provenance"]["fetched_at"] == (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        assert not is_available_only("ALLOWED", availability)


async def test_rate_reappearance_keeps_retired_interval_and_opens_new_version(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    async with session.begin():
        original = (await session.scalars(select(ParkingRate).where(ParkingRate.vehicle_type == "YELLOW"))).one()
        original_id, key = original.id, original.source_record_id
    changed = row()
    changed["FareInfo"]["FareRule"][0]["ParkingRates"] = 40
    retired_at, back_at = NOW + timedelta(seconds=10), NOW + timedelta(seconds=20)
    await pipeline.ingest(snapshot([changed], fetched_at=retired_at))
    async with session.begin():
        assert (await session.get(ParkingRate, original_id)).effective_to == retired_at
    await pipeline.ingest(snapshot(fetched_at=back_at))
    await pipeline.ingest(snapshot(fetched_at=back_at + timedelta(seconds=10)))
    async with session.begin():
        versions = (
            await session.scalars(
                select(ParkingRate).where(ParkingRate.source_record_id == key).order_by(ParkingRate.id)
            )
        ).all()
        assert [(v.id == original_id, v.effective_from, v.effective_to) for v in versions] == [
            (True, None, retired_at),
            (False, back_at, None),
        ]
        reopened = versions[1]
        assert (await session.scalar(select(func.count()).where(ParkingRateSource.rate_id == reopened.id))) == 1
        lot_id = (await session.scalars(select(ParkingLot.id))).one()
        repository = ParkingRepository(session)
        for at, expected in ((retired_at + timedelta(seconds=5), set()), (back_at, {reopened.id})):
            lot = await repository.detail(lot_id)
            live_ids = {rate["rate_id"] for zone in lot.zones for rate in resolve_rates(zone.rates, "YELLOW", at)[1]}
            assert live_ids & {v.id for v in versions} == expected


async def test_future_source_timestamp_cannot_block_later_snapshots(live):
    session, pipeline = live
    far_future = "Tue Oct 06 00:02:00 CST 2099"
    result = await pipeline.ingest(snapshot(stamp=far_future))
    async with session.begin():
        batch = await session.get(RawImportBatch, result.batch_id)
        assert batch.source_updated_at is not None and batch.source_updated_at.year == 2099
        assert batch.raw_payload["data"]["UPDATETIME"] == far_future
        assert batch.error_summary == {"warnings": {"FUTURE_SOURCE_UPDATED_AT": 1}}
        lot = (await session.scalars(select(ParkingLot))).one()
        assert lot.source_updated_at is None
    later = NOW + timedelta(minutes=5)
    result = await pipeline.ingest(snapshot(fetched_at=later, stamp="Tue Oct 06 00:06:00 CST 2026"))
    assert result.failed == 0 and result.normalized == 1
    realtime = await pipeline.ingest(
        snapshot([{"id": "M4-TEST", "availableheavymotor": 2}], kind="realtime", fetched_at=later)
    )
    assert realtime.failed == 0
    async with session.begin():
        lot = (await session.scalars(select(ParkingLot))).one()
        assert lot.fetched_at == later and lot.source_updated_at.year == 2026


async def test_previously_stored_future_timestamp_is_not_ordering_evidence(live):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    async with session.begin():
        lot = (await session.scalars(select(ParkingLot))).one()
        lot.source_updated_at = datetime(2099, 1, 1, tzinfo=UTC)
    result = await pipeline.ingest(snapshot(fetched_at=NOW + timedelta(minutes=5)))
    assert result.failed == 0 and result.normalized == 1


async def test_data_error_is_a_record_failure_not_a_batch_abort(live):
    session, pipeline = live
    original = pipeline.adapter.normalize_static

    def normalize(record):
        normalized = original(record)
        return replace(normalized, name="x" * 201) if record["id"] == "M4-DATA-ERROR" else normalized

    pipeline.adapter.normalize_static = normalize
    result = await pipeline.ingest(snapshot([row("M4-DATA-ERROR"), row()]))
    assert result.failed == result.normalized == 1
    async with session.begin():
        raw = (await session.scalars(select(RawParkingRecord).where(RawParkingRecord.status == "INVALID"))).one()
        assert raw.error_code == "DATA_ERROR"


async def test_cli_invalid_json_replay_is_durable_failed_batch(live, tmp_path, monkeypatch, capsys):
    session, _ = live
    from app.ingestion import cli

    class BorrowedSession:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    class DummyEngine:
        async def dispose(self):
            pass

    path = tmp_path / "invalid.json"
    body = b'{"data":{"park":[],"value":NaN}}'
    path.write_bytes(body)
    monkeypatch.setattr(cli, "get_sessionmaker", lambda: BorrowedSession)
    monkeypatch.setattr(cli, "get_db_engine", DummyEngine)
    code = await run(
        parser().parse_args(
            ["--feed", "static", "--static-file", str(path), "--fetched-at", NOW.isoformat(), "--no-cache"]
        )
    )
    assert code == 1 and json.loads(capsys.readouterr().out)["status"] == "FAILED"
    async with session.begin():
        batch = (await session.scalars(select(RawImportBatch))).one()
        assert batch.error_summary["errors"] == {"INVALID_JSON": 1}
        assert base64.b64decode(batch.raw_payload["body_base64"]) == body


@pytest.mark.parametrize("stamp", [None, "invalid", "Tue Oct 06 00:03:00 CST 2026"])
async def test_required_missing_or_future_source_timestamp_means_unknown_freshness(live, stamp):
    session, pipeline = live
    await pipeline.ingest(snapshot())
    await pipeline.ingest(snapshot([{"id": "M4-TEST", "availableheavymotor": 2}], kind="realtime", stamp=stamp))
    async with session.begin():
        lot_id = (await session.scalars(select(ParkingLot.id))).one()
        lot = await ParkingRepository(session).detail(lot_id)
        heavy = next(z for z in lot.zones if z.facts.space_type == "HEAVY_ONLY")
        availability = resolve_availability(heavy.realtime, NOW)
        assert availability["status"] == "AVAILABLE" and availability["freshness"]["status"] == "UNKNOWN"
