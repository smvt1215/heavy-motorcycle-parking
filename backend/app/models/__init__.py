"""ORM models. Importing this package registers every table on `Base.metadata` (used by Alembic)."""

from app.models.base import Base
from app.models.data_source import DataSource
from app.models.enums import (
    DataSourceType,
    EntranceType,
    ImportBatchStatus,
    ParkingSpaceType,
    RateDayType,
    RateParseStatus,
    RateType,
    RawRecordStatus,
    RealtimeStatus,
    ReportStatus,
    ReportType,
    RuleKind,
    UserRole,
    VehicleType,
)
from app.models.ingestion import RawImportBatch, RawParkingRecord
from app.models.parking import ParkingEntrance, ParkingFacility, ParkingLot, ParkingRule, ParkingZone
from app.models.rate import ParkingRate, ParkingRateRule, ParkingRateSource
from app.models.realtime import ParkingRealtime
from app.models.user import AccessToken, Favorite, ReportPhoto, User, UserReport, UserVehicle

__all__ = [
    "AccessToken",
    "Base",
    "DataSource",
    "DataSourceType",
    "EntranceType",
    "Favorite",
    "ImportBatchStatus",
    "ParkingEntrance",
    "ParkingFacility",
    "ParkingLot",
    "ParkingRate",
    "ParkingRateRule",
    "ParkingRateSource",
    "ParkingRealtime",
    "ParkingRule",
    "ParkingSpaceType",
    "ParkingZone",
    "RateDayType",
    "RateParseStatus",
    "RateType",
    "RawImportBatch",
    "RawParkingRecord",
    "RawRecordStatus",
    "RealtimeStatus",
    "ReportPhoto",
    "ReportStatus",
    "ReportType",
    "RuleKind",
    "User",
    "UserReport",
    "UserRole",
    "UserVehicle",
    "VehicleType",
]
