"""Taipei City adapter for the TCMSV V2 open-data feeds.

Dataset: https://data.taipei/dataset/detail?id=d5c0656b-5250-4179-a491-c94daa56ef2c
Static:   https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json
Realtime: https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json
Both envelopes are {"data": {"UPDATETIME": "Tue Oct 06 00:02:00 CST 2026", "park": [...]}};
"CST" is China/Taiwan Standard Time and is read as Asia/Taipei, never US Central.

Conservative mapping policy:

* `tw97x`/`tw97y` are the lot centre in TWD97 TM2 (EPSG:3826), projected with
  always_xy=True and errcheck=True and required to land in Taiwan
  (lat 22..26.5, lng 119..123). Entrance coordinates are never a fallback.
* `EntranceCoord.EntrancecoordInfo[]`: `Xcod`=lat, `Ycod`=lng (WGS84),
  `Address`=label. Invalid coordinates keep the entrance with both NULL; heavy
  access is never stated by the source, so it is always NULL.
* Counts accept a nonnegative int or a full ASCII-digit string only (no bools,
  fractions, signs, whitespace or ""). Zones: `totalcar` -> car (CAR_SHARED,
  car=TRUE), `totalmotor` -> motor (MOTO_SHARED, green/white=TRUE),
  `totallargemotor` -> heavy (HEAVY_ONLY, yellow/red=TRUE). Only a positive
  count grants those permissions; everything else stays NULL. A zero count
  omits the zone (never NOT_ALLOWED). A missing count creates a capacity-NULL
  zone with all-NULL permissions only when a FareRule of that channel exists
  (C/CM -> car, M -> motor, HM -> heavy). A lot without supported zones keeps
  its location/entrances with no zones; no zone is invented.
* `FareInfo.FareRule[]` (ParkingType, RateType, ChargeableSTime,
  ChargeableETime, ParkingRates, CUnit) is frequently incomplete and `payex`
  carries conditions absent from it, so structured rates are at most
  PARTIALLY_PARSED and never assert a unit (CUnit is kept only as raw evidence).
  RateType 1 timed -> TIME_BLOCK, 2 -> PER_ENTRY, 3 -> MONTHLY, 4 -> FREE,
  5..8 multi-month -> CUSTOM, 9 charging excluded, unknown -> RAW_ONLY.
  Vehicles: C -> CAR on car; CM -> CAR on car plus LARGE_HEAVY on an existing
  heavy zone; M -> NORMAL_HEAVY on motor; HM -> LARGE_HEAVY on heavy; T (bus)
  unsupported. Exact duplicate FareRules are deduplicated; distinct
  (conflicting) ones survive with content-hash keys. Windows: "00"-"24" means
  unrestricted, a 24 end means midnight, anything else invalid -> INVALID.
* `payex` is attached verbatim to every existing zone as a vehicle-NULL rate
  with a conservative text-parser status and is also structured rates' raw_text.
  Even a completely parsed unlabelled tariff never confirms a selected vehicle's price.
* Realtime: availablecar/availablemotor/availableheavymotor -> car/motor/heavy
  (availablebus ignored). Missing or -9 -> UNKNOWN; a valid count -> AVAILABLE
  (>0) or FULL (0); any other value is a record failure. No totals are attached
  (the writer joins static capacity), CLOSED is never inferred, and heavy counts
  never come from generic counts.
"""

import math
import re
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.ingestion.base import BaseParkingAdapter
from app.ingestion.common import (
    _canonical,
    _coordinate_number,
    _digest,
    _in_taiwan,
    _lot_center,
    _optional_text,
    _require_record,
    _strict_count,
)
from app.ingestion.contracts import (
    NormalizedEntrance,
    NormalizedLot,
    NormalizedObservation,
    NormalizedRate,
    NormalizedRealtime,
    NormalizedZone,
    ParsedRate,
    ParsedRateRule,
    RecordError,
)
from app.ingestion.rate_parser import MAX_AMOUNT, parse_rate_text
from app.ingestion.sources import TAIPEI
from app.models.enums import ParkingSpaceType, RateParseStatus, RateType, RealtimeStatus, VehicleType

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

