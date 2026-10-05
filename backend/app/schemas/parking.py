"""Validated public v1 queries and shared response contracts."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.domain.parking import absolute_instant

PublicVehicle = Literal["YELLOW", "RED"]
SearchSpace = Literal["HEAVY_ONLY", "MOTO_SHARED", "CAR_SHARED"]
Status = Literal["ALLOWED", "NOT_ALLOWED", "UNKNOWN"]


class VehicleQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vehicle: PublicVehicle
    at: AwareDatetime | None = None

    @field_validator("at", mode="before")
    @classmethod
    def explicit_rfc3339(cls, value):
        # Pydantic also accepts numeric Unix timestamps; the wire contract does not.
        if (
            value is not None
            and not isinstance(value, datetime)
            and (
                not isinstance(value, str)
                or "T" not in value
                or not (value.endswith(("Z", "z")) or "+" in value[10:] or "-" in value[10:])
            )
        ):
            raise ValueError("at must be an RFC3339 timestamp with an offset")
        return value

    @field_validator("at")
    @classmethod
    def utc_instant(cls, value):
        return absolute_instant(value) if value is not None else None


class NearbyQuery(VehicleQuery):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    radius: int = Field(default=1500, gt=0, le=5000)
    space_type: SearchSpace | None = None
    available_only: bool = False
    hourly_rate_max_twd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    daily_max_required: bool = False
    include_unknown: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(WireModel):
    lat: float
    lng: float


class Source(WireModel):
    source_id: int
    source_type: str | None
    source_record_id: str | None = None
    source_updated_at: datetime | None
    fetched_at: datetime | None
    verified_at: datetime | None


class RuleEvidence(WireModel):
    rule_id: int
    provenance: Source


class ZoneCompatibility(WireModel):
    status: Status
    vehicle: PublicVehicle
    reason: str
    confidence: float | None
    provenance: Source | None
    rule_evidence: list[RuleEvidence]


class RateSummary(WireModel):
    display_text: str | None
    comparison_eligible: bool
    comparison_hourly_rate_twd: int | float | None
    daily_max_twd: int | float | None
    parse_status: str
    provenance: Source | None
    supporting_sources: list[Source] = Field(default_factory=list)


class Freshness(WireModel):
    status: Literal["FRESH", "STALE", "UNKNOWN"]


class Availability(WireModel):
    status: Literal["AVAILABLE", "FULL", "UNKNOWN", "CLOSED"]
    available: int | None
    total: int | None
    freshness: Freshness
    provenance: Source


class CommonZone(WireModel):
    zone_id: int
    name: str | None
    space_type: Literal["HEAVY_ONLY", "MOTO_SHARED", "CAR_SHARED", "LIGHT_MOTO_ONLY"]
    capacity: int | None
    compatibility: ZoneCompatibility
    rate_summary: RateSummary | None
    availability: Availability | None


class Rate(WireModel):
    rate_id: int
    rate_type: str | None
    currency: str
    base_amount: int | float | None
    unit_minutes: int | None
    free_minutes: int | None
    daily_max_twd: int | float | None
    description: str | None
    raw_text: str | None
    parse_status: str
    applicability: Literal["MATCH", "UNKNOWN"]
    provenance: Source
    supporting_sources: list[Source] = Field(default_factory=list)


class RatesZone(CommonZone):
    rates: list[Rate]


class Entrance(WireModel):
    id: int
    name: str | None
    location: Location | None
    entrance_type: str | None
    heavy_motorcycle_access: Status
    notes: str | None
    provenance: Source


class AvailabilitySummary(WireModel):
    status: Literal["AVAILABLE", "FULL", "UNKNOWN", "CLOSED"]
    available: int | None
    total: int | None
    coverage: Literal["COMPLETE", "PARTIAL", "NONE"]
    eligible_zone_count: int
    fresh_realtime_zone_count: int
    freshness: Freshness
    oldest_source_updated_at: datetime | None
    oldest_fetched_at: datetime | None
    contributing_sources: list[Source]


class LotCompatibility(WireModel):
    status: Literal["ALLOWED", "UNKNOWN"]
    vehicle: PublicVehicle


class NearbyItem(WireModel):
    id: int
    name: str
    distance_m: int
    location: Location
    compatibility: LotCompatibility
    zones: list[CommonZone]
    availability_summary: AvailabilitySummary
    ranking_group: int
    ranking_score_bp: int | None


class Page(WireModel):
    next_cursor: str | None
    has_more: bool


class NearbyResponse(WireModel):
    evaluation_at: datetime
    sort_version: int
    items: list[NearbyItem]
    page: Page


class DetailResponse(WireModel):
    id: int
    name: str
    vehicle: PublicVehicle
    evaluation_at: datetime
    location: Location
    zones: list[CommonZone]
    entrances: list[Entrance]


class RatesResponse(WireModel):
    parking_id: int
    vehicle: PublicVehicle
    evaluation_at: datetime
    zones: list[RatesZone]


class RealtimeResponse(WireModel):
    parking_id: int
    vehicle: PublicVehicle
    evaluation_at: datetime
    zones: list[CommonZone]
    availability_summary: AvailabilitySummary
