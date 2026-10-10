"""Deterministic community-verification policies, independent of ORM and HTTP.

Contract: docs/community-verification-contract.md. Photo times come only from
EXIF DateTimeOriginal/OffsetTimeOriginal read by the backend before re-encoding;
upload, file-modification and device-zone times are never substituted, and a
missing offset is never assumed to be Asia/Taipei.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from typing import NamedTuple

from app.models.enums import CasePublicationState, ContributionEntryType, PhotoTimeStatus, PublicationStatus

CORROBORATION_THRESHOLD = 3
PHOTO_WINDOW = timedelta(days=30)
OBSERVATION_TERM = timedelta(days=90)
IDEMPOTENCY_TTL = timedelta(hours=24)
TIME_PARSER_VERSION = "exif-time-1"

_DATETIME = re.compile(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})$")
_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")
_SUBSEC = re.compile(r"^\d{1,9}$")
# Real-world UTC offsets span -12:00 to +14:00.
_MIN_OFFSET = timedelta(hours=-12)
_MAX_OFFSET = timedelta(hours=14)


class PhotoTime(NamedTuple):
    captured_at: datetime | None
    status: PhotoTimeStatus


def _blank(value: str | None) -> bool:
    # EXIF writes unknown dates as spaces; NUL padding is common too.
    return value is None or not value.strip(" \x00")


def exif_raw_for_storage(value: str | None, max_length: int) -> str | None:
    """Raw EXIF string as persisted: blank/NUL-padded values become NULL.

    Values longer than the column are not stored; they are always INVALID, which
    the database accepts without raw text.
    """
    if _blank(value):
        return None
    stored = value.strip(" \x00")
    return stored if len(stored) <= max_length else None


def classify_photo_time(
    datetime_original: str | None,
    offset_time_original: str | None,
    subsec_time_original: str | None,
    received_at: datetime,
) -> PhotoTime:
    """Status fixed at server receipt: VALID within [received_at - 30 days, received_at]."""
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    if _blank(datetime_original):
        return PhotoTime(None, PhotoTimeStatus.MISSING)
    match = _DATETIME.match(datetime_original.strip("\x00"))
    if match is None:
        return PhotoTime(None, PhotoTimeStatus.INVALID)
    microsecond = 0
    if not _blank(subsec_time_original):
        subsec = subsec_time_original.strip(" \x00")
        if _SUBSEC.match(subsec) is None:
            return PhotoTime(None, PhotoTimeStatus.INVALID)
        microsecond = int(subsec[:6].ljust(6, "0"))
    try:
        local = datetime(*(int(part) for part in match.groups()), microsecond=microsecond)
    except ValueError:
        return PhotoTime(None, PhotoTimeStatus.INVALID)
    if _blank(offset_time_original):
        return PhotoTime(None, PhotoTimeStatus.TIMEZONE_UNKNOWN)
    offset_match = _OFFSET.match(offset_time_original.strip(" \x00"))
    if offset_match is None:
        return PhotoTime(None, PhotoTimeStatus.INVALID)
    sign, hours, minutes = offset_match.groups()
    if int(minutes) >= 60:
        return PhotoTime(None, PhotoTimeStatus.INVALID)
    offset = timedelta(hours=int(hours), minutes=int(minutes)) * (-1 if sign == "-" else 1)
    if not _MIN_OFFSET <= offset <= _MAX_OFFSET:
        return PhotoTime(None, PhotoTimeStatus.INVALID)
    captured_at = local.replace(tzinfo=timezone(offset)).astimezone(UTC)
    if captured_at > received_at:
        return PhotoTime(captured_at, PhotoTimeStatus.FUTURE)
    if captured_at < received_at - PHOTO_WINDOW:
        return PhotoTime(captured_at, PhotoTimeStatus.OUTSIDE_WINDOW)
    return PhotoTime(captured_at, PhotoTimeStatus.VALID)


def counts_at_publication(status: PhotoTimeStatus, captured_at: datetime | None, publish_at: datetime) -> bool:
    """Second, independent check at automatic publication: still within the last 30 days.

    The receipt-time status is never rewritten. After publication, natural photo
    ageing never revokes eligibility when support is recounted.
    """
    if status != PhotoTimeStatus.VALID or captured_at is None:
        return False
    return publish_at - PHOTO_WINDOW <= captured_at <= publish_at


def publication_term_end(first_published_at: datetime) -> datetime:
    return first_published_at + OBSERVATION_TERM


def derive_publication_status(
    state: CasePublicationState, published_until: datetime | None, now: datetime
) -> PublicationStatus:
    """EXPIRED is derived at read time; the term is [first_published_at, published_until)."""
    if state in (CasePublicationState.UNPUBLISHED, CasePublicationState.WITHDRAWN):
        return PublicationStatus(state.value)
    if published_until is not None and now >= published_until:
        return PublicationStatus.EXPIRED
    return PublicationStatus(state.value)


class ContributionLevel(StrEnum):
    NEWCOMER = "NEWCOMER"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


_LEVELS = ((50, ContributionLevel.L4), (20, ContributionLevel.L3), (5, ContributionLevel.L2), (1, ContributionLevel.L1))


def contribution_level(points: int) -> ContributionLevel:
    """Recognition only: levels never change vote weight, thresholds or source authority."""
    for minimum, level in _LEVELS:
        if points >= minimum:
            return level
    return ContributionLevel.NEWCOMER


@dataclass(frozen=True)
class LedgerEntry:
    case_id: int
    entry_type: ContributionEntryType
    points: int


def effective_points(entries: Iterable[LedgerEntry]) -> int:
    """One participant's points from ledger entries in insertion order.

    A case whose latest FREEZE/UNFREEZE marker is FREEZE contributes nothing.
    """
    totals: dict[int, int] = {}
    frozen: dict[int, bool] = {}
    for entry in entries:
        totals[entry.case_id] = totals.get(entry.case_id, 0) + entry.points
        if entry.entry_type == ContributionEntryType.FREEZE:
            frozen[entry.case_id] = True
        elif entry.entry_type == ContributionEntryType.UNFREEZE:
            frozen[entry.case_id] = False
    return sum(points for case_id, points in totals.items() if not frozen.get(case_id, False))
