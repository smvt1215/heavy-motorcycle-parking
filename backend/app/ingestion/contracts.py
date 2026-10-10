"""City adapters emit validated snapshots; writers assign database identifiers."""

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from app.models.enums import (
    ParkingSpaceType,
    RateDayType,
    RateParseStatus,
    RateType,
    RealtimeStatus,
    RuleKind,
    VehicleType,
)


class RecordError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ParsedRateRule:
    day_type: RateDayType = RateDayType.ALL
    start_time: time | None = None
    end_time: time | None = None
    amount: Decimal | None = None
    unit_minutes: int | None = None
    start_minute: int | None = None
    end_minute: int | None = None
    max_amount: Decimal | None = None
    schedule: dict[str, Any] | None = None


@dataclass(frozen=True)
class ParsedRate:
    parse_status: RateParseStatus
    raw_text: str
    rate_type: RateType | None = None
    base_amount: Decimal | None = None
    unit_minutes: int | None = None
    free_minutes: int | None = None
    daily_max_amount: Decimal | None = None
    rules: tuple[ParsedRateRule, ...] = ()


@dataclass(frozen=True)
class NormalizedRate:
    key: str
    vehicle: VehicleType | None
    parsed: ParsedRate
    raw_payload: dict[str, Any] = field(default_factory=dict)
    policy_code: str | None = None
    effective_from: datetime | None = None


@dataclass(frozen=True)
class NormalizedRule:
    key: str
    policy_code: str
    normal_heavy: bool | None
    large_heavy: bool | None
    rule_kind: RuleKind
    authority_priority: int
    effective_from: datetime
    schedule: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedZone:
    key: str
    name: str
    space_type: ParkingSpaceType
    capacity: int | None
    normal_heavy: bool | None = None
    large_heavy: bool | None = None
    green: bool | None = None
    white: bool | None = None
    yellow: bool | None = None
    red: bool | None = None
    car: bool | None = None
    rates: tuple[NormalizedRate, ...] = ()
    rules: tuple[NormalizedRule, ...] = ()


@dataclass(frozen=True)
class NormalizedEntrance:
    key: str
    name: str | None
    lat: float | None
    lng: float | None
    heavy_access: bool | None = None


@dataclass(frozen=True)
class NormalizedLot:
    external_id: str
    name: str
    address: str | None
    district: str | None
    lat: float
    lng: float
    zones: tuple[NormalizedZone, ...]
    entrances: tuple[NormalizedEntrance, ...] = ()


@dataclass(frozen=True)
class NormalizedObservation:
    zone_key: str
    status: RealtimeStatus
    available: int | None
    total: int | None = None


@dataclass(frozen=True)
class NormalizedRealtime:
    external_id: str
    observations: tuple[NormalizedObservation, ...]


@dataclass(frozen=True)
class FeedSnapshot:
    kind: str
    payload: Any
    fetched_at: datetime
    source_updated_at: datetime | None


@dataclass(frozen=True)
class ImportResult:
    batch_id: int
    status: str
    total: int
    normalized: int
    failed: int
