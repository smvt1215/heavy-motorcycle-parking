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

from app.models.enums import (
    CasePublicationState,
    CaseStance,
    ContributionEntryType,
    PhotoTimeStatus,
    PrecheckCode,
    PrecheckOutcome,
    PublicationBasis,
    PublicationStatus,
)

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


# --- Deterministic precheck -------------------------------------------------------------

PRECHECK_RULE_VERSION = "precheck-1"


class Decision(StrEnum):
    PUBLISH = "PUBLISH"
    AWAIT = "AWAIT"
    MANUAL = "MANUAL"
    SUSPEND = "SUSPEND"
    KEEP = "KEEP"


@dataclass(frozen=True)
class EvidencePhoto:
    photo_id: int
    sha256: str
    time_status: PhotoTimeStatus
    captured_at: datetime | None
    deleted: bool = False


@dataclass(frozen=True)
class StanceInput:
    stance_id: int
    participant_id: int
    stance: CaseStance
    observed_value: dict | None
    photos: tuple[EvidencePhoto, ...] = ()
    participant_suspended: bool = False


@dataclass(frozen=True)
class CaseInput:
    low_risk: bool
    author_participant_id: int
    author_suspended: bool
    proposed_value: dict
    original_photos: tuple[EvidencePhoto, ...]
    stances: tuple[StanceInput, ...]
    publication_state: CasePublicationState
    publication_basis: PublicationBasis | None
    published_until: datetime | None
    auto_publish_enabled: bool


@dataclass(frozen=True)
class CheckResult:
    code: PrecheckCode
    outcome: PrecheckOutcome
    reason_code: str | None = None
    detail: dict | None = None


@dataclass(frozen=True)
class PrecheckReport:
    outcome: PrecheckOutcome
    decision: Decision
    results: tuple[CheckResult, ...]
    supporters: tuple[int, ...]
    objectors: tuple[int, ...]


def _counted(photos, seen: set[str], counts) -> tuple[int, int]:
    """(counted, duplicates). A photo counts once per case by normalized SHA-256."""
    counted = duplicates = 0
    for photo in photos:
        if photo.deleted or not counts(photo):
            continue
        if photo.sha256 in seen:
            duplicates += 1
            continue
        seen.add(photo.sha256)
        counted += 1
    return counted, duplicates


def evaluate_case(case: CaseInput, now: datetime) -> PrecheckReport:
    """Precheck a case revision. No AI or confidence score decides publication.

    Before publication a photo counts only when VALID at receipt and still within
    the 30-day window at `now`; once published, recounting ignores natural ageing.
    Stances from the author or suspended participants never count. A valid
    objection opposes with independent eligible evidence and a different value.
    """
    unpublished = case.publication_state == CasePublicationState.UNPUBLISHED
    if unpublished:

        def counts(photo: EvidencePhoto) -> bool:
            return counts_at_publication(photo.time_status, photo.captured_at, now)
    else:

        def counts(photo: EvidencePhoto) -> bool:
            return photo.time_status == PhotoTimeStatus.VALID

    results: list[CheckResult] = []

    def add(code, outcome, reason=None, detail=None):
        results.append(CheckResult(code, outcome, reason, detail))

    if case.author_suspended:
        add(PrecheckCode.IDENTITY, PrecheckOutcome.NEEDS_MANUAL, "AUTHOR_SUSPENDED")
    else:
        add(PrecheckCode.IDENTITY, PrecheckOutcome.PASS)
    add(PrecheckCode.REQUIRED_FIELDS, PrecheckOutcome.PASS)
    add(PrecheckCode.SCOPE, PrecheckOutcome.PASS)

    seen: set[str] = set()
    original, duplicates = _counted(case.original_photos, seen, counts)
    supporters: list[int] = []
    objectors: list[int] = []
    for stance in case.stances:
        if stance.participant_id == case.author_participant_id or stance.participant_suspended:
            continue
        if stance.stance == CaseStance.CANNOT_CONFIRM:
            continue
        counted, dup = _counted(stance.photos, seen, counts)
        duplicates += dup
        if not counted:
            continue
        if stance.stance == CaseStance.SUPPORT:
            supporters.append(stance.participant_id)
        elif stance.observed_value != case.proposed_value:
            objectors.append(stance.participant_id)
    uploaded = any(not photo.deleted for photo in case.original_photos)
    if original:
        add(PrecheckCode.PHOTO_TIME, PrecheckOutcome.PASS)
    elif uploaded:
        add(PrecheckCode.PHOTO_TIME, PrecheckOutcome.NEEDS_MANUAL, "ORIGINAL_PHOTO_TIME_UNVERIFIED")
    elif len(supporters) >= CORROBORATION_THRESHOLD:
        # Corroborated but the author never uploaded: a person must look at it.
        add(PrecheckCode.PHOTO_TIME, PrecheckOutcome.NEEDS_MANUAL, "ORIGINAL_PHOTO_MISSING")
    else:
        # The author may still be uploading; wait instead of queueing for review.
        add(PrecheckCode.PHOTO_TIME, PrecheckOutcome.FAIL, "ORIGINAL_PHOTO_MISSING")
    if case.low_risk:
        add(PrecheckCode.SOURCE_APPLICABILITY, PrecheckOutcome.PASS)
    else:
        add(PrecheckCode.SOURCE_APPLICABILITY, PrecheckOutcome.NEEDS_MANUAL, "NO_VERIFIED_PARSER")
    add(PrecheckCode.EVIDENCE_DUPLICATE, PrecheckOutcome.PASS, detail={"duplicates_ignored": duplicates})

    if len(supporters) >= CORROBORATION_THRESHOLD:
        add(PrecheckCode.CORROBORATION, PrecheckOutcome.PASS, detail={"supporters": len(supporters)})
    else:
        add(
            PrecheckCode.CORROBORATION,
            PrecheckOutcome.FAIL,
            "INSUFFICIENT_CORROBORATION",
            {"supporters": len(supporters), "required": CORROBORATION_THRESHOLD},
        )
    if objectors:
        add(
            PrecheckCode.SOURCE_CONFLICT, PrecheckOutcome.NEEDS_MANUAL, "VALID_OBJECTION", {"objectors": len(objectors)}
        )
    else:
        add(PrecheckCode.SOURCE_CONFLICT, PrecheckOutcome.PASS)

    blocking = {result.outcome for result in results}
    if PrecheckOutcome.NEEDS_MANUAL in blocking or PrecheckOutcome.FAIL in blocking:
        add(PrecheckCode.PUBLICATION_ELIGIBILITY, PrecheckOutcome.FAIL, "NOT_ELIGIBLE")
    elif not case.auto_publish_enabled:
        add(PrecheckCode.PUBLICATION_ELIGIBILITY, PrecheckOutcome.NEEDS_MANUAL, "AUTO_PUBLISH_DISABLED")
    else:
        add(PrecheckCode.PUBLICATION_ELIGIBILITY, PrecheckOutcome.PASS)

    outcomes = {result.outcome for result in results}
    outcome = (
        PrecheckOutcome.NEEDS_MANUAL
        if PrecheckOutcome.NEEDS_MANUAL in outcomes
        else PrecheckOutcome.FAIL
        if PrecheckOutcome.FAIL in outcomes
        else PrecheckOutcome.PASS
    )
    return PrecheckReport(
        outcome, _decide(case, now, results, supporters, objectors), tuple(results), tuple(supporters), tuple(objectors)
    )


