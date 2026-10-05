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
    GREEN = "GREEN"
    WHITE = "WHITE"
    YELLOW = "YELLOW"
    RED = "RED"
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
    PERMISSION_CORRECTION = "PERMISSION_CORRECTION"
    RATE_CORRECTION = "RATE_CORRECTION"
    AVAILABILITY_CORRECTION = "AVAILABILITY_CORRECTION"
    ENTRANCE_CORRECTION = "ENTRANCE_CORRECTION"
    LOCATION_CORRECTION = "LOCATION_CORRECTION"
    CLOSURE = "CLOSURE"
    OTHER = "OTHER"


class ReportStatus(StrEnum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


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
