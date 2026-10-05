"""Selected-vehicle endpoint orchestration; routers only validate and delegate."""

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.domain.discovery import LotSnapshot, ZoneSnapshot
from app.domain.errors import DiscoveryError
from app.domain.parking import absolute_instant
from app.domain.schedules import HolidayCalendar
from app.schemas.parking import NearbyQuery
from app.services.compatibility import ParkingCompatibilityService, is_nearby_eligible, rollup_nearby_compatibility
from app.services.cursors import SORT_VERSION, CursorCodec, query_fingerprint
from app.services.ranking import ranking_score, sort_key
from app.services.rates import resolve_rates
from app.services.realtime import aggregate_availability, is_available_only, resolve_availability


class DiscoveryRepository(Protocol):
    async def nearby(self, lat: float, lng: float, radius: int) -> tuple[LotSnapshot, ...]: ...

    async def detail(self, parking_id: int) -> LotSnapshot | None: ...


class ParkingDiscoveryService:
    def __init__(
        self,
        repository: DiscoveryRepository,
        cursor_codec: CursorCodec,
        holiday_calendar: HolidayCalendar | None = None,
    ):
        self.repository = repository
        self.cursor_codec = cursor_codec
        self.holiday_calendar = holiday_calendar
        self.compatibility = ParkingCompatibilityService(holiday_calendar)

    def _zone(self, lot: LotSnapshot, zone: ZoneSnapshot, vehicle: str, evaluation_at: datetime, now: datetime):
        result = self.compatibility.evaluate(lot.facts, zone.facts, vehicle, evaluation_at)
        evidence = result.to_dict()["provenance"]
        # A singular source is exposed only when exactly one rule won. Full tied
        # evidence always survives in an additive field, including contradictions.
        compatibility = {
            "status": result.status.value,
            "vehicle": vehicle,
            "reason": result.reason,
            "confidence": result.confidence,
            "provenance": evidence[0] if len(evidence) == 1 else None,
            "rule_evidence": [
                {"rule_id": rule_id, "provenance": source}
                for rule_id, source in zip(result.rule_ids, evidence, strict=True)
            ],
        }
        summary, rates = resolve_rates(zone.rates, vehicle, evaluation_at, self.holiday_calendar)
        base = {
            "zone_id": zone.facts.zone_id,
            "name": zone.name,
            "space_type": zone.facts.space_type,
            "capacity": zone.capacity,
            "compatibility": compatibility,
            "rate_summary": summary,
            "availability": resolve_availability(zone.realtime, now),
        }
        return base, rates, result

    async def nearby(self, query: NearbyQuery, received_at: datetime) -> dict:
        now = absolute_instant(received_at)
        fingerprint = query_fingerprint(query.model_dump(exclude={"at", "cursor"}))
        cursor = self.cursor_codec.decode(query.cursor, fingerprint, query.at) if query.cursor is not None else None
        evaluation_at = cursor.evaluation_at if cursor is not None else (query.at or now)
        lots = await self.repository.nearby(query.lat, query.lng, query.radius)
        items = []
        for lot in lots:
            returned = []
            results = []
            for zone in lot.zones:
                base, _, result = self._zone(lot, zone, query.vehicle, evaluation_at, now)
                if not is_nearby_eligible(result, query.include_unknown):
                    continue
                if query.space_type is not None and zone.facts.space_type != query.space_type:
                    continue
                if query.available_only and not is_available_only(result.status.value, base["availability"]):
                    continue
                rate = base["rate_summary"]
                if query.hourly_rate_max_twd is not None and (
                    result.status != "ALLOWED"
                    or rate is None
                    or not rate["comparison_eligible"]
                    or rate["comparison_hourly_rate_twd"] is None
                    or Decimal(str(rate["comparison_hourly_rate_twd"])) > Decimal(str(query.hourly_rate_max_twd))
                ):
                    continue
                if query.daily_max_required and (
                    result.status != "ALLOWED" or rate is None or rate["daily_max_twd"] is None
                ):
                    continue
                returned.append(base)
                results.append(result)
            status = rollup_nearby_compatibility(results)
            if status is None:
                continue
            availability = aggregate_availability(returned)
            items.append(
                {
                    "id": lot.facts.parking_id,
                    "name": lot.name,
                    "distance_m": lot.distance_m,
                    "location": {"lat": lot.lat, "lng": lot.lng},
                    "compatibility": {"status": status.value, "vehicle": query.vehicle},
                    "zones": returned,
                    "availability_summary": availability,
                    "ranking_group": 0 if status == "ALLOWED" else 1,
                    "ranking_score_bp": (
                        ranking_score(lot.distance_m, query.radius, returned, availability, lot.entrances)
                        if status == "ALLOWED"
                        else None
                    ),
                }
            )
        ordered = sorted(items, key=sort_key)
        if cursor is not None:
            ordered = [item for item in ordered if sort_key(item) > cursor.key]
        has_more = len(ordered) > query.limit
        page = ordered[: query.limit]
        next_cursor = (
            self.cursor_codec.encode(fingerprint, evaluation_at, sort_key(page[-1])) if has_more and page else None
        )
        return {
            "evaluation_at": evaluation_at,
            "sort_version": SORT_VERSION,
            "items": page,
            "page": {"next_cursor": next_cursor, "has_more": has_more},
        }

    async def selected(self, parking_id: int, vehicle: str, at: datetime | None, now: datetime, mode: str) -> dict:
        now = absolute_instant(now)
        evaluation_at = absolute_instant(at) if at is not None else now
        lot = await self.repository.detail(parking_id)
        if lot is None:
            raise DiscoveryError("PARKING_NOT_FOUND", "The parking lot was not found.", 404)
        zones = []
        for zone in lot.zones:
            base, rates, _ = self._zone(lot, zone, vehicle, evaluation_at, now)
            if mode == "rates":
                base["rates"] = rates
            zones.append(base)
        response = {"vehicle": vehicle, "evaluation_at": evaluation_at, "zones": zones}
        if mode == "detail":
            response.update(
                id=parking_id,
                name=lot.name,
                location={"lat": lot.lat, "lng": lot.lng},
                entrances=[
                    {
                        "id": entrance.entrance_id,
                        "name": entrance.name,
                        "location": (
                            {"lat": entrance.lat, "lng": entrance.lng}
                            if entrance.lat is not None and entrance.lng is not None
                            else None
                        ),
                        "entrance_type": entrance.entrance_type,
                        "heavy_motorcycle_access": entrance.access,
                        "notes": entrance.notes,
                        "provenance": {
                            "source_id": entrance.provenance.source_id,
                            "source_type": entrance.provenance.source_type,
                            "source_record_id": entrance.provenance.source_record_id,
                            "source_updated_at": entrance.provenance.source_updated_at,
                            "fetched_at": entrance.provenance.fetched_at,
                            "verified_at": entrance.provenance.verified_at,
                        },
                    }
                    for entrance in lot.entrances
                ],
            )
        else:
            response["parking_id"] = parking_id
        if mode == "realtime":
            response["availability_summary"] = aggregate_availability(zones)
        return response
