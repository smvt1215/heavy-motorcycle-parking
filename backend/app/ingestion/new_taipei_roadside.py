"""New Taipei roadside cells; undocumented status codes never become occupancy.

The feed describes individual cells. Exact ordinary motorcycle labels and an
explicit charging mode are required for policy scope; fees and isnowcash alone
prove neither scope nor permission. Unknown memo restrictions block the grant.
"""

import re
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.ingestion.common import _coordinate_number, _in_taiwan, _optional_text, _require_record
from app.ingestion.contracts import (
    NormalizedLot,
    NormalizedObservation,
    NormalizedRate,
    NormalizedRealtime,
    NormalizedZone,
    ParsedRate,
    ParsedRateRule,
    RecordError,
)
from app.ingestion.new_taipei import NewTaipeiParkingAdapter, _text
from app.ingestion.policies import RoadsideScope, apply_roadside_policy
from app.ingestion.rate_parser import parse_rate_text
from app.ingestion.sources import NEW_TAIPEI_ROADSIDE_SOURCE
from app.models.enums import ParkingSpaceType, RateParseStatus, RateType, RealtimeStatus, VehicleType

# Exact single `pay` charging modes. 限時計次收 (time-limited per entry) is the mode
# every real motorcycle cell carried on 2026-10-10. Combined weekday/holiday values
# such as "限時計次收,假日限時計次收" and holiday-only modes stay unresolved.
PAID_MODES = {
    "計時收費": {RateType.HOURLY, RateType.TIME_BLOCK},
    "計次收費": {RateType.PER_ENTRY},
    "限時計次收": {RateType.PER_ENTRY},
}


def charging_schedule(record: dict[str, Any]) -> dict | None:
    days = {"週一-週五": [0, 1, 2, 3, 4], "週一-週六": [0, 1, 2, 3, 4, 5], "每天": list(range(7))}
    weekday = days.get(record.get("day"))
    hours = record.get("hour")
    if weekday is None or not isinstance(hours, str):
        return None
    if hours == "00:00-24:00":
        return {"timezone": "Asia/Taipei", "weekdays": weekday}
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d", hours):
        return None
    start, end = hours.split("-")
    if start == end:
        return None
    return {"timezone": "Asia/Taipei", "weekdays": weekday, "start_time": start, "end_time": end}


class NewTaipeiRoadsideAdapter(NewTaipeiParkingAdapter):
    city = "new_taipei_roadside"
    source = NEW_TAIPEI_ROADSIDE_SOURCE

    def record_key(self, record: Any) -> str | None:
        return _optional_text(record.get("id")) if isinstance(record, dict) else None

    def raw_rate_text(self, record: Any) -> str | None:
        return record.get("paycash") if isinstance(record, dict) and isinstance(record.get("paycash"), str) else None

    def source_updated_at(self, payload: Any) -> datetime | None:
        return None

    def normalize_static(self, record: Any) -> NormalizedLot:
        rec = _require_record(record)
        external_id = _text(rec, "id", 240, required=True)
        cell_id = _text(rec, "cellid", 64, required=True)
        road = _text(rec, "roadname", 100, required=True)
        category = _text(rec, "name", 64, required=True)
        lat, lng = _coordinate_number(rec.get("latitude")), _coordinate_number(rec.get("longitude"))
        if lat is None or lng is None or not _in_taiwan(lat, lng):
            raise RecordError("INVALID_COORDINATES", "Roadside latitude/longitude must be valid Taiwan coordinates")
        motor = category == "機車停車位"
        car = category == "汽車停車位"
        # Other reserved/ambiguous categories stay UNKNOWN; no generic vehicle grant.
        zone = NormalizedZone(
            key="cell",
            name=category,
            space_type=ParkingSpaceType.MOTO_SHARED if motor else ParkingSpaceType.CAR_SHARED,
            capacity=1,
            normal_heavy=True if motor else None,
            car=True if car else None,
        )
        if rec.get("memo") not in ("", "智慧化車格地磁;"):
            zone = replace(zone, normal_heavy=None, car=None)
        schedule = charging_schedule(rec)
        terms = self.raw_rate_text(rec)
        if terms:
            parsed = parse_rate_text(terms)
            charging_types = {"免費": {RateType.FREE}, **PAID_MODES}
            mode = rec.get("pay")
            supported = charging_types.get(mode, set()) if isinstance(mode, str) else set()
            if parsed.parse_status == RateParseStatus.PARSED and parsed.rate_type not in supported:
                parsed = ParsedRate(RateParseStatus.PARTIALLY_PARSED, terms)
            parsed = replace(
                parsed,
                rules=(
                    *parsed.rules,
                    ParsedRateRule(
                        schedule=schedule if schedule is not None else {"unresolved_roadside_schedule": True}
                    ),
                ),
            )
            zone = replace(
                zone,
                rates=(
                    NormalizedRate(
                        key="posted",
                        vehicle=VehicleType.NORMAL_HEAVY if motor else VehicleType.CAR if car else None,
                        parsed=parsed,
                        raw_payload={k: rec.get(k) for k in ("pay", "paycash", "day", "hour", "memo")},
                    ),
                ),
            )
        memo = rec.get("memo")
        zone = apply_roadside_policy(
            zone,
            RoadsideScope(
                city="new_taipei",
                public=True if rec.get("countycode") == "65000" else None,
                paid=True if rec.get("pay") in PAID_MODES else None,
                ordinary_motorcycle=motor,
                special_restriction=False if memo in ("", "智慧化車格地磁;") else None,
                schedule=schedule,
                evidence={k: rec.get(k) for k in ("id", "cellid", "name", "countycode", "pay", "day", "hour", "memo")},
            ),
        )
        # Preserve category and cell identity in the display; the full road name
        # remains the address and the original record is retained as evidence.
        road_limit = 200 - len(category) - len(cell_id) - 2
        display_road = road if len(road) <= road_limit else f"{road[: road_limit - 1]}…"
        return NormalizedLot(
            external_id=external_id,
            name=f"{display_road} {category} {cell_id}",
            address=road,
            district=None,
            lat=lat,
            lng=lng,
            zones=(zone,),
        )

    def normalize_realtime(self, record: Any) -> NormalizedRealtime:
        rec = _require_record(record)
        external_id = _text(rec, "id", 240, required=True)
        return NormalizedRealtime(external_id, (NormalizedObservation("cell", RealtimeStatus.UNKNOWN, None),))
