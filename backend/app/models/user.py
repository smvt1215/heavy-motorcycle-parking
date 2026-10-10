from datetime import datetime

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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin
from app.models.enums import (
    ReportStatus,
    ReportType,
    VehicleType,
    report_status_enum,
    report_type_enum,
    vehicle_type_enum,
)


class User(TimestampMixin, Base):
    """Local profile keyed by the identity provider's token subject; no credentials stored."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_subject", name="uq_users_auth_subject"),
        CheckConstraint("role IN ('USER', 'MODERATOR')", name="ck_users_role"),
        CheckConstraint(
            "preferred_vehicle IS NULL OR preferred_vehicle IN ('NORMAL_HEAVY', 'LARGE_HEAVY')",
            name="ck_users_preferred_vehicle_heavy",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auth_subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(16), server_default=text("'USER'"))
    # A client-side default only; selected-vehicle endpoints never read it.
    legacy_preferred_vehicle: Mapped[str | None] = mapped_column(String(16))
    preferred_vehicle: Mapped[VehicleType | None] = mapped_column(vehicle_type_enum)


class AccessToken(CreatedAtMixin, Base):
    """Opaque bearer token. Only its SHA-256 digest is stored, never the token itself."""

    __tablename__ = "access_tokens"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_access_tokens_user_id_users", ondelete="CASCADE"),
        UniqueConstraint("token_hash", name="uq_access_tokens_token_hash"),
        CheckConstraint("expires_at > created_at", name="ck_access_tokens_expires_after_created"),
        Index("ix_access_tokens_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserVehicle(TimestampMixin, Base):
    """Saved vehicle. Never used to infer the `vehicle` query parameter."""

    __tablename__ = "user_vehicles"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_vehicles_user_id_users", ondelete="CASCADE"),
        Index("ix_user_vehicles_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    legacy_vehicle_type: Mapped[str | None] = mapped_column(String(16))
    vehicle_type: Mapped[VehicleType | None] = mapped_column(vehicle_type_enum)
    nickname: Mapped[str | None] = mapped_column(String(100))


class Favorite(CreatedAtMixin, Base):
    __tablename__ = "favorites"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_favorites_user_id_users", ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_favorites_parking_id_parking_lots", ondelete="CASCADE"
        ),
        UniqueConstraint("user_id", "parking_id", name="uq_favorites_user_id_parking_id"),
        Index("ix_favorites_parking_id", "parking_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    parking_id: Mapped[int] = mapped_column(Integer)


class UserReport(TimestampMixin, Base):
    """Community report; stays separately attributable and never overwrites official data."""

    __tablename__ = "user_reports"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_reports_user_id_users", ondelete="SET NULL"),
        ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name="fk_user_reports_resolved_by_user_id_users",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_user_reports_parking_id_parking_lots", ondelete="CASCADE"
        ),
        # Same-lot zone guard. NO ACTION (checked at statement end) so a lot-delete cascade can remove both.
        ForeignKeyConstraint(
            ["zone_id", "parking_id"],
            ["parking_zones.id", "parking_zones.parking_id"],
            name="fk_user_reports_zone_id_parking_id_parking_zones",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= created_at", name="ck_user_reports_resolved_after_created"
        ),
        Index("ix_user_reports_parking_id", "parking_id"),
        Index("ix_user_reports_zone_id", "zone_id"),
        Index("ix_user_reports_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    parking_id: Mapped[int] = mapped_column(Integer)
    zone_id: Mapped[int | None] = mapped_column(Integer)
    report_type: Mapped[ReportType] = mapped_column(report_type_enum)
    status: Mapped[ReportStatus] = mapped_column(report_status_enum, server_default=text("'PENDING'"))
    description: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[int | None] = mapped_column(Integer)


class ReportPhoto(CreatedAtMixin, Base):
    __tablename__ = "report_photos"
    __table_args__ = (
        ForeignKeyConstraint(
            ["report_id"], ["user_reports.id"], name="fk_report_photos_report_id_user_reports", ondelete="CASCADE"
        ),
        UniqueConstraint("storage_key", name="uq_report_photos_storage_key"),
        CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_report_photos_byte_size_nonnegative"),
        CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_report_photos_dimensions_positive",
        ),
        Index("ix_report_photos_report_id", "report_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(Integer)
    # Object-storage key; files themselves live in S3-compatible storage.
    storage_key: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str | None] = mapped_column(String(100))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
