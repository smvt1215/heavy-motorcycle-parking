"""Pure realtime availability normalization, filtering and lot aggregation.

Observation status (AVAILABLE/FULL/UNKNOWN/CLOSED) and freshness
(FRESH/STALE/UNKNOWN) are independent dimensions: aging never rewrites the
observed status. Freshness is always evaluated against a caller-supplied
*current* instant, never against a pinned `evaluation_at`, and this module
never reads the wall clock itself.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.discovery import RealtimeFact
from app.domain.parking import Provenance, absolute_instant
from app.services.compatibility import _provenance_dict, _rfc3339

AVAILABLE = "AVAILABLE"
FULL = "FULL"
CLOSED = "CLOSED"
UNKNOWN = "UNKNOWN"
STATUSES = frozenset({AVAILABLE, FULL, CLOSED, UNKNOWN})
_COUNTABLE = frozenset({AVAILABLE, FULL, CLOSED})

FRESH = "FRESH"
STALE = "STALE"

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
NONE = "NONE"

_PROVENANCE_FIELDS = ("source_id", "source_type", "source_record_id", "source_updated_at", "fetched_at", "verified_at")


def _count(value: Any) -> bool:
    """A trustworthy count is an actual nonnegative int; bool and float are rejected."""
    return type(value) is int and value >= 0


def _status(value: Any) -> str | None:
    if not isinstance(value, str) or str(value) not in STATUSES:
        return None
    return str(value)


def validate_counts(status: str, available: int | None, total: int | None) -> bool:
    """Numeric integrity of one observation.

    Counts are nullable, but when present must be actual nonnegative ints with
    `available <= total`. AVAILABLE requires a confirmed `available > 0`;
    FULL and CLOSED require a confirmed `available == 0`. UNKNOWN may retain
    valid numeric fields (so this returns True for them), but UNKNOWN is never
    a COMPLETE contributor or an `available_only` match; those checks are
    enforced by `is_available_only` and `aggregate_availability`.
    """
    status = _status(status)
    if status is None:
        return False
    if any(value is not None and not _count(value) for value in (available, total)):
        return False
    if available is not None and total is not None and available > total:
        return False
    if status == AVAILABLE:
        return available is not None and available > 0
    if status in (FULL, CLOSED):
        return available == 0
    return True


def freshness_status(fetched_at: datetime | None, freshness_seconds: int, now: datetime) -> str:
    """FRESH when `0 <= now - fetched_at <= freshness_seconds`, STALE when older.

    Missing or future fetch times, and a non-integer/negative threshold, are
    indeterminable and therefore UNKNOWN; they never count as FRESH.
    """
    current = absolute_instant(now)
    if fetched_at is None:
        return UNKNOWN
    if type(freshness_seconds) is not int or freshness_seconds < 0:
        return UNKNOWN
    fetched = absolute_instant(fetched_at)
    if fetched > current:
        return UNKNOWN
    return FRESH if current - fetched <= timedelta(seconds=freshness_seconds) else STALE


def resolve_availability(fact: RealtimeFact | None, now: datetime) -> dict[str, Any] | None:
    """Zone availability wire object, or None when no observation exists.

    An unsupported status or untrustworthy counts degrade to `UNKNOWN` with
    null counts, so the observation can neither qualify `available_only` nor
    contribute to a numeric aggregate. A valid observation keeps its status
    regardless of freshness (STALE never replaces AVAILABLE/FULL/CLOSED).
    """
    if fact is None:
        return None
    if not isinstance(fact, RealtimeFact) or not isinstance(fact.provenance, Provenance):
        raise ValueError("Expected a RealtimeFact with Provenance")
    current = absolute_instant(now)
    status = _status(fact.status)
    available, total = fact.available, fact.total
    if status is None or not validate_counts(status, available, total):
        status, available, total = UNKNOWN, None, None
    origin = fact.provenance.fetched_at
    if fact.freshness_uses_source_timestamp:
        updated = fact.provenance.source_updated_at
        origin = (
            min(origin, updated)
            if origin is not None and origin <= current and updated is not None and updated <= current
            else None
        )
    return {
        "status": status,
        "available": available,
        "total": total,
        "freshness": {"status": freshness_status(origin, fact.freshness_seconds, current)},
        "provenance": _provenance_dict(fact.provenance),
    }


def _instant(value: Any) -> datetime | None:
    """Parse a serialized RFC3339 instant; anything naive or malformed is None."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _fresh_trustworthy(availability: Any) -> bool:
    """Shared FRESH + fetched_at + integrity checks on a serialized availability."""
    if not isinstance(availability, dict):
        return False
    freshness = availability.get("freshness")
    provenance = availability.get("provenance")
    if not isinstance(freshness, dict) or freshness.get("status") != FRESH:
        return False
    if not isinstance(provenance, dict) or _instant(provenance.get("fetched_at")) is None:
        return False
    if type(provenance.get("source_id")) is not int:
        return False
    return validate_counts(availability.get("status"), availability.get("available"), availability.get("total"))