CAR = "car"
MOTOR = "motor"
HEAVY = "heavy"
_ZONE_ORDER = (CAR, MOTOR, HEAVY)
_CAPACITY_FIELDS = ((CAR, "totalcar"), (MOTOR, "totalmotor"), (HEAVY, "totallargemotor"))
_REALTIME_FIELDS = ((CAR, "availablecar"), (MOTOR, "availablemotor"), (HEAVY, "availableheavymotor"))
_ZONE_NAMES = {CAR: "汽車", MOTOR: "機車", HEAVY: "大型重型機車"}
_ZONE_TYPES = {
    CAR: ParkingSpaceType.CAR_SHARED,
    MOTOR: ParkingSpaceType.MOTO_SHARED,
    HEAVY: ParkingSpaceType.HEAVY_ONLY,
}
_POSITIVE_PERMISSIONS: dict[str, dict[str, bool]] = {
    CAR: {"car": True},
    MOTOR: {"normal_heavy": True, "green": True, "white": True},
    HEAVY: {"large_heavy": True, "yellow": True, "red": True},
}
_PARKING_TYPE_TARGETS: dict[str, tuple[tuple[str, VehicleType], ...]] = {
    "C": ((CAR, VehicleType.CAR),),
    "CM": ((CAR, VehicleType.CAR), (HEAVY, VehicleType.LARGE_HEAVY)),
    "M": ((MOTOR, VehicleType.NORMAL_HEAVY),),
    "HM": ((HEAVY, VehicleType.LARGE_HEAVY),),
}
# Channels that may create a capacity-NULL zone; CM never creates a heavy zone.
_CHANNEL_ZONES = {"C": CAR, "CM": CAR, "M": MOTOR, "HM": HEAVY}
_RATE_TYPES = {
    "1": RateType.TIME_BLOCK,
    "2": RateType.PER_ENTRY,
    "3": RateType.MONTHLY,
    "4": RateType.FREE,
    "5": RateType.CUSTOM,
    "6": RateType.CUSTOM,
    "7": RateType.CUSTOM,
    "8": RateType.CUSTOM,
}
_EXCLUDED_RATE_TYPES = frozenset({"9"})

_MONEY_TEXT = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_CLOCK = re.compile(r"([0-9]{1,2})(?::([0-9]{2}))?")
_UPDATETIME = re.compile(
    r"(?P<wd>Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(?P<day>[0-9]{2}) (?P<h>[0-9]{2}):(?P<mi>[0-9]{2}):(?P<s>[0-9]{2}) CST (?P<y>[0-9]{4})"
)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = {
    name: index
    for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}


class _InvalidValue(ValueError):
    """Internal: a structured fare field is malformed."""


def parse_updatetime(value: Any) -> datetime | None:
    """Parse the official English UPDATETIME as Asia/Taipei; None when missing or malformed."""
    if not isinstance(value, str):
        return None
    match = _UPDATETIME.fullmatch(value)
    if match is None:
        return None
    try:
        parsed = datetime(
            int(match["y"]),
            _MONTHS[match["mon"]],
            int(match["day"]),
            int(match["h"]),
            int(match["mi"]),
            int(match["s"]),
            tzinfo=TAIPEI_TZ,
        )
    except ValueError:
        return None
    if _WEEKDAYS[parsed.weekday()] != match["wd"]:
        return None
    return parsed


def _required_text(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if isinstance(value, str) and value.strip():
        normalized = value.strip()
        maximum = 240 if field == "id" else 200
        if len(normalized) > maximum:
            raise RecordError(f"INVALID_{field.upper()}", f"'{field}' exceeds {maximum} characters")
        return normalized
    raise RecordError(f"MISSING_{field.upper()}", f"'{field}' must be a non-empty string")


def _capacity(record: dict[str, Any], field: str) -> int | None:
    value = record.get(field)
    if value is None:
        return None
    count = _strict_count(value)
    if count is None:
        raise RecordError("INVALID_CAPACITY", f"'{field}' must be a nonnegative integer, got {value!r}")
    return count


def _entrances(raw: Any) -> tuple[NormalizedEntrance, ...]:
    if not isinstance(raw, dict):
        return ()
    infos = raw.get("EntrancecoordInfo")
    if isinstance(infos, dict):
        infos = [infos]
    if not isinstance(infos, list):
        return ()
    entrances: list[NormalizedEntrance] = []
    seen: set[str] = set()
    for info in infos:
        if not isinstance(info, dict):
            continue
        if (name := _optional_text(info.get("Address"))) is not None and len(name) > 200:
            raise RecordError("INVALID_ENTRANCE_NAME", "Entrance Address exceeds 200 characters")
        key = f"entrance:{_digest(info)}"
        if key in seen:
            continue
        seen.add(key)
        lat = _coordinate_number(info.get("Xcod"))
        lng = _coordinate_number(info.get("Ycod"))
        if lat is None or lng is None or not _in_taiwan(lat, lng):
            lat = lng = None
        entrances.append(NormalizedEntrance(key=key, name=name, lat=lat, lng=lng, heavy_access=None))
    return tuple(entrances)


def _code(value: Any) -> str | None:
    if type(value) is int:
        return str(value)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _fare_rules(info: Any) -> list[dict[str, Any]]:
    """FareRule entries in source order with exact duplicates removed."""
    if not isinstance(info, dict):
        return []
    rules = info.get("FareRule")
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list):
        return []
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        canonical = _canonical(rule)
        if canonical not in seen:
            seen.add(canonical)
            unique.append(rule)
    return unique


