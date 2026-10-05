"""Conservative selected-vehicle rate resolution at a pinned instant.

Policy (deliberately conservative; nothing here parses raw text):

* Only rates whose `vehicle` equals the explicit selected vehicle are
  considered. An unspecified (NULL) vehicle never claims a selected-vehicle
  price, so such rates appear neither in the summary nor in the rates list.
* A rate is *applicable* when `evaluation_at` lies in its half-open
  `[effective_from, effective_to)` window and its own schedule matches in
  Asia/Taipei. An inverted window or an UNKNOWN schedule (malformed, holiday
  without calendar coverage) keeps the rate as *uncertain* evidence: it is
  listed, but blocks any confirmed summary value. Nothing falls back to a
  cheaper or lower-priority rate.
* Rate rules carry their own `day_type`, local `start_time`/`end_time` and
  schedule. They are intersected with the parent (AND) using the M2 schedule
  matcher, so overnight windows evaluate day filters on their starting day.
  When a rate has rules, the matching rules define the price at the instant:
  no matching rule, any UNKNOWN rule, or matching rules with different hourly
  values make the rate non-comparable (never the cheapest, never the base).
  A rule restating neither `amount` nor `unit_minutes` inherits both from the
  parent; restating only one of them is ambiguous. Rules with a parking
  duration range (other than open-ended from minute 0) are duration-dependent.
  `max_amount` on a rule may be a window cap, so it is never reported as a
  daily cap.
* A comparison hourly value is produced only for PARSED, TWD, FREE (amount
  null/0 => 0) or HOURLY/TIME_BLOCK with a nonnegative Decimal/int amount, an
  actual positive int `unit_minutes` with an exact finite decimal hourly
  equivalent and no free minutes. Progressive, per-entry, flat, daily,
  monthly, custom, partially parsed, raw-only and invalid records are
  non-comparable.
* A daily cap is confirmed independently of the hourly comparison (e.g. a
  PROGRESSIVE rate may still have a confirmed cap) when every applicable rate
  is certain, PARSED, TWD and states the same valid `daily_max_amount`.
* Several applicable rates must agree exactly (by Decimal value) to yield a
  comparison value or cap; identical ties are recognized and all their
  evidence is kept in `supporting_sources`.

`rate_id` orders output and picks the primary provenance only for stable
serialization; it never decides a price. Monetary values are compared as
Decimal and emitted as JSON numbers (int when integral).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any

from app.domain.discovery import RateFact, RateRuleFact
from app.domain.parking import VEHICLES, Provenance, absolute_instant
from app.domain.schedules import HolidayCalendar, ScheduleMatch, match_schedule
from app.services.compatibility import _provenance_dict

PARSED = "PARSED"
TWD = "TWD"
_UNIT_RATE_TYPES = frozenset({"HOURLY", "TIME_BLOCK"})
_PARSE_SEVERITY = {"PARSED": 0, "PARTIALLY_PARSED": 1, "RAW_ONLY": 2, "INVALID": 3}


@dataclass(frozen=True)
class _Terms:
    """A deterministic price term; `unit_minutes is None` denotes FREE."""

    hourly: Decimal
    amount: Decimal
    unit_minutes: int | None


@dataclass(frozen=True)
class _Evaluation:
    rate: RateFact
    certain: bool
    terms: _Terms | None
    cap: Decimal | None


def _money(value: Any) -> Decimal | None:
    """Finite nonnegative Decimal or int; floats, bools and strings are not money."""
    if isinstance(value, Decimal):
        return value if value.is_finite() and value >= 0 else None
    if type(value) is int and value >= 0:
        return Decimal(value)
    return None


def _number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _text(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else format(value.normalize(), "f")


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _label(value: Any) -> str | None:
    return str(value) if isinstance(value, str) else None


def _combine(*matches: ScheduleMatch) -> ScheduleMatch:
    if ScheduleMatch.NO_MATCH in matches:
        return ScheduleMatch.NO_MATCH
    if ScheduleMatch.UNKNOWN in matches:
        return ScheduleMatch.UNKNOWN
    return ScheduleMatch.MATCH


def _clock(value: Any) -> str | None:
    """Local wall-clock time as HH:MM:SS; offsets and sub-second precision are unsupported."""
    if not isinstance(value, time) or value.tzinfo is not None or value.microsecond:
        return None
    return value.strftime("%H:%M:%S")


def _rate_applicability(rate: RateFact, at: datetime, calendar: HolidayCalendar | None) -> ScheduleMatch:
    start = absolute_instant(rate.effective_from) if rate.effective_from is not None else None
    end = absolute_instant(rate.effective_to) if rate.effective_to is not None else None
    if start is not None and end is not None and start >= end:
        return ScheduleMatch.UNKNOWN
    if (start is not None and at < start) or (end is not None and at >= end):
        return ScheduleMatch.NO_MATCH
    return match_schedule(rate.schedule, at, calendar)


def _rule_match(rule: RateRuleFact, at: datetime, calendar: HolidayCalendar | None) -> ScheduleMatch:
    """Intersect a rule's day_type + local window with its own schedule."""
    day_type = _label(rule.day_type)
    if day_type is None:
        return ScheduleMatch.UNKNOWN
    derived: dict[str, Any] = {"day_type": day_type}
    if day_type == "SPECIAL":
        dates = rule.schedule.get("dates") if isinstance(rule.schedule, dict) else None
        if dates is None:
            return ScheduleMatch.UNKNOWN
        derived["dates"] = dates
    if rule.start_time is not None or rule.end_time is not None:
        start, end = _clock(rule.start_time), _clock(rule.end_time)
        if start is None or end is None:
            return ScheduleMatch.UNKNOWN
        derived["start_time"], derived["end_time"] = start, end
    schedule = rule.schedule
    if isinstance(schedule, dict) and "start_time" not in schedule and "end_time" not in schedule:
        # JSON weekday/date constraints share the column window's starting day.
        schedule = {**schedule, **{key: value for key, value in derived.items() if key in ("start_time", "end_time")}}
    return _combine(match_schedule(derived, at, calendar), match_schedule(schedule, at, calendar))


