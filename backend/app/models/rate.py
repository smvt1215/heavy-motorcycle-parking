from datetime import datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin
from app.models.enums import (
    RateDayType,
    RateParseStatus,
    RateType,
    VehicleType,
    rate_day_type_enum,
    rate_parse_status_enum,
    rate_type_enum,
    vehicle_type_enum,
)

MONEY = Numeric(10, 2)


class ParkingRate(TimestampMixin, Base):
    """Zone-scoped rate. Unparsed/ambiguous fields stay NULL; raw_text preserves the original."""

    __tablename__ = "parking_rates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["zone_id"], ["parking_zones.id"], name="fk_parking_rates_zone_id_parking_zones", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["source_id"], ["data_sources.id"], name="fk_parking_rates_source_id_data_sources", ondelete="RESTRICT"
        ),
        CheckConstraint("base_amount IS NULL OR base_amount >= 0", name="ck_parking_rates_base_amount_nonnegative"),
        CheckConstraint("unit_minutes IS NULL OR unit_minutes > 0", name="ck_parking_rates_unit_minutes_positive"),
        CheckConstraint("free_minutes IS NULL OR free_minutes >= 0", name="ck_parking_rates_free_minutes_nonnegative"),
        CheckConstraint(
            "daily_max_amount IS NULL OR daily_max_amount >= 0", name="ck_parking_rates_daily_max_amount_nonnegative"
        ),
        CheckConstraint(
            "parse_status <> 'PARSED' OR rate_type IS NOT NULL", name="ck_parking_rates_parsed_requires_rate_type"
        ),
        CheckConstraint(
            "parse_status = 'PARSED' OR raw_text IS NOT NULL", name="ck_parking_rates_unparsed_requires_raw_text"
        ),
        CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_from < effective_to",
            name="ck_parking_rates_effective_window_ordered",
        ),
        Index("ix_parking_rates_zone_id", "zone_id"),
        Index("ix_parking_rates_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(Integer)
    rate_type: Mapped[RateType | None] = mapped_column(rate_type_enum)
    # NULL = unspecified vehicle applicability; never confirmation of a selected
    # vehicle's price. Explicit upstream all-vehicle rates can be mapped per vehicle.
    legacy_vehicle_type: Mapped[str | None] = mapped_column(String(16))
    vehicle_type: Mapped[VehicleType | None] = mapped_column(vehicle_type_enum)
    currency: Mapped[str] = mapped_column(String(3), server_default=text("'TWD'"))
    base_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    unit_minutes: Mapped[int | None] = mapped_column(Integer)
    free_minutes: Mapped[int | None] = mapped_column(Integer)
    daily_max_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    raw_text: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    parse_status: Mapped[RateParseStatus] = mapped_column(rate_parse_status_enum)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_id: Mapped[int] = mapped_column(Integer)
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rules: Mapped[list["ParkingRateRule"]] = relationship(back_populates="rate", passive_deletes=True)


class ParkingRateRule(TimestampMixin, Base):
    __tablename__ = "parking_rate_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rate_id"], ["parking_rates.id"], name="fk_parking_rate_rules_rate_id_parking_rates", ondelete="CASCADE"
        ),
        # Local Asia/Taipei times; end < start denotes an overnight window, so no ordering check.
        CheckConstraint("(start_time IS NULL) = (end_time IS NULL)", name="ck_parking_rate_rules_time_window_complete"),
        # NULL/NULL = no local time restriction; otherwise a nonempty half-open
        # window. Python/psycopg cannot read PostgreSQL's TIME '24:00:00'.
        CheckConstraint(
            "start_time IS NULL OR (start_time < TIME '24:00:00' AND end_time < TIME '24:00:00' "
            "AND start_time <> end_time)",
            name="ck_parking_rate_rules_time_window_valid",
        ),
        CheckConstraint(
            "start_minute IS NULL OR start_minute >= 0", name="ck_parking_rate_rules_start_minute_nonnegative"
        ),
        CheckConstraint("end_minute IS NULL OR end_minute > 0", name="ck_parking_rate_rules_end_minute_positive"),
        CheckConstraint(
            "start_minute IS NULL OR end_minute IS NULL OR end_minute > start_minute",
            name="ck_parking_rate_rules_minute_range_ordered",
        ),
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_parking_rate_rules_amount_nonnegative"),
        CheckConstraint("unit_minutes IS NULL OR unit_minutes > 0", name="ck_parking_rate_rules_unit_minutes_positive"),
        CheckConstraint("max_amount IS NULL OR max_amount >= 0", name="ck_parking_rate_rules_max_amount_nonnegative"),
        Index("ix_parking_rate_rules_rate_id", "rate_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rate_id: Mapped[int] = mapped_column(Integer)
    day_type: Mapped[RateDayType] = mapped_column(rate_day_type_enum)
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    # Parking-duration range this rule covers (e.g. progressive tiers).
    start_minute: Mapped[int | None] = mapped_column(Integer)
    end_minute: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    unit_minutes: Mapped[int | None] = mapped_column(Integer)
    max_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)

    rate: Mapped[ParkingRate] = relationship(back_populates="rules")


class ParkingRateSource(CreatedAtMixin, Base):
    """Evidence record backing a rate; several sources may support one normalized rate."""

    __tablename__ = "parking_rate_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rate_id"], ["parking_rates.id"], name="fk_parking_rate_sources_rate_id_parking_rates", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["source_id"],
            ["data_sources.id"],
            name="fk_parking_rate_sources_source_id_data_sources",
            ondelete="RESTRICT",
        ),
        Index("ix_parking_rate_sources_rate_id", "rate_id"),
        Index("ix_parking_rate_sources_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rate_id: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[int] = mapped_column(Integer)
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
