"""Normative nearby ranking for `sort_version=1` (see docs/api.md).

Compatibility is a hard filter, never a score component. Every component is
an integer basis-point value in 0..10000 computed with exact nonnegative
integer round-half-up; confirmed price/confidence use only returned ALLOWED
zones, so UNKNOWN-zone facts can never alter a confirmed lot score. Any change
to a formula, band, weight, rounding rule or key order requires a new
sort_version.
"""

from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from typing import Any

from app.domain.discovery import EntranceFact

SORT_VERSION = 1
MAX_BP = 10000
WEIGHTS = {"distance": 35, "availability": 25, "price": 20, "confidence": 15, "entrance": 5}

_PRICE_BANDS: tuple[tuple[Decimal, int], ...] = (
    (Decimal(20), 8000),
    (Decimal(30), 6000),
    (Decimal(50), 4000),
)


def _strict_int(value: Any, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def round_half_up(n: int, d: int) -> int:
    """`floor((n + d/2) / d)` for integer `n >= 0`, `d > 0`, without floats."""
    _strict_int(n, "numerator")
    _strict_int(d, "denominator", 1)
    return (2 * n + d) // (2 * d)


def _clamp(value: int) -> int:
    return max(0, min(MAX_BP, value))


def _decimal(value: Any) -> Decimal | None:
    """Finite numeric wire value as Decimal; bool/str/NaN/inf are rejected.

    Floats go through `str` so a JSON value such as 0.33335 is scaled as the
    written decimal rather than its binary approximation.
    """
    if type(value) is bool or not isinstance(value, int | float | Decimal):
        return None
    if isinstance(value, float) and not isfinite(value):
        return None
    result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    return result if result.is_finite() else None


def _allowed_zones(zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for zone in zones:
        compatibility = zone.get("compatibility") if isinstance(zone, dict) else None
        if isinstance(compatibility, dict) and compatibility.get("status") == "ALLOWED":
            result.append(zone)
    return result


def distance_component(distance_m: int, radius_m: int) -> int:
    """`round_half_up(10000 * max(radius - distance, 0) / radius)`."""
    _strict_int(distance_m, "distance_m")
    _strict_int(radius_m, "radius_m", 1)
    return _clamp(round_half_up(MAX_BP * max(radius_m - distance_m, 0), radius_m))


def availability_component(summary: dict[str, Any] | None) -> int:
    """`round_half_up(10000 * available / total)` for COMPLETE coverage only.

    Non-COMPLETE coverage, `total <= 0`, and defensively any non-int or
    inconsistent (`available > total`) summary get no availability boost.
    """
    if not isinstance(summary, dict) or summary.get("coverage") != "COMPLETE":
        return 0
    available, total = summary.get("available"), summary.get("total")
    if type(available) is not int or type(total) is not int or total <= 0:
        return 0
    if not 0 <= available <= total:
        return 0
    return _clamp(round_half_up(MAX_BP * available, total))


def price_component(zones: list[dict[str, Any]]) -> int:
    """Band of the lowest eligible ALLOWED-zone hourly comparison value.

    0 TWD => 10000, (0,20] => 8000, (20,30] => 6000, (30,50] => 4000, >50 => 2000,
    no eligible value => 0. Only `comparison_eligible is True` summaries with a
    finite nonnegative number count.
    """
    values = []
    for zone in _allowed_zones(zones):
        summary = zone.get("rate_summary")
        if not isinstance(summary, dict) or summary.get("comparison_eligible") is not True:
            continue
        value = _decimal(summary.get("comparison_hourly_rate_twd"))
        if value is not None and value >= 0:
            values.append(value)
    if not values:
        return 0
    lowest = min(values)
    if lowest == 0:
        return MAX_BP
    for upper, score in _PRICE_BANDS:
        if lowest <= upper:
            return score
    return 2000


def confidence_component(zones: list[dict[str, Any]]) -> int:
    """`round_half_up(clamp(max ALLOWED confidence, 0, 1) * 10000)` using Decimal; all null => 0."""
    values = []
    for zone in _allowed_zones(zones):
        value = _decimal(zone["compatibility"].get("confidence"))
        if value is not None:
            values.append(value)
    if not values:
        return 0
    confidence = min(max(max(values), Decimal(0)), Decimal(1))
    return _clamp(int((confidence * MAX_BP).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def _usable_coordinates(entrance: EntranceFact) -> bool:
    lat, lng = entrance.lat, entrance.lng
    for value in (lat, lng):
        if type(value) is bool or not isinstance(value, int | float) or not isfinite(value):
            return False
    return -90 <= lat <= 90 and -180 <= lng <= 180


def entrance_component(entrances: tuple[EntranceFact, ...]) -> int:
    """10000 for a coordinate-bearing ALLOWED entrance, else 5000 for a
    coordinate-bearing UNKNOWN entrance, else 0. Access values match exactly."""
    accesses = set()
    for entrance in entrances:
        if not isinstance(entrance, EntranceFact):
            raise ValueError("Expected EntranceFact objects")
        if isinstance(entrance.access, str) and _usable_coordinates(entrance):
            accesses.add(str(entrance.access))
    if "ALLOWED" in accesses:
        return MAX_BP
    if "UNKNOWN" in accesses:
        return 5000
    return 0


def ranking_components(
    distance_m: int,
    radius_m: int,
    zones: list[dict[str, Any]],
    availability_summary: dict[str, Any] | None,
    entrances: tuple[EntranceFact, ...],
) -> dict[str, int]:
    """All five sort_version=1 integer components for one lot."""
    return {
        "distance": distance_component(distance_m, radius_m),
        "availability": availability_component(availability_summary),
        "price": price_component(zones),
        "confidence": confidence_component(zones),
        "entrance": entrance_component(entrances),
    }


def ranking_score(
    distance_m: int,
    radius_m: int,
    zones: list[dict[str, Any]],
    availability_summary: dict[str, Any] | None,
    entrances: tuple[EntranceFact, ...],
) -> int:
    """`floor((weighted_sum + 50) / 100)` of the weighted components."""
    components = ranking_components(distance_m, radius_m, zones, availability_summary, entrances)
    return round_half_up(sum(components[name] * weight for name, weight in WEIGHTS.items()), 100)


def sort_key(item: dict[str, Any]) -> tuple[int, ...]:
    """Ascending keyset tuple.

    Confirmed ALLOWED lots: `(0, -ranking_score_bp, distance_m, id)`.
    Unknown-only lots: `(1, distance_m, id)`; their score is ignored.
    NOT_ALLOWED (or anything else) is never ranked and raises.
    """
    compatibility = item.get("compatibility")
    status = compatibility.get("status") if isinstance(compatibility, dict) else None
    parking_id = _strict_int(item.get("id"), "id")
    distance = _strict_int(item.get("distance_m"), "distance_m")
    if status == "ALLOWED":
        score = _strict_int(item.get("ranking_score_bp"), "ranking_score_bp")
        if score > MAX_BP:
            raise ValueError("ranking_score_bp must be within 0..10000")
        return (0, -score, distance, parking_id)
    if status == "UNKNOWN":
        return (1, distance, parking_id)
    raise ValueError("Only ALLOWED and UNKNOWN lots can be ranked")