def _duration_dependent(rule: RateRuleFact) -> bool:
    return rule.end_minute is not None or rule.start_minute not in (None, 0)


def _normalize(rate_type: str, amount: Any, unit_minutes: Any, free_minutes: Any) -> _Terms | None:
    if rate_type == "FREE":
        if amount is None:
            return _Terms(Decimal(0), Decimal(0), None)
        value = _money(amount)
        return _Terms(Decimal(0), Decimal(0), None) if value is not None and value == 0 else None
    if rate_type not in _UNIT_RATE_TYPES or (free_minutes is not None and _nonnegative_int(free_minutes) != 0):
        return None
    value, unit = _money(amount), _positive_int(unit_minutes)
    if value is None or unit is None:
        return None
    equivalent = Fraction(value) * 60 / unit
    denominator = equivalent.denominator
    for divisor in (2, 5):
        while denominator % divisor == 0:
            denominator //= divisor
    if denominator != 1:
        return None
    with localcontext() as context:
        context.prec = max(60, len(str(equivalent.numerator)) + len(str(equivalent.denominator)) + 1)
        hourly = Decimal(equivalent.numerator) / Decimal(equivalent.denominator)
    return _Terms(hourly, value, unit)


def _price_terms(rate: RateFact, at: datetime, calendar: HolidayCalendar | None) -> _Terms | None:
    rate_type = _label(rate.rate_type)
    if rate.parse_status != PARSED or rate.currency != TWD or rate_type is None:
        return None
    if not rate.rules:
        return _normalize(rate_type, rate.base_amount, rate.unit_minutes, rate.free_minutes)
    matches = [(rule, _rule_match(rule, at, calendar)) for rule in rate.rules]
    if any(match is ScheduleMatch.UNKNOWN for _, match in matches):
        return None
    applicable = sorted((rule for rule, match in matches if match is ScheduleMatch.MATCH), key=lambda r: r.rule_id)
    if not applicable:
        return None
    terms = []
    for rule in applicable:
        if _duration_dependent(rule):
            return None
        if rule.amount is None and rule.unit_minutes is None:
            amount, unit = rate.base_amount, rate.unit_minutes
        elif rate_type != "FREE" and (rule.amount is None or rule.unit_minutes is None):
            return None
        else:
            amount, unit = rule.amount, rule.unit_minutes
        term = _normalize(rate_type, amount, unit, rate.free_minutes)
        if term is None:
            return None
        terms.append(term)
    return terms[0] if len({term.hourly for term in terms}) == 1 else None


def _daily_cap(rate: RateFact) -> Decimal | None:
    if rate.parse_status != PARSED or rate.currency != TWD:
        return None
    return _money(rate.daily_max_amount)


def _validate(rates: Iterable[RateFact]) -> tuple[RateFact, ...]:
    result = tuple(rates)
    for rate in result:
        if not isinstance(rate, RateFact) or type(rate.rate_id) is not int:
            raise ValueError("Rates must be RateFact objects with integer IDs")
        if not isinstance(rate.provenance, Provenance) or any(
            not isinstance(item, Provenance) for item in rate.supporting_sources
        ):
            raise ValueError("Rate evidence must provide Provenance")
        if any(not isinstance(rule, RateRuleFact) for rule in rate.rules):
            raise ValueError("Rate rules must be RateRuleFact objects")
    if len({rate.rate_id for rate in result}) != len(result):
        raise ValueError("Rate IDs must be unique")
    if len({rate.zone_id for rate in result}) > 1:
        raise ValueError("Rates of different zones must not be resolved together")
    return result


