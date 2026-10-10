"""Pure community-verification policies from docs/community-verification-contract.md."""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.domain.community import (
    OBSERVATION_TERM,
    PHOTO_WINDOW,
    ContributionLevel,
    LedgerEntry,
    classify_photo_time,
    contribution_level,
    counts_at_publication,
    derive_publication_status,
    effective_points,
    publication_term_end,
)
from app.models.enums import CasePublicationState as State
from app.models.enums import ContributionEntryType as Entry
from app.models.enums import PhotoTimeStatus as Time
from app.models.enums import PublicationStatus as Status

RECEIVED = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)  # 12:00 Asia/Taipei


def test_complete_metadata_is_converted_with_its_own_offset():
    photo = classify_photo_time("2026:10:10 11:30:00", "+08:00", None, RECEIVED)
    assert photo == (datetime(2026, 10, 10, 3, 30, tzinfo=UTC), Time.VALID)
    other_zone = classify_photo_time("2026:10:09 23:30:00", "-05:00", None, RECEIVED)
    assert other_zone.captured_at == datetime(2026, 10, 10, 4, 30, tzinfo=UTC)
    assert other_zone.status == Time.FUTURE


def test_subseconds_are_kept_when_present_and_optional_otherwise():
    photo = classify_photo_time("2026:10:10 11:30:00", "+08:00", "1234567", RECEIVED)
    assert photo.status == Time.VALID
    assert photo.captured_at.microsecond == 123456
    assert classify_photo_time("2026:10:10 11:30:00", "+08:00", "  ", RECEIVED).status == Time.VALID


@pytest.mark.parametrize(
    "captured, status",
    [
        (RECEIVED, Time.VALID),
        (RECEIVED - PHOTO_WINDOW, Time.VALID),
        (RECEIVED - PHOTO_WINDOW - timedelta(seconds=1), Time.OUTSIDE_WINDOW),
        (RECEIVED + timedelta(seconds=1), Time.FUTURE),
    ],
)
def test_receipt_window_boundaries_are_inclusive_without_future_tolerance(captured, status):
    exif = captured.astimezone(UTC).strftime("%Y:%m:%d %H:%M:%S")
    assert classify_photo_time(exif, "+00:00", None, RECEIVED) == (captured, status)


@pytest.mark.parametrize("value", [None, "", "                   ", "\x00" * 19])
def test_missing_original_time(value):
    assert classify_photo_time(value, "+08:00", None, RECEIVED) == (None, Time.MISSING)


@pytest.mark.parametrize("offset", [None, "", "      "])
def test_missing_offset_is_never_assumed_to_be_taipei(offset):
    assert classify_photo_time("2026:10:10 11:30:00", offset, None, RECEIVED) == (None, Time.TIMEZONE_UNKNOWN)


@pytest.mark.parametrize(
    "datetime_original, offset, subsec",
    [
        ("2026-10-10 11:30:00", "+08:00", None),
        ("0000:00:00 00:00:00", "+08:00", None),
        ("2026:02:30 11:30:00", "+08:00", None),
        ("2026:10:10 24:00:00", "+08:00", None),
        ("2026:10:10 11:30:00", "+0800", None),
        ("2026:10:10 11:30:00", "+08:60", None),
        ("2026:10:10 11:30:00", "+15:00", None),
        ("2026:10:10 11:30:00", "-13:00", None),
        ("2026:10:10 11:30:00", "+08:00", "12a"),
        ("bad", None, None),
    ],
)
def test_malformed_or_contradictory_fields_are_invalid(datetime_original, offset, subsec):
    assert classify_photo_time(datetime_original, offset, subsec, RECEIVED) == (None, Time.INVALID)


def test_receipt_time_must_be_absolute():
    with pytest.raises(ValueError):
        classify_photo_time("2026:10:10 11:30:00", "+08:00", None, RECEIVED.replace(tzinfo=None))


def test_publication_rechecks_window_but_never_rewrites_receipt_status():
    captured = RECEIVED - timedelta(days=20)
    assert counts_at_publication(Time.VALID, captured, RECEIVED + timedelta(days=10))
    assert not counts_at_publication(Time.VALID, captured, RECEIVED + timedelta(days=10, seconds=1))
    for status in (Time.MISSING, Time.TIMEZONE_UNKNOWN, Time.INVALID, Time.FUTURE, Time.OUTSIDE_WINDOW):
        assert not counts_at_publication(status, captured, RECEIVED)


def test_expired_is_derived_at_read_time_and_withdrawn_stays_withdrawn():
    first = RECEIVED
    until = publication_term_end(first)
    assert until - first == OBSERVATION_TERM == timedelta(days=90)
    before = until - timedelta(microseconds=1)
    assert derive_publication_status(State.PUBLISHED, until, before) == Status.PUBLISHED
    assert derive_publication_status(State.PUBLISHED, until, until) == Status.EXPIRED
    assert derive_publication_status(State.SUSPENDED, until, before) == Status.SUSPENDED
    assert derive_publication_status(State.SUSPENDED, until, until) == Status.EXPIRED
    assert derive_publication_status(State.WITHDRAWN, until, until) == Status.WITHDRAWN
    assert derive_publication_status(State.UNPUBLISHED, None, until) == Status.UNPUBLISHED
    # Source-confirmed publications have no observation term.
    assert derive_publication_status(State.PUBLISHED, None, until) == Status.PUBLISHED


@pytest.mark.parametrize(
    "points, level",
    [
        (0, ContributionLevel.NEWCOMER),
        (1, ContributionLevel.L1),
        (4, ContributionLevel.L1),
        (5, ContributionLevel.L2),
        (20, ContributionLevel.L3),
        (49, ContributionLevel.L3),
        (50, ContributionLevel.L4),
    ],
)
def test_contribution_levels(points, level):
    assert contribution_level(points) == level


def test_frozen_awards_do_not_count_and_revocations_reverse():
    entries = [
        LedgerEntry(1, Entry.AWARD, 1),
        LedgerEntry(2, Entry.AWARD, 1),
        LedgerEntry(3, Entry.AWARD, 1),
        LedgerEntry(2, Entry.FREEZE, 0),
        LedgerEntry(3, Entry.FREEZE, 0),
        LedgerEntry(3, Entry.UNFREEZE, 0),
        LedgerEntry(1, Entry.REVOKE, -1),
    ]
    assert effective_points(entries) == 1
    assert effective_points(entries + [LedgerEntry(2, Entry.UNFREEZE, 0)]) == 2


def test_original_photo_limit_defaults_to_15_mb(monkeypatch):
    monkeypatch.delenv("REPORT_PHOTO_MAX_BYTES", raising=False)
    assert Settings(_env_file=None).report_photo_max_bytes == 15_000_000
