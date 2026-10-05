"""core schema

Revision ID: 002_core_schema
Revises: 001_bootstrap
Create Date: 2026-10-05 10:00:00.000000

Self-contained: does not import app.models, so later model changes cannot alter this revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002_core_schema"
down_revision: str | None = "001_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: dict[str, tuple[str, ...]] = {
    "data_source_type": ("GOVERNMENT", "OPERATOR", "COMMUNITY", "MANUAL"),
    "parking_space_type": ("HEAVY_ONLY", "MOTO_SHARED", "CAR_SHARED", "LIGHT_MOTO_ONLY"),
    "vehicle_type": ("GREEN", "WHITE", "YELLOW", "RED", "CAR"),
    "parking_rule_kind": ("BASELINE", "EXCEPTION"),
    "parking_rate_type": (
        "FREE",
        "HOURLY",
        "PER_ENTRY",
        "TIME_BLOCK",
        "PROGRESSIVE",
        "FLAT",
        "DAILY",
        "MONTHLY",
        "CUSTOM",
    ),
    "rate_parse_status": ("PARSED", "PARTIALLY_PARSED", "RAW_ONLY", "INVALID"),
    "rate_day_type": ("ALL", "WEEKDAY", "WEEKEND", "HOLIDAY", "SPECIAL"),
    "realtime_status": ("AVAILABLE", "FULL", "UNKNOWN", "CLOSED"),
    "parking_entrance_type": ("VEHICLE", "PEDESTRIAN", "MIXED"),
    "import_batch_status": ("PENDING", "RUNNING", "SUCCEEDED", "PARTIALLY_SUCCEEDED", "FAILED"),
    "raw_record_status": ("PENDING", "NORMALIZED", "INVALID"),
    "user_report_type": (
        "PERMISSION_CORRECTION",
        "RATE_CORRECTION",
        "AVAILABILITY_CORRECTION",
        "ENTRANCE_CORRECTION",
        "LOCATION_CORRECTION",
        "CLOSURE",
        "OTHER",
    ),
    "user_report_status": ("PENDING", "UNDER_REVIEW", "ACCEPTED", "REJECTED"),
}

# Tables in creation (dependency) order; dropped in reverse.
TABLES: tuple[str, ...] = (
    "data_sources",
    "parking_lots",
    "parking_zones",
    "parking_rules",
    "parking_rates",
    "parking_rate_rules",
    "parking_rate_sources",
    "parking_realtime",
    "parking_entrances",
    "parking_facilities",
    "raw_import_batches",
    "raw_parking_records",
    "users",
    "user_vehicles",
    "favorites",
    "user_reports",
    "report_photos",
)


def _enum(name: str) -> postgresql.ENUM:
    # Types are created/dropped explicitly below, never implicitly by table DDL.
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _point(name: str, nullable: bool) -> sa.Column:
    return sa.Column(name, Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=nullable)


def _source_fk(table: str, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["source_id"], ["data_sources.id"], name=f"fk_{table}_source_id_data_sources", ondelete=ondelete
    )


def _provenance(source_nullable: bool = False, verified: bool = True) -> list[sa.Column]:
    columns = [
        sa.Column("source_id", sa.Integer(), nullable=source_nullable),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        _ts("source_updated_at"),
        _ts("fetched_at"),
    ]
    if verified:
        columns.append(_ts("verified_at"))
    return columns


def _window_check(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "effective_from IS NULL OR effective_to IS NULL OR effective_from < effective_to",
        name=f"ck_{table}_effective_window_ordered",
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=False)

    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_type", _enum("data_source_type"), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("license", sa.Text(), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("freshness_seconds", sa.Integer(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_data_sources"),
        sa.UniqueConstraint("code", name="uq_data_sources_code"),
        sa.CheckConstraint(
            "freshness_seconds IS NULL OR freshness_seconds > 0",
            name="ck_data_sources_freshness_seconds_positive",
        ),
    )

    op.create_table(
        "parking_lots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column("district", sa.String(length=64), nullable=True),
        _point("location", nullable=False),
        _ts("source_updated_at"),
        _ts("fetched_at"),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_lots"),
        _source_fk("parking_lots"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_parking_lots_source_id_external_id"),
        sa.CheckConstraint(
            "external_id IS NULL OR source_id IS NOT NULL", name="ck_parking_lots_external_id_requires_source"
        ),
    )
    op.create_index("ix_parking_lots_location", "parking_lots", ["location"], postgresql_using="gist")

    op.create_table(
        "parking_zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parking_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("space_type", _enum("parking_space_type"), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_zones"),
        sa.ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_parking_zones_parking_id_parking_lots", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("id", "parking_id", name="uq_parking_zones_id_parking_id"),
        sa.CheckConstraint("capacity IS NULL OR capacity >= 0", name="ck_parking_zones_capacity_nonnegative"),
    )
    op.create_index("ix_parking_zones_parking_id", "parking_zones", ["parking_id"])

    op.create_table(
        "parking_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parking_id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("green_plate_allowed", sa.Boolean(), nullable=True),
        sa.Column("white_plate_allowed", sa.Boolean(), nullable=True),
        sa.Column("yellow_plate_allowed", sa.Boolean(), nullable=True),
        sa.Column("red_plate_allowed", sa.Boolean(), nullable=True),
        sa.Column("car_allowed", sa.Boolean(), nullable=True),
        sa.Column("rule_kind", _enum("parking_rule_kind"), nullable=False),
        sa.Column("authority_priority", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _ts("effective_from"),
        _ts("effective_to"),
        sa.Column("schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_provenance(),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_rules"),
        sa.ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_parking_rules_parking_id_parking_lots", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["zone_id", "parking_id"],
            ["parking_zones.id", "parking_zones.parking_id"],
            name="fk_parking_rules_zone_id_parking_id_parking_zones",
            ondelete="CASCADE",
        ),
        _source_fk("parking_rules"),
        _window_check("parking_rules"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_parking_rules_confidence_range"
        ),
    )
    op.create_index("ix_parking_rules_parking_id", "parking_rules", ["parking_id"])
    op.create_index("ix_parking_rules_zone_id", "parking_rules", ["zone_id"])
    op.create_index("ix_parking_rules_source_id", "parking_rules", ["source_id"])

    op.create_table(
        "parking_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("rate_type", _enum("parking_rate_type"), nullable=True),
        sa.Column("vehicle_type", _enum("vehicle_type"), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'TWD'"), nullable=False),
        sa.Column("base_amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("unit_minutes", sa.Integer(), nullable=True),
        sa.Column("free_minutes", sa.Integer(), nullable=True),
        sa.Column("daily_max_amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parse_status", _enum("rate_parse_status"), nullable=False),
        _ts("effective_from"),
        _ts("effective_to"),
        sa.Column("schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_provenance(),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_rates"),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["parking_zones.id"], name="fk_parking_rates_zone_id_parking_zones", ondelete="CASCADE"
        ),
        _source_fk("parking_rates"),
        sa.CheckConstraint("base_amount IS NULL OR base_amount >= 0", name="ck_parking_rates_base_amount_nonnegative"),
        sa.CheckConstraint("unit_minutes IS NULL OR unit_minutes > 0", name="ck_parking_rates_unit_minutes_positive"),
        sa.CheckConstraint(
            "free_minutes IS NULL OR free_minutes >= 0", name="ck_parking_rates_free_minutes_nonnegative"
        ),
        sa.CheckConstraint(
            "daily_max_amount IS NULL OR daily_max_amount >= 0", name="ck_parking_rates_daily_max_amount_nonnegative"
        ),
        sa.CheckConstraint(
            "parse_status <> 'PARSED' OR rate_type IS NOT NULL", name="ck_parking_rates_parsed_requires_rate_type"
        ),
        sa.CheckConstraint(
            "parse_status = 'PARSED' OR raw_text IS NOT NULL", name="ck_parking_rates_unparsed_requires_raw_text"
        ),
        _window_check("parking_rates"),
    )
    op.create_index("ix_parking_rates_zone_id", "parking_rates", ["zone_id"])
    op.create_index("ix_parking_rates_source_id", "parking_rates", ["source_id"])

    op.create_table(
        "parking_rate_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rate_id", sa.Integer(), nullable=False),
        sa.Column("day_type", _enum("rate_day_type"), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("start_minute", sa.Integer(), nullable=True),
        sa.Column("end_minute", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("unit_minutes", sa.Integer(), nullable=True),
        sa.Column("max_amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_rate_rules"),
        sa.ForeignKeyConstraint(
            ["rate_id"], ["parking_rates.id"], name="fk_parking_rate_rules_rate_id_parking_rates", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "(start_time IS NULL) = (end_time IS NULL)", name="ck_parking_rate_rules_time_window_complete"
        ),
        sa.CheckConstraint(
            "start_time IS NULL OR (start_time < TIME '24:00:00' AND end_time < TIME '24:00:00' "
            "AND start_time <> end_time)",
            name="ck_parking_rate_rules_time_window_valid",
        ),
        sa.CheckConstraint(
            "start_minute IS NULL OR start_minute >= 0", name="ck_parking_rate_rules_start_minute_nonnegative"
        ),
        sa.CheckConstraint("end_minute IS NULL OR end_minute > 0", name="ck_parking_rate_rules_end_minute_positive"),
        sa.CheckConstraint(
            "start_minute IS NULL OR end_minute IS NULL OR end_minute > start_minute",
            name="ck_parking_rate_rules_minute_range_ordered",
        ),
        sa.CheckConstraint("amount IS NULL OR amount >= 0", name="ck_parking_rate_rules_amount_nonnegative"),
        sa.CheckConstraint(
            "unit_minutes IS NULL OR unit_minutes > 0", name="ck_parking_rate_rules_unit_minutes_positive"
        ),
        sa.CheckConstraint(
            "max_amount IS NULL OR max_amount >= 0", name="ck_parking_rate_rules_max_amount_nonnegative"
        ),
    )
    op.create_index("ix_parking_rate_rules_rate_id", "parking_rate_rules", ["rate_id"])

    op.create_table(
        "parking_rate_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rate_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _ts("source_updated_at"),
        _ts("fetched_at"),
        _ts("verified_at"),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_rate_sources"),
        sa.ForeignKeyConstraint(
            ["rate_id"],
            ["parking_rates.id"],
            name="fk_parking_rate_sources_rate_id_parking_rates",
            ondelete="CASCADE",
        ),
        _source_fk("parking_rate_sources"),
    )
    op.create_index("ix_parking_rate_sources_rate_id", "parking_rate_sources", ["rate_id"])
    op.create_index("ix_parking_rate_sources_source_id", "parking_rate_sources", ["source_id"])

    op.create_table(
        "parking_realtime",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("status", _enum("realtime_status"), nullable=False),
        sa.Column("available_spaces", sa.Integer(), nullable=True),
        sa.Column("total_spaces", sa.Integer(), nullable=True),
        *_provenance(verified=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_realtime"),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["parking_zones.id"], name="fk_parking_realtime_zone_id_parking_zones", ondelete="CASCADE"
        ),
        _source_fk("parking_realtime"),
        sa.CheckConstraint(
            "available_spaces IS NULL OR available_spaces >= 0",
            name="ck_parking_realtime_available_spaces_nonnegative",
        ),
        sa.CheckConstraint(
            "total_spaces IS NULL OR total_spaces >= 0", name="ck_parking_realtime_total_spaces_nonnegative"
        ),
        sa.CheckConstraint(
            "available_spaces IS NULL OR total_spaces IS NULL OR available_spaces <= total_spaces",
            name="ck_parking_realtime_available_within_total",
        ),
        sa.CheckConstraint(
            "status <> 'AVAILABLE' OR (available_spaces IS NOT NULL AND available_spaces > 0)",
            name="ck_parking_realtime_available_status_has_spaces",
        ),
        sa.CheckConstraint(
            "status NOT IN ('FULL', 'CLOSED') OR (available_spaces IS NOT NULL AND available_spaces = 0)",
            name="ck_parking_realtime_full_closed_status_zero_spaces",
        ),
    )
    op.create_index("ix_parking_realtime_zone_id_fetched_at", "parking_realtime", ["zone_id", "fetched_at"])
    op.create_index("ix_parking_realtime_source_id", "parking_realtime", ["source_id"])

    op.create_table(
        "parking_entrances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parking_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("entrance_type", _enum("parking_entrance_type"), nullable=True),
        _point("location", nullable=True),
        sa.Column("heavy_motorcycle_access", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_provenance(),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_entrances"),
        sa.ForeignKeyConstraint(
            ["parking_id"],
            ["parking_lots.id"],
            name="fk_parking_entrances_parking_id_parking_lots",
            ondelete="CASCADE",
        ),
        _source_fk("parking_entrances"),
    )
    op.create_index("ix_parking_entrances_parking_id", "parking_entrances", ["parking_id"])
    op.create_index("ix_parking_entrances_source_id", "parking_entrances", ["source_id"])
    op.create_index("ix_parking_entrances_location", "parking_entrances", ["location"], postgresql_using="gist")

    op.create_table(
        "parking_facilities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parking_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_parking_facilities"),
        sa.ForeignKeyConstraint(
            ["parking_id"],
            ["parking_lots.id"],
            name="fk_parking_facilities_parking_id_parking_lots",
            ondelete="CASCADE",
        ),
        _source_fk("parking_facilities"),
        sa.UniqueConstraint("parking_id", "code", name="uq_parking_facilities_parking_id_code"),
    )

    op.create_table(
        "raw_import_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("status", _enum("import_batch_status"), server_default=sa.text("'PENDING'"), nullable=False),
        _ts("started_at"),
        _ts("finished_at"),
        sa.Column("total_records", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("normalized_records", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_records", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_raw_import_batches"),
        _source_fk("raw_import_batches"),
        sa.UniqueConstraint("id", "source_id", name="uq_raw_import_batches_id_source_id"),
        sa.CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at",
            name="ck_raw_import_batches_finished_after_started",
        ),
        sa.CheckConstraint(
            "total_records >= 0 AND normalized_records >= 0 AND failed_records >= 0",
            name="ck_raw_import_batches_counters_nonnegative",
        ),
    )
    op.create_index("ix_raw_import_batches_source_id", "raw_import_batches", ["source_id"])

    op.create_table(
        "raw_parking_records",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("record_type", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_rate_text", sa.Text(), nullable=True),
        sa.Column("status", _enum("raw_record_status"), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _ts("source_updated_at"),
        _ts("fetched_at"),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_raw_parking_records"),
        sa.ForeignKeyConstraint(
            ["batch_id", "source_id"],
            ["raw_import_batches.id", "raw_import_batches.source_id"],
            name="fk_raw_parking_records_batch_id_source_id_raw_import_batches",
            ondelete="RESTRICT",
        ),
        _source_fk("raw_parking_records"),
    )
    op.create_index("ix_raw_parking_records_batch_id", "raw_parking_records", ["batch_id"])
    op.create_index("ix_raw_parking_records_source_id_external_id", "raw_parking_records", ["source_id", "external_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("auth_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("auth_subject", name="uq_users_auth_subject"),
    )

    op.create_table(
        "user_vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_type", _enum("vehicle_type"), nullable=False),
        sa.Column("nickname", sa.String(length=100), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_user_vehicles"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_vehicles_user_id_users", ondelete="CASCADE"),
    )
    op.create_index("ix_user_vehicles_user_id", "user_vehicles", ["user_id"])

    op.create_table(
        "favorites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parking_id", sa.Integer(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_favorites"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_favorites_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_favorites_parking_id_parking_lots", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", "parking_id", name="uq_favorites_user_id_parking_id"),
    )
    op.create_index("ix_favorites_parking_id", "favorites", ["parking_id"])

    op.create_table(
        "user_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("parking_id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("report_type", _enum("user_report_type"), nullable=False),
        sa.Column("status", _enum("user_report_status"), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _ts("resolved_at"),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_user_reports"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_reports_user_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_user_reports_parking_id_parking_lots", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["zone_id", "parking_id"],
            ["parking_zones.id", "parking_zones.parking_id"],
            name="fk_user_reports_zone_id_parking_id_parking_zones",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= created_at", name="ck_user_reports_resolved_after_created"
        ),
    )
    op.create_index("ix_user_reports_parking_id", "user_reports", ["parking_id"])
    op.create_index("ix_user_reports_zone_id", "user_reports", ["zone_id"])
    op.create_index("ix_user_reports_user_id", "user_reports", ["user_id"])

    op.create_table(
        "report_photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_report_photos"),
        sa.ForeignKeyConstraint(
            ["report_id"], ["user_reports.id"], name="fk_report_photos_report_id_user_reports", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("storage_key", name="uq_report_photos_storage_key"),
        sa.CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_report_photos_byte_size_nonnegative"),
        sa.CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_report_photos_dimensions_positive",
        ),
    )
    op.create_index("ix_report_photos_report_id", "report_photos", ["report_id"])


def downgrade() -> None:
    # Indexes and constraints go with their tables. PostGIS stays until 001 downgrade.
    for table in reversed(TABLES):
        op.drop_table(table)

    bind = op.get_bind()
    for name in reversed(tuple(ENUMS)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=False)
