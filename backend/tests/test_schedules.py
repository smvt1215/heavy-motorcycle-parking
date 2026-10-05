from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.domain.schedules import CalendarSnapshot, ScheduleMatch, match_schedule

MONDAY = datetime(2026, 10, 5, 4, tzinfo=UTC)  # Monday noon in Taipei.
SATURDAY = datetime(2026, 10, 10, 4, tzinfo=UTC)


@pytest.mark.parametrize("schedule", [None, {}, {"timezone": "Asia/Taipei"}, {"day_type": "ALL"}])
def test_unrestricted_schedules(schedule):
    assert match_schedule(schedule, MONDAY) == ScheduleMatch.MATCH


@pytest.mark.parametrize(
    "day_type, instant, expected",
    [
        ("WEEKDAY", MONDAY, "MATCH"),
        ("WEEKDAY", SATURDAY, "NO_MATCH"),
        ("WEEKEND", MONDAY, "NO_MATCH"),
        ("WEEKEND", SATURDAY, "MATCH"),
    ],
)
def test_weekday_and_weekend_are_local_calendar_days(day_type, instant, expected):
    assert match_schedule({"day_type": day_type}, instant) == expected


def test_utc_day_is_converted_before_schedule_evaluation():
    # Friday UTC has already become Saturday in Taipei.
    instant = datetime(2026, 10, 9, 17, tzinfo=UTC)
    assert match_schedule({"day_type": "WEEKEND"}, instant) == ScheduleMatch.MATCH
    assert match_schedule({"weekdays": [4]}, instant) == ScheduleMatch.NO_MATCH


def test_equivalent_absolute_instants_have_identical_schedule_result():
    schedule = {"weekdays": [0], "start_time": "12:00", "end_time": "13:00"}
    foreign_offset = MONDAY.astimezone(timezone(timedelta(hours=-7)))
    assert match_schedule(schedule, MONDAY) == match_schedule(schedule, foreign_offset) == ScheduleMatch.MATCH


@pytest.mark.parametrize("minute, expected", [(59, "NO_MATCH"), (60, "MATCH"), (119, "MATCH"), (120, "NO_MATCH")])
def test_time_window_is_half_open(minute, expected):
    instant = datetime(2026, 10, 5, 3, tzinfo=UTC) + timedelta(minutes=minute)
    assert match_schedule({"start_time": "12:00", "end_time": "13:00"}, instant) == expected


def test_seconds_are_preserved_at_time_boundaries():
    schedule = {"start_time": "12:00:30", "end_time": "12:00:45"}
    assert match_schedule(schedule, MONDAY + timedelta(seconds=29)) == ScheduleMatch.NO_MATCH
    assert match_schedule(schedule, MONDAY + timedelta(seconds=30)) == ScheduleMatch.MATCH
    assert match_schedule(schedule, MONDAY + timedelta(seconds=45)) == ScheduleMatch.NO_MATCH


@pytest.mark.parametrize("local_hour, expected", [(0, "MATCH"), (5, "MATCH"), (6, "NO_MATCH"), (21, "NO_MATCH")])
def test_overnight_windows_anchor_weekdays_to_starting_day(local_hour, expected):
    local_sat = datetime(2026, 10, 10, local_hour, tzinfo=timezone(timedelta(hours=8)))
    schedule = {"weekdays": [4], "start_time": "22:00", "end_time": "06:00"}
    assert match_schedule(schedule, local_sat) == expected


def test_overnight_special_date_and_midnight_endpoint():
    schedule = {"day_type": "SPECIAL", "dates": ["2026-10-09"], "start_time": "22:00", "end_time": "06:00"}
    assert match_schedule(schedule, datetime(2026, 10, 9, 17, tzinfo=UTC)) == ScheduleMatch.MATCH
    assert match_schedule(schedule, datetime(2026, 10, 10, 17, tzinfo=UTC)) == ScheduleMatch.NO_MATCH
    until_midnight = {"weekdays": [4], "start_time": "22:00", "end_time": "00:00"}
    assert match_schedule(until_midnight, datetime(2026, 10, 9, 15, tzinfo=UTC)) == ScheduleMatch.MATCH
    assert match_schedule(until_midnight, datetime(2026, 10, 9, 16, tzinfo=UTC)) == ScheduleMatch.NO_MATCH


