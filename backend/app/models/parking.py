from datetime import datetime
from typing import Any

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    EntranceType,
    ParkingSpaceType,
    RuleKind,
    entrance_type_enum,
    parking_space_type_enum,
    rule_kind_enum,
)


def _point_geography() -> Geography:
    # GeoAlchemy mutates type.nullable on column attachment. Each column needs its
    # own type instance so the lot's NOT NULL cannot leak into nullable entrances.
    return Geography(geometry_type="POINT", srid=4326, spatial_index=False)


class ParkingLot(TimestampMixin, Base):
    __tablename__ = "parking_lots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id"], ["data_sources.id"], name="fk_parking_lots_source_id_data_sources", ondelete="RESTRICT"
        ),
        # NULL external_id rows never conflict (NULLs are distinct).
        UniqueConstraint("source_id", "external_id", name="uq_parking_lots_source_id_external_id"),
        CheckConstraint(
            "external_id IS NULL OR source_id IS NOT NULL", name="ck_parking_lots_external_id_requires_source"
        ),
        Index("ix_parking_lots_location", "location", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(64))
    district: Mapped[str | None] = mapped_column(String(64))
    # Lot center; entrances carry their own coordinates.
    location: Mapped[WKBElement] = mapped_column(_point_geography())
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    zones: Mapped[list["ParkingZone"]] = relationship(back_populates="lot", passive_deletes=True)


class ParkingZone(TimestampMixin, Base):
    __tablename__ = "parking_zones"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_parking_zones_parking_id_parking_lots", ondelete="CASCADE"
        ),
        # Target for composite (zone_id, parking_id) FKs that keep zone-scoped rows in the same lot.
        UniqueConstraint("id", "parking_id", name="uq_parking_zones_id_parking_id"),
        CheckConstraint("capacity IS NULL OR capacity >= 0", name="ck_parking_zones_capacity_nonnegative"),
        Index("ix_parking_zones_parking_id", "parking_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parking_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(200))
    space_type: Mapped[ParkingSpaceType] = mapped_column(parking_space_type_enum)
    capacity: Mapped[int | None] = mapped_column(Integer)

    lot: Mapped[ParkingLot] = relationship(back_populates="zones")


class ParkingRule(TimestampMixin, Base):
    """Normalized permission fact. Equal-precedence contradictions intentionally coexist."""

    __tablename__ = "parking_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_parking_rules_parking_id_parking_lots", ondelete="CASCADE"
        ),
        # MATCH SIMPLE: NULL zone_id (lot-wide) skips the check; otherwise the zone must belong to parking_id.
        ForeignKeyConstraint(
            ["zone_id", "parking_id"],
            ["parking_zones.id", "parking_zones.parking_id"],
            name="fk_parking_rules_zone_id_parking_id_parking_zones",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_id"], ["data_sources.id"], name="fk_parking_rules_source_id_data_sources", ondelete="RESTRICT"
        ),
        CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_from < effective_to",
            name="ck_parking_rules_effective_window_ordered",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_parking_rules_confidence_range"
        ),
        Index("ix_parking_rules_parking_id", "parking_id"),
        Index("ix_parking_rules_zone_id", "zone_id"),
        Index("ix_parking_rules_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parking_id: Mapped[int] = mapped_column(Integer)
    zone_id: Mapped[int | None] = mapped_column(Integer)
    # Tri-state permissions: NULL = unknown. No defaults on purpose.
    green_plate_allowed: Mapped[bool | None] = mapped_column(Boolean)
    white_plate_allowed: Mapped[bool | None] = mapped_column(Boolean)
    yellow_plate_allowed: Mapped[bool | None] = mapped_column(Boolean)
    red_plate_allowed: Mapped[bool | None] = mapped_column(Boolean)
    car_allowed: Mapped[bool | None] = mapped_column(Boolean)
    # Assigned by adapters from explicit source policy; never inferred.
    rule_kind: Mapped[RuleKind] = mapped_column(rule_kind_enum)
    authority_priority: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Local schedule constraints, interpreted in Asia/Taipei for v1.
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Descriptive only; never used for precedence or conflict resolution.
    confidence: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int] = mapped_column(Integer)
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParkingEntrance(TimestampMixin, Base):
    __tablename__ = "parking_entrances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parking_id"],
            ["parking_lots.id"],
            name="fk_parking_entrances_parking_id_parking_lots",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_id"], ["data_sources.id"], name="fk_parking_entrances_source_id_data_sources", ondelete="RESTRICT"
        ),
        Index("ix_parking_entrances_parking_id", "parking_id"),
        Index("ix_parking_entrances_source_id", "source_id"),
        Index("ix_parking_entrances_location", "location", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parking_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(200))
    entrance_type: Mapped[EntranceType | None] = mapped_column(entrance_type_enum)
    # NULL when the source has no usable entrance coordinate.
    location: Mapped[WKBElement | None] = mapped_column(_point_geography())
    # Tri-state: TRUE=ALLOWED, FALSE=NOT_ALLOWED, NULL=UNKNOWN.
    heavy_motorcycle_access: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int] = mapped_column(Integer)
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParkingFacility(TimestampMixin, Base):
    __tablename__ = "parking_facilities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parking_id"],
            ["parking_lots.id"],
            name="fk_parking_facilities_parking_id_parking_lots",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_id"], ["data_sources.id"], name="fk_parking_facilities_source_id_data_sources", ondelete="RESTRICT"
        ),
        UniqueConstraint("parking_id", "code", name="uq_parking_facilities_parking_id_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parking_id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(Integer)
