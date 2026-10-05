from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin
from app.models.enums import RealtimeStatus, realtime_status_enum


class ParkingRealtime(CreatedAtMixin, Base):
    """Zone-scoped availability observation.

    Freshness is derived from fetched_at and source policy at read time and is never stored;
    lot-level totals are API projections and are never stored either.
    """

    __tablename__ = "parking_realtime"
    __table_args__ = (
        ForeignKeyConstraint(
            ["zone_id"], ["parking_zones.id"], name="fk_parking_realtime_zone_id_parking_zones", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["source_id"], ["data_sources.id"], name="fk_parking_realtime_source_id_data_sources", ondelete="RESTRICT"
        ),
        CheckConstraint(
            "available_spaces IS NULL OR available_spaces >= 0",
            name="ck_parking_realtime_available_spaces_nonnegative",
        ),
        CheckConstraint(
            "total_spaces IS NULL OR total_spaces >= 0", name="ck_parking_realtime_total_spaces_nonnegative"
        ),
        CheckConstraint(
            "available_spaces IS NULL OR total_spaces IS NULL OR available_spaces <= total_spaces",
            name="ck_parking_realtime_available_within_total",
        ),
        # Explicit IS NOT NULL: a bare comparison with NULL would let the CHECK pass.
        CheckConstraint(
            "status <> 'AVAILABLE' OR (available_spaces IS NOT NULL AND available_spaces > 0)",
            name="ck_parking_realtime_available_status_has_spaces",
        ),
        CheckConstraint(
            "status NOT IN ('FULL', 'CLOSED') OR (available_spaces IS NOT NULL AND available_spaces = 0)",
            name="ck_parking_realtime_full_closed_status_zero_spaces",
        ),
        Index("ix_parking_realtime_zone_id_fetched_at", "zone_id", "fetched_at"),
        Index("ix_parking_realtime_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    zone_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[RealtimeStatus] = mapped_column(realtime_status_enum)
    available_spaces: Mapped[int | None] = mapped_column(Integer)
    total_spaces: Mapped[int | None] = mapped_column(Integer)
    source_id: Mapped[int] = mapped_column(Integer)
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL => freshness UNKNOWN; FRESH can never be derived without it.
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
