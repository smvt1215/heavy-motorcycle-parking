from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin
from app.models.enums import (
    ImportBatchStatus,
    RawRecordStatus,
    import_batch_status_enum,
    raw_record_status_enum,
)


class RawImportBatch(TimestampMixin, Base):
    __tablename__ = "raw_import_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id"], ["data_sources.id"], name="fk_raw_import_batches_source_id_data_sources", ondelete="RESTRICT"
        ),
        # Target for raw_parking_records (batch_id, source_id) so a record cannot claim another source's batch.
        UniqueConstraint("id", "source_id", name="uq_raw_import_batches_id_source_id"),
        CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at",
            name="ck_raw_import_batches_finished_after_started",
        ),
        CheckConstraint(
            "total_records >= 0 AND normalized_records >= 0 AND failed_records >= 0",
            name="ck_raw_import_batches_counters_nonnegative",
        ),
        Index("ix_raw_import_batches_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[ImportBatchStatus] = mapped_column(import_batch_status_enum, server_default=text("'PENDING'"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_records: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    normalized_records: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_records: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    feed_kind: Mapped[str | None] = mapped_column(String(32))
    raw_payload: Mapped[Any | None] = mapped_column(JSONB)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RawParkingRecord(CreatedAtMixin, Base):
    """Verbatim upstream record. Duplicates and invalid rows are retained as evidence."""

    __tablename__ = "raw_parking_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "source_id"],
            ["raw_import_batches.id", "raw_import_batches.source_id"],
            name="fk_raw_parking_records_batch_id_source_id_raw_import_batches",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_id"],
            ["data_sources.id"],
            name="fk_raw_parking_records_source_id_data_sources",
            ondelete="RESTRICT",
        ),
        Index("ix_raw_parking_records_batch_id", "batch_id"),
        Index("ix_raw_parking_records_source_id_external_id", "source_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[int] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(255))
    record_type: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    raw_rate_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RawRecordStatus] = mapped_column(raw_record_status_enum, server_default=text("'PENDING'"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