def _rule_targets(rule: dict[str, Any]) -> tuple[tuple[str, VehicleType], ...]:
    if _code(rule.get("RateType")) in _EXCLUDED_RATE_TYPES:
        return ()
    return _PARKING_TYPE_TARGETS.get(_code(rule.get("ParkingType")) or "", ())


def _channel_zone(rule: dict[str, Any]) -> str | None:
    if _code(rule.get("RateType")) in _EXCLUDED_RATE_TYPES:
        return None
    return _CHANNEL_ZONES.get(_code(rule.get("ParkingType")) or "")


def _structured_amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if type(value) is int:
        amount = Decimal(value)
    elif type(value) is float:
        if not math.isfinite(value):
            raise _InvalidValue("non-finite ParkingRates")
        amount = Decimal(repr(value))
    elif isinstance(value, str) and _MONEY_TEXT.fullmatch(value):
        amount = Decimal(value)
    else:
        raise _InvalidValue(f"malformed ParkingRates {value!r}")
    exponent = amount.as_tuple().exponent
    if amount < 0 or amount > MAX_AMOUNT or not isinstance(exponent, int) or exponent < -2:
        raise _InvalidValue(f"ParkingRates out of NUMERIC(10,2) range {value!r}")
    return amount


def _clock_minutes(value: Any) -> int:
    if type(value) is int:
        hour, minute = value, 0
    elif isinstance(value, str) and (match := _CLOCK.fullmatch(value)):
        hour, minute = int(match[1]), int(match[2] or 0)
    else:
        raise _InvalidValue(f"malformed chargeable time {value!r}")
    if not (0 <= hour <= 24 and 0 <= minute <= 59) or (hour == 24 and minute != 0):
        raise _InvalidValue(f"chargeable time out of range {value!r}")
    return hour * 60 + minute


