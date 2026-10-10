"""New Taipei City adapter for the data.ntpc.gov.tw parking datasets.

Static:   新北市路外公共停車場資訊 (B1464EF0-9C7C-4A6F-ABF7-6BDF32847E68)
Realtime: 新北市公有路外停車場即時賸餘車位數 (E09B35A5-A738-48CC-B0F5-570B67AD9C78)
Both are paged JSON arrays (`?page=N&size=1000`). The downloader stores
`{"page_size": 1000, "pages": [[...], ...]}`; file replay also accepts one plain
array. Neither feed publishes an update time, so `source_updated_at` is None.

Conservative mapping policy (same normalized contract as Taipei):

* `TW97X`/`TW97Y` are the lot centre in TWD97 TM2 (EPSG:3826). The feed has
  no entrance coordinates, so no entrance is created.
* Counts: `TOTALCAR` -> car (CAR_SHARED, car=TRUE), `TOTALMOTOR` -> motor
  (MOTO_SHARED, green/white=TRUE). Only a positive count grants those
  permissions; LARGE_HEAVY baseline stays NULL because generic counts do not establish it.
  A separate official policy rule grants only exactly verified managed public car zones. `""` means missing; a zero
  omits the zone. There is no heavy-motorcycle count field.
* `PAYEX` is `;`-separated `<vehicle><terms>` segments. `小型車` -> CAR on car,
  `機車` -> NORMAL_HEAVY on motor, `重型機車` -> LARGE_HEAVY on heavy. A missing
  count with a matching fee channel creates a capacity-NULL zone with all-NULL
  permissions; a `重型機車` fee therefore yields an UNKNOWN heavy zone and never
  confirms that heavy motorcycles may park there. `身障車`/`身障機車` (permit
  holders), `大型車` (bus/truck) and unlabelled segments are not selected-vehicle
  prices. Each segment's terms go through the shared conservative text parser;
  `計時N元` states no time unit and stays PARTIALLY_PARSED. The full PAYEX is
  also attached verbatim to every zone as a vehicle-NULL rate.
* `TYPE`, `SUMMARY`, `TEL`, `SERVICETIME`, `TOTALBIKE` remain raw evidence only.
* Realtime: `AVAILABLECAR` only -> car. Missing, `""` or `-9` -> UNKNOWN; a
  valid count -> AVAILABLE (>0) or FULL (0); anything else fails the record.
"""

import re
import unicodedata
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.ingestion.base import BaseParkingAdapter
from app.ingestion.common import _digest, _lot_center, _optional_text, _require_record, _strict_count
from app.ingestion.contracts import (
    NormalizedLot,
    NormalizedObservation,
    NormalizedRate,
    NormalizedRealtime,
    NormalizedZone,
    RecordError,
)
from app.ingestion.policies import MANAGED_FACILITIES, NEW_TAIPEI_PUBLIC_CAR, managed_facility, policy_rule
from app.ingestion.rate_parser import parse_rate_text
from app.ingestion.sources import NEW_TAIPEI
from app.models.enums import ParkingSpaceType, RealtimeStatus, VehicleType

CAR = "car"
MOTOR = "motor"
HEAVY = "heavy"
_ZONE_ORDER = (CAR, MOTOR, HEAVY)
_CAPACITY_FIELDS = {CAR: "TOTALCAR", MOTOR: "TOTALMOTOR"}
_ZONE_NAMES = {CAR: "汽車", MOTOR: "機車", HEAVY: "重型機車"}
_ZONE_TYPES = {
    CAR: ParkingSpaceType.CAR_SHARED,
    MOTOR: ParkingSpaceType.MOTO_SHARED,
    HEAVY: ParkingSpaceType.HEAVY_ONLY,
}
_POSITIVE_PERMISSIONS: dict[str, dict[str, bool]] = {
    CAR: {"car": True},
    MOTOR: {"normal_heavy": True, "green": True, "white": True},
}
# Longest labels first so 重型機車/身障機車 are never read as 機車.
_LABELS: tuple[tuple[str, tuple[tuple[str, VehicleType], ...]], ...] = (
    ("身障機車", ()),
    ("重型機車", ((HEAVY, VehicleType.LARGE_HEAVY),)),
    ("身障車", ()),
    ("大型車", ()),
    ("小型車", ((CAR, VehicleType.CAR),)),
    ("機車", ((MOTOR, VehicleType.NORMAL_HEAVY),)),
)
_SEGMENT_SPLIT = re.compile(r"[;；]")
MAX_TEXT = 200


