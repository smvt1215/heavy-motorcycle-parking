"""Preloaded discovery snapshots, independent of HTTP and persistence."""

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from app.domain.parking import ParkingFacts, Provenance, ZoneFacts


@dataclass(frozen=True)
class RateRuleFact:
    rule_id: int
    day_type: str = "ALL"
    start_time: time | None = None
    end_time: time | None = None
    start_minute: int | None = None
    end_minute: int | None = None
    amount: Decimal | None = None
    unit_minutes: int | None = None
    max_amount: Decimal | None = None
    schedule: dict[str, Any] | None = None


@dataclass(frozen=True)
class RateFact:
    rate_id: int
    zone_id: int
    vehicle: str | None
    rate_type: str | None
    parse_status: str
    provenance: Provenance
    currency: str = "TWD"
    base_amount: Decimal | None = None
    unit_minutes: int | None = None
    free_minutes: int | None = None
    daily_max_amount: Decimal | None = None
    raw_text: str | None = None
    description: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    schedule: dict[str, Any] | None = None
    rules: tuple[RateRuleFact, ...] = ()
    supporting_sources: tuple[Provenance, ...] = ()


@dataclass(frozen=True)
class RealtimeFact:
    observation_id: int
    zone_id: int
    status: str
    available: int | None
    total: int | None
    provenance: Provenance
    freshness_seconds: int = 120
    freshness_uses_source_timestamp: bool = False


@dataclass(frozen=True)
class EntranceFact:
    entrance_id: int
    name: str | None
    lat: float | None
    lng: float | None
    access: str
    provenance: Provenance
    entrance_type: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ZoneSnapshot:
    facts: ZoneFacts
    name: str | None = None
    capacity: int | None = None
    rates: tuple[RateFact, ...] = ()
    realtime: RealtimeFact | None = None


@dataclass(frozen=True)
class LotSnapshot:
    facts: ParkingFacts
    name: str
    lat: float
    lng: float
    zones: tuple[ZoneSnapshot, ...] = ()
    entrances: tuple[EntranceFact, ...] = ()
    distance_m: int = 0
