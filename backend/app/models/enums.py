"""Domain enums persisted as native named PostgreSQL enum types.

Member names equal their values so the database labels match the wire values.
Migration `002_core_schema` declares the same type names and labels explicitly.
"""

from enum import StrEnum

import sqlalchemy as sa


class DataSourceType(StrEnum):
    GOVERNMENT = "GOVERNMENT"
    OPERATOR = "OPERATOR"
    COMMUNITY = "COMMUNITY"
    MANUAL = "MANUAL"


class ParkingSpaceType(StrEnum):
    HEAVY_ONLY = "HEAVY_ONLY"
    MOTO_SHARED = "MOTO_SHARED"
    CAR_SHARED = "CAR_SHARED"
    LIGHT_MOTO_ONLY = "LIGHT_MOTO_ONLY"


class VehicleType(StrEnum):
    NORMAL_HEAVY = "NORMAL_HEAVY"
    LARGE_HEAVY = "LARGE_HEAVY"
    CAR = "CAR"


class RuleKind(StrEnum):
    BASELINE = "BASELINE"
    EXCEPTION = "EXCEPTION"


class RateType(StrEnum):
    FREE = "FREE"
    HOURLY = "HOURLY"
    PER_ENTRY = "PER_ENTRY"
    TIME_BLOCK = "TIME_BLOCK"
    PROGRESSIVE = "PROGRESSIVE"
    FLAT = "FLAT"
    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    CUSTOM = "CUSTOM"


class RateParseStatus(StrEnum):
    PARSED = "PARSED"
    PARTIALLY_PARSED = "PARTIALLY_PARSED"
    RAW_ONLY = "RAW_ONLY"
    INVALID = "INVALID"


class RateDayType(StrEnum):
    ALL = "ALL"
    WEEKDAY = "WEEKDAY"
    WEEKEND = "WEEKEND"
    HOLIDAY = "HOLIDAY"
    SPECIAL = "SPECIAL"


class RealtimeStatus(StrEnum):
    """Observed availability. Freshness (FRESH/STALE/UNKNOWN) is derived, never stored here."""

    AVAILABLE = "AVAILABLE"
    FULL = "FULL"
    UNKNOWN = "UNKNOWN"
    CLOSED = "CLOSED"


class EntranceType(StrEnum):
    VEHICLE = "VEHICLE"
    PEDESTRIAN = "PEDESTRIAN"
    MIXED = "MIXED"


class ImportBatchStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_SUCCEEDED = "PARTIALLY_SUCCEEDED"
    FAILED = "FAILED"


class RawRecordStatus(StrEnum):
    PENDING = "PENDING"
    NORMALIZED = "NORMALIZED"
    INVALID = "INVALID"


class ReportType(StrEnum):
    PARKING_ALLOWED = "PARKING_ALLOWED"
    PARKING_NOT_ALLOWED = "PARKING_NOT_ALLOWED"
    WRONG_SPACE_TYPE = "WRONG_SPACE_TYPE"
    WRONG_RATE = "WRONG_RATE"
    WRONG_AVAILABILITY = "WRONG_AVAILABILITY"
    WRONG_ENTRANCE = "WRONG_ENTRANCE"
    CLOSED = "CLOSED"
    PLATE_RECOGNITION_FAILED = "PLATE_RECOGNITION_FAILED"
    GATE_SENSOR_FAILED = "GATE_SENSOR_FAILED"
    OTHER = "OTHER"


class ReportStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class UserRole(StrEnum):
    USER = "USER"
    MODERATOR = "MODERATOR"


# Community verification (migration 006). See docs/community-verification-contract.md.


class CommunityFactType(StrEnum):
    """Low-risk observations publish after corroboration; the rest need a verified source."""

    LIGHTING = "LIGHTING"
    RAIN_COVER = "RAIN_COVER"
    CHARGING = "CHARGING"
    ENTRANCE_LOCATION = "ENTRANCE_LOCATION"
    PARKING_PERMISSION = "PARKING_PERMISSION"
    RATE = "RATE"
    ENTRANCE_ACCESS = "ENTRANCE_ACCESS"


LOW_RISK_FACT_TYPES = frozenset(
    {
        CommunityFactType.LIGHTING,
        CommunityFactType.RAIN_COVER,
        CommunityFactType.CHARGING,
        CommunityFactType.ENTRANCE_LOCATION,
    }
)


class SourceFactKind(StrEnum):
    PERMISSION = "PERMISSION"
    RATE = "RATE"
    REALTIME = "REALTIME"
    ENTRANCE = "ENTRANCE"
    FACILITY = "FACILITY"


class CaseReviewStatus(StrEnum):
    PRECHECK_PENDING = "PRECHECK_PENDING"
    AWAITING_CORROBORATION = "AWAITING_CORROBORATION"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class CasePublicationState(StrEnum):
    """Stored lifecycle. EXPIRED is derived at read time and never stored."""

    UNPUBLISHED = "UNPUBLISHED"
    PUBLISHED = "PUBLISHED"
    SUSPENDED = "SUSPENDED"
    WITHDRAWN = "WITHDRAWN"


class PublicationStatus(StrEnum):
    """API publication status: the stored state plus derived EXPIRED."""

    UNPUBLISHED = "UNPUBLISHED"
    PUBLISHED = "PUBLISHED"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"


