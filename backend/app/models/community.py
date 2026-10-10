"""Community verification records (migration 006).

Cases, evidence and stances are community evidence only. Nothing here writes
parking rules, rates, realtime or entrances; source-backed facts still go through
source-specific normalization. Contract: docs/community-verification-contract.md.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin
from app.models.enums import (
    CaseEventType,
    CasePublicationState,
    CaseReviewStatus,
    CaseStance,
    CommunityFactType,
    ContributionEntryType,
    ContributionReason,
    PhotoTimeStatus,
    PrecheckCode,
    PrecheckOutcome,
    PublicationBasis,
    RuleKind,
    SourceFactKind,
    VehicleType,
    case_event_type_enum,
    case_publication_state_enum,
    case_review_status_enum,
    case_stance_enum,
    community_fact_type_enum,
    contribution_entry_type_enum,
    contribution_reason_enum,
    photo_time_status_enum,
    precheck_code_enum,
    precheck_outcome_enum,
    publication_basis_enum,
    rule_kind_enum,
    source_fact_kind_enum,
    vehicle_type_enum,
)

LOW_RISK_SQL = "('LIGHTING', 'RAIN_COVER', 'CHARGING', 'ENTRANCE_LOCATION')"
SOURCE_RESOLVED_SQL = "('PARKING_PERMISSION', 'RATE', 'ENTRANCE_ACCESS')"
HTTPS_URL_SQL = "~ '^https://[^[:space:]]+$'"
SHA256_SQL = "~ '^[0-9a-f]{64}$'"


class SourceVerification(TimestampMixin, Base):
    """Moderator verification of a source's identity, relation and confirmable fact scope.

    Rule tier and authority come from this configuration, never from a reviewer's
    case decision. `parser_code` NULL means no implemented parser: manual review only.
    """

    __tablename__ = "source_verifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id"],
            ["data_sources.id"],
            name="fk_source_verifications_source_id_data_sources",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parking_id"],
            ["parking_lots.id"],
            name="fk_source_verifications_parking_id_parking_lots",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["zone_id", "parking_id"],
            ["parking_zones.id", "parking_zones.parking_id"],
            name="fk_source_verifications_zone_id_parking_id_parking_zones",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["verified_by_participant_id"],
            ["community_participants.id"],
            name="fk_source_verifications_verified_by_participant_id",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(fact_kind = 'PERMISSION' AND rule_kind IS NOT NULL AND authority_priority IS NOT NULL) OR "
            "(fact_kind <> 'PERMISSION' AND rule_kind IS NULL AND authority_priority IS NULL)",
            name="ck_source_verifications_rule_tier_only_for_permission",
        ),
        CheckConstraint(
            "(parser_code IS NULL) = (parser_config_version IS NULL)",
            name="ck_source_verifications_parser_versioned",
        ),
        CheckConstraint("zone_id IS NULL OR parking_id IS NOT NULL", name="ck_source_verifications_zone_requires_lot"),
        CheckConstraint(f"evidence_url {HTTPS_URL_SQL}", name="ck_source_verifications_evidence_url_https"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= verified_at", name="ck_source_verifications_revoked_after_verified"
        ),
        Index("ix_source_verifications_source_id", "source_id"),
        Index("ix_source_verifications_parking_id", "parking_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer)
    fact_kind: Mapped[SourceFactKind] = mapped_column(source_fact_kind_enum)
    # NULL parking_id: the verification covers every lot the source itself publishes.
    parking_id: Mapped[int | None] = mapped_column(Integer)
    # NULL zone_id with a lot: lot-wide scope.
    zone_id: Mapped[int | None] = mapped_column(Integer)
    parser_code: Mapped[str | None] = mapped_column(String(64))
    parser_config_version: Mapped[str | None] = mapped_column(String(32))
    rule_kind: Mapped[RuleKind | None] = mapped_column(rule_kind_enum)
    authority_priority: Mapped[int | None] = mapped_column(Integer)
    evidence_url: Mapped[str] = mapped_column(Text)
    evidence_note: Mapped[str | None] = mapped_column(Text)
    verified_by_participant_id: Mapped[int] = mapped_column(Integer)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommunityParticipant(CreatedAtMixin, Base):
    """Internal, never-reassigned participant identity.

    Deleting a user nulls `user_id` but keeps the row, so recusal and
    one-stance-per-person checks keep working for historical cases.
    """

    __tablename__ = "community_participants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_community_participants_user_id_users", ondelete="SET NULL"
        ),
        UniqueConstraint("user_id", name="uq_community_participants_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)


class CommunityCase(TimestampMixin, Base):
    __tablename__ = "community_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parking_id"], ["parking_lots.id"], name="fk_community_cases_parking_id_parking_lots", ondelete="CASCADE"
        ),
        # NO ACTION so a lot-delete cascade removes zones and cases together.
        ForeignKeyConstraint(
            ["zone_id", "parking_id"],
            ["parking_zones.id", "parking_zones.parking_id"],
            name="fk_community_cases_zone_id_parking_id_parking_zones",
        ),
        ForeignKeyConstraint(
            ["author_participant_id"],
            ["community_participants.id"],
            name="fk_community_cases_author_participant_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["origin_report_id"],
            ["user_reports.id"],
            name="fk_community_cases_origin_report_id_user_reports",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["superseded_by_case_id"],
            ["community_cases.id"],
            name="fk_community_cases_superseded_by_case_id_community_cases",
        ),
        CheckConstraint(
            "vehicle IS NULL OR vehicle IN ('NORMAL_HEAVY', 'LARGE_HEAVY')", name="ck_community_cases_vehicle_rider"
        ),
        CheckConstraint(
            f"(fact_type IN {SOURCE_RESOLVED_SQL} AND vehicle IS NOT NULL AND zone_id IS NOT NULL) OR "
            f"(fact_type IN {LOW_RISK_SQL} AND vehicle IS NULL)",
            name="ck_community_cases_scope_by_fact_type",
        ),
        CheckConstraint(
            "(publication_state = 'UNPUBLISHED' AND publication_basis IS NULL AND first_published_at IS NULL "
            "AND published_until IS NULL) OR "
            "(publication_state <> 'UNPUBLISHED' AND publication_basis IS NOT NULL AND first_published_at IS NOT NULL)",
            name="ck_community_cases_publication_recorded",
        ),
        CheckConstraint(
            "publication_basis IS NULL OR publication_basis = 'VERIFIED_SOURCE' "
            "OR published_until IS NOT DISTINCT FROM first_published_at + interval '90 days'",
            name="ck_community_cases_observation_term_90_days",
        ),
        CheckConstraint(
            f"publication_basis IS DISTINCT FROM 'COMMUNITY_CORROBORATED' OR fact_type IN {LOW_RISK_SQL}",
            name="ck_community_cases_corroboration_low_risk_only",
        ),
        CheckConstraint(
            "publication_state <> 'PUBLISHED' OR review_status = 'ACCEPTED'",
            name="ck_community_cases_published_requires_accepted",
        ),
        CheckConstraint(
            "(publication_state = 'WITHDRAWN') = (withdrawn_at IS NOT NULL)",
            name="ck_community_cases_withdrawn_recorded",
        ),
        CheckConstraint(
            "(review_status = 'SUPERSEDED') = (superseded_by_case_id IS NOT NULL)",
            name="ck_community_cases_superseded_has_successor",
        ),
        CheckConstraint(
            "superseded_by_case_id IS NULL OR superseded_by_case_id <> id",
            name="ck_community_cases_not_self_superseded",
        ),
        CheckConstraint("version >= 1 AND current_revision >= 1", name="ck_community_cases_versions_positive"),
        Index("ix_community_cases_parking_id", "parking_id"),
        Index("ix_community_cases_zone_id", "zone_id"),
        Index("ix_community_cases_author_participant_id", "author_participant_id"),
        Index("ix_community_cases_review_status", "review_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parking_id: Mapped[int] = mapped_column(Integer)
    zone_id: Mapped[int | None] = mapped_column(Integer)
    fact_type: Mapped[CommunityFactType] = mapped_column(community_fact_type_enum)
    # Explicit rider class for source-resolved facts; NULL (vehicle-agnostic) for low-risk ones.
    vehicle: Mapped[VehicleType | None] = mapped_column(vehicle_type_enum)
    author_participant_id: Mapped[int] = mapped_column(Integer)
    # Optional link to a legacy report a moderator converted; legacy rows are never backfilled.
    origin_report_id: Mapped[int | None] = mapped_column(Integer)
    review_status: Mapped[CaseReviewStatus] = mapped_column(
        case_review_status_enum, server_default=text("'PRECHECK_PENDING'")
    )
    publication_state: Mapped[CasePublicationState] = mapped_column(
        case_publication_state_enum, server_default=text("'UNPUBLISHED'")
    )
    publication_basis: Mapped[PublicationBasis | None] = mapped_column(publication_basis_enum)
    # Set once; resuming a suspended case never resets the term.
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    # Optimistic concurrency for moderator writes (409 VERSION_CONFLICT on mismatch).
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    superseded_by_case_id: Mapped[int | None] = mapped_column(Integer)


class CommunityCaseRevision(CreatedAtMixin, Base):
    """Append-only proposal versions; requesting evidence creates a new revision."""

    __tablename__ = "community_case_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"],
            ["community_cases.id"],
            name="fk_community_case_revisions_case_id_community_cases",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by_participant_id"],
            ["community_participants.id"],
            name="fk_community_case_revisions_created_by_participant_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_verification_id"],
            ["source_verifications.id"],
            name="fk_community_case_revisions_source_verification_id",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("case_id", "revision", name="uq_community_case_revisions_case_id_revision"),
        # Target for composite FKs that keep evidence on the same case.
        UniqueConstraint("id", "case_id", name="uq_community_case_revisions_id_case_id"),
        CheckConstraint("revision >= 1", name="ck_community_case_revisions_revision_positive"),
        CheckConstraint(
            "jsonb_typeof(proposed_value) = 'object'", name="ck_community_case_revisions_proposed_value_object"
        ),
        CheckConstraint(
            "description IS NULL OR char_length(description) <= 1000",
            name="ck_community_case_revisions_description_length",
        ),
        CheckConstraint(
            "description_approved_at IS NULL OR description IS NOT NULL",
            name="ck_community_case_revisions_approval_requires_text",
        ),
        CheckConstraint(
            f"source_url IS NULL OR source_url {HTTPS_URL_SQL}", name="ck_community_case_revisions_source_url_https"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    proposed_value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Free text is private until a moderator approves it for display.
    description: Mapped[str | None] = mapped_column(Text)
    description_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_verification_id: Mapped[int | None] = mapped_column(Integer)
    created_by_participant_id: Mapped[int] = mapped_column(Integer)


class CommunityCaseStance(CreatedAtMixin, Base):
    """One active stance per participant per case; changing it withdraws the old row."""

    __tablename__ = "community_case_stances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"],
            ["community_cases.id"],
            name="fk_community_case_stances_case_id_community_cases",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["participant_id"],
            ["community_participants.id"],
            name="fk_community_case_stances_participant_id",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "case_id", name="uq_community_case_stances_id_case_id"),
        CheckConstraint(
            "stance = 'CANNOT_CONFIRM' OR coalesce(jsonb_typeof(observed_value) = 'object', false)",
            name="ck_community_case_stances_observation_required",
        ),
        CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= created_at",
            name="ck_community_case_stances_withdrawn_after_created",
        ),
        CheckConstraint(
            "(invalidated_at IS NULL) = (invalidated_reason IS NULL)",
            name="ck_community_case_stances_invalidation_has_reason",
        ),
        Index(
            "uq_community_case_stances_active",
            "case_id",
            "participant_id",
            unique=True,
            postgresql_where=text("withdrawn_at IS NULL AND invalidated_at IS NULL"),
        ),
        Index("ix_community_case_stances_participant_id", "participant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer)
    participant_id: Mapped[int] = mapped_column(Integer)
    stance: Mapped[CaseStance] = mapped_column(case_stance_enum)
    observed_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_reason: Mapped[str | None] = mapped_column(String(64))


class CommunityEvidencePhoto(CreatedAtMixin, Base):
    """Private photo evidence plus the backend-extracted metadata time.

    Only the needed EXIF strings are kept; never GPS or the full EXIF block. The
    time status is fixed at receipt. Deletion clears the object and private time
    content but keeps the row for audit and duplicate detection.
    """

    __tablename__ = "community_evidence_photos"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"],
            ["community_cases.id"],
            name="fk_community_evidence_photos_case_id_community_cases",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["revision_id", "case_id"],
            ["community_case_revisions.id", "community_case_revisions.case_id"],
            name="fk_community_evidence_photos_revision_id_case_id",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["stance_id", "case_id"],
            ["community_case_stances.id", "community_case_stances.case_id"],
            name="fk_community_evidence_photos_stance_id_case_id",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["participant_id"],
            ["community_participants.id"],
            name="fk_community_evidence_photos_participant_id",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("storage_key", name="uq_community_evidence_photos_storage_key"),
        CheckConstraint("(revision_id IS NULL) <> (stance_id IS NULL)", name="ck_community_evidence_photos_one_owner"),
        CheckConstraint(f"normalized_sha256 {SHA256_SQL}", name="ck_community_evidence_photos_normalized_sha256_hex"),
        CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0", name="ck_community_evidence_photos_byte_size_nonnegative"
        ),
        CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_community_evidence_photos_dimensions_positive",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR (storage_key IS NULL AND exif_datetime_original IS NULL "
            "AND exif_offset_time_original IS NULL AND exif_subsec_time_original IS NULL AND captured_at IS NULL)",
            name="ck_community_evidence_photos_deletion_clears_private_content",
        ),
        CheckConstraint(
            "deleted_at IS NOT NULL OR storage_key IS NOT NULL", name="ck_community_evidence_photos_live_has_object"
        ),
        CheckConstraint(
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
        Index("ix_community_evidence_photos_case_id_normalized_sha256", "case_id", "normalized_sha256"),
        Index("ix_community_evidence_photos_participant_id", "participant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer)
    # Exactly one owner: the original report revision or a corroborating/objecting stance.
    revision_id: Mapped[int | None] = mapped_column(Integer)
    stance_id: Mapped[int | None] = mapped_column(Integer)
    participant_id: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    content_type: Mapped[str | None] = mapped_column(String(100))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # SHA-256 of the normalized stored JPEG: deterministic same-case deduplication only.
    normalized_sha256: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exif_datetime_original: Mapped[str | None] = mapped_column(String(32))
    exif_offset_time_original: Mapped[str | None] = mapped_column(String(8))
    exif_subsec_time_original: Mapped[str | None] = mapped_column(String(16))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_status: Mapped[PhotoTimeStatus] = mapped_column(photo_time_status_enum)
    time_parser_version: Mapped[str] = mapped_column(String(32))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommunityPrecheck(Base):
    """One deterministic precheck run over a case revision. Immutable."""

    __tablename__ = "community_prechecks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"],
            ["community_cases.id"],
            name="fk_community_prechecks_case_id_community_cases",
            ondelete="CASCADE",
        ),
        CheckConstraint("revision >= 1", name="ck_community_prechecks_revision_positive"),
        Index("ix_community_prechecks_case_id", "case_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    rule_version: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[PrecheckOutcome] = mapped_column(precheck_outcome_enum)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CommunityPrecheckResult(Base):
    __tablename__ = "community_precheck_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["precheck_id"],
            ["community_prechecks.id"],
            name="fk_community_precheck_results_precheck_id_community_prechecks",
            ondelete="CASCADE",
        ),
        UniqueConstraint("precheck_id", "check_code", name="uq_community_precheck_results_precheck_id_check_code"),
        CheckConstraint(
            "outcome = 'PASS' OR reason_code IS NOT NULL", name="ck_community_precheck_results_non_pass_has_reason"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    precheck_id: Mapped[int] = mapped_column(Integer)
    check_code: Mapped[PrecheckCode] = mapped_column(precheck_code_enum)
    outcome: Mapped[PrecheckOutcome] = mapped_column(precheck_outcome_enum)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class CommunityCaseEvent(CreatedAtMixin, Base):
    """Immutable case timeline. A database trigger rejects UPDATE."""

    __tablename__ = "community_case_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"],
            ["community_cases.id"],
            name="fk_community_case_events_case_id_community_cases",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["actor_participant_id"],
            ["community_participants.id"],
            name="fk_community_case_events_actor_participant_id",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "event_type NOT IN ('MANUAL_ACCEPTED', 'MANUAL_REJECTED', 'EVIDENCE_REQUESTED', 'SUPERSEDED') "
            "OR (actor_participant_id IS NOT NULL AND reason_code IS NOT NULL AND reason_text IS NOT NULL)",
            name="ck_community_case_events_manual_decision_has_reason",
        ),
        CheckConstraint("case_version >= 1", name="ck_community_case_events_case_version_positive"),
        Index("ix_community_case_events_case_id_id", "case_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[CaseEventType] = mapped_column(case_event_type_enum)
    case_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int | None] = mapped_column(Integer)
    # NULL actor = the system (precheck, automatic publication or suspension).
    actor_participant_id: Mapped[int | None] = mapped_column(Integer)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    reason_text: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ContributionLedgerEntry(CreatedAtMixin, Base):
    """Append-only contribution points. A database trigger rejects UPDATE.

    At most one AWARD and one REVOKE per participant per case. FREEZE/UNFREEZE
    carry no points; a frozen award does not count toward points or level.
    """

    __tablename__ = "contribution_ledger"
    __table_args__ = (
        ForeignKeyConstraint(
            ["participant_id"],
            ["community_participants.id"],
            name="fk_contribution_ledger_participant_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["case_id"],
            ["community_cases.id"],
            name="fk_contribution_ledger_case_id_community_cases",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["reverses_entry_id"],
            ["contribution_ledger.id"],
            name="fk_contribution_ledger_reverses_entry_id_contribution_ledger",
        ),
        CheckConstraint(
            "CASE entry_type WHEN 'AWARD' THEN points = 1 WHEN 'REVOKE' THEN points = -1 ELSE points = 0 END",
            name="ck_contribution_ledger_points_by_entry_type",
        ),
        CheckConstraint(
            "(entry_type = 'AWARD') = (reverses_entry_id IS NULL)",
            name="ck_contribution_ledger_non_award_references_award",
        ),
        Index(
            "uq_contribution_ledger_one_award",
            "case_id",
            "participant_id",
            unique=True,
            postgresql_where=text("entry_type = 'AWARD'"),
        ),
        Index(
            "uq_contribution_ledger_one_revoke",
            "case_id",
            "participant_id",
            unique=True,
            postgresql_where=text("entry_type = 'REVOKE'"),
        ),
        Index("ix_contribution_ledger_participant_id", "participant_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    participant_id: Mapped[int] = mapped_column(Integer)
    case_id: Mapped[int] = mapped_column(Integer)
    entry_type: Mapped[ContributionEntryType] = mapped_column(contribution_entry_type_enum)
    reason: Mapped[ContributionReason] = mapped_column(contribution_reason_enum)
    points: Mapped[int] = mapped_column(SmallInteger)
    reverses_entry_id: Mapped[int | None] = mapped_column(BigInteger)


class IdempotencyRecord(CreatedAtMixin, Base):
    """Stored result of an Idempotency-Key write, kept 24 hours."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_idempotency_records_user_id_users", ondelete="CASCADE"
        ),
        UniqueConstraint(
            "user_id", "operation", "idempotency_key", name="uq_idempotency_records_user_id_operation_idempotency_key"
        ),
        CheckConstraint("char_length(idempotency_key) BETWEEN 1 AND 128", name="ck_idempotency_records_key_length"),
        CheckConstraint(f"request_sha256 {SHA256_SQL}", name="ck_idempotency_records_request_sha256_hex"),
        CheckConstraint("response_status BETWEEN 200 AND 599", name="ck_idempotency_records_response_status_range"),
        CheckConstraint("expires_at > created_at", name="ck_idempotency_records_expires_after_created"),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(SmallInteger)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
