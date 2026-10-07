"""New Taipei paged downloads and live PostGIS ingestion alongside Taipei."""

import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.db import get_session
from app.ingestion.cli import parser, run
from app.ingestion.contracts import FeedSnapshot
from app.ingestion.downloader import DownloadError, ParkingDownloader
from app.ingestion.new_taipei import NewTaipeiParkingAdapter
from app.ingestion.pipeline import ParkingIngestionPipeline
from app.ingestion.sources import NEW_TAIPEI, NEW_TAIPEI_REALTIME, NEW_TAIPEI_STATIC, FeedPolicy
from app.ingestion.taipei import TaipeiParkingAdapter
from app.main import create_app
from app.models import (
    DataSource,
    ParkingLot,
    ParkingRate,
    ParkingRealtime,
    ParkingRule,
    ParkingZone,
    RawImportBatch,
    RawParkingRecord,
)
from app.repositories.parking import ParkingRepository
from app.services.compatibility import ParkingCompatibilityService
from app.services.realtime import resolve_availability

NOW = datetime(2026, 10, 6, 17, 30, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "new_taipei"
TAIPEI_STAMP = "Wed Oct 07 01:29:00 CST 2026"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def ntpc(kind="static", payload=None, fetched_at=NOW):
    payload = fixture(f"{kind}_sample.json") if payload is None else payload
    return FeedSnapshot(kind, payload, fetched_at, None)


# --- paged downloads -------------------------------------------------------------------


def paged_transport(pages, seen, *, fail_page=None, status=200):
    def respond(request):
        page = int(request.url.params["page"])
        seen.append((page, int(request.url.params["size"])))
        if page == fail_page:
            return httpx.Response(status, json={"error": "x"})
        return httpx.Response(200, json=pages[page] if page < len(pages) else [])

    return httpx.MockTransport(respond)


def small(policy: FeedPolicy, size=2, max_pages=5) -> FeedPolicy:
    return FeedPolicy(
        policy.kind, policy.code, policy.name, policy.url, policy.freshness_seconds, page_size=size, max_pages=max_pages
    )


async def test_paged_download_keeps_pages_and_stops_on_short_page():
    seen, clock = [], iter([NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)])
    pages = [[{"ID": "1"}, {"ID": "2"}], [{"ID": "3"}, {"ID": "4"}], [{"ID": "5"}]]
    async with httpx.AsyncClient(transport=paged_transport(pages, seen)) as client:
        download = await ParkingDownloader(client, clock=lambda: next(clock)).fetch(small(NEW_TAIPEI_STATIC))
    assert seen == [(0, 2), (1, 2), (2, 2)]
    assert download.payload == {"page_size": 2, "pages": pages}
    # The first page receipt is the snapshot instant: freshness never looks newer than the oldest page.
    assert download.fetched_at == NOW
    assert len(NewTaipeiParkingAdapter().records(download.payload)) == 5


async def test_exact_multiple_of_page_size_ends_with_empty_page():
    seen = []
    async with httpx.AsyncClient(transport=paged_transport([[{"ID": "1"}, {"ID": "2"}]], seen)) as client:
        download = await ParkingDownloader(client, clock=lambda: NOW).fetch(small(NEW_TAIPEI_REALTIME))
    assert [page for page, _ in seen] == [0, 1]
    assert download.payload["pages"][-1] == []


async def test_any_failed_page_fails_the_whole_snapshot():
    seen, delays = [], []

    async def sleep(delay):
        delays.append(delay)

    pages = [[{"ID": "1"}, {"ID": "2"}], [{"ID": "3"}]]
    async with httpx.AsyncClient(transport=paged_transport(pages, seen, fail_page=1, status=503)) as client:
        with pytest.raises(DownloadError) as caught:
            await ParkingDownloader(client, sleep=sleep, clock=lambda: NOW).fetch(small(NEW_TAIPEI_STATIC))
    assert caught.value.code == "HTTP_RETRYABLE" and len(delays) == 3
    # Pages already received and the first receipt instant stay auditable.
    assert caught.value.fetched_at == NOW
    assert caught.value.evidence == {
        "page_size": 2,
        "pages": [pages[0]],
        "failed_page": 1,
        "failed_page_evidence": None,
    }