class PublicationBasis(StrEnum):
    COMMUNITY_CORROBORATED = "COMMUNITY_CORROBORATED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    VERIFIED_SOURCE = "VERIFIED_SOURCE"


class CaseStance(StrEnum):
    SUPPORT = "SUPPORT"
    OPPOSE = "OPPOSE"
    CANNOT_CONFIRM = "CANNOT_CONFIRM"


class PhotoTimeStatus(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    TIMEZONE_UNKNOWN = "TIMEZONE_UNKNOWN"
    INVALID = "INVALID"
    FUTURE = "FUTURE"
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"


class PrecheckOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_MANUAL = "NEEDS_MANUAL"


class PrecheckCode(StrEnum):
    IDENTITY = "IDENTITY"
    REQUIRED_FIELDS = "REQUIRED_FIELDS"
    SCOPE = "SCOPE"
    PHOTO_TIME = "PHOTO_TIME"
    SOURCE_APPLICABILITY = "SOURCE_APPLICABILITY"
    EVIDENCE_DUPLICATE = "EVIDENCE_DUPLICATE"
    CORROBORATION = "CORROBORATION"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    PUBLICATION_ELIGIBILITY = "PUBLICATION_ELIGIBILITY"


class CaseEventType(StrEnum):
    SUBMITTED = "SUBMITTED"
    REVISION_SUBMITTED = "REVISION_SUBMITTED"
    PRECHECK_COMPLETED = "PRECHECK_COMPLETED"
    STANCE_RECORDED = "STANCE_RECORDED"
    STANCE_WITHDRAWN = "STANCE_WITHDRAWN"
    STANCE_INVALIDATED = "STANCE_INVALIDATED"
    PUBLISHED = "PUBLISHED"
    SUSPENDED = "SUSPENDED"
    RESUMED = "RESUMED"
    WITHDRAWN = "WITHDRAWN"
    MANUAL_ACCEPTED = "MANUAL_ACCEPTED"
    MANUAL_REJECTED = "MANUAL_REJECTED"
    EVIDENCE_REQUESTED = "EVIDENCE_REQUESTED"
    SUPERSEDED = "SUPERSEDED"
    PHOTO_DELETED = "PHOTO_DELETED"


MANUAL_DECISION_EVENTS = frozenset(
    {
        CaseEventType.MANUAL_ACCEPTED,
        CaseEventType.MANUAL_REJECTED,
        CaseEventType.EVIDENCE_REQUESTED,
        CaseEventType.SUPERSEDED,
    }
)


class ContributionEntryType(StrEnum):
    AWARD = "AWARD"
    FREEZE = "FREEZE"
    UNFREEZE = "UNFREEZE"
    REVOKE = "REVOKE"


class ContributionReason(StrEnum):
    ORIGINAL_REPORT = "ORIGINAL_REPORT"
    CORROBORATION = "CORROBORATION"
    UPHELD_OBJECTION = "UPHELD_OBJECTION"


# Shared column types; one instance per PostgreSQL type name.
data_source_type_enum = sa.Enum(DataSourceType, name="data_source_type")
parking_space_type_enum = sa.Enum(ParkingSpaceType, name="parking_space_type")
vehicle_type_enum = sa.Enum(VehicleType, name="vehicle_type")
rule_kind_enum = sa.Enum(RuleKind, name="parking_rule_kind")
rate_type_enum = sa.Enum(RateType, name="parking_rate_type")
rate_parse_status_enum = sa.Enum(RateParseStatus, name="rate_parse_status")
rate_day_type_enum = sa.Enum(RateDayType, name="rate_day_type")
realtime_status_enum = sa.Enum(RealtimeStatus, name="realtime_status")
entrance_type_enum = sa.Enum(EntranceType, name="parking_entrance_type")
import_batch_status_enum = sa.Enum(ImportBatchStatus, name="import_batch_status")
raw_record_status_enum = sa.Enum(RawRecordStatus, name="raw_record_status")
report_type_enum = sa.Enum(ReportType, name="user_report_type")
report_status_enum = sa.Enum(ReportStatus, name="user_report_status")
community_fact_type_enum = sa.Enum(CommunityFactType, name="community_fact_type")
source_fact_kind_enum = sa.Enum(SourceFactKind, name="source_fact_kind")
case_review_status_enum = sa.Enum(CaseReviewStatus, name="case_review_status")
case_publication_state_enum = sa.Enum(CasePublicationState, name="case_publication_state")
publication_basis_enum = sa.Enum(PublicationBasis, name="publication_basis")
case_stance_enum = sa.Enum(CaseStance, name="case_stance")
photo_time_status_enum = sa.Enum(PhotoTimeStatus, name="photo_time_status")
precheck_outcome_enum = sa.Enum(PrecheckOutcome, name="precheck_outcome")
precheck_code_enum = sa.Enum(PrecheckCode, name="precheck_code")
case_event_type_enum = sa.Enum(CaseEventType, name="case_event_type")
contribution_entry_type_enum = sa.Enum(ContributionEntryType, name="contribution_entry_type")
contribution_reason_enum = sa.Enum(ContributionReason, name="contribution_reason")
