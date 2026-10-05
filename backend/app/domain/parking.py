"""Immutable facts supplied by repositories/adapters to the compatibility engine."""

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Literal

Vehicle = Literal["GREEN", "WHITE", "YELLOW", "RED", "CAR"]
SpaceType = Literal["HEAVY_ONLY", "MOTO_SHARED", "CAR_SHARED", "LIGHT_MOTO_ONLY"]
RuleKind = Literal["BASELINE", "EXCEPTION"]
VEHICLES: tuple[Vehicle, ...] = ("GREEN", "WHITE", "YELLOW", "RED", "CAR")
SPACE_TYPES: tuple[SpaceType, ...] = ("HEAVY_ONLY", "MOTO_SHARED", "CAR_SHARED", "LIGHT_MOTO_ONLY")


def _integer_id(value: int, field: str) -> None:
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer ID, not a string or boolean")


def absolute_instant(value: datetime) -> datetime:
    """Require an explicit instant; never guess a timezone for naive input."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An offset-aware datetime is required")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class Provenance:
    source_id: int
    source_type: str | None = None
    source_record_id: str | None = None
    source_updated_at: datetime | None = None
    fetched_at: datetime | None = None
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        _integer_id(self.source_id, "source_id")
        for value in (self.source_updated_at, self.fetched_at, self.verified_at):
            if value is not None:
                absolute_instant(value)


@dataclass(frozen=True)
class RuleFact:
    rule_id: int
    parking_id: int
    zone_id: int | None
    rule_kind: RuleKind
    authority_priority: int
    provenance: Provenance
    green_plate_allowed: bool | None = None
    white_plate_allowed: bool | None = None
    yellow_plate_allowed: bool | None = None
    red_plate_allowed: bool | None = None
    car_allowed: bool | None = None
    active: bool = True
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    schedule: dict[str, Any] | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        _integer_id(self.rule_id, "rule_id")
        _integer_id(self.parking_id, "parking_id")
        if self.zone_id is not None:
            _integer_id(self.zone_id, "zone_id")
        if not isinstance(self.provenance, Provenance):
            raise ValueError("Rule evidence must provide Provenance")
        if self.rule_kind not in ("BASELINE", "EXCEPTION"):
            raise ValueError("Unsupported rule kind")
        if type(self.authority_priority) is not int:
            raise ValueError("Authority priority must be an explicitly configured integer")
        if type(self.active) is not bool:
            raise ValueError("Rule activity must be a boolean")
        for field in (
            "green_plate_allowed",
            "white_plate_allowed",
            "yellow_plate_allowed",
            "red_plate_allowed",
            "car_allowed",
        ):
            value = getattr(self, field)
            if value is not None and type(value) is not bool:
                raise ValueError("Vehicle permissions must be TRUE, FALSE or NULL")
        start = absolute_instant(self.effective_from) if self.effective_from is not None else None
        end = absolute_instant(self.effective_to) if self.effective_to is not None else None
        if start is not None and end is not None and start >= end:
            raise ValueError("Effective windows must be nonempty [from, to)")
        if self.confidence is not None and (
            type(self.confidence) not in (float, int) or not isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("Confidence must be finite and between zero and one")


@dataclass(frozen=True)
class ParkingFacts:
    parking_id: int
    rules: tuple[RuleFact, ...] = ()

    def __post_init__(self) -> None:
        _integer_id(self.parking_id, "parking_id")


@dataclass(frozen=True)
class ZoneFacts:
    zone_id: int
    parking_id: int
    space_type: SpaceType

    def __post_init__(self) -> None:
        _integer_id(self.zone_id, "zone_id")
        _integer_id(self.parking_id, "parking_id")
        if not isinstance(self.space_type, str) or str(self.space_type) not in SPACE_TYPES:
            raise ValueError("Unsupported parking space type")
        object.__setattr__(self, "space_type", str(self.space_type))