def _decide(case, now, results, supporters, objectors) -> Decision:
    state = case.publication_state
    if state == CasePublicationState.UNPUBLISHED:
        outcomes = {result.code: result.outcome for result in results}
        if PrecheckOutcome.NEEDS_MANUAL in outcomes.values():
            return Decision.MANUAL
        return (
            Decision.PUBLISH
            if outcomes[PrecheckCode.PUBLICATION_ELIGIBILITY] == PrecheckOutcome.PASS
            else Decision.AWAIT
        )
    if state != CasePublicationState.PUBLISHED:
        return Decision.KEEP
    if derive_publication_status(state, case.published_until, now) == PublicationStatus.EXPIRED:
        return Decision.KEEP
    if objectors:
        return Decision.SUSPEND
    if case.publication_basis == PublicationBasis.COMMUNITY_CORROBORATED:
        photo_time = next(result for result in results if result.code == PrecheckCode.PHOTO_TIME)
        # Withdrawn support or deleted evidence suspends a community-corroborated observation.
        if len(supporters) < CORROBORATION_THRESHOLD or photo_time.outcome != PrecheckOutcome.PASS:
            return Decision.SUSPEND
    return Decision.KEEP


# --- Proposal / observation values -------------------------------------------------------

_ENTRANCE_TYPES = {"VEHICLE", "PEDESTRIAN", "MIXED", None}


def _strict_bool(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def normalize_value(fact_type: str, value) -> dict:
    """Strictly validate a proposed/observed value for a fact type; unknown keys are rejected.

    A value never expresses "unknown": riders who cannot tell use CANNOT_CONFIRM.
    """
    if not isinstance(value, dict):
        raise ValueError("value must be an object")

    def keys(required: set[str], optional: set[str] = frozenset()) -> None:
        present = set(value)
        if not required <= present or not present <= required | optional:
            raise ValueError(f"value keys must be {sorted(required | optional)}")

    if fact_type in ("LIGHTING", "RAIN_COVER", "CHARGING"):
        keys({"present"})
        return {"present": _strict_bool(value["present"])}
    if fact_type == "PARKING_PERMISSION":
        keys({"allowed"})
        return {"allowed": _strict_bool(value["allowed"])}
    if fact_type == "RATE":
        keys({"raw_text"})
        text = value["raw_text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError("raw_text must be 1-500 characters")
        return {"raw_text": text.strip()}
    if fact_type == "ENTRANCE_ACCESS":
        keys({"heavy_motorcycle_access"}, {"entrance_id"})
        entrance_id = value.get("entrance_id")
        if entrance_id is not None and (
            isinstance(entrance_id, bool) or not isinstance(entrance_id, int) or entrance_id < 1
        ):
            raise ValueError("entrance_id must be a positive integer or null")
        return {"entrance_id": entrance_id, "heavy_motorcycle_access": _strict_bool(value["heavy_motorcycle_access"])}
    if fact_type == "ENTRANCE_LOCATION":
        keys({"latitude", "longitude"}, {"entrance_type"})
        lat, lng = value["latitude"], value["longitude"]
        for number in (lat, lng):
            if isinstance(number, bool) or not isinstance(number, int | float):
                raise ValueError("latitude/longitude must be numbers")
        if not (21.5 <= lat <= 26.5 and 118.0 <= lng <= 122.5):
            raise ValueError("coordinates must be within Taiwan")
        entrance_type = value.get("entrance_type")
        if entrance_type not in _ENTRANCE_TYPES:
            raise ValueError("entrance_type must be VEHICLE, PEDESTRIAN, MIXED or null")
        return {"latitude": float(lat), "longitude": float(lng), "entrance_type": entrance_type}
    raise ValueError("unsupported fact type")
