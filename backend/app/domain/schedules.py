"""Conservative schedule matching at an explicit instant in Asia/Taipei."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.domain.parking import absolute_instant

TAIPEI = ZoneInfo("Asia/Taipei")
_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\Z")
_FIELDS = {"timezone", "day_type", "weekdays", "dates", "start_time", "end_time"}


class ScheduleMatch(StrEnum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNKNOWN = "UNKNOWN"


class HolidayCalendar(Protocol):
    """A preloaded calendar; missing coverage returns None, never assumed False."""

    def is_holiday(self, day: date) -> bool | None: ...


@dataclass(frozen=True)
class CalendarSnapshot:
    days: Mapping[date, bool]

    def is_holiday(self, day: date) -> bool | None:
        return self.days.get(day)


def _local_time(value: Any) -> time:
    if not isinstance(value, str) or not _TIME_PATTERN.fullmatch(value):
        raise ValueError("Expected HH:MM or HH:MM:SS without an offset")
    return time.fromisoformat(value)


def _dates(value: Any) -> set[date]:
    if not isinstance(value, list):
        raise ValueError("Dates must be a list")
    result = set()
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item):
            raise ValueError("Expected YYYY-MM-DD")
        result.add(date.fromisoformat(item))
    return result


def match_schedule(
    schedule: dict[str, Any] | None, evaluation_at: datetime, holiday_calendar: HolidayCalendar | None = None
) -> ScheduleMatch:
    """Combine constraints with AND; overnight day filters use the starting day.

    Malformed or unsupported constraints return UNKNOWN, so an uncertain
    higher-priority rule cannot disappear and expose a lower-priority permission.
    """
    local = absolute_instant(evaluation_at).astimezone(TAIPEI)
    if schedule is None:
        return ScheduleMatch.MATCH
    try:
        if not isinstance(schedule, dict) or set(schedule) - _FIELDS:
            raise ValueError("Unsupported schedule fields")
        if schedule.get("timezone", "Asia/Taipei") != "Asia/Taipei":
            raise ValueError("Only Asia/Taipei schedules are supported")
        day_type = schedule.get("day_type", "ALL")
        if day_type not in ("ALL", "WEEKDAY", "WEEKEND", "HOLIDAY", "SPECIAL"):
            raise ValueError("Unsupported day type")
        weekdays = schedule.get("weekdays")
        if "weekdays" in schedule and (
            not isinstance(weekdays, list) or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)
        ):
            raise ValueError("Weekdays must be integers 0 (Monday) to 6 (Sunday)")
        dates = _dates(schedule["dates"]) if "dates" in schedule else None
        if day_type == "SPECIAL" and dates is None:
            raise ValueError("SPECIAL requires explicit dates")
        start = _local_time(schedule["start_time"]) if "start_time" in schedule else None
        end = _local_time(schedule["end_time"]) if "end_time" in schedule else None
        if (start is None) != (end is None) or (start is not None and start == end):
            raise ValueError("A nonempty time window needs both endpoints")
    except (TypeError, ValueError):
        return ScheduleMatch.UNKNOWN

    day = local.date()
    if start is not None and end is not None:
        current = local.time()
        if start < end:
            if not start <= current < end:
                return ScheduleMatch.NO_MATCH
        else:
            if current >= start:
                pass
            elif current < end:
                day -= timedelta(days=1)
            else:
                return ScheduleMatch.NO_MATCH
    if weekdays is not None and day.weekday() not in weekdays:
        return ScheduleMatch.NO_MATCH
    if dates is not None and day not in dates:
        return ScheduleMatch.NO_MATCH
    if day_type == "WEEKDAY" and day.weekday() >= 5:
        return ScheduleMatch.NO_MATCH
    if day_type == "WEEKEND" and day.weekday() < 5:
        return ScheduleMatch.NO_MATCH
    if day_type == "HOLIDAY":
        if holiday_calendar is None:
            return ScheduleMatch.UNKNOWN
        holiday = holiday_calendar.is_holiday(day)
        if type(holiday) is not bool:
            return ScheduleMatch.UNKNOWN
        if not holiday:
            return ScheduleMatch.NO_MATCH
    return ScheduleMatch.MATCH
