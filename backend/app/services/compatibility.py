"""Pure selected-vehicle parking compatibility resolution and nearby policy.

The service consumes immutable domain facts only; it performs no ORM, HTTP,
UI or network access and never reads the wall clock.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.domain.parking import (
    VEHICLES,
    ParkingFacts,
    Provenance,
    RuleFact,
    SpaceType,
    Vehicle,
    ZoneFacts,
    absolute_instant,
)
from app.domain.schedules import HolidayCalendar, ScheduleMatch, match_schedule

REASON_EXPLICIT = "explicit_vehicle_permission"
REASON_CONFLICT = "conflicting_permissions"
REASON_UNKNOWN_PERMISSION = "unknown_permission"
REASON_SCHEDULE_UNKNOWN = "schedule_unknown"
REASON_SPACE_TYPE_DEFAULT = "space_type_default"

_PERMISSION_FIELDS: dict[str, str] = {
    "NORMAL_HEAVY": "normal_heavy_allowed",
    "LARGE_HEAVY": "large_heavy_allowed",
}

# Descriptive defaults apply only when no explicit rule exists.
_SPACE_TYPE_DEFAULTS: dict[str, frozenset[str]] = {
    "HEAVY_ONLY": frozenset({"LARGE_HEAVY"}),
    "MOTO_SHARED": frozenset({"NORMAL_HEAVY", "LARGE_HEAVY"}),
    "CAR_SHARED": frozenset({"LARGE_HEAVY"}),
    "LIGHT_MOTO_ONLY": frozenset({"NORMAL_HEAVY"}),
}

_HEAVY_VEHICLES = frozenset({"LARGE_HEAVY"})


class CompatibilityStatus(StrEnum):
    ALLOWED = "ALLOWED"
    NOT_ALLOWED = "NOT_ALLOWED"
    UNKNOWN = "UNKNOWN"


def _rfc3339(value: datetime | None) -> str | None:
    if value is None:
        return None
    return absolute_instant(value).isoformat().replace("+00:00", "Z")


def _provenance_dict(provenance: Provenance) -> dict[str, Any]:
    return {
        "source_id": provenance.source_id,
        "source_type": provenance.source_type,
        "source_record_id": provenance.source_record_id,
        "source_updated_at": _rfc3339(provenance.source_updated_at),
        "fetched_at": _rfc3339(provenance.fetched_at),
        "verified_at": _rfc3339(provenance.verified_at),
    }


@dataclass(frozen=True)
class CompatibilityResult:
    status: CompatibilityStatus
    parking_id: int
    zone_id: int
    vehicle: Vehicle
    evaluation_at: datetime
    space_type: SpaceType
    reason: str
    provenance: tuple[Provenance, ...] = ()
    rule_ids: tuple[int, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CompatibilityStatus):
            raise ValueError("Compatibility status must be a CompatibilityStatus")
        if absolute_instant(self.evaluation_at) != self.evaluation_at or self.evaluation_at.tzinfo is not UTC:
            raise ValueError("evaluation_at must be expressed in UTC")
        if not isinstance(self.provenance, tuple) or not isinstance(self.rule_ids, tuple):
            raise ValueError("Provenance and rule IDs must be immutable tuples")
        if len(self.provenance) != len(self.rule_ids):
            raise ValueError("Every winning rule needs exactly one provenance record")
        if self.status is CompatibilityStatus.UNKNOWN and self.confidence is not None:
            raise ValueError("UNKNOWN compatibility has no confidence")

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, deterministic representation with explicit nulls."""
        return {
            "status": self.status.value,
            "parking_id": self.parking_id,
            "zone_id": self.zone_id,
            "vehicle": self.vehicle,
            "evaluation_at": _rfc3339(self.evaluation_at),
            "space_type": self.space_type,
            "reason": self.reason,
            "provenance": [_provenance_dict(item) for item in self.provenance],
            "rule_ids": list(self.rule_ids),
            "confidence": self.confidence,
        }


