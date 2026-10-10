"""New Taipei roadside static cells from the complete CSV document."""

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.ingestion.contracts import FeedSnapshot
from app.ingestion.downloader import DownloadError, ParkingDownloader, decode_csv, decode_document
from app.ingestion.new_taipei_roadside import NewTaipeiRoadsideAdapter
from app.ingestion.pipeline import ParkingIngestionPipeline
from app.models import ParkingLot, ParkingRule, RawImportBatch

NOW = datetime(2026, 10, 10, 2, tzinfo=UTC)
CSV_BYTES = (Path(__file__).parent / "fixtures/new_taipei/roadside_real.csv").read_bytes()
ADAPTER = NewTaipeiRoadsideAdapter()
STATIC = ADAPTER.source.feeds["static"]


def test_static_feed_is_the_complete_csv_file_and_realtime_stays_paged_json():
    assert STATIC.url.endswith("/csv/file") and STATIC.document_format == "csv"
    assert STATIC.page_size is None and STATIC.reconcile_min_ratio == 0.8
    realtime = ADAPTER.source.feeds["realtime"]
    assert realtime.url.endswith("/json") and realtime.page_size == 1000 and realtime.document_format == "json"


def test_csv_envelope_keeps_header_rows_and_exact_digest():
    envelope = decode_csv(CSV_BYTES, NOW)
    assert envelope["format"] == "csv" and envelope["sha256"] == hashlib.sha256(CSV_BYTES).hexdigest()
    assert envelope["header"][:3] == ["id", "cellid", "name"]
    assert len(envelope["rows"]) == 10
    assert all(isinstance(value, str) for row in envelope["rows"] for value in row.values())
    assert ADAPTER.records(envelope) == envelope["rows"]
    assert decode_document(CSV_BYTES, NOW, "csv") == envelope


@pytest.mark.parametrize(
    "body, message",
    [
        (b"", "no header"),
        (b"\xff\xfeid,name\n1,x\n", "UTF-8"),
        (b"id,name\n1,x,extra\n", "has 3 fields"),
        (b"id,name\n1\n", "has 1 fields"),
        (b"id,id\n1,2\n", "unique"),
        (b'id,name\n1,"unterminated\n', "malformed"),
    ],
)
def test_malformed_csv_fails_the_whole_document_with_raw_bytes(body, message):
    with pytest.raises(DownloadError, match=message) as caught:
        decode_csv(body, NOW)
    assert caught.value.code == "INVALID_CSV"
    assert base64.b64decode(caught.value.evidence["body_base64"]) == body
    assert caught.value.fetched_at == NOW


def test_unknown_document_format_is_rejected():
    with pytest.raises(ValueError):
        decode_document(b"{}", NOW, "xml")


async def test_downloader_fetches_the_csv_document_once():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=CSV_BYTES, headers={"content-type": "text/csv;charset=UTF-8"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        download = await ParkingDownloader(client, clock=lambda: NOW).fetch(STATIC)
    assert len(requests) == 1 and not requests[0].url.params
    assert download.payload["sha256"] == hashlib.sha256(CSV_BYTES).hexdigest()
    assert download.fetched_at == NOW


@pytest.fixture(scope="module")
def migrated():
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")


@pytest.fixture
async def db(migrated):
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        outer = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            yield session
        await outer.rollback()
    await engine.dispose()


async def test_csv_snapshot_ingests_cells_the_json_api_omits_and_rejects_conflicting_ids(db):
    envelope = decode_csv(CSV_BYTES, NOW)
    result = await ParkingIngestionPipeline(db, ADAPTER).ingest(FeedSnapshot("static", envelope, NOW, None))
    # Four rows share id 968 with different roads: none becomes a normalized cell.
    assert (result.total, result.normalized, result.failed) == (10, 6, 4)
    async with db.begin():
        batch = await db.get(RawImportBatch, result.batch_id)
        assert batch.error_summary["errors"] == {"DUPLICATE_ID_CONFLICT": 4}
        assert batch.raw_payload["sha256"] == envelope["sha256"]
        lots = {lot.external_id: lot for lot in (await db.scalars(select(ParkingLot))).all()}
        assert set(lots) == {"151148", "151162", "154800", "159680", "20", "21"}
        # 159680 is a real motorcycle cell absent from the paged JSON API on 2026-10-10.
        policies = {
            lot.external_id
            for lot in lots.values()
            for rule in (await db.scalars(select(ParkingRule).where(ParkingRule.parking_id == lot.id))).all()
            if rule.large_heavy_allowed is True
        }
    assert policies == {"151148", "151162", "159680"}