def _text(record: dict[str, Any], field: str, maximum: int, *, required: bool) -> str | None:
    value = record.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise RecordError(f"MISSING_{field}", f"'{field}' must be a non-empty string")
        return None
    if not isinstance(value, str):
        raise RecordError(f"INVALID_{field}", f"'{field}' must be a string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise RecordError(f"INVALID_{field}", f"'{field}' exceeds {maximum} characters")
    return normalized


def _capacity(record: dict[str, Any], field: str) -> int | None:
    value = record.get(field)
    if value is None or value == "":
        return None
    count = _strict_count(value)
    if count is None:
        raise RecordError("INVALID_CAPACITY", f"'{field}' must be a nonnegative integer, got {value!r}")
    return count


def _segments(payex: str | None) -> list[tuple[str, tuple[tuple[str, VehicleType], ...], str]]:
    """(segment, targets, terms) for each labelled fee segment, exact duplicates removed."""
    if payex is None:
        return []
    result, seen = [], set()
    for raw in _SEGMENT_SPLIT.split(payex):
        segment = raw.strip()
        if not segment or segment in seen:
            continue
        seen.add(segment)
        normalized = unicodedata.normalize("NFKC", segment)
        for label, targets in _LABELS:
            if normalized.startswith(label):
                result.append((segment, targets, normalized[len(label) :]))
                break
    return result


def _zone(zone: str, capacity: int | None, rates: tuple[NormalizedRate, ...]) -> NormalizedZone:
    permissions = _POSITIVE_PERMISSIONS.get(zone, {}) if capacity is not None and capacity > 0 else {}
    return NormalizedZone(
        key=zone,
        name=_ZONE_NAMES[zone],
        space_type=_ZONE_TYPES[zone],
        capacity=capacity,
        rates=rates,
        **permissions,
    )


class NewTaipeiParkingAdapter(BaseParkingAdapter):
    city = "new_taipei"
    source = NEW_TAIPEI

    def records(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return list(payload)
        pages = payload.get("pages") if isinstance(payload, dict) else None
        if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
            raise RecordError("INVALID_ENVELOPE", "payload must be a JSON array or {'pages': [[...], ...]}")
        return [record for page in pages for record in page]

    def source_updated_at(self, payload: Any) -> datetime | None:
        return None

    def record_key(self, record: Any) -> str | None:
        return _optional_text(record.get("ID")) if isinstance(record, dict) else None

    def raw_rate_text(self, record: Any) -> str | None:
        value = record.get("PAYEX") if isinstance(record, dict) else None
        return value if isinstance(value, str) else None

    def normalize_static(self, record: Any) -> NormalizedLot:
        rec = _require_record(record)
        external_id = _text(rec, "ID", 240, required=True)
        name = _text(rec, "NAME", MAX_TEXT, required=True)
        district = _text(rec, "AREA", 64, required=False)
        address = _text(rec, "ADDRESS", MAX_TEXT, required=False)
        lat, lng = _lot_center(rec.get("TW97X"), rec.get("TW97Y"))
        payex_value = rec.get("PAYEX")
        payex = payex_value if isinstance(payex_value, str) and payex_value.strip() else None
        segments = _segments(payex)

        capacities = {zone: _capacity(rec, field) for zone, field in _CAPACITY_FIELDS.items()}
        capacities[HEAVY] = None
        channels = {zone for _, targets, _ in segments for zone, _ in targets}
        present = [
            zone for zone in _ZONE_ORDER if (zone in channels if capacities[zone] is None else capacities[zone] > 0)
        ]

        rates: dict[str, list[NormalizedRate]] = {zone: [] for zone in present}
        for segment, targets, terms in segments:
            parsed = replace(parse_rate_text(terms), raw_text=segment)
            payload = {"source_field": "PAYEX", "segment": segment, "payex": payex}
            for zone, vehicle in targets:
                if zone in rates:
                    rates[zone].append(
                        NormalizedRate(
                            key=f"segment:{zone}:{vehicle.value}:{_digest(segment)}",
                            vehicle=vehicle,
                            parsed=parsed,
                            raw_payload=payload,
                        )
                    )
        if payex is not None:
            for zone in present:
                rates[zone].append(
                    NormalizedRate(
                        key=f"payex:{_digest(payex)}",
                        vehicle=None,
                        parsed=parse_rate_text(payex),
                        raw_payload={"source_field": "PAYEX", "payex": payex},
                    )
                )

        normalized_zones = [_zone(zone, capacities[zone], tuple(rates[zone])) for zone in present]
        verified = managed_facility(rec)
        if verified is not None:
            normalized_zones = [
                replace(
                    zone,
                    rules=(
                        policy_rule(
                            NEW_TAIPEI_PUBLIC_CAR,
                            zone,
                            evidence={
                                "facility": verified,
                                "roster_url": MANAGED_FACILITIES["roster_url"],
                                "roster_sha256": MANAGED_FACILITIES["roster_sha256"],
                                "roster_published_at": MANAGED_FACILITIES["roster_published_at"],
                                "mapping_verified_at": MANAGED_FACILITIES["verified_at"],
                            },
                        ),
                    ),
                )
                if zone.key == CAR and zone.car is True
                else zone
                for zone in normalized_zones
            ]

        return NormalizedLot(
            external_id=external_id,
            name=name,
            address=address,
            district=district,
            lat=lat,
            lng=lng,
            zones=tuple(normalized_zones),
        )

    def normalize_realtime(self, record: Any) -> NormalizedRealtime:
        rec = _require_record(record)
        external_id = _text(rec, "ID", 240, required=True)
        value = rec.get("AVAILABLECAR")
        if value is None or value == "" or value == "-9" or (type(value) is int and value == -9):
            observation = NormalizedObservation(zone_key=CAR, status=RealtimeStatus.UNKNOWN, available=None)
        else:
            count = _strict_count(value)
            if count is None:
                raise RecordError(
                    "INVALID_AVAILABILITY", f"'AVAILABLECAR' must be a nonnegative integer or -9, got {value!r}"
                )
            status = RealtimeStatus.AVAILABLE if count > 0 else RealtimeStatus.FULL
            observation = NormalizedObservation(zone_key=CAR, status=status, available=count)
        return NormalizedRealtime(external_id=external_id, observations=(observation,))