def _window(start_raw: Any, end_raw: Any) -> tuple[time | None, time | None] | None:
    """None: no window stated; (None, None): explicit 00-24; else half-open local window."""
    start_missing = start_raw is None or start_raw == ""
    end_missing = end_raw is None or end_raw == ""
    if start_missing and end_missing:
        return None
    if start_missing or end_missing:
        raise _InvalidValue("incomplete chargeable window")
    start = _clock_minutes(start_raw)
    end = _clock_minutes(end_raw)
    if start == 0 and end == 1440:
        return (None, None)
    if start >= 1440 or start == end % 1440:
        raise _InvalidValue(f"invalid chargeable window {start_raw!r}-{end_raw!r}")
    end %= 1440
    return (time(start // 60, start % 60), time(end // 60, end % 60))


def _structured_rate(rule: dict[str, Any], payex: str | None) -> ParsedRate:
    raw_text = payex if payex is not None else _canonical(rule)
    rate_type = _RATE_TYPES.get(_code(rule.get("RateType")) or "")
    if rate_type is None:
        return ParsedRate(RateParseStatus.RAW_ONLY, raw_text)
    try:
        amount = _structured_amount(rule.get("ParkingRates"))
        window = _window(rule.get("ChargeableSTime"), rule.get("ChargeableETime"))
    except _InvalidValue:
        return ParsedRate(RateParseStatus.INVALID, raw_text)
    rules: tuple[ParsedRateRule, ...] = ()
    if window is not None and window != (None, None):
        rules = (ParsedRateRule(start_time=window[0], end_time=window[1], amount=amount),)
    return ParsedRate(RateParseStatus.PARTIALLY_PARSED, raw_text, rate_type=rate_type, base_amount=amount, rules=rules)


def _payex(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _zone_present(capacity: int | None, has_channel: bool) -> bool:
    return has_channel if capacity is None else capacity > 0


def _zone(zone: str, capacity: int | None, rates: tuple[NormalizedRate, ...]) -> NormalizedZone:
    permissions = _POSITIVE_PERMISSIONS[zone] if capacity is not None and capacity > 0 else {}
    return NormalizedZone(
        key=zone,
        name=_ZONE_NAMES[zone],
        space_type=_ZONE_TYPES[zone],
        capacity=capacity,
        rates=rates,
        **permissions,
    )


def _observation(record: dict[str, Any], zone: str, field: str) -> NormalizedObservation:
    value = record.get(field)
    if value is None or (type(value) is int and value == -9) or value == "-9":
        return NormalizedObservation(zone_key=zone, status=RealtimeStatus.UNKNOWN, available=None)
    count = _strict_count(value)
    if count is None:
        raise RecordError("INVALID_AVAILABILITY", f"'{field}' must be a nonnegative integer or -9, got {value!r}")
    status = RealtimeStatus.AVAILABLE if count > 0 else RealtimeStatus.FULL
    return NormalizedObservation(zone_key=zone, status=status, available=count)


def _envelope_data(payload: Any) -> dict[str, Any] | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


class TaipeiParkingAdapter(BaseParkingAdapter):
    city = "taipei"
    source = TAIPEI

    def records(self, payload: Any) -> list[Any]:
        data = _envelope_data(payload)
        if data is None:
            raise RecordError("INVALID_ENVELOPE", "payload must be an object with a 'data' object")
        park = data.get("park")
        if not isinstance(park, list):
            raise RecordError("INVALID_ENVELOPE", "'data.park' must be a list")
        return list(park)

    def source_updated_at(self, payload: Any) -> datetime | None:
        data = _envelope_data(payload)
        return parse_updatetime(data.get("UPDATETIME")) if data is not None else None

    def record_key(self, record: Any) -> str | None:
        if isinstance(record, dict):
            return _optional_text(record.get("id"))
        return None

    def raw_rate_text(self, record: Any) -> str | None:
        value = record.get("payex") if isinstance(record, dict) else None
        return value if isinstance(value, str) else None

    def normalize_static(self, record: Any) -> NormalizedLot:
        rec = _require_record(record)
        external_id = _required_text(rec, "id")
        name = _required_text(rec, "name")
        if (district := _optional_text(rec.get("area"))) is not None and len(district) > 64:
            raise RecordError("INVALID_AREA", "'area' exceeds 64 characters")
        lat, lng = _lot_center(rec.get("tw97x"), rec.get("tw97y"))
        capacities = {zone: _capacity(rec, field) for zone, field in _CAPACITY_FIELDS}
        payex = _payex(rec.get("payex"))
        fare_rules = _fare_rules(rec.get("FareInfo"))
        channels = {zone for rule in fare_rules if (zone := _channel_zone(rule)) is not None}
        present = [zone for zone in _ZONE_ORDER if _zone_present(capacities[zone], zone in channels)]

        rates: dict[str, list[NormalizedRate]] = {zone: [] for zone in present}
        for rule in fare_rules:
            targets = [(zone, vehicle) for zone, vehicle in _rule_targets(rule) if zone in rates]
            if not targets:
                continue
            parsed = _structured_rate(rule, payex)
            payload = {"source_field": "FareInfo.FareRule", "fare_rule": rule, "payex": payex}
            rule_digest = _digest(rule)
            for zone, vehicle in targets:
                rates[zone].append(
                    NormalizedRate(
                        key=f"fare:{zone}:{vehicle.value}:{rule_digest}",
                        vehicle=vehicle,
                        parsed=parsed,
                        raw_payload=payload,
                    )
                )
        if payex is not None:
            payex_key = f"payex:{_digest(payex)}"
            for zone in present:
                rates[zone].append(
                    NormalizedRate(
                        key=payex_key,
                        vehicle=None,
                        parsed=parse_rate_text(payex),
                        raw_payload={"source_field": "payex", "payex": payex},
                    )
                )

        return NormalizedLot(
            external_id=external_id,
            name=name,
            address=_optional_text(rec.get("address")),
            district=district,
            lat=lat,
            lng=lng,
            zones=tuple(_zone(zone, capacities[zone], tuple(rates[zone])) for zone in present),
            entrances=_entrances(rec.get("EntranceCoord")),
        )

    def normalize_realtime(self, record: Any) -> NormalizedRealtime:
        rec = _require_record(record)
        external_id = _required_text(rec, "id")
        observations = tuple(_observation(rec, zone, field) for zone, field in _REALTIME_FIELDS)
        return NormalizedRealtime(external_id=external_id, observations=observations)