@pytest.mark.parametrize("value, expected", [(True, "MATCH"), (False, "NO_MATCH"), (None, "UNKNOWN")])
def test_holidays_require_explicit_calendar_coverage(value, expected):
    calendar = CalendarSnapshot({MONDAY.date(): value} if value is not None else {})
    assert match_schedule({"day_type": "HOLIDAY"}, MONDAY, calendar) == expected


def test_absent_holiday_calendar_is_unknown():
    assert match_schedule({"day_type": "HOLIDAY"}, MONDAY) == ScheduleMatch.UNKNOWN


def test_holiday_rule_overnight_uses_previous_days_calendar_coverage():
    calendar = CalendarSnapshot({date(2026, 10, 9): True, date(2026, 10, 10): False})
    schedule = {"day_type": "HOLIDAY", "start_time": "22:00", "end_time": "06:00"}
    assert match_schedule(schedule, datetime(2026, 10, 9, 17, tzinfo=UTC), calendar) == ScheduleMatch.MATCH


def test_constraints_are_intersections_and_known_exclusion_needs_no_calendar():
    assert match_schedule({"weekdays": [0], "dates": ["2026-10-05"]}, MONDAY) == ScheduleMatch.MATCH
    assert match_schedule({"weekdays": [0], "dates": ["2026-10-06"]}, MONDAY) == ScheduleMatch.NO_MATCH
    assert match_schedule({"weekdays": [], "day_type": "HOLIDAY"}, MONDAY) == ScheduleMatch.NO_MATCH
    assert match_schedule({"dates": []}, MONDAY) == ScheduleMatch.NO_MATCH


@pytest.mark.parametrize(
    "schedule",
    [
        [],
        False,
        "WEEKDAY",
        {"day_type": "WORKDAY"},
        {"day_type": None},
        {"timezone": "UTC"},
        {"timezone": None},
        {"is_open": True},
        {"weekdays": [True]},
        {"weekdays": [1.0]},
        {"weekdays": ["1"]},
        {"weekdays": [7]},
        {"weekdays": [-1]},
        {"weekdays": None},
        {"weekdays": "MON"},
        {"dates": "2026-10-05"},
        {"dates": ["2026-02-30"]},
        {"dates": ["20261005"]},
        {"dates": [False]},
        {"dates": None},
        {"day_type": "SPECIAL"},
        {"start_time": "12:00"},
        {"end_time": "13:00"},
        {"start_time": "24:00", "end_time": "06:00"},
        {"start_time": "22:00", "end_time": "24:00"},
        {"start_time": "12:00", "end_time": "12:00"},
        {"start_time": "9:00", "end_time": "12:00"},
        {"start_time": "09:00+08:00", "end_time": "12:00"},
        {"start_time": None, "end_time": None},
    ],
)
def test_malformed_or_unsupported_schedule_is_unknown(schedule):
    assert match_schedule(schedule, MONDAY) == ScheduleMatch.UNKNOWN


@pytest.mark.parametrize("schedule", [None, {"day_type": "ALL"}])
def test_naive_evaluation_timestamp_is_rejected_even_for_unrestricted_schedule(schedule):
    with pytest.raises(ValueError, match="offset-aware"):
        match_schedule(schedule, datetime(2026, 10, 5, 12))


def test_schedule_payload_is_not_mutated():
    schedule = {"dates": ["2026-10-05"], "weekdays": [0], "start_time": "12:00", "end_time": "13:00"}
    original = {**schedule, "dates": list(schedule["dates"]), "weekdays": list(schedule["weekdays"])}
    match_schedule(schedule, MONDAY)
    assert schedule == original