def _member(rate: RateFact, certain: bool) -> dict[str, Any]:
    return {
        "rate_id": rate.rate_id,
        "rate_type": _label(rate.rate_type),
        "currency": rate.currency,
        "base_amount": _number(_money(rate.base_amount)),
        "unit_minutes": _positive_int(rate.unit_minutes),
        "free_minutes": _nonnegative_int(rate.free_minutes),
        "daily_max_twd": _number(_money(rate.daily_max_amount)) if rate.currency == TWD else None,
        "description": rate.description,
        "raw_text": rate.raw_text,
        "parse_status": _label(rate.parse_status),
        "applicability": "MATCH" if certain else "UNKNOWN",
        "provenance": _provenance_dict(rate.provenance),
        "supporting_sources": [_provenance_dict(item) for item in rate.supporting_sources],
    }


def _parse_status(evaluations: list[_Evaluation]) -> str | None:
    """Worst parser status; unrecognized labels rank as most severe."""
    labels = [_label(item.rate.parse_status) or "INVALID" for item in evaluations]
    return max(labels, key=lambda label: (_PARSE_SEVERITY.get(label, len(_PARSE_SEVERITY)), label))


def _display_text(terms: _Terms | None, cap: Decimal | None, evaluations: list[_Evaluation]) -> str | None:
    cap_text = f"最高{_text(cap)}元/日" if cap is not None else None
    if terms is not None:
        if terms.unit_minutes is None:
            price = "免費"
        elif terms.unit_minutes == 60:
            price = f"{_text(terms.amount)}元/小時"
        else:
            price = f"{_text(terms.amount)}元/{terms.unit_minutes}分鐘"
        return f"{price}・{cap_text}" if cap_text else price
    texts: list[str] = []
    for item in evaluations:
        text = item.rate.description or item.rate.raw_text
        if text and text not in texts:
            texts.append(text)
    if texts:
        return "；".join(texts)
    return cap_text


def _summary(evaluations: list[_Evaluation]) -> dict[str, Any]:
    certain = all(item.certain for item in evaluations)
    terms_set = [item.terms for item in evaluations]
    hourly = {term.hourly for term in terms_set if term is not None}
    eligible = certain and None not in terms_set and len(hourly) == 1
    caps = {item.cap for item in evaluations}
    cap = next(iter(caps)) if certain and len(caps) == 1 and None not in caps else None
    terms = evaluations[0].terms if eligible else None

    primary = _provenance_dict(evaluations[0].rate.provenance)
    seen = {tuple(primary.values())}
    supporting = []
    evidence = [item.rate.provenance for item in evaluations[1:]]
    evidence += [source for item in evaluations for source in item.rate.supporting_sources]
    for source in evidence:
        serialized = _provenance_dict(source)
        if tuple(serialized.values()) not in seen:
            seen.add(tuple(serialized.values()))
            supporting.append(serialized)
    return {
        "display_text": _display_text(terms, cap, evaluations),
        "comparison_eligible": eligible,
        "comparison_hourly_rate_twd": _number(terms.hourly) if terms is not None else None,
        "daily_max_twd": _number(cap),
        "parse_status": _parse_status(evaluations),
        "provenance": primary,
        "supporting_sources": supporting,
    }


def resolve_rates(
    rates: tuple[RateFact, ...],
    vehicle: str,
    evaluation_at: datetime,
    holiday_calendar: HolidayCalendar | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return `(rate_summary, rates)` for one zone, explicit vehicle and instant.

    `rates` lists every applicable or uncertain exact-vehicle record in
    `rate_id` order with raw/partial text preserved. `rate_summary` is None
    when no such record exists; otherwise it reports a comparison value and
    daily cap only when they are deterministic (see module docstring).
    """
    if not isinstance(vehicle, str) or str(vehicle) not in VEHICLES:
        raise ValueError("An explicit supported vehicle is required")
    vehicle = str(vehicle)
    at = absolute_instant(evaluation_at)
    if holiday_calendar is not None and not callable(getattr(holiday_calendar, "is_holiday", None)):
        raise ValueError("Holiday calendar must provide is_holiday(day)")
    evaluations = []
    for rate in sorted(_validate(rates), key=lambda item: item.rate_id):
        if _label(rate.vehicle) != vehicle:
            continue
        applicability = _rate_applicability(rate, at, holiday_calendar)
        if applicability is ScheduleMatch.NO_MATCH:
            continue
        rule_matches = [_rule_match(rule, at, holiday_calendar) for rule in rate.rules]
        certain = (
            applicability is ScheduleMatch.MATCH
            and ScheduleMatch.UNKNOWN not in rule_matches
            and (not rate.rules or ScheduleMatch.MATCH in rule_matches)
        )
        evaluations.append(
            _Evaluation(
                rate=rate,
                certain=certain,
                terms=_price_terms(rate, at, holiday_calendar) if certain else None,
                cap=_daily_cap(rate) if certain else None,
            )
        )
    if not evaluations:
        return None, []
    return _summary(evaluations), [_member(item.rate, item.certain) for item in evaluations]