async def test_invalid_json_on_later_page_keeps_earlier_pages_and_its_bytes():
    def respond(request):
        if request.url.params["page"] == "0":
            return httpx.Response(200, json=[{"ID": "1"}, {"ID": "2"}])
        return httpx.Response(200, content=b"not json")

    clock = iter([NOW, NOW + timedelta(seconds=1)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DownloadError) as caught:
            await ParkingDownloader(client, clock=lambda: next(clock)).fetch(small(NEW_TAIPEI_STATIC))
    assert caught.value.code == "INVALID_JSON" and caught.value.fetched_at == NOW
    evidence = caught.value.evidence
    assert evidence["pages"] == [[{"ID": "1"}, {"ID": "2"}]] and evidence["failed_page"] == 1
    assert base64.b64decode(evidence["failed_page_evidence"]["body_base64"]) == b"not json"


async def test_partial_page_failure_is_recorded_with_evidence(live):
    session, pipeline = live
    error = DownloadError(
        "HTTP_RETRYABLE",
        "Page 1: Source HTTP 503",
        evidence={"page_size": 1000, "pages": [[{"ID": "1"}]], "failed_page": 1, "failed_page_evidence": None},
        fetched_at=NOW,
    )
    result = await pipeline.record_download_failure("static", error)
    assert result.status == "FAILED"
    async with session.begin():
        batch = await session.get(RawImportBatch, result.batch_id)
        assert batch.raw_payload["pages"] == [[{"ID": "1"}]] and batch.fetched_at == NOW
        assert batch.error_summary["errors"] == {"HTTP_RETRYABLE": 1}
        assert await session.scalar(select(func.count()).select_from(ParkingLot)) == 0


async def test_non_array_page_and_runaway_pagination_are_download_failures():
    def object_page(request):
        return httpx.Response(200, json={"message": "maintenance"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(object_page)) as client:
        with pytest.raises(DownloadError) as caught:
            await ParkingDownloader(client, clock=lambda: NOW).fetch(small(NEW_TAIPEI_STATIC))
    assert caught.value.code == "INVALID_PAGE" and caught.value.evidence["failed_page"] == 0
    assert caught.value.evidence["failed_page_evidence"] == {"message": "maintenance"}

    seen = []
    full = [[{"ID": str(i)}, {"ID": f"{i}b"}] for i in range(10)]
    async with httpx.AsyncClient(transport=paged_transport(full, seen)) as client:
        with pytest.raises(DownloadError) as caught:
            await ParkingDownloader(client, clock=lambda: NOW).fetch(small(NEW_TAIPEI_STATIC, max_pages=3))
    assert caught.value.code == "PAGE_LIMIT_EXCEEDED" and len(seen) == 3


# --- live database ---------------------------------------------------------------------


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
            yield session, ParkingIngestionPipeline(session, NewTaipeiParkingAdapter())
        await outer.rollback()
    await engine.dispose()


async def ids(session, model):
    return sorted((await session.scalars(select(model.id))).all())


async def ntpc_lot(session, external_id):
    return (await session.scalars(select(ParkingLot).where(ParkingLot.external_id == external_id))).one()


async def test_import_normalizes_sources_rules_rates_and_realtime(live):
    session, pipeline = live
    static = await pipeline.ingest(ntpc())
    realtime = await pipeline.ingest(ntpc("realtime", fetched_at=NOW + timedelta(seconds=30)))
    assert (static.status, static.total, static.normalized, static.failed) == ("SUCCEEDED", 6, 6, 0)
    assert (realtime.status, realtime.normalized, realtime.failed) == ("SUCCEEDED", 6, 0)
    async with session.begin():
        sources = {s.code: s for s in (await session.scalars(select(DataSource))).all()}
        assert sources["NEW_TAIPEI_STATIC"].attribution == NEW_TAIPEI.attribution
        assert sources["NEW_TAIPEI_REALTIME"].freshness_uses_source_timestamp is False
        lot = await ntpc_lot(session, "110014")
        assert lot.city == "新北市" and lot.source_id == sources["NEW_TAIPEI_STATIC"].id
        raw = (await session.scalars(select(RawParkingRecord).where(RawParkingRecord.external_id == "110014"))).first()
        assert raw.raw_rate_text.startswith("小型車計時20元") and raw.payload["TYPE"] == "1"
        batch = await session.get(RawImportBatch, static.batch_id)
        assert batch.raw_payload == fixture("static_sample.json") and batch.source_updated_at is None

        rules = (await session.scalars(select(ParkingRule).where(ParkingRule.parking_id == lot.id))).all()
        assert all(r.source_record_id.startswith("new_taipei:rule:") for r in rules)
        assert all(r.yellow_plate_allowed is None and r.red_plate_allowed is None for r in rules)

        detail = await ParkingRepository(session).detail(lot.id)
        compat = ParkingCompatibilityService()
        statuses = {z.facts.space_type: compat.evaluate(detail.facts, z.facts, "RED", NOW).status for z in detail.zones}
        assert statuses == {"CAR_SHARED": "UNKNOWN", "HEAVY_ONLY": "UNKNOWN"}
        car = next(z for z in detail.zones if z.facts.space_type == "CAR_SHARED")
        availability = resolve_availability(car.realtime, NOW + timedelta(seconds=60))
        assert availability["status"] == "AVAILABLE" and availability["available"] == 18
        # No common upstream update instant exists, so static capacity is never a realtime total.
        assert availability["total"] is None
        assert availability["freshness"]["status"] == "FRESH"
        stale = resolve_availability(car.realtime, NOW + timedelta(minutes=10))
        assert stale["status"] == "AVAILABLE" and stale["freshness"]["status"] == "STALE"

        unknown = await ntpc_lot(session, "010056")
        car_zone = (await session.scalars(select(ParkingZone).where(ParkingZone.parking_id == unknown.id))).one()
        observation = (
            await session.scalars(select(ParkingRealtime).where(ParkingRealtime.zone_id == car_zone.id))
        ).one()
        assert observation.status == "UNKNOWN" and observation.available_spaces is None


async def test_reimport_is_idempotent(live):
    session, pipeline = live
    await pipeline.ingest(ntpc())
    await pipeline.ingest(ntpc("realtime"))
    async with session.begin():
        before = {m: await ids(session, m) for m in (ParkingLot, ParkingZone, ParkingRule, ParkingRate)}
        observations = await session.scalar(select(func.count()).select_from(ParkingRealtime))
    again = await pipeline.ingest(ntpc(fetched_at=NOW + timedelta(hours=1)))
    same = await pipeline.ingest(ntpc("realtime"))
    assert again.status == "SUCCEEDED"
    assert same.status == "SUCCEEDED"
    async with session.begin():
        for model, original in before.items():
            assert await ids(session, model) == original
        # Re-running the identical realtime fetch instant deduplicates observations.
        assert await session.scalar(select(func.count()).select_from(ParkingRealtime)) == observations
        open_rates = await session.scalar(
            select(func.count()).select_from(ParkingRate).where(ParkingRate.effective_to.is_(None))
        )
        assert open_rates == len(before[ParkingRate])


async def test_source_failures_do_not_corrupt_previous_normalized_data(live):
    session, pipeline = live
    await pipeline.ingest(ntpc())
    async with session.begin():
        lot = await ntpc_lot(session, "110052")
        lot_id, fetched = lot.id, lot.fetched_at
        zones = {z.id: (z.capacity, z.source_active) for z in (await session.scalars(select(ParkingZone))).all()}

    failed = await pipeline.record_download_failure(
        "static", DownloadError("HTTP_RETRYABLE", "Source HTTP 503", fetched_at=NOW + timedelta(hours=1))
    )
    envelope = await pipeline.ingest(ntpc(payload={"pages": "broken"}, fetched_at=NOW + timedelta(hours=2)))
    # A truncated paged snapshot (one surviving lot) must not retire the others.
    truncated = await pipeline.ingest(
        ntpc(payload={"pages": [fixture("static_sample.json")["pages"][0][:1]]}, fetched_at=NOW + timedelta(hours=3))
    )
    assert failed.status == envelope.status == "FAILED"
    assert truncated.status == "SUCCEEDED"
    async with session.begin():
        batch = await session.get(RawImportBatch, truncated.batch_id)
        assert batch.error_summary["warnings"] == {"RECONCILE_SKIPPED_INCOMPLETE_SNAPSHOT": 1}
        await session.refresh(lot := await session.get(ParkingLot, lot_id))
        assert lot.fetched_at == fetched
        current = {z.id: (z.capacity, z.source_active) for z in (await session.scalars(select(ParkingZone))).all()}
        assert current == zones


async def test_complete_snapshot_still_retires_absent_lots(live):
    session, pipeline = live
    await pipeline.ingest(ntpc())
    payload = fixture("static_sample.json")
    payload["pages"][0] = [r for r in payload["pages"][0] if r["ID"] != "060145"]
    result = await pipeline.ingest(ntpc(payload=payload, fetched_at=NOW + timedelta(hours=1)))
    assert result.status == "SUCCEEDED"
    async with session.begin():
        gone = await ntpc_lot(session, "060145")
        zones = (await session.scalars(select(ParkingZone).where(ParkingZone.parking_id == gone.id))).all()
        assert zones and all(z.source_active is False and z.capacity is None for z in zones)


async def test_conflicting_duplicate_ids_fail_without_choosing_a_row(live):
    session, pipeline = live
    payload = fixture("static_sample.json")
    first = payload["pages"][0][0]
    payload["pages"][0].append({**first, "TOTALCAR": "0"})
    payload["pages"][0].append(copy.deepcopy(payload["pages"][0][1]))
    result = await pipeline.ingest(ntpc(payload=payload))
    assert (result.status, result.normalized, result.failed) == ("PARTIALLY_SUCCEEDED", 6, 2)
    async with session.begin():
        assert (await session.scalars(select(ParkingLot).where(ParkingLot.external_id == first["ID"]))).first() is None


async def test_known_count_without_car_zone_is_a_record_failure_but_unknown_is_skipped(live):
    session, pipeline = live
    await pipeline.ingest(ntpc())
    rows = [{"ID": "010189", "AVAILABLECAR": "-9"}, {"ID": "010189b", "AVAILABLECAR": "3"}]
    payload = fixture("static_sample.json")
    payload["pages"][0].append({**payload["pages"][0][4], "ID": "010189b"})
    await pipeline.ingest(ntpc(payload=payload))
    result = await pipeline.ingest(ntpc("realtime", payload=[rows[0]]))
    assert (result.status, result.failed) == ("SUCCEEDED", 0)
    result = await pipeline.ingest(ntpc("realtime", payload=[rows[1]], fetched_at=NOW + timedelta(seconds=5)))
    assert (result.status, result.failed) == ("FAILED", 1)
    async with session.begin():
        raw = (
            await session.scalars(select(RawParkingRecord).where(RawParkingRecord.batch_id == result.batch_id))
        ).one()
        assert raw.error_code == "ZONE_NOT_IMPORTED"


async def test_cli_replays_new_taipei_fixtures(live, tmp_path, monkeypatch, capsys):
    session, _ = live
    from app.ingestion import cli

    class BorrowedSession:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    class DummyEngine:
        async def dispose(self):
            pass

    monkeypatch.setattr(cli, "get_sessionmaker", lambda: BorrowedSession)
    monkeypatch.setattr(cli, "get_db_engine", DummyEngine)
    code = await run(
        parser().parse_args(
            [
                "--city",
                "new_taipei",
                "--feed",
                "all",
                "--static-file",
                str(FIXTURES / "static_sample.json"),
                "--realtime-file",
                str(FIXTURES / "realtime_sample.json"),
                "--fetched-at",
                NOW.isoformat(),
                "--no-cache",
            ]
        )
    )
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == 0 and [r["status"] for r in results] == ["SUCCEEDED", "SUCCEEDED"]
    async with session.begin():
        assert await session.scalar(select(func.count()).select_from(ParkingLot)) == 6


async def test_same_nearby_api_returns_taipei_and_new_taipei_records(live):
    session, ntpc_pipeline = live
    taipei = ParkingIngestionPipeline(session, TaipeiParkingAdapter())
    taipei_row = {
        "id": "TPE-M7",
        "name": "臺北測試停車場",
        "area": "松山區",
        "tw97x": "306457.778",
        "tw97y": "2772361.517",
        "totalcar": 10,
        "totallargemotor": "3",
    }
    payload = {"data": {"UPDATETIME": TAIPEI_STAMP, "park": [taipei_row]}}
    await taipei.ingest(FeedSnapshot("static", payload, NOW, TaipeiParkingAdapter().source_updated_at(payload)))
    near = fixture("static_sample.json")
    # Synthetic coordinates ~500 m from the Taipei lot so one radius covers both cities.
    near["pages"][0][1] |= {"TW97X": "306900", "TW97Y": "2772600"}
    await ntpc_pipeline.ingest(ntpc(payload=near))

    app = create_app()
    app.state.clock = lambda: NOW

    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    query = {"lat": 25.0585, "lng": 121.5596, "radius": 1500, "vehicle": "RED"}
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        confirmed = (await client.get("/api/v1/parking/nearby", params=query)).json()
        included = (await client.get("/api/v1/parking/nearby", params={**query, "include_unknown": True})).json()

    assert [item["name"] for item in confirmed["items"]] == ["臺北測試停車場"]
    names = {item["name"]: item for item in included["items"]}
    assert set(names) == {"臺北測試停車場", "忠厚市場地下停車場"}
    ntpc_item = names["忠厚市場地下停車場"]
    assert ntpc_item["compatibility"]["status"] == "UNKNOWN" and ntpc_item["ranking_group"] == 1
    assert {z["compatibility"]["status"] for z in ntpc_item["zones"]} == {"UNKNOWN"}
    assert names["臺北測試停車場"]["compatibility"]["status"] == "ALLOWED"
