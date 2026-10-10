"""Community verification: source verification, cases, evidence, prechecks and ledger.

Revision ID: 006_community_verification
Revises: 005_vehicle_classes

Additive only. Legacy user_reports keep their PENDING/VERIFIED/REJECTED/SUPERSEDED
semantics; nothing is backfilled into cases, published, awarded points, or given a
guessed photo time. Case events, precheck runs/results and the contribution ledger
reject UPDATE through a trigger. Contract: docs/community-verification-contract.md.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "006_community_verification"
down_revision = "005_vehicle_classes"
branch_labels = None
depends_on = None

ENUMS = {
    "community_fact_type": (
        "LIGHTING",
        "RAIN_COVER",
        "CHARGING",
        "ENTRANCE_LOCATION",
        "PARKING_PERMISSION",
        "RATE",
        "ENTRANCE_ACCESS",
    ),
    "source_fact_kind": ("PERMISSION", "RATE", "REALTIME", "ENTRANCE", "FACILITY"),
    "case_review_status": (
        "PRECHECK_PENDING",
        "AWAITING_CORROBORATION",
        "MANUAL_REVIEW",
        "NEEDS_EVIDENCE",
        "ACCEPTED",
        "REJECTED",
        "SUPERSEDED",
    ),
    "case_publication_state": ("UNPUBLISHED", "PUBLISHED", "SUSPENDED", "WITHDRAWN"),
    "publication_basis": ("COMMUNITY_CORROBORATED", "MANUAL_REVIEW", "VERIFIED_SOURCE"),
    "case_stance": ("SUPPORT", "OPPOSE", "CANNOT_CONFIRM"),
    "photo_time_status": ("VALID", "MISSING", "TIMEZONE_UNKNOWN", "INVALID", "FUTURE", "OUTSIDE_WINDOW"),
    "precheck_outcome": ("PASS", "FAIL", "NEEDS_MANUAL"),
    "precheck_code": (
        "IDENTITY",
        "REQUIRED_FIELDS",
        "SCOPE",
        "PHOTO_TIME",
        "SOURCE_APPLICABILITY",
        "EVIDENCE_DUPLICATE",
        "CORROBORATION",
        "SOURCE_CONFLICT",
        "PUBLICATION_ELIGIBILITY",
    ),
    "case_event_type": (
        "SUBMITTED",
        "REVISION_SUBMITTED",
        "PRECHECK_COMPLETED",
        "STANCE_RECORDED",
        "STANCE_WITHDRAWN",
        "STANCE_INVALIDATED",
        "PUBLISHED",
        "SUSPENDED",
        "RESUMED",
        "WITHDRAWN",
        "MANUAL_ACCEPTED",
        "MANUAL_REJECTED",
        "EVIDENCE_REQUESTED",
        "SUPERSEDED",
        "PHOTO_DELETED",
    ),
    "contribution_entry_type": ("AWARD", "FREEZE", "UNFREEZE", "REVOKE"),
    "contribution_reason": ("ORIGINAL_REPORT", "CORROBORATION", "UPHELD_OBJECTION"),
}
IMMUTABLE_TABLES = ("community_case_events", "community_prechecks", "community_precheck_results", "contribution_ledger")
TABLES = (
    "idempotency_records",
    "contribution_ledger",
    "community_case_events",
    "community_precheck_results",
    "community_prechecks",
    "community_evidence_photos",
    "community_case_stances",
    "community_case_revisions",
    "community_cases",
    "source_verifications",
    "community_participants",
)
LOW_RISK = "('LIGHTING', 'RAIN_COVER', 'CHARGING', 'ENTRANCE_LOCATION')"
SOURCE_RESOLVED = "('PARKING_PERMISSION', 'RATE', 'ENTRANCE_ACCESS')"
HTTPS_URL = "~ '^https://[^[:space:]]+$'"
SHA256 = "~ '^[0-9a-f]{64}$'"


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def _tz(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _participant_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        ["community_participants.id"],
        name=f"fk_{table}_{column}",
        ondelete="RESTRICT",
    )


def _case_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["case_id"], ["community_cases.id"], name=f"fk_{table}_case_id_community_cases", ondelete="CASCADE"
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name, labels in ENUMS.items():
        postgresql.ENUM(*labels, name=name).create(bind)

    op.create_table(
        "community_participants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_community_participants"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_community_participants_user_id_users", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("user_id", name="uq_community_participants_user_id"),
    )

    table = "source_verifications"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("fact_kind", _enum("source_fact_kind"), nullable=False),
        sa.Column("parking_id", sa.Integer(), nullable=True),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("parser_code", sa.String(64), nullable=True),
        sa.Column("parser_config_version", sa.String(32), nullable=True),
        sa.Column("rule_kind", _enum("parking_rule_kind"), nullable=True),
        sa.Column("authority_priority", sa.Integer(), nullable=True),
        sa.Column("evidence_url", sa.Text(), nullable=False),
        sa.Column("evidence_note", sa.Text(), nullable=True),
        sa.Column("verified_by_participant_id", sa.Integer(), nullable=False),
        _tz("verified_at", nullable=False),
        _tz("revoked_at"),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_source_verifications"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_sources.id"],
            name="fk_source_verifications_source_id_data_sources",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parking_id"],
            ["parking_lots.id"],
            name="fk_source_verifications_parking_id_parking_lots",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id", "parking_id"],
            ["parking_zones.id", "parking_zones.parking_id"],
            name="fk_source_verifications_zone_id_parking_id_parking_zones",
            ondelete="CASCADE",
        ),
        _participant_fk(table, "verified_by_participant_id"),
        sa.CheckConstraint(
            "(fact_kind = 'PERMISSION' AND rule_kind IS NOT NULL AND authority_priority IS NOT NULL) OR "
            "(fact_kind <> 'PERMISSION' AND rule_kind IS NULL AND authority_priority IS NULL)",
            name="ck_source_verifications_rule_tier_only_for_permission",
        ),
        sa.CheckConstraint(
            "(parser_code IS NULL) = (parser_config_version IS NULL)",
            name="ck_source_verifications_parser_versioned",
        ),
        sa.CheckConstraint(
            "zone_id IS NULL OR parking_id IS NOT NULL", name="ck_source_verifications_zone_requires_lot"
        ),
        sa.CheckConstraint(f"evidence_url {HTTPS_URL}", name="ck_source_verifications_evidence_url_https"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= verified_at", name="ck_source_verifications_revoked_after_verified"
        ),
    )
    op.create_index("ix_source_verifications_source_id", table, ["source_id"])
    op.create_index("ix_source_verifications_parking_id", table, ["parking_id"])

    table = "community_cases"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parking_id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("fact_type", _enum("community_fact_type"), nullable=False),
        sa.Column("vehicle", _enum("vehicle_type"), nullable=True),
        sa.Column("author_participant_id", sa.Integer(), nullable=False),
        sa.Column("origin_report_id", sa.Integer(), nullable=True),
        sa.Column(
            "review_status",
            _enum("case_review_status"),
            server_default=sa.text("'PRECHECK_PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "publication_state",
            _enum("case_publication_state"),
            server_default=sa.text("'UNPUBLISHED'"),
            nullable=False,
        ),
        sa.Column("publication_basis", _enum("publication_basis"), nullable=True),
        _tz("first_published_at"),
        _tz("published_until"),
        _tz("withdrawn_at"),
        sa.Column("current_revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("superseded_by_case_id", sa.Integer(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_community_cases"),
        sa.ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_community_cases_parking_id_parking_lots", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["zone_id", "parking_id"],
            ["parking_zones.id", "parking_zones.parking_id"],
            name="fk_community_cases_zone_id_parking_id_parking_zones",
        ),
        _participant_fk(table, "author_participant_id"),
        sa.ForeignKeyConstraint(
            ["origin_report_id"],
            ["user_reports.id"],
            name="fk_community_cases_origin_report_id_user_reports",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_case_id"],
            ["community_cases.id"],
            name="fk_community_cases_superseded_by_case_id_community_cases",
        ),
        sa.CheckConstraint(
            "vehicle IS NULL OR vehicle IN ('NORMAL_HEAVY', 'LARGE_HEAVY')", name="ck_community_cases_vehicle_rider"
        ),
        sa.CheckConstraint(
            f"(fact_type IN {SOURCE_RESOLVED} AND vehicle IS NOT NULL AND zone_id IS NOT NULL) OR "
            f"(fact_type IN {LOW_RISK} AND vehicle IS NULL)",
            name="ck_community_cases_scope_by_fact_type",
        ),
        sa.CheckConstraint(
            "(publication_state = 'UNPUBLISHED' AND publication_basis IS NULL AND first_published_at IS NULL "
            "AND published_until IS NULL) OR "
            "(publication_state <> 'UNPUBLISHED' AND publication_basis IS NOT NULL AND first_published_at IS NOT NULL)",
            name="ck_community_cases_publication_recorded",
        ),
        sa.CheckConstraint(
            "publication_basis IS NULL OR publication_basis = 'VERIFIED_SOURCE' "
            "OR published_until IS NOT DISTINCT FROM first_published_at + interval '90 days'",
            name="ck_community_cases_observation_term_90_days",
        ),
        sa.CheckConstraint(
            f"publication_basis IS DISTINCT FROM 'COMMUNITY_CORROBORATED' OR fact_type IN {LOW_RISK}",
            name="ck_community_cases_corroboration_low_risk_only",
        ),
        sa.CheckConstraint(
            "publication_state <> 'PUBLISHED' OR review_status = 'ACCEPTED'",
            name="ck_community_cases_published_requires_accepted",
        ),
        sa.CheckConstraint(
            "(publication_state = 'WITHDRAWN') = (withdrawn_at IS NOT NULL)",
            name="ck_community_cases_withdrawn_recorded",
        ),
        sa.CheckConstraint(
            "(review_status = 'SUPERSEDED') = (superseded_by_case_id IS NOT NULL)",
            name="ck_community_cases_superseded_has_successor",
        ),
        sa.CheckConstraint(
            "superseded_by_case_id IS NULL OR superseded_by_case_id <> id",
            name="ck_community_cases_not_self_superseded",
        ),
        sa.CheckConstraint("version >= 1 AND current_revision >= 1", name="ck_community_cases_versions_positive"),
    )
    for column in ("parking_id", "zone_id", "author_participant_id", "review_status"):
        op.create_index(f"ix_community_cases_{column}", table, [column])

    table = "community_case_revisions"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("proposed_value", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _tz("description_approved_at"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_verification_id", sa.Integer(), nullable=True),
        sa.Column("created_by_participant_id", sa.Integer(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_community_case_revisions"),
        _case_fk(table),
        _participant_fk(table, "created_by_participant_id"),
        sa.ForeignKeyConstraint(
            ["source_verification_id"],
            ["source_verifications.id"],
            name="fk_community_case_revisions_source_verification_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("case_id", "revision", name="uq_community_case_revisions_case_id_revision"),
        sa.UniqueConstraint("id", "case_id", name="uq_community_case_revisions_id_case_id"),
        sa.CheckConstraint("revision >= 1", name="ck_community_case_revisions_revision_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(proposed_value) = 'object'", name="ck_community_case_revisions_proposed_value_object"
        ),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 1000",
            name="ck_community_case_revisions_description_length",
        ),
        sa.CheckConstraint(
            "description_approved_at IS NULL OR description IS NOT NULL",
            name="ck_community_case_revisions_approval_requires_text",
        ),
        sa.CheckConstraint(
            f"source_url IS NULL OR source_url {HTTPS_URL}", name="ck_community_case_revisions_source_url_https"
        ),
    )

    table = "community_case_stances"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("stance", _enum("case_stance"), nullable=False),
        sa.Column("observed_value", postgresql.JSONB(), nullable=True),
        _tz("withdrawn_at"),
        _tz("invalidated_at"),
        sa.Column("invalidated_reason", sa.String(64), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_community_case_stances"),
        _case_fk(table),
        _participant_fk(table, "participant_id"),
        sa.UniqueConstraint("id", "case_id", name="uq_community_case_stances_id_case_id"),
        sa.CheckConstraint(
            "stance = 'CANNOT_CONFIRM' OR coalesce(jsonb_typeof(observed_value) = 'object', false)",
            name="ck_community_case_stances_observation_required",
        ),
        sa.CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= created_at",
            name="ck_community_case_stances_withdrawn_after_created",
        ),
        sa.CheckConstraint(
            "(invalidated_at IS NULL) = (invalidated_reason IS NULL)",
            name="ck_community_case_stances_invalidation_has_reason",
        ),
    )
    op.create_index(
        "uq_community_case_stances_active",
        table,
        ["case_id", "participant_id"],
        unique=True,
        postgresql_where=sa.text("withdrawn_at IS NULL AND invalidated_at IS NULL"),
    )
    op.create_index("ix_community_case_stances_participant_id", table, ["participant_id"])

    table = "community_evidence_photos"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.Integer(), nullable=True),
        sa.Column("stance_id", sa.Integer(), nullable=True),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("normalized_sha256", sa.String(64), nullable=False),
        _tz("received_at", nullable=False),
        sa.Column("exif_datetime_original", sa.String(32), nullable=True),
        sa.Column("exif_offset_time_original", sa.String(8), nullable=True),
        sa.Column("exif_subsec_time_original", sa.String(16), nullable=True),
        _tz("captured_at"),
        sa.Column("time_status", _enum("photo_time_status"), nullable=False),
        sa.Column("time_parser_version", sa.String(32), nullable=False),
        _tz("deleted_at"),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_community_evidence_photos"),
        _case_fk(table),
        sa.ForeignKeyConstraint(
            ["revision_id", "case_id"],
            ["community_case_revisions.id", "community_case_revisions.case_id"],
            name="fk_community_evidence_photos_revision_id_case_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stance_id", "case_id"],
            ["community_case_stances.id", "community_case_stances.case_id"],
            name="fk_community_evidence_photos_stance_id_case_id",
            ondelete="CASCADE",
        ),
        _participant_fk(table, "participant_id"),
        sa.UniqueConstraint("storage_key", name="uq_community_evidence_photos_storage_key"),
        sa.CheckConstraint(
            "(revision_id IS NULL) <> (stance_id IS NULL)", name="ck_community_evidence_photos_one_owner"
        ),
        sa.CheckConstraint(f"normalized_sha256 {SHA256}", name="ck_community_evidence_photos_normalized_sha256_hex"),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0", name="ck_community_evidence_photos_byte_size_nonnegative"
        ),
        sa.CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_community_evidence_photos_dimensions_positive",
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR (storage_key IS NULL AND exif_datetime_original IS NULL "
            "AND exif_offset_time_original IS NULL AND exif_subsec_time_original IS NULL AND captured_at IS NULL)",
            name="ck_community_evidence_photos_deletion_clears_private_content",
        ),
        sa.CheckConstraint(
            "deleted_at IS NOT NULL OR storage_key IS NOT NULL", name="ck_community_evidence_photos_live_has_object"
        ),
        sa.CheckConstraint(
            "deleted_at IS NOT NULL OR CASE time_status "
            "WHEN 'VALID' THEN exif_datetime_original IS NOT NULL AND exif_offset_time_original IS NOT NULL "
            "AND captured_at IS NOT NULL AND captured_at <= received_at "
            "AND captured_at >= received_at - interval '30 days' "
            "WHEN 'FUTURE' THEN exif_datetime_original IS NOT NULL AND exif_offset_time_original IS NOT NULL "
            "AND captured_at IS NOT NULL AND captured_at > received_at "
            "WHEN 'OUTSIDE_WINDOW' THEN exif_datetime_original IS NOT NULL AND exif_offset_time_original IS NOT NULL "
            "AND captured_at IS NOT NULL AND captured_at < received_at - interval '30 days' "
            "WHEN 'MISSING' THEN exif_datetime_original IS NULL AND captured_at IS NULL "
            "WHEN 'TIMEZONE_UNKNOWN' THEN exif_datetime_original IS NOT NULL "
            "AND exif_offset_time_original IS NULL AND captured_at IS NULL "
            "ELSE captured_at IS NULL END",
            name="ck_community_evidence_photos_time_status_consistent",
        ),
    )
    op.create_index("ix_community_evidence_photos_case_id_normalized_sha256", table, ["case_id", "normalized_sha256"])
    op.create_index("ix_community_evidence_photos_participant_id", table, ["participant_id"])

    table = "community_prechecks"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("outcome", _enum("precheck_outcome"), nullable=False),
        _tz("evaluated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_community_prechecks"),
        _case_fk(table),
        sa.CheckConstraint("revision >= 1", name="ck_community_prechecks_revision_positive"),
    )
    op.create_index("ix_community_prechecks_case_id", table, ["case_id"])

    op.create_table(
        "community_precheck_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("precheck_id", sa.Integer(), nullable=False),
        sa.Column("check_code", _enum("precheck_code"), nullable=False),
        sa.Column("outcome", _enum("precheck_outcome"), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_community_precheck_results"),
        sa.ForeignKeyConstraint(
            ["precheck_id"],
            ["community_prechecks.id"],
            name="fk_community_precheck_results_precheck_id_community_prechecks",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("precheck_id", "check_code", name="uq_community_precheck_results_precheck_id_check_code"),
        sa.CheckConstraint(
            "outcome = 'PASS' OR reason_code IS NOT NULL", name="ck_community_precheck_results_non_pass_has_reason"
        ),
    )

    table = "community_case_events"
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("event_type", _enum("case_event_type"), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("actor_participant_id", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_community_case_events"),
        _case_fk(table),
        _participant_fk(table, "actor_participant_id"),
        sa.CheckConstraint(
            "event_type NOT IN ('MANUAL_ACCEPTED', 'MANUAL_REJECTED', 'EVIDENCE_REQUESTED', 'SUPERSEDED') "
            "OR (actor_participant_id IS NOT NULL AND reason_code IS NOT NULL AND reason_text IS NOT NULL)",
            name="ck_community_case_events_manual_decision_has_reason",
        ),
        sa.CheckConstraint("case_version >= 1", name="ck_community_case_events_case_version_positive"),
    )
    op.create_index("ix_community_case_events_case_id_id", table, ["case_id", "id"])

    table = "contribution_ledger"
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("entry_type", _enum("contribution_entry_type"), nullable=False),
        sa.Column("reason", _enum("contribution_reason"), nullable=False),
        sa.Column("points", sa.SmallInteger(), nullable=False),
        sa.Column("reverses_entry_id", sa.BigInteger(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_contribution_ledger"),
        _participant_fk(table, "participant_id"),
        _case_fk(table),
        sa.ForeignKeyConstraint(
            ["reverses_entry_id"],
            ["contribution_ledger.id"],
            name="fk_contribution_ledger_reverses_entry_id_contribution_ledger",
        ),
        sa.CheckConstraint(
            "CASE entry_type WHEN 'AWARD' THEN points = 1 WHEN 'REVOKE' THEN points = -1 ELSE points = 0 END",
            name="ck_contribution_ledger_points_by_entry_type",
        ),
        sa.CheckConstraint(
            "(entry_type = 'AWARD') = (reverses_entry_id IS NULL)",
            name="ck_contribution_ledger_non_award_references_award",
        ),
    )
    for entry_type in ("award", "revoke"):
        op.create_index(
            f"uq_contribution_ledger_one_{entry_type}",
            table,
            ["case_id", "participant_id"],
            unique=True,
            postgresql_where=sa.text(f"entry_type = '{entry_type.upper()}'"),
        )
    op.create_index("ix_contribution_ledger_participant_id", table, ["participant_id"])

    table = "idempotency_records"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        _tz("expires_at", nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_idempotency_records_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "user_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_records_user_id_operation_idempotency_key",
        ),
        sa.CheckConstraint("char_length(idempotency_key) BETWEEN 1 AND 128", name="ck_idempotency_records_key_length"),
        sa.CheckConstraint(f"request_sha256 {SHA256}", name="ck_idempotency_records_request_sha256_hex"),
        sa.CheckConstraint("response_status BETWEEN 200 AND 599", name="ck_idempotency_records_response_status_range"),
        sa.CheckConstraint("expires_at > created_at", name="ck_idempotency_records_expires_after_created"),
    )
    op.create_index("ix_idempotency_records_expires_at", table, ["expires_at"])

    op.execute(
        """
        CREATE FUNCTION community_reject_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    for name in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {name}_append_only BEFORE UPDATE ON {name} "
            "FOR EACH ROW EXECUTE FUNCTION community_reject_update()"
        )


def downgrade() -> None:
    for name in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER {name}_append_only ON {name}")
    op.execute("DROP FUNCTION community_reject_update()")
    for name in TABLES:
        op.drop_table(name)
    bind = op.get_bind()
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(bind)