class ParkingCompatibilityService:
    """Resolve `(parking_id, zone_id, vehicle, evaluation_at)` deterministically."""

    __slots__ = ("_holiday_calendar",)

    def __init__(self, holiday_calendar: HolidayCalendar | None = None) -> None:
        if holiday_calendar is not None and not callable(getattr(holiday_calendar, "is_holiday", None)):
            raise ValueError("Holiday calendar must provide is_holiday(day)")
        self._holiday_calendar = holiday_calendar

    def evaluate(
        self, parking: ParkingFacts, zone: ZoneFacts, vehicle: Vehicle, timestamp: datetime
    ) -> CompatibilityResult:
        if not isinstance(parking, ParkingFacts) or not isinstance(zone, ZoneFacts):
            raise ValueError("Parking and zone must be domain fact objects")
        if not isinstance(vehicle, str):
            raise ValueError("An explicit supported vehicle is required")
        vehicle = str(vehicle)
        if vehicle not in VEHICLES:
            raise ValueError("An explicit supported vehicle is required")
        evaluation_at = absolute_instant(timestamp)
        if zone.parking_id != parking.parking_id:
            raise ValueError("Zone does not belong to the requested parking lot")
        rules = tuple(parking.rules)
        if any(not isinstance(rule, RuleFact) for rule in rules):
            raise ValueError("Parking rules must be RuleFact objects")
        if len({rule.rule_id for rule in rules}) != len(rules):
            raise ValueError("Rule IDs must be unique")

        candidates = self._candidates(rules, zone, evaluation_at)

        def result(
            status: CompatibilityStatus,
            reason: str,
            winners: tuple[tuple[RuleFact, ScheduleMatch], ...] = (),
            confidence: float | None = None,
        ) -> CompatibilityResult:
            return CompatibilityResult(
                status=status,
                parking_id=parking.parking_id,
                zone_id=zone.zone_id,
                vehicle=vehicle,
                evaluation_at=evaluation_at,
                space_type=zone.space_type,
                reason=reason,
                provenance=tuple(rule.provenance for rule, _ in winners),
                rule_ids=tuple(rule.rule_id for rule, _ in winners),
                confidence=confidence,
            )

        if not candidates:
            allowed = vehicle in _SPACE_TYPE_DEFAULTS[zone.space_type]
            status = CompatibilityStatus.ALLOWED if allowed else CompatibilityStatus.NOT_ALLOWED
            return result(status, REASON_SPACE_TYPE_DEFAULT)

        top_key = max(_precedence(rule) for rule, _ in candidates)
        # rule_id orders evidence for stable output only; it never decides legality.
        winners = tuple(
            sorted((pair for pair in candidates if _precedence(pair[0]) == top_key), key=lambda pair: pair[0].rule_id)
        )

        if any(match is ScheduleMatch.UNKNOWN for _, match in winners):
            return result(CompatibilityStatus.UNKNOWN, REASON_SCHEDULE_UNKNOWN, winners)

        field = _PERMISSION_FIELDS[vehicle]
        permissions = {getattr(rule, field) for rule, _ in winners}
        if True in permissions and False in permissions:
            return result(CompatibilityStatus.UNKNOWN, REASON_CONFLICT, winners)
        if None in permissions:
            return result(CompatibilityStatus.UNKNOWN, REASON_UNKNOWN_PERMISSION, winners)

        known = [rule.confidence for rule, _ in winners if rule.confidence is not None]
        confidence = float(min(known)) if len(known) == len(winners) else None
        status = CompatibilityStatus.ALLOWED if permissions == {True} else CompatibilityStatus.NOT_ALLOWED
        return result(status, REASON_EXPLICIT, winners, confidence)

    def _candidates(
        self, rules: tuple[RuleFact, ...], zone: ZoneFacts, evaluation_at: datetime
    ) -> tuple[tuple[RuleFact, ScheduleMatch], ...]:
        """Applicable rules plus schedule-UNKNOWN rules, which stay as uncertain candidates."""
        candidates = []
        for rule in rules:
            if not rule.active:
                continue
            if rule.parking_id != zone.parking_id or rule.zone_id not in (None, zone.zone_id):
                continue
            if rule.effective_from is not None and evaluation_at < absolute_instant(rule.effective_from):
                continue
            if rule.effective_to is not None and evaluation_at >= absolute_instant(rule.effective_to):
                continue
            match = match_schedule(rule.schedule, evaluation_at, self._holiday_calendar)
            if match is ScheduleMatch.NO_MATCH:
                continue
            candidates.append((rule, match))
        return tuple(candidates)


def _precedence(rule: RuleFact) -> tuple[bool, bool, int]:
    return (rule.zone_id is not None, rule.rule_kind == "EXCEPTION", rule.authority_priority)


def _require_result(value: Any) -> CompatibilityResult:
    if not isinstance(value, CompatibilityResult):
        raise ValueError("Expected a CompatibilityResult")
    return value


def is_nearby_eligible(result: CompatibilityResult, include_unknown: bool = False) -> bool:
    """Normal nearby search policy; prohibited-location browsing is a separate mode."""
    result = _require_result(result)
    if type(include_unknown) is not bool:
        raise ValueError("include_unknown must be a boolean")
    if result.vehicle in _HEAVY_VEHICLES and result.space_type == "LIGHT_MOTO_ONLY":
        return False
    if result.status is CompatibilityStatus.ALLOWED:
        return True
    if result.status is CompatibilityStatus.UNKNOWN:
        return include_unknown
    return False


def filter_nearby_zones(
    results: Iterable[CompatibilityResult], include_unknown: bool = False
) -> tuple[CompatibilityResult, ...]:
    if type(include_unknown) is not bool:
        raise ValueError("include_unknown must be a boolean")
    return tuple(result for result in results if is_nearby_eligible(result, include_unknown))


def rollup_nearby_compatibility(returned_zones: Iterable[CompatibilityResult]) -> CompatibilityStatus | None:
    """Derive lot status from returned zones only; None means the lot is omitted."""
    zones = tuple(_require_result(zone) for zone in returned_zones)
    if any(not is_nearby_eligible(zone, include_unknown=True) for zone in zones):
        raise ValueError("Ineligible zones must be filtered before lot rollup")
    if len({(zone.parking_id, zone.vehicle, zone.evaluation_at) for zone in zones}) > 1:
        raise ValueError("Rollup requires zones of one lot, vehicle and evaluation_at")
    statuses = {zone.status for zone in zones}
    if CompatibilityStatus.ALLOWED in statuses:
        return CompatibilityStatus.ALLOWED
    if CompatibilityStatus.UNKNOWN in statuses:
        return CompatibilityStatus.UNKNOWN
    return None