def is_available_only(compatibility_status: str, availability: dict[str, Any] | None) -> bool:
    """`available_only=true` zone predicate.

    Requires ALLOWED compatibility, AVAILABLE status, actual int `available > 0`,
    FRESH freshness and a non-null realtime `fetched_at`. `total` may be null;
    when supplied it must be a nonnegative int with `available <= total`.
    Integrity is re-verified so an invalid fixture (e.g. FRESH without
    fetched_at) can never qualify.
    """
    if compatibility_status != "ALLOWED" or not _fresh_trustworthy(availability):
        return False
    assert availability is not None
    available = availability.get("available")
    return availability.get("status") == AVAILABLE and type(available) is int and available > 0


def _complete_contributor(availability: Any) -> bool:
    """COMPLETE contributor: AVAILABLE/FULL/CLOSED, both counts known and valid, FRESH."""
    if not _fresh_trustworthy(availability):
        return False
    return (
        availability.get("status") in _COUNTABLE
        and _count(availability.get("available"))
        and _count(availability.get("total"))
    )


def _source_sort_key(provenance: dict[str, Any]) -> tuple[Any, ...]:
    rest = tuple((provenance.get(field) is None, str(provenance.get(field) or "")) for field in _PROVENANCE_FIELDS[1:])
    return (provenance["source_id"], *rest)


def _unknown_summary(coverage: str, eligible: int, fresh: int) -> dict[str, Any]:
    return {
        "status": UNKNOWN,
        "available": None,
        "total": None,
        "coverage": coverage,
        "eligible_zone_count": eligible,
        "fresh_realtime_zone_count": fresh,
        "freshness": {"status": UNKNOWN},
        "oldest_source_updated_at": None,
        "oldest_fetched_at": None,
        "contributing_sources": [],
    }


def _zone_status(zone: Any) -> Any:
    compatibility = zone.get("compatibility") if isinstance(zone, dict) else None
    return compatibility.get("status") if isinstance(compatibility, dict) else None


def aggregate_availability(zones: list[dict[str, Any]]) -> dict[str, Any]:
    """Lot `availability_summary` derived from returned ALLOWED zones only.

    Non-ALLOWED zones (UNKNOWN/NOT_ALLOWED) are ignored entirely, even if they
    carry valid realtime. Coverage is COMPLETE only when every ALLOWED zone is
    a COMPLETE contributor; PARTIAL/NONE (including zero ALLOWED zones) expose
    UNKNOWN status, null totals, UNKNOWN freshness, null timestamps and no
    sources, so partial child sums can never be shown as lot totals.

    For COMPLETE: status is AVAILABLE when the sum is positive, CLOSED when every
    contributor is CLOSED, FULL when the sum is zero with at least one FULL and
    the rest FULL/CLOSED, otherwise UNKNOWN defensively. `oldest_source_updated_at`
    is the minimum only when every contributor supplies it; `oldest_fetched_at`
    is the minimum fetch time; `contributing_sources` deduplicates provenance by
    its full field tuple (not merely `source_id`), so it covers every
    contributor and is never empty.
    """
    eligible = [zone for zone in zones if _zone_status(zone) == "ALLOWED"]
    contributors = [zone["availability"] for zone in eligible if _complete_contributor(zone.get("availability"))]
    if not eligible or not contributors:
        return _unknown_summary(NONE, len(eligible), len(contributors))
    if len(contributors) < len(eligible):
        return _unknown_summary(PARTIAL, len(eligible), len(contributors))

    available = sum(item["available"] for item in contributors)
    total = sum(item["total"] for item in contributors)
    statuses = {item["status"] for item in contributors}
    if available > 0:
        status = AVAILABLE
    elif statuses == {CLOSED}:
        status = CLOSED
    elif FULL in statuses and statuses <= {FULL, CLOSED}:
        status = FULL
    else:
        status = UNKNOWN

    provenances = [item["provenance"] for item in contributors]
    updated = [_instant(item.get("source_updated_at")) for item in provenances]
    fetched = [_instant(item["fetched_at"]) for item in provenances]
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in provenances:
        normalized = {field: item.get(field) for field in _PROVENANCE_FIELDS}
        unique.setdefault(tuple(normalized.values()), normalized)
    return {
        "status": status,
        "available": available,
        "total": total,
        "coverage": COMPLETE,
        "eligible_zone_count": len(eligible),
        "fresh_realtime_zone_count": len(contributors),
        "freshness": {"status": FRESH},
        "oldest_source_updated_at": None if None in updated else _rfc3339(min(updated)),
        "oldest_fetched_at": _rfc3339(min(fetched)),
        "contributing_sources": sorted(unique.values(), key=_source_sort_key),
    }
