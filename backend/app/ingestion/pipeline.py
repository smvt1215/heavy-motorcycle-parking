"""Durable raw capture, isolated record validation, and atomic normalized writes."""

import base64
import json
import math
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.base import BaseParkingAdapter
from app.ingestion.contracts import FeedSnapshot, ImportResult, RecordError
from app.ingestion.downloader import DownloadError, ParkingDownloader
from app.ingestion.writer import ParkingIngestionWriter
from app.models import ImportBatchStatus, RawImportBatch, RawParkingRecord, RawRecordStatus


class ParkingIngestionPipeline:
    def __init__(self, session: AsyncSession, adapter: BaseParkingAdapter, *, cache: Any = None):
        self.session, self.adapter, self.cache = session, adapter, cache
        self.source = adapter.source
        self.writer = ParkingIngestionWriter(session, self.source)

    def _record_id(self, record: Any) -> str | None:
        try:
            value = self.adapter.record_key(record)
        except Exception:  # noqa: BLE001 - bookkeeping must never fail the batch
            return None
        return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 240 else None

    async def download_and_ingest(self, kind: str, downloader: ParkingDownloader) -> ImportResult:
        policy = self.source.feeds[kind]
        try:
            download = await downloader.fetch(policy)
        except DownloadError as exc:
            return await self.record_download_failure(kind, exc)
        return await self.ingest(
            FeedSnapshot(kind, download.payload, download.fetched_at, self.adapter.source_updated_at(download.payload))
        )

    async def record_download_failure(self, kind: str, error: DownloadError) -> ImportResult:
        async with self.session.begin():
            sources = await self.writer.ensure_sources()
            batch = RawImportBatch(
                source_id=sources[kind],
                feed_kind=kind,
                raw_payload=_stored_payload(error.evidence),
                fetched_at=error.fetched_at,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                status=ImportBatchStatus.FAILED,
                total_records=0,
                normalized_records=0,
                failed_records=0,
                error_summary={"errors": {error.code: 1}, "message": str(error)},
            )
            self.session.add(batch)
            await self.session.flush()
            batch_id = batch.id
        return ImportResult(batch_id, ImportBatchStatus.FAILED, 0, 0, 0)

    async def ingest(self, snapshot: FeedSnapshot) -> ImportResult:
        if snapshot.kind not in self.source.feeds or snapshot.fetched_at.utcoffset() is None:
            raise ValueError("Known feed and absolute fetched_at are required")
        if snapshot.source_updated_at is not None and snapshot.source_updated_at.utcoffset() is None:
            raise ValueError("source_updated_at must be an absolute instant")
        warnings = {}
        evidence = snapshot
        if snapshot.source_updated_at is not None and snapshot.source_updated_at > snapshot.fetched_at:
            # Raw evidence keeps the upstream value, but a future source clock is
            # never used for monotonic ordering or freshness of normalized facts.
            snapshot = replace(snapshot, source_updated_at=None)
            warnings["FUTURE_SOURCE_UPDATED_AT"] = 1
        envelope_error = None
        try:
            records = self.adapter.records(snapshot.payload)
        except RecordError as exc:
            records, envelope_error = [], exc
        # Raw evidence commits independently. Even a later integrity/connection
        # failure cannot erase the received envelope and its row-level values.
        async with self.session.begin():
            sources = await self.writer.ensure_sources()
            batch = RawImportBatch(
                source_id=sources[snapshot.kind],
                feed_kind=snapshot.kind,
                raw_payload=_stored_payload(snapshot.payload),
                source_updated_at=evidence.source_updated_at,
                fetched_at=snapshot.fetched_at,
                started_at=datetime.now(UTC),
                status=ImportBatchStatus.RUNNING,
                total_records=len(records),
                normalized_records=0,
                failed_records=0,
            )
            self.session.add(batch)
            await self.session.flush()
            batch_id = batch.id
            raw_ids = []
            for record in records:
                raw = RawParkingRecord(
                    batch_id=batch_id,
                    source_id=sources[snapshot.kind],
                    external_id=_safe_text(self._record_id(record)),
                    record_type=snapshot.kind,
                    payload=_stored_payload(record if isinstance(record, dict) else {"_value": record}),
                    raw_rate_text=_safe_text(self.adapter.raw_rate_text(record)),
                    source_updated_at=evidence.source_updated_at,
                    fetched_at=snapshot.fetched_at,
                    status=RawRecordStatus.PENDING,
                )
                self.session.add(raw)
                await self.session.flush()
                raw_ids.append(raw.id)

        if envelope_error is not None:
            return await self._finish(batch_id, 0, 0, {envelope_error.code: 1}, force_failed=True, warnings=warnings)
        normalized, failed, errors = 0, 0, Counter()
        conflicts = _conflicting_ids(records, self._record_id)
        seen = set()
        try:
            async with self.session.begin():
                # Both feeds share this transaction lock. Stable child identities
                # and monotonic update checks must also hold for overlapping workers.
                await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": self.source.lock_key})
                await self.writer.narrow_policy_history()
                for index, (record, raw_id) in enumerate(zip(records, raw_ids, strict=True)):
                    if index:
                        # Persist the previous record's statuses, then drop loaded ORM
                        # objects: one long transaction must not make every autoflush
                        # scan an ever-growing identity map (O(n^2) for large feeds).
                        await self.session.flush()
                        self.session.expunge_all()
                    raw = await self.session.get(RawParkingRecord, raw_id)
                    external_id = self._record_id(record)
                    try:
                        if _unsupported_json(record):
                            raise RecordError("UNSUPPORTED_JSON_VALUE", "Record contains a value unsupported by JSONB")
                        if external_id in conflicts:
                            raise RecordError("DUPLICATE_ID_CONFLICT", "Conflicting rows share an upstream lot ID")
                        if external_id is not None and external_id in seen:
                            # Exact duplicate raw rows remain evidence, but have one normalized identity.
                            raw.status = RawRecordStatus.NORMALIZED
                            normalized += 1
                            continue
                        async with self.session.begin_nested():
                            if snapshot.kind == "static":
                                await self.writer.write_lot(
                                    self.adapter.normalize_static(record), snapshot, sources["static"]
                                )
                            else:
                                await self.writer.write_realtime(
                                    self.adapter.normalize_realtime(record),
                                    snapshot,
                                    sources["realtime"],
                                    sources["static"],
                                )
                            await self.session.flush()
                        raw.status = RawRecordStatus.NORMALIZED
                        normalized += 1
                        if external_id is not None:
                            seen.add(external_id)
                    except (RecordError, IntegrityError, DataError) as exc:
                        code = (
                            exc.code
                            if isinstance(exc, RecordError)
                            else "INTEGRITY_ERROR"
                            if isinstance(exc, IntegrityError)
                            else "DATA_ERROR"
                        )
                        raw.status, raw.error_code = RawRecordStatus.INVALID, code
                        raw.error_message = (
                            str(exc)
                            if isinstance(exc, RecordError)
                            else "Record value rejected by database"
                            if isinstance(exc, DataError)
                            else "Record violates database integrity"
                        )
                        errors[code] += 1
                        failed += 1
                if snapshot.kind == "static":
                    present = {key for record in records if (key := self._record_id(record)) is not None}
                    if await self._snapshot_complete_enough(present, sources["static"]):
                        await self.writer.reconcile_missing_lots(present, snapshot, sources["static"])
                    else:
                        warnings["RECONCILE_SKIPPED_INCOMPLETE_SNAPSHOT"] = 1
        except Exception:
            # Fatal failures roll back normalized writes as a unit. Mark every raw
            # row failed instead of claiming successful writes that were rolled back.
            await self.session.rollback()
            async with self.session.begin():
                rows = (
                    await self.session.scalars(select(RawParkingRecord).where(RawParkingRecord.batch_id == batch_id))
                ).all()
                for row in rows:
                    row.status, row.error_code = RawRecordStatus.INVALID, "BATCH_ABORTED"
                    row.error_message = "Normalized transaction rolled back; raw evidence retained"
            await self._finish(
                batch_id, 0, len(records), {"BATCH_ABORTED": len(records)}, force_failed=True, warnings=warnings
            )
            raise
        result = await self._finish(batch_id, normalized, failed, dict(errors), warnings=warnings)
        if self.cache is not None:
            try:
                await self.cache.set(
                    f"ingestion:{self.source.key}:{snapshot.kind}:latest",
                    json.dumps(
                        {
                            "batch_id": result.batch_id,
                            "status": result.status,
                            "fetched_at": snapshot.fetched_at.isoformat(),
                            "normalized": result.normalized,
                            "failed": result.failed,
                        }
                    ),
                    ex=self.source.feeds[snapshot.kind].freshness_seconds,
                )
            except Exception:
                async with self.session.begin():
                    batch = await self.session.get(RawImportBatch, batch_id)
                    batch.error_summary = {**(batch.error_summary or {}), "cache": "UNAVAILABLE"}
        return result

    async def _snapshot_complete_enough(self, present: set[str], static_source_id: int) -> bool:
        """Guard paged sources: a truncated snapshot must not retire valid lots.

        Compares unique lot IDs with lots that currently have an active source zone.
        Without a configured ratio every valid snapshot is complete (Taipei semantics).
        """
        ratio = self.source.feeds["static"].reconcile_min_ratio
        if ratio is None:
            return True
        baseline = await self.writer.active_lot_count(static_source_id)
        return len(present) >= ratio * baseline

    async def _finish(self, batch_id, normalized, failed, errors, *, force_failed=False, warnings=None) -> ImportResult:
        status = (
            ImportBatchStatus.FAILED
            if force_failed or (failed and not normalized)
            else ImportBatchStatus.PARTIALLY_SUCCEEDED
            if failed
            else ImportBatchStatus.SUCCEEDED
        )
        async with self.session.begin():
            batch = await self.session.get(RawImportBatch, batch_id)
            batch.status, batch.finished_at = status, datetime.now(UTC)
            batch.normalized_records, batch.failed_records = normalized, failed
            summary = {**({"errors": errors} if errors else {}), **({"warnings": warnings} if warnings else {})}
            batch.error_summary = summary or None
            total = batch.total_records
        return ImportResult(batch_id, status, total, normalized, failed)


def _conflicting_ids(records: list[Any], record_id) -> set[str]:
    representations, conflicts = {}, set()
    for record in records:
        external_id = record_id(record)
        if external_id is None:
            continue
        representation = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if external_id in representations and representations[external_id] != representation:
            conflicts.add(external_id)
        representations[external_id] = representation
    return conflicts


def _safe_text(value: Any) -> str | None:
    return value if isinstance(value, str) and not _unsupported_json(value) else None


def _unsupported_json(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_unsupported_json(key) or _unsupported_json(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_unsupported_json(item) for item in value)
    return False


def _stored_payload(value: Any) -> Any:
    if not _unsupported_json(value):
        return value
    # ASCII JSON inside base64 preserves NUL/lone-surrogate values reversibly,
    # without asking PostgreSQL JSONB to decode unsupported Unicode escapes.
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return {"encoding": "base64-json-ascii", "data": base64.b64encode(encoded).decode("ascii")}
