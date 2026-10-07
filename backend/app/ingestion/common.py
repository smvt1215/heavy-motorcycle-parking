"""City-independent validators shared by parking source adapters.

Coordinates, counts and canonical digests behave identically for every city so
city adapters only map field names and source-specific vocabulary.
"""

import hashlib
import json
import math
import re
from functools import cache
from typing import Any

from pyproj import Transformer
from pyproj.exceptions import ProjError

from app.ingestion.contracts import RecordError

MAX_COUNT = 2_147_483_647
LAT_RANGE = (22.0, 26.5)
LNG_RANGE = (119.0, 123.0)

_DIGITS = re.compile(r"[0-9]+")
_DECIMAL_TEXT = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


@cache
def _twd97_to_wgs84() -> Transformer:
    return Transformer.from_crs(3826, 4326, always_xy=True)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:24]


def _require_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise RecordError("INVALID_RECORD", f"record must be an object, got {type(record).__name__}")
    return record


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strict_count(value: Any) -> int | None:
    """Nonnegative actual int or full ASCII-digit string; anything else -> None."""
    if type(value) is int:
        count = value
    elif isinstance(value, str) and _DIGITS.fullmatch(value):
        digits = value.lstrip("0") or "0"
        if len(digits) > 10:
            return None
        count = int(digits)
    else:
        return None
    return count if 0 <= count <= MAX_COUNT else None


def _coordinate_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) or isinstance(value, str) and _DECIMAL_TEXT.fullmatch(value):
        try:
            number = float(value)
        except OverflowError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _in_taiwan(lat: float, lng: float) -> bool:
    return (
        math.isfinite(lat)
        and math.isfinite(lng)
        and LAT_RANGE[0] <= lat <= LAT_RANGE[1]
        and LNG_RANGE[0] <= lng <= LNG_RANGE[1]
    )


def _lot_center(x_raw: Any, y_raw: Any) -> tuple[float, float]:
    x = _coordinate_number(x_raw)
    y = _coordinate_number(y_raw)
    if x is None or y is None:
        raise RecordError("INVALID_COORDINATES", f"tw97x/tw97y must be finite numbers, got {x_raw!r}/{y_raw!r}")
    try:
        lng, lat = _twd97_to_wgs84().transform(x, y, errcheck=True)
    except ProjError as exc:
        raise RecordError("INVALID_COORDINATES", f"TWD97 projection failed: {exc}") from exc
    if not _in_taiwan(lat, lng):
        raise RecordError("INVALID_COORDINATES", f"projected lot centre outside Taiwan: lat={lat}, lng={lng}")
    return lat, lng
