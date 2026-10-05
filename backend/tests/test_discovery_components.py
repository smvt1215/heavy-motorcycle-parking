"""Pure-component tests for M3 rates, realtime and sort_version=1 ranking."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.discovery import EntranceFact, RateFact, RateRuleFact, RealtimeFact
from app.domain.parking import Provenance
from app.domain.schedules import CalendarSnapshot
from app.models.enums import RealtimeStatus, VehicleType
from app.services.compatibility import CompatibilityStatus
from app.services.ranking import (
    availability_component,
    confidence_component,
    distance_component,
    entrance_component,
    price_component,
    ranking_components,
    ranking_score,
    round_half_up,
    sort_key,
)
from app.services.rates import resolve_rates
from app.services.realtime import (
    aggregate_availability,
    is_available_only,
    resolve_availability,
    validate_counts,
)

TAIPEI = timezone(timedelta(hours=8))
# 2026-10-05 is a Monday; Taipei is UTC+8.
MON_01 = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)  # Mon 01:00 Taipei, overnight window started Sunday
MON_08 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # Mon 08:00 Taipei
MON_10 = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)  # Mon 10:00 Taipei
MON_20 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)  # Mon 20:00 Taipei
MON_23 = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)  # Mon 23:00 Taipei
TUE_01 = datetime(2026, 10, 5, 17, 0, tzinfo=UTC)  # Tue 01:00 Taipei, overnight window started Monday
TUE_10 = datetime(2026, 10, 6, 2, 0, tzinfo=UTC)  # Tue 10:00 Taipei
SAT_01 = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)  # Sat 01:00 Taipei, overnight window started Friday
SAT_10 = datetime(2026, 10, 3, 2, 0, tzinfo=UTC)  # Sat 10:00 Taipei
NOW = datetime(2026, 10, 5, 2, 10, tzinfo=UTC)
RATE_UPDATED = datetime(2026, 10, 1, tzinfo=UTC)
RATE_FETCHED = datetime(2026, 10, 2, 1, tzinfo=UTC)
RT_FETCHED = NOW - timedelta(seconds=60)
RT_UPDATED = NOW - timedelta(seconds=120)


# --------------------------------------------------------------------------- rates


def rate_prov(source_id=7, record=None):
    return Provenance(
        source_id=source_id,
        source_type="GOVERNMENT",
        source_record_id=record or f"rate-{source_id}",
        source_updated_at=RATE_UPDATED,
        fetched_at=RATE_FETCHED,
    )


def rate_prov_dict(source_id=7, record=None):
    return {
        "source_id": source_id,
        "source_type": "GOVERNMENT",
        "source_record_id": record or f"rate-{source_id}",
        "source_updated_at": "2026-10-01T00:00:00Z",
        "fetched_at": "2026-10-02T01:00:00Z",
        "verified_at": None,
    }


def rate(rate_id=901, **fields):
    values = {
        "rate_id": rate_id,
        "zone_id": 20,
        "vehicle": "RED",
        "rate_type": "HOURLY",
        "parse_status": "PARSED",
        "provenance": rate_prov(),
        "base_amount": Decimal("20"),
        "unit_minutes": 60,
        "free_minutes": 0,
        "daily_max_amount": Decimal("100"),
        "description": "20元/小時，最高100元/日",
    }
    values.update(fields)
    return RateFact(**values)


def resolve(*rates, vehicle="RED", at=MON_10, calendar=None):
    return resolve_rates(tuple(rates), vehicle, at, calendar)


def hourly_rules(*rules):
    """Rate whose price is defined only by its rules."""
    return rate(base_amount=None, unit_minutes=None, rules=tuple(rules))


def test_empty_rates_yield_no_summary_and_no_members():
    assert resolve() == (None, [])


def test_simple_hourly_rate_exact_wire_output():
    summary, members = resolve(rate(base_amount=Decimal("20.00"), daily_max_amount=Decimal("100.00")))
    assert summary == {
        "display_text": "20元/小時・最高100元/日",
        "comparison_eligible": True,
        "comparison_hourly_rate_twd": 20,
        "daily_max_twd": 100,
        "parse_status": "PARSED",
        "provenance": rate_prov_dict(),
        "supporting_sources": [],
    }
    assert members == [
        {
            "rate_id": 901,
            "rate_type": "HOURLY",
            "currency": "TWD",
            "base_amount": 20,
            "unit_minutes": 60,
            "free_minutes": 0,
            "daily_max_twd": 100,
            "description": "20元/小時，最高100元/日",
            "raw_text": None,
            "parse_status": "PARSED",
            "applicability": "MATCH",
            "provenance": rate_prov_dict(),
            "supporting_sources": [],
        }
    ]
    assert type(summary["comparison_hourly_rate_twd"]) is int
    assert type(summary["daily_max_twd"]) is int
    assert json.loads(json.dumps([summary, members])) == [summary, members]


@pytest.mark.parametrize(
    "rate_type, amount, unit, expected",
    [
        ("HOURLY", Decimal("20"), 60, 20),
        ("HOURLY", Decimal("10"), 30, 20),
        ("HOURLY", 30, 60, 30),
        ("HOURLY", 0, 60, 0),
        ("HOURLY", Decimal("7.5"), 60, 7.5),
        ("TIME_BLOCK", Decimal("15"), 30, 30),
        ("TIME_BLOCK", Decimal("10"), 20, 30),
        ("TIME_BLOCK", Decimal("5"), 15, 20),
        ("TIME_BLOCK", Decimal("12.5"), 30, 25),
        ("TIME_BLOCK", Decimal("1"), 1, 60),
        # Only exact finite hourly equivalents qualify; no recurring decimals.
        ("TIME_BLOCK", Decimal("10"), 45, None),
        ("TIME_BLOCK", Decimal("50"), 120, 25),
        ("TIME_BLOCK", Decimal("15"), 45, 20),
        ("HOURLY", Decimal("20"), 90, None),
        # Invalid unit_minutes.
        ("HOURLY", Decimal("20"), 0, None),
        ("HOURLY", Decimal("20"), -60, None),
        ("HOURLY", Decimal("20"), True, None),
        ("HOURLY", Decimal("20"), 60.0, None),
        ("HOURLY", Decimal("20"), None, None),
        # Invalid amounts.
        ("HOURLY", None, 60, None),
        ("HOURLY", Decimal("-1"), 60, None),
        ("HOURLY", 20.0, 60, None),
        ("HOURLY", True, 60, None),
        ("HOURLY", "20", 60, None),
        ("HOURLY", Decimal("NaN"), 60, None),
        ("HOURLY", Decimal("Infinity"), 60, None),
    ],
)
def test_unit_rate_normalization(rate_type, amount, unit, expected):
    summary, members = resolve(rate(rate_type=rate_type, base_amount=amount, unit_minutes=unit))
    assert summary["comparison_eligible"] is (expected is not None)
    assert summary["comparison_hourly_rate_twd"] == expected
    if expected is not None:
        assert type(summary["comparison_hourly_rate_twd"]) is type(expected)
    # The daily cap is an independent deterministic fact.
    assert summary["daily_max_twd"] == 100
    assert len(members) == 1


def test_time_block_display_keeps_source_unit():
    summary, _ = resolve(rate(rate_type="TIME_BLOCK", base_amount=Decimal("12.5"), unit_minutes=30))
    assert summary["display_text"] == "12.5元/30分鐘・最高100元/日"
    assert summary["comparison_hourly_rate_twd"] == 25


def test_fractional_hourly_value_is_emitted_as_json_number():
    summary, members = resolve(rate(base_amount=Decimal("7.50"), daily_max_amount=Decimal("99.5")))
    assert summary["comparison_hourly_rate_twd"] == 7.5
    assert summary["daily_max_twd"] == 99.5
    assert members[0]["base_amount"] == 7.5
    assert json.dumps(summary["comparison_hourly_rate_twd"]) == "7.5"


@pytest.mark.parametrize("amount", [None, Decimal("0"), Decimal("0.00"), 0])
def test_free_rate_is_zero_per_hour(amount):
    summary, _ = resolve(
        rate(rate_type="FREE", base_amount=amount, unit_minutes=None, daily_max_amount=None, description="免費")
    )
    assert summary["comparison_eligible"] is True
    assert summary["comparison_hourly_rate_twd"] == 0
    assert summary["daily_max_twd"] is None
    assert summary["display_text"] == "免費"


@pytest.mark.parametrize("amount", [Decimal("10"), Decimal("-1"), 0.0, True])
def test_free_rate_with_contradictory_or_invalid_amount_is_not_comparable(amount):
    summary, _ = resolve(rate(rate_type="FREE", base_amount=amount, description="免費?"))
    assert summary["comparison_eligible"] is False
    assert summary["comparison_hourly_rate_twd"] is None
    assert summary["display_text"] == "免費?"


@pytest.mark.parametrize("rate_type", ["PER_ENTRY", "PROGRESSIVE", "FLAT", "DAILY", "MONTHLY", "CUSTOM"])
def test_ineligible_rate_models_keep_confirmed_cap_without_hourly_value(rate_type):
    summary, members = resolve(rate(rate_type=rate_type, description=f"{rate_type} 說明"))
    assert summary["comparison_eligible"] is False
    assert summary["comparison_hourly_rate_twd"] is None
    assert summary["daily_max_twd"] == 100
    assert summary["parse_status"] == "PARSED"
    assert summary["display_text"] == f"{rate_type} 說明"
    assert members[0]["rate_type"] == rate_type


def test_cap_only_display_when_no_source_text():
    summary, _ = resolve(rate(rate_type="PROGRESSIVE", description=None, raw_text=None))
    assert summary["display_text"] == "最高100元/日"


def test_no_text_and_no_confirmed_values_yields_null_display():
    summary, _ = resolve(rate(rate_type="CUSTOM", description=None, daily_max_amount=None))
    assert summary["display_text"] is None
    assert summary["daily_max_twd"] is None


@pytest.mark.parametrize("status", ["PARTIALLY_PARSED", "RAW_ONLY", "INVALID"])
def test_unparsed_rates_preserve_raw_text_and_never_confirm(status):
    raw = "每小時20元（假日另計）最高100"
    summary, members = resolve(rate(parse_status=status, description=None, raw_text=raw))
    assert summary["comparison_eligible"] is False
    assert summary["comparison_hourly_rate_twd"] is None
    assert summary["daily_max_twd"] is None
    assert summary["parse_status"] == status
    assert summary["display_text"] == raw
    # Stored partial fields are reported per record, never promoted to the summary.
    assert members[0]["raw_text"] == raw
    assert members[0]["base_amount"] == 20
    assert members[0]["parse_status"] == status


def test_raw_only_rate_without_type_or_amounts():
    summary, members = resolve(
        rate(
            parse_status="RAW_ONLY",
            rate_type=None,
            base_amount=None,
            unit_minutes=None,
            free_minutes=None,
            daily_max_amount=None,
            description=None,
            raw_text="洽管理員",
        )
    )
    assert summary["display_text"] == "洽管理員"
    assert summary["comparison_eligible"] is False
    assert members[0]["rate_type"] is None
    assert members[0]["base_amount"] is members[0]["unit_minutes"] is members[0]["free_minutes"] is None


def test_non_twd_currency_is_never_compared_or_capped():
    summary, members = resolve(rate(currency="USD"))
    assert summary["comparison_eligible"] is False
    assert summary["daily_max_twd"] is None
    assert members[0]["currency"] == "USD"
    assert members[0]["daily_max_twd"] is None


@pytest.mark.parametrize(
    "free_minutes, eligible", [(None, True), (0, True), (30, False), (True, False), (False, False), (0.0, False)]
)
def test_free_minutes_make_hourly_equivalent_duration_dependent(free_minutes, eligible):
    summary, _ = resolve(rate(free_minutes=free_minutes))
    assert summary["comparison_eligible"] is eligible
    assert summary["daily_max_twd"] == 100


def test_unspecified_vehicle_never_claims_selected_vehicle_price():
    assert resolve(rate(vehicle=None)) == (None, [])


def test_other_vehicle_rates_are_excluded():
    assert resolve(rate(vehicle="YELLOW")) == (None, [])
    assert resolve(rate(vehicle="YELLOW"), vehicle="YELLOW")[0]["comparison_eligible"] is True


def test_null_vehicle_rate_does_not_contradict_or_join_exact_vehicle_rate():
    summary, members = resolve(rate(901, vehicle=None, base_amount=Decimal("5")), rate(902))
    assert [member["rate_id"] for member in members] == [902]
    assert summary["comparison_hourly_rate_twd"] == 20


def test_vehicle_enum_is_accepted():
    summary, _ = resolve(rate(vehicle=VehicleType.RED), vehicle=VehicleType.RED)
    assert summary["comparison_eligible"] is True


@pytest.mark.parametrize("vehicle", ["BLUE", "red", None, ""])
def test_vehicle_must_be_explicit_and_supported(vehicle):
    with pytest.raises(ValueError):
        resolve(rate(), vehicle=vehicle)


def test_evaluation_instant_must_be_offset_aware_and_offsets_are_equivalent():
    with pytest.raises(ValueError):
        resolve(rate(), at=datetime(2026, 10, 5, 10))
    assert resolve(rate(), at=MON_10.astimezone(TAIPEI)) == resolve(rate(), at=MON_10)


@pytest.mark.parametrize(
    "effective_from, effective_to, listed",
    [
        (MON_10 + timedelta(seconds=1), None, False),
        (MON_10, None, True),
        (None, MON_10, False),
        (None, MON_10 + timedelta(seconds=1), True),
        (MON_10 - timedelta(days=1), MON_10 + timedelta(days=1), True),
    ],
)
def test_effective_window_is_half_open(effective_from, effective_to, listed):
    summary, members = resolve(rate(effective_from=effective_from, effective_to=effective_to))
    assert (summary is not None) is listed
    assert len(members) == int(listed)
    if listed:
        assert summary["comparison_eligible"] is True


def test_inverted_effective_window_is_uncertain_evidence_not_confirmation():
    summary, members = resolve(rate(effective_from=MON_10 + timedelta(days=1), effective_to=MON_10 - timedelta(days=1)))
    assert len(members) == 1
    assert summary["comparison_eligible"] is False
    assert summary["daily_max_twd"] is None
    assert members[0]["applicability"] == "UNKNOWN"


def test_naive_effective_window_is_rejected():
    with pytest.raises(ValueError):
        resolve(rate(effective_from=datetime(2026, 1, 1)))


@pytest.mark.parametrize("at, expected", [(MON_10, 20), (SAT_10, 30)])
def test_weekday_and_weekend_rates(at, expected):
    weekday = rate(901, schedule={"day_type": "WEEKDAY"})
    weekend = rate(902, base_amount=Decimal("30"), schedule={"day_type": "WEEKEND"}, provenance=rate_prov(8))
    summary, members = resolve(weekday, weekend, at=at)
    assert summary["comparison_hourly_rate_twd"] == expected
    assert len(members) == 1


def test_weekday_rate_does_not_apply_on_weekend():
    assert resolve(rate(schedule={"day_type": "WEEKDAY"}), at=SAT_10) == (None, [])


def test_holiday_rate_without_calendar_is_uncertain():
    summary, members = resolve(rate(schedule={"day_type": "HOLIDAY"}))
    assert len(members) == 1
    assert summary["comparison_eligible"] is False
    assert summary["daily_max_twd"] is None


def test_holiday_rate_with_calendar_coverage():
    holiday = CalendarSnapshot({date(2026, 10, 5): True})
    regular = CalendarSnapshot({date(2026, 10, 5): False})
    uncovered = CalendarSnapshot({date(2026, 10, 6): True})
    hol_rate = rate(schedule={"day_type": "HOLIDAY"})
    assert resolve(hol_rate, calendar=holiday)[0]["comparison_eligible"] is True
    assert resolve(hol_rate, calendar=regular) == (None, [])
    assert resolve(hol_rate, calendar=uncovered)[0]["comparison_eligible"] is False


def test_unknown_schedule_rate_blocks_confirmed_rate_without_fallback():
    confirmed = rate(901)
    uncertain = rate(902, base_amount=Decimal("5"), schedule={"day_type": "HOLIDAY"}, provenance=rate_prov(8))
    summary, members = resolve(confirmed, uncertain)
    assert [member["rate_id"] for member in members] == [901, 902]
    assert summary["comparison_eligible"] is False
    assert summary["comparison_hourly_rate_twd"] is None
    assert summary["daily_max_twd"] is None


@pytest.mark.parametrize(
    "schedule", [{"timezone": "UTC"}, {"day_type": "NIGHT"}, {"start_time": "08:00"}, {"unknown_field": 1}]
)
def test_malformed_rate_schedule_is_uncertain(schedule):
    summary, members = resolve(rate(schedule=schedule))
    assert len(members) == 1
    assert summary["comparison_eligible"] is False
    assert summary["daily_max_twd"] is None


DAY = RateRuleFact(1, start_time=time(8), end_time=time(20), amount=Decimal("20"), unit_minutes=60)
NIGHT = RateRuleFact(2, start_time=time(20), end_time=time(8), amount=Decimal("10"), unit_minutes=60)


@pytest.mark.parametrize(
    "at, expected", [(MON_08, 20), (MON_10, 20), (MON_20, 10), (MON_23, 10), (TUE_01, 10), (TUE_10, 20)]
)
def test_day_and_night_rules_select_the_rule_at_the_instant(at, expected):
    summary, _ = resolve(hourly_rules(DAY, NIGHT), at=at)
    assert summary["comparison_eligible"] is True
    assert summary["comparison_hourly_rate_twd"] == expected
    assert summary["display_text"] == f"{expected}元/小時・最高100元/日"


def test_rule_window_ending_at_midnight_uses_zero_endpoint():
    summary, _ = resolve(
        hourly_rules(RateRuleFact(1, start_time=time(20), end_time=time(0), amount=Decimal("10"), unit_minutes=60)),
        at=MON_23,
    )
    assert summary["comparison_hourly_rate_twd"] == 10


@pytest.mark.parametrize("at, expected", [(MON_01, 10), (SAT_01, 30), (TUE_01, 30), (MON_10, 20)])
def test_overnight_rule_day_type_uses_starting_day(at, expected):
    night = {"start_time": time(20), "end_time": time(8), "unit_minutes": 60}
    rules = (
        RateRuleFact(1, day_type="WEEKEND", amount=Decimal("10"), **night),
        RateRuleFact(2, day_type="WEEKDAY", amount=Decimal("30"), **night),
        RateRuleFact(3, start_time=time(8), end_time=time(20), amount=Decimal("20"), unit_minutes=60),
    )
    summary, _ = resolve(hourly_rules(*rules), at=at)
    assert summary["comparison_hourly_rate_twd"] == expected


def test_overnight_column_window_anchors_json_weekdays_and_dates():
    rule = RateRuleFact(
        1,
        start_time=time(22),
        end_time=time(6),
        amount=Decimal(20),
        unit_minutes=60,
        schedule={"weekdays": [4], "dates": ["2026-10-02"]},
    )
    summary, _ = resolve(hourly_rules(rule), at=SAT_01)
    assert summary["comparison_hourly_rate_twd"] == 20
    assert summary["daily_max_twd"] == 100


def test_rule_is_intersected_with_parent_schedule():
    weekend_rule = RateRuleFact(1, day_type="WEEKEND", amount=Decimal("30"), unit_minutes=60)
    parent = rate(base_amount=None, unit_minutes=None, schedule={"day_type": "WEEKDAY"}, rules=(weekend_rule,))
    assert resolve(parent, at=SAT_10) == (None, [])
    summary, members = resolve(parent, at=MON_10)
    # Parent applies but no rule covers this instant; neither price nor cap is confirmed.
    assert len(members) == 1
    assert summary["comparison_eligible"] is False
    assert summary["daily_max_twd"] is None


def test_no_matching_rule_does_not_fall_back_to_parent_base_amount():
    summary, _ = resolve(rate(rules=(NIGHT,)), at=MON_10)
    assert summary["comparison_eligible"] is False
    assert summary["comparison_hourly_rate_twd"] is None


def test_rule_own_schedule_is_intersected_with_its_day_type():
    monday_only = RateRuleFact(1, schedule={"weekdays": [0]}, amount=Decimal("20"), unit_minutes=60)
    assert resolve(hourly_rules(monday_only), at=MON_10)[0]["comparison_hourly_rate_twd"] == 20
    assert resolve(hourly_rules(monday_only), at=TUE_10)[0]["comparison_eligible"] is False
    contradictory = RateRuleFact(
        2, day_type="WEEKEND", schedule={"weekdays": [0]}, amount=Decimal("20"), unit_minutes=60
    )
    assert resolve(hourly_rules(contradictory), at=MON_10)[0]["comparison_eligible"] is False


def test_holiday_rule_requires_calendar_coverage():
    holiday_rule = RateRuleFact(1, day_type="HOLIDAY", amount=Decimal("40"), unit_minutes=60)
    summary, _ = resolve(hourly_rules(holiday_rule, DAY))
    assert summary["comparison_eligible"] is False
    assert summary["daily_max_twd"] is None
    holiday = CalendarSnapshot({date(2026, 10, 5): True})
    regular = CalendarSnapshot({date(2026, 10, 5): False})
    # Holiday: both HOLIDAY (40) and DAY (20) apply and contradict.
    assert resolve(hourly_rules(holiday_rule, DAY), calendar=holiday)[0]["comparison_eligible"] is False
    assert resolve(hourly_rules(holiday_rule), calendar=holiday)[0]["comparison_hourly_rate_twd"] == 40
    assert resolve(hourly_rules(holiday_rule, DAY), calendar=regular)[0]["comparison_hourly_rate_twd"] == 20


def test_special_rule_requires_explicit_dates():
    dated = RateRuleFact(
        1, day_type="SPECIAL", schedule={"dates": ["2026-10-05"]}, amount=Decimal("50"), unit_minutes=60
    )
    undated = RateRuleFact(1, day_type="SPECIAL", amount=Decimal("50"), unit_minutes=60)
    assert resolve(hourly_rules(dated))[0]["comparison_hourly_rate_twd"] == 50
    assert resolve(hourly_rules(dated), at=TUE_10)[0]["comparison_eligible"] is False
    assert resolve(hourly_rules(undated))[0]["comparison_eligible"] is False


def test_conflicting_applicable_rules_never_choose_cheapest():
    cheap = RateRuleFact(1, amount=Decimal("10"), unit_minutes=60)
    expensive = RateRuleFact(2, amount=Decimal("30"), unit_minutes=60)
    summary, _ = resolve(hourly_rules(cheap, expensive))
    assert summary["comparison_eligible"] is False
    assert summary["comparison_hourly_rate_twd"] is None
    assert summary["display_text"] == "20元/小時，最高100元/日"
    assert summary["daily_max_twd"] == 100


def test_identical_applicable_rules_are_a_tie_not_a_conflict():
    first = RateRuleFact(2, amount=Decimal("10"), unit_minutes=30)
    second = RateRuleFact(1, amount=Decimal("20.00"), unit_minutes=60)
    summary, _ = resolve(hourly_rules(first, second))
    assert summary["comparison_hourly_rate_twd"] == 20
    # Display uses the lowest rule_id term for stable output.
    assert summary["display_text"] == "20元/小時・最高100元/日"


@pytest.mark.parametrize(
    "start_minute, end_minute, eligible",
    [(None, None, True), (0, None, True), (0, 60, False), (60, None, False), (60, 120, False), (None, 60, False)],
)
def test_duration_dependent_rules_are_not_comparable(start_minute, end_minute, eligible):
    rule = RateRuleFact(1, start_minute=start_minute, end_minute=end_minute, amount=Decimal("20"), unit_minutes=60)
    assert resolve(hourly_rules(rule))[0]["comparison_eligible"] is eligible


def test_rule_price_terms_must_be_complete_or_inherited_together():
    amount_only = RateRuleFact(1, amount=Decimal("20"))
    unit_only = RateRuleFact(1, unit_minutes=60)
    schedule_only = RateRuleFact(1, start_time=time(8), end_time=time(20))
    assert resolve(rate(rules=(amount_only,)))[0]["comparison_eligible"] is False
    assert resolve(rate(rules=(unit_only,)))[0]["comparison_eligible"] is False
    # A rule restating neither term only restricts when the parent price applies.
    assert resolve(rate(rules=(schedule_only,)))[0]["comparison_hourly_rate_twd"] == 20
    assert resolve(rate(rules=(schedule_only,)), at=MON_23)[0]["comparison_eligible"] is False


@pytest.mark.parametrize(
    "fields",
    [
        {"start_time": time(8, tzinfo=UTC), "end_time": time(20)},
        {"start_time": time(8, 0, 0, 1), "end_time": time(20)},
        {"start_time": time(8)},
        {"end_time": time(20)},
        {"start_time": time(8), "end_time": time(8)},
        {"start_time": "08:00", "end_time": "20:00"},
        {"day_type": "NIGHT"},
        {"day_type": None},
        {"schedule": {"timezone": "UTC"}},
    ],
)
def test_malformed_rule_windows_are_unknown_and_not_comparable(fields):
    rule = RateRuleFact(1, amount=Decimal("20"), unit_minutes=60, **fields)
    summary, members = resolve(hourly_rules(rule))
    assert len(members) == 1
    assert summary["comparison_eligible"] is False
    # Unresolved applicability blocks a scheduled cap as well as price comparison.
    assert summary["daily_max_twd"] is None
    assert members[0]["applicability"] == "UNKNOWN"


def test_rule_max_amount_is_never_reported_as_daily_cap():
    rule = RateRuleFact(1, amount=Decimal("20"), unit_minutes=60, max_amount=Decimal("50"))
    summary, members = resolve(rate(daily_max_amount=None, rules=(rule,)))
    assert summary["comparison_hourly_rate_twd"] == 20
    assert summary["daily_max_twd"] is None
    assert members[0]["daily_max_twd"] is None


def test_free_rate_rules():
    free_rule = RateRuleFact(1, start_time=time(8), end_time=time(20), amount=Decimal("0"))
    paid_rule = RateRuleFact(1, amount=Decimal("10"), unit_minutes=60)
    free = rate(rate_type="FREE", base_amount=None, unit_minutes=None, rules=(free_rule,))
    assert resolve(free)[0]["comparison_hourly_rate_twd"] == 0
    assert resolve(free, at=MON_23)[0]["comparison_eligible"] is False
    assert resolve(rate(rate_type="FREE", rules=(paid_rule,)))[0]["comparison_eligible"] is False


def test_contradictory_rates_are_not_resolved_to_the_cheapest():
    summary, members = resolve(
        rate(902, base_amount=Decimal("30"), provenance=rate_prov(8), description="30元/小時"),
        rate(901, description="20元/小時"),
    )
    assert [member["rate_id"] for member in members] == [901, 902]
    assert summary["comparison_eligible"] is False
    assert summary["comparison_hourly_rate_twd"] is None
    assert summary["display_text"] == "20元/小時；30元/小時"
    # Both certain rates state the same cap, so it is still deterministic.
    assert summary["daily_max_twd"] == 100
    assert summary["provenance"] == rate_prov_dict(7)
    assert summary["supporting_sources"] == [rate_prov_dict(8)]


@pytest.mark.parametrize(
    "caps, expected",
    [
        ((Decimal("100"), Decimal("100.00")), 100),
        ((Decimal("100"), Decimal("150")), None),
        ((Decimal("100"), None), None),
        ((None, None), None),
    ],
)
def test_daily_cap_requires_agreement_of_every_applicable_rate(caps, expected):
    summary, _ = resolve(
        rate(901, daily_max_amount=caps[0]), rate(902, daily_max_amount=caps[1], provenance=rate_prov(8))
    )
    assert summary["comparison_hourly_rate_twd"] == 20
    assert summary["daily_max_twd"] == expected


def test_daily_cap_requires_parsed_rate():
    partial = rate(902, parse_status="PARTIALLY_PARSED", raw_text="最高100", provenance=rate_prov(8))
    summary, _ = resolve(rate(901), partial)
    assert summary["daily_max_twd"] is None
    assert summary["comparison_eligible"] is False
    assert summary["parse_status"] == "PARTIALLY_PARSED"


def test_identical_tied_rates_keep_all_evidence():
    extra = Provenance(source_id=9, source_type="OPERATOR", source_record_id="op-1")
    summary, members = resolve(
        rate(901),
        rate(902, rate_type="TIME_BLOCK", base_amount=Decimal("10"), unit_minutes=30, provenance=rate_prov(8)),
        rate(903, base_amount=Decimal("20.00"), provenance=rate_prov(7), supporting_sources=(extra, rate_prov(8))),
    )
    assert summary["comparison_eligible"] is True
    assert summary["comparison_hourly_rate_twd"] == 20
    assert summary["daily_max_twd"] == 100
    assert summary["display_text"] == "20元/小時・最高100元/日"
    assert summary["provenance"] == rate_prov_dict(7)
    # Deduplicated by the full provenance tuple; the primary is never repeated.
    assert summary["supporting_sources"] == [
        rate_prov_dict(8),
        {
            "source_id": 9,
            "source_type": "OPERATOR",
            "source_record_id": "op-1",
            "source_updated_at": None,
            "fetched_at": None,
            "verified_at": None,
        },
    ]
    assert [member["rate_id"] for member in members] == [901, 902, 903]
    assert len(members[2]["supporting_sources"]) == 2


def test_same_source_id_with_different_record_is_separate_evidence():
    summary, _ = resolve(rate(901), rate(902, provenance=rate_prov(7, record="rate-7b")))
    assert summary["supporting_sources"] == [rate_prov_dict(7, record="rate-7b")]


def test_worst_parse_status_is_reported():
    summary, _ = resolve(
        rate(901),
        rate(902, parse_status="RAW_ONLY", raw_text="x", provenance=rate_prov(8)),
        rate(903, parse_status="PARTIALLY_PARSED", raw_text="y"),
    )
    assert summary["parse_status"] == "RAW_ONLY"


def test_duplicate_display_texts_are_not_repeated():
    summary, _ = resolve(
        rate(901, rate_type="CUSTOM", description="洽詢"), rate(902, rate_type="CUSTOM", description="洽詢")
    )
    assert summary["display_text"] == "洽詢"


@pytest.mark.parametrize(
    "rates",
    [
        (rate(901), rate(901)),
        (rate(901), rate(902, zone_id=21)),
        ("not a rate",),
        (rate(rate_id=True),),
        (rate(901, provenance={"source_id": 7}),),
        (rate(901, supporting_sources=("x",)),),
        (rate(901, rules=("x",)),),
    ],
)
def test_invalid_rate_inputs_are_rejected(rates):
    with pytest.raises(ValueError):
        resolve_rates(rates, "RED", MON_10)


def test_calendar_must_provide_is_holiday():
    with pytest.raises(ValueError):
        resolve(rate(), calendar=object())


# ------------------------------------------------------------------------ realtime


def rt_prov(source_id=9, record="rt-9", fetched=RT_FETCHED, updated=RT_UPDATED):
    return Provenance(
        source_id=source_id,
        source_type="OPERATOR",
        source_record_id=record,
        source_updated_at=updated,
        fetched_at=fetched,
    )


def fact(status="AVAILABLE", available=8, total=20, provenance=None, freshness_seconds=120, zone_id=20):
    return RealtimeFact(
        observation_id=1,
        zone_id=zone_id,
        status=status,
        available=available,
        total=total,
        provenance=provenance or rt_prov(),
        freshness_seconds=freshness_seconds,
    )


@pytest.mark.parametrize(
    "status, available, total, valid",
    [
        ("AVAILABLE", 8, 20, True),
        ("AVAILABLE", 8, None, True),
        ("AVAILABLE", 20, 20, True),
        ("AVAILABLE", 1, 1, True),
        ("AVAILABLE", 0, 20, False),
        ("AVAILABLE", 0, 0, False),
        ("AVAILABLE", None, 20, False),
        ("AVAILABLE", None, None, False),
        ("AVAILABLE", 21, 20, False),
        ("AVAILABLE", -1, 20, False),
        ("AVAILABLE", 8, -1, False),
        ("AVAILABLE", True, 20, False),
        ("AVAILABLE", 8, True, False),
        ("AVAILABLE", 8.0, 20, False),
        ("AVAILABLE", 8, 20.0, False),
        ("AVAILABLE", "8", 20, False),
        ("FULL", 0, 20, True),
        ("FULL", 0, None, True),
        ("FULL", 0, 0, True),
        ("FULL", 1, 20, False),
        ("FULL", None, 20, False),
        ("FULL", False, 20, False),
        ("FULL", 0.0, 20, False),
        ("CLOSED", 0, 0, True),
        ("CLOSED", 0, None, True),
        ("CLOSED", 2, 10, False),
        ("CLOSED", None, None, False),
        ("UNKNOWN", None, None, True),
        ("UNKNOWN", 3, 10, True),
        ("UNKNOWN", 0, None, True),
        ("UNKNOWN", 11, 10, False),
        ("UNKNOWN", -1, None, False),
        ("UNKNOWN", True, None, False),
        ("STALE", 1, 2, False),
        ("available", 1, 2, False),
        (None, 1, 2, False),
        (1, 1, 2, False),
    ],
)
def test_validate_counts(status, available, total, valid):
    assert validate_counts(status, available, total) is valid


def test_validate_counts_accepts_status_enum():
    assert validate_counts(RealtimeStatus.FULL, 0, 4) is True


def test_resolve_availability_none_without_observation():
    assert resolve_availability(None, NOW) is None


def test_fresh_observation_exact_wire_output():
    assert resolve_availability(fact(), NOW) == {
        "status": "AVAILABLE",
        "available": 8,
        "total": 20,
        "freshness": {"status": "FRESH"},
        "provenance": {
            "source_id": 9,
            "source_type": "OPERATOR",
            "source_record_id": "rt-9",
            "source_updated_at": "2026-10-05T02:08:00Z",
            "fetched_at": "2026-10-05T02:09:00Z",
            "verified_at": None,
        },
    }


@pytest.mark.parametrize(
    "age, freshness",
    [
        (timedelta(0), "FRESH"),
        (timedelta(seconds=120), "FRESH"),
        (timedelta(seconds=120, microseconds=1), "STALE"),
        (timedelta(hours=5), "STALE"),
        (timedelta(microseconds=-1), "UNKNOWN"),
        (timedelta(seconds=-60), "UNKNOWN"),
    ],
)
def test_freshness_threshold_and_future_fetch(age, freshness):
    result = resolve_availability(fact(provenance=rt_prov(fetched=NOW - age)), NOW)
    assert result["freshness"] == {"status": freshness}
    assert result["status"] == "AVAILABLE"


def test_freshness_uses_fact_threshold():
    stale = rt_prov(fetched=NOW - timedelta(seconds=200))
    assert resolve_availability(fact(provenance=stale), NOW)["freshness"]["status"] == "STALE"
    assert resolve_availability(fact(provenance=stale, freshness_seconds=300), NOW)["freshness"]["status"] == "FRESH"
    exact = rt_prov(fetched=NOW)
    assert resolve_availability(fact(provenance=exact, freshness_seconds=0), NOW)["freshness"]["status"] == "FRESH"


@pytest.mark.parametrize("threshold", [-1, True, 1.5, None])
def test_invalid_freshness_threshold_is_unknown(threshold):
    assert resolve_availability(fact(freshness_seconds=threshold), NOW)["freshness"]["status"] == "UNKNOWN"


def test_missing_fetched_at_is_unknown_freshness_and_keeps_status():
    result = resolve_availability(fact("FULL", 0, 20, provenance=rt_prov(fetched=None)), NOW)
    assert result["status"] == "FULL"
    assert result["available"] == 0
    assert result["freshness"] == {"status": "UNKNOWN"}
    assert result["provenance"]["fetched_at"] is None


@pytest.mark.parametrize("status, available, total", [("AVAILABLE", 8, 20), ("FULL", 0, 20), ("CLOSED", 0, None)])
def test_stale_never_changes_observed_status_or_counts(status, available, total):
    old = rt_prov(fetched=NOW - timedelta(days=1))
    result = resolve_availability(fact(status, available, total, provenance=old), NOW)
    assert (result["status"], result["available"], result["total"]) == (status, available, total)
    assert result["freshness"] == {"status": "STALE"}


@pytest.mark.parametrize(
    "status, available, total",
    [
        ("AVAILABLE", 0, 20),
        ("AVAILABLE", 21, 20),
        ("AVAILABLE", True, 20),
        ("AVAILABLE", 8.0, 20),
        ("AVAILABLE", 8, -1),
        ("FULL", 3, 20),
        ("CLOSED", None, 20),
        ("STALE", 1, 2),
        ("busy", 1, 2),
    ],
)
def test_invalid_observation_degrades_to_unknown_with_null_counts(status, available, total):
    result = resolve_availability(fact(status, available, total), NOW)
    assert (result["status"], result["available"], result["total"]) == ("UNKNOWN", None, None)
    assert result["freshness"] == {"status": "FRESH"}
    assert is_available_only("ALLOWED", result) is False
    assert aggregate_availability([zone(availability=result)])["coverage"] == "NONE"


def test_unknown_status_retains_valid_counts_but_never_qualifies_or_aggregates():
    result = resolve_availability(fact("UNKNOWN", 3, 10), NOW)
    assert (result["status"], result["available"], result["total"]) == ("UNKNOWN", 3, 10)
    assert is_available_only("ALLOWED", result) is False
    summary = aggregate_availability([zone(availability=result)])
    assert summary["coverage"] == "NONE"
    assert summary["available"] is summary["total"] is None


def test_resolve_availability_requires_aware_now_and_fact():
    with pytest.raises(ValueError):
        resolve_availability(fact(), datetime(2026, 10, 5, 10))
    with pytest.raises(ValueError):
        resolve_availability({"status": "AVAILABLE"}, NOW)
    assert resolve_availability(fact(), NOW.astimezone(TAIPEI)) == resolve_availability(fact(), NOW)


def available_only_base(**changes):
    availability = resolve_availability(fact(total=None), NOW)
    availability.update(changes)
    return availability


def test_available_only_qualifies_with_null_or_valid_total():
    assert is_available_only("ALLOWED", available_only_base()) is True
    assert is_available_only("ALLOWED", resolve_availability(fact(), NOW)) is True
    assert is_available_only(CompatibilityStatus.ALLOWED, available_only_base()) is True


def _with_provenance(**changes):
    availability = available_only_base()
    availability["provenance"] = {**availability["provenance"], **changes}
    return availability


@pytest.mark.parametrize(
    "compatibility, availability",
    [
        ("UNKNOWN", available_only_base()),
        ("NOT_ALLOWED", available_only_base()),
        ("ALLOWED", None),
        ("ALLOWED", resolve_availability(fact("FULL", 0, 20), NOW)),
        ("ALLOWED", resolve_availability(fact("CLOSED", 0, 20), NOW)),
        ("ALLOWED", resolve_availability(fact("UNKNOWN", 3, 20), NOW)),
        ("ALLOWED", available_only_base(available=0)),
        ("ALLOWED", available_only_base(available=None)),
        ("ALLOWED", available_only_base(available=True)),
        ("ALLOWED", available_only_base(available=8.0)),
        ("ALLOWED", available_only_base(total=7)),
        ("ALLOWED", available_only_base(total=-1)),
        ("ALLOWED", available_only_base(total=20.0)),
        ("ALLOWED", available_only_base(freshness={"status": "STALE"})),
        ("ALLOWED", available_only_base(freshness={"status": "UNKNOWN"})),
        ("ALLOWED", available_only_base(freshness=None)),
        ("ALLOWED", available_only_base(provenance=None)),
        # An invalid fixture: FRESH without fetched_at can never qualify.
        ("ALLOWED", _with_provenance(fetched_at=None)),
        ("ALLOWED", _with_provenance(fetched_at="yesterday")),
        ("ALLOWED", _with_provenance(fetched_at="2026-10-05T02:09:00")),
        ("ALLOWED", resolve_availability(fact(provenance=rt_prov(fetched=NOW - timedelta(days=1))), NOW)),
        ("ALLOWED", resolve_availability(fact(provenance=rt_prov(fetched=None)), NOW)),
        ("ALLOWED", "AVAILABLE"),
    ],
)
def test_available_only_rejections(compatibility, availability):
    assert is_available_only(compatibility, availability) is False


def zone(status="ALLOWED", availability=None, zone_id=20, rate_summary=None, confidence=None):
    return {
        "zone_id": zone_id,
        "compatibility": {"status": status, "confidence": confidence},
        "rate_summary": rate_summary,
        "availability": availability,
    }


def avail(status="AVAILABLE", available=8, total=20, provenance=None, freshness_seconds=120):
    return resolve_availability(fact(status, available, total, provenance, freshness_seconds), NOW)


def test_aggregate_with_no_zones_is_none_coverage():
    assert aggregate_availability([]) == {
        "status": "UNKNOWN",
        "available": None,
        "total": None,
        "coverage": "NONE",
        "eligible_zone_count": 0,
        "fresh_realtime_zone_count": 0,
        "freshness": {"status": "UNKNOWN"},
        "oldest_source_updated_at": None,
        "oldest_fetched_at": None,
        "contributing_sources": [],
    }


def test_aggregate_ignores_non_allowed_zones_entirely():
    summary = aggregate_availability([zone("UNKNOWN", avail()), zone("NOT_ALLOWED", avail())])
    assert summary["coverage"] == "NONE"
    assert summary["eligible_zone_count"] == 0
    complete = aggregate_availability(
        [zone("ALLOWED", avail(available=2, total=5)), zone("UNKNOWN", avail()), zone("NOT_ALLOWED", avail())]
    )
    assert (complete["coverage"], complete["available"], complete["total"]) == ("COMPLETE", 2, 5)
    assert complete["eligible_zone_count"] == 1


def test_aggregate_complete_exact_output():
    first = rt_prov(9, "rt-a", fetched=NOW - timedelta(seconds=30), updated=NOW - timedelta(minutes=5))
    second = rt_prov(10, "rt-b", fetched=NOW - timedelta(seconds=90), updated=NOW - timedelta(minutes=2))
    summary = aggregate_availability(
        [
            zone(availability=avail(provenance=first), zone_id=20),
            zone(availability=avail("FULL", 0, 10, provenance=second), zone_id=21),
        ]
    )
    assert summary == {
        "status": "AVAILABLE",
        "available": 8,
        "total": 30,
        "coverage": "COMPLETE",
        "eligible_zone_count": 2,
        "fresh_realtime_zone_count": 2,
        "freshness": {"status": "FRESH"},
        "oldest_source_updated_at": "2026-10-05T02:05:00Z",
        "oldest_fetched_at": "2026-10-05T02:08:30Z",
        "contributing_sources": [
            resolve_availability(fact(provenance=first), NOW)["provenance"],
            resolve_availability(fact(provenance=second), NOW)["provenance"],
        ],
    }


@pytest.mark.parametrize(
    "statuses, expected",
    [
        (("CLOSED",), "CLOSED"),
        (("CLOSED", "CLOSED"), "CLOSED"),
        (("FULL",), "FULL"),
        (("FULL", "FULL"), "FULL"),
        (("FULL", "CLOSED"), "FULL"),
        (("AVAILABLE", "CLOSED"), "AVAILABLE"),
        (("AVAILABLE", "FULL", "CLOSED"), "AVAILABLE"),
    ],
)
def test_complete_status_mix(statuses, expected):
    zones = [
        zone(availability=avail(status, 3 if status == "AVAILABLE" else 0, 10), zone_id=index)
        for index, status in enumerate(statuses)
    ]
    summary = aggregate_availability(zones)
    assert summary["coverage"] == "COMPLETE"
    assert summary["status"] == expected
    assert summary["total"] == 10 * len(statuses)


@pytest.mark.parametrize(
    "incomplete",
    [
        None,
        avail(total=None),
        avail("FULL", 0, None),
        avail("UNKNOWN", 3, 10),
        avail("AVAILABLE", 0, 10),
        avail(provenance=rt_prov(fetched=NOW - timedelta(hours=1))),
        avail(provenance=rt_prov(fetched=None)),
        {**avail(), "freshness": {"status": "FRESH"}, "provenance": {**avail()["provenance"], "fetched_at": None}},
        {**avail(), "total": 20.0},
        {**avail(), "provenance": {**avail()["provenance"], "source_id": "9"}},
    ],
)
def test_one_non_contributor_makes_partial_with_unknown_null_summary(incomplete):
    summary = aggregate_availability([zone(availability=avail(), zone_id=1), zone(availability=incomplete, zone_id=2)])
    assert summary == {
        "status": "UNKNOWN",
        "available": None,
        "total": None,
        "coverage": "PARTIAL",
        "eligible_zone_count": 2,
        "fresh_realtime_zone_count": 1,
        "freshness": {"status": "UNKNOWN"},
        "oldest_source_updated_at": None,
        "oldest_fetched_at": None,
        "contributing_sources": [],
    }


def test_no_fresh_contributor_is_none_coverage():
    summary = aggregate_availability([zone(availability=None), zone(availability=avail(total=None))])
    assert summary["coverage"] == "NONE"
    assert (summary["eligible_zone_count"], summary["fresh_realtime_zone_count"]) == (2, 0)
    assert summary["status"] == "UNKNOWN"


def test_missing_source_updated_at_nulls_oldest_but_keeps_fetch_minimum():
    first = rt_prov(9, "a", fetched=NOW - timedelta(seconds=10), updated=None)
    second = rt_prov(10, "b", fetched=NOW - timedelta(seconds=100), updated=NOW - timedelta(seconds=200))
    summary = aggregate_availability([zone(availability=avail(provenance=item)) for item in (first, second)])
    assert summary["coverage"] == "COMPLETE"
    assert summary["oldest_source_updated_at"] is None
    assert summary["oldest_fetched_at"] == "2026-10-05T02:08:20Z"


def test_source_minima_are_independent_per_timestamp():
    first = rt_prov(9, "a", fetched=NOW - timedelta(seconds=100), updated=NOW - timedelta(seconds=110))
    second = rt_prov(10, "b", fetched=NOW - timedelta(seconds=10), updated=NOW - timedelta(seconds=500))
    summary = aggregate_availability([zone(availability=avail(provenance=item)) for item in (first, second)])
    assert summary["oldest_fetched_at"] == "2026-10-05T02:08:20Z"
    assert summary["oldest_source_updated_at"] == "2026-10-05T02:01:40Z"


def test_contributing_sources_deduplicate_by_full_tuple_and_cover_every_contributor():
    shared = rt_prov(9, "rt-9")
    other_record = rt_prov(9, "rt-9b")
    zones = [
        zone(availability=avail(provenance=shared), zone_id=1),
        zone(availability=avail(provenance=shared), zone_id=2),
        zone(availability=avail(provenance=other_record), zone_id=3),
    ]
    summary = aggregate_availability(zones)
    assert summary["coverage"] == "COMPLETE"
    assert summary["fresh_realtime_zone_count"] == 3
    assert [source["source_record_id"] for source in summary["contributing_sources"]] == ["rt-9", "rt-9b"]
    for item in zones:
        assert item["availability"]["provenance"] in summary["contributing_sources"]


def test_aggregate_accepts_compatibility_enum_status():
    summary = aggregate_availability([zone(CompatibilityStatus.ALLOWED, avail())])
    assert summary["coverage"] == "COMPLETE"


def test_aggregate_does_not_mutate_inputs():
    zones = [zone(availability=avail()), zone(availability=avail(total=None))]
    snapshot = deepcopy(zones)
    aggregate_availability(zones)
    assert zones == snapshot


# ------------------------------------------------------------------------- ranking


@pytest.mark.parametrize(
    "n, d, expected",
    [
        (0, 1, 0),
        (1, 1, 1),
        (1, 2, 1),
        (3, 2, 2),
        (5, 2, 3),
        (1, 3, 0),
        (2, 3, 1),
        (9, 4, 2),
        (10, 4, 3),
        (149, 100, 1),
        (150, 100, 2),
        (250, 100, 3),
        (10**30 + 1, 2, 5 * 10**29 + 1),
    ],
)
def test_round_half_up(n, d, expected):
    assert round_half_up(n, d) == expected


@pytest.mark.parametrize("n, d", [(-1, 2), (1, 0), (1, -1), (True, 1), (1, True), (1.0, 1), (1, 2.0), ("1", 1)])
def test_round_half_up_rejects_invalid_operands(n, d):
    with pytest.raises(ValueError):
        round_half_up(n, d)


@pytest.mark.parametrize(
    "distance, radius, expected",
    [
        (0, 1500, 10000),
        (1500, 1500, 0),
        (1501, 1500, 0),
        (750, 1500, 5000),
        (420, 1500, 7200),
        (1, 1500, 9993),
        (1, 3, 6667),
        (2, 3, 3333),
        (19999, 20000, 1),  # exactly 0.5 rounds up
        (999, 1000, 10),
        (0, 1, 10000),
    ],
)
def test_distance_component(distance, radius, expected):
    assert distance_component(distance, radius) == expected


@pytest.mark.parametrize("distance, radius", [(-1, 1500), (1.0, 1500), (True, 1500), (0, 0), (0, -5), (0, 1500.0)])
def test_distance_component_rejects_invalid_inputs(distance, radius):
    with pytest.raises(ValueError):
        distance_component(distance, radius)


def complete(available, total):
    return {"coverage": "COMPLETE", "available": available, "total": total}


@pytest.mark.parametrize(
    "summary, expected",
    [
        (complete(8, 20), 4000),
        (complete(20, 20), 10000),
        (complete(0, 20), 0),
        (complete(1, 3), 3333),
        (complete(2, 3), 6667),
        (complete(1, 20000), 1),  # exactly 0.5 rounds up
        (complete(0, 0), 0),
        (complete(21, 20), 0),
        (complete(-1, 20), 0),
        (complete(8.0, 20), 0),
        (complete(True, 20), 0),
        ({"coverage": "PARTIAL", "available": 8, "total": 20}, 0),
        ({"coverage": "NONE", "available": None, "total": None}, 0),
        (None, 0),
    ],
)
def test_availability_component(summary, expected):
    assert availability_component(summary) == expected


def priced(value, status="ALLOWED", eligible=True):
    return zone(status, rate_summary={"comparison_eligible": eligible, "comparison_hourly_rate_twd": value})


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 10000),
        (0.0, 10000),
        (Decimal("0.00"), 10000),
        (0.01, 8000),
        (20, 8000),
        (Decimal("20.00"), 8000),
        (20.01, 6000),
        (30, 6000),
        (30.5, 4000),
        (50, 4000),
        (50.01, 2000),
        (1000, 2000),
    ],
)
def test_price_bands(value, expected):
    assert price_component([priced(value)]) == expected


@pytest.mark.parametrize(
    "zones, expected",
    [
        ([], 0),
        ([zone()], 0),
        ([priced(25), priced(45)], 6000),
        ([priced(60), priced(0, status="UNKNOWN")], 2000),
        ([priced(60), priced(0, status="NOT_ALLOWED")], 2000),
        ([priced(60), priced(0, eligible=False)], 2000),
        ([priced(60), priced(0, eligible=1)], 2000),
        ([priced(None)], 0),
        ([priced(True)], 0),
        ([priced("10")], 0),
        ([priced(-5)], 0),
        ([priced(float("nan"))], 0),
        ([priced(float("inf"))], 0),
        ([priced(0, status="UNKNOWN")], 0),
    ],
)
def test_price_component_uses_only_eligible_allowed_values(zones, expected):
    assert price_component(zones) == expected


@pytest.mark.parametrize(
    "confidences, expected",
    [
        ([1.0], 10000),
        ([1], 10000),
        ([0.8], 8000),
        ([None, 0.6], 6000),
        ([0.2, 0.7], 7000),
        ([None], 0),
        ([], 0),
        ([0.33335], 3334),  # half-up on the written decimal, not binary float
        ([Decimal("0.33335")], 3334),
        ([0.00005], 1),
        ([0.00004], 0),
        ([0.99995], 10000),
        ([1.5], 10000),
        ([-0.2], 0),
        ([True], 0),
        ([float("nan")], 0),
        ([float("inf")], 0),
        (["0.9"], 0),
    ],
)
def test_confidence_component(confidences, expected):
    assert confidence_component([zone(confidence=value) for value in confidences]) == expected


def test_confidence_ignores_unknown_zones():
    zones = [zone("ALLOWED", confidence=0.5), zone("UNKNOWN", confidence=1.0), zone("NOT_ALLOWED", confidence=1.0)]
    assert confidence_component(zones) == 5000
    assert confidence_component([zone("UNKNOWN", confidence=1.0)]) == 0


def entrance(access, lat=25.0333, lng=121.5625, entrance_id=1):
    return EntranceFact(entrance_id, "入口", lat, lng, access, Provenance(source_id=11))


@pytest.mark.parametrize(
    "entrances, expected",
    [
        ((entrance("ALLOWED"),), 10000),
        ((entrance("UNKNOWN"), entrance("ALLOWED")), 10000),
        ((entrance("ALLOWED", lat=None), entrance("UNKNOWN")), 5000),
        ((entrance("UNKNOWN"),), 5000),
        ((entrance("NOT_ALLOWED"), entrance("UNKNOWN")), 5000),
        ((entrance("NOT_ALLOWED"),), 0),
        ((), 0),
        ((entrance("UNKNOWN", lng=None),), 0),
        ((entrance("ALLOWED", lat=None, lng=None),), 0),
        ((entrance("ALLOWED", lat=91.0),), 0),
        ((entrance("ALLOWED", lng=-180.5),), 0),
        ((entrance("ALLOWED", lat=float("nan")),), 0),
        ((entrance("ALLOWED", lat=True),), 0),
        ((entrance("allowed"),), 0),
        ((entrance(None),), 0),
        ((entrance("ALLOWED", lat=0, lng=0),), 10000),
    ],
)
def test_entrance_component(entrances, expected):
    assert entrance_component(entrances) == expected


def test_entrance_component_rejects_non_facts():
    with pytest.raises(ValueError):
        entrance_component(({"access": "ALLOWED"},))


def test_ranking_components_and_weighted_score():
    zones = [
        zone(
            confidence=1.0,
            availability=avail(),
            rate_summary={"comparison_eligible": True, "comparison_hourly_rate_twd": 20},
        )
    ]
    summary = aggregate_availability(zones)
    entrances = (entrance("ALLOWED"),)
    assert ranking_components(420, 1500, zones, summary, entrances) == {
        "distance": 7200,
        "availability": 4000,
        "price": 8000,
        "confidence": 10000,
        "entrance": 10000,
    }
    # 7200*35 + 4000*25 + 8000*20 + 10000*15 + 10000*5 = 712000
    assert ranking_score(420, 1500, zones, summary, entrances) == 7120


def test_maximum_and_minimum_scores():
    best = [zone(confidence=1, availability=avail(available=20, total=20), rate_summary=priced(0)["rate_summary"])]
    assert ranking_score(0, 1500, best, aggregate_availability(best), (entrance("ALLOWED"),)) == 10000
    assert ranking_score(1500, 1500, [], None, ()) == 0


@pytest.mark.parametrize(
    "distance, radius, expected",
    [
        (999, 1000, 4),  # component 10 -> weighted 350 -> 3.5 rounds up
        (9999, 10000, 0),  # component 1 -> weighted 35 -> 0.35 rounds down
        (2, 3, 1167),  # component 3333 -> weighted 116655 -> 1166.55
        (1, 3, 2333),  # component 6667 -> weighted 233345 -> 2333.45
    ],
)
def test_final_score_rounding(distance, radius, expected):
    assert ranking_score(distance, radius, [], None, ()) == expected


def test_entrance_unknown_contributes_half_weight():
    # 5000 * 5 = 25000 -> 250
    assert ranking_score(1500, 1500, [], None, (entrance("UNKNOWN"),)) == 250


def test_unknown_zone_facts_never_alter_confirmed_score():
    allowed = zone(
        "ALLOWED",
        avail(available=2, total=10),
        zone_id=1,
        confidence=0.5,
        rate_summary={"comparison_eligible": True, "comparison_hourly_rate_twd": 60},
    )
    unknown = zone(
        "UNKNOWN",
        avail(available=10, total=10),
        zone_id=2,
        confidence=1.0,
        rate_summary={"comparison_eligible": True, "comparison_hourly_rate_twd": 0},
    )
    alone = [allowed]
    mixed = [allowed, unknown]
    assert aggregate_availability(mixed) == aggregate_availability(alone)
    args = (300, 1500)
    assert ranking_components(*args, mixed, aggregate_availability(mixed), ()) == ranking_components(
        *args, alone, aggregate_availability(alone), ()
    )
    assert ranking_score(*args, mixed, aggregate_availability(mixed), ()) == ranking_score(
        *args, alone, aggregate_availability(alone), ()
    )


def test_unknown_only_lot_gets_no_confirmed_boosts():
    unknown = zone(
        "UNKNOWN",
        avail(),
        confidence=1.0,
        rate_summary={"comparison_eligible": True, "comparison_hourly_rate_twd": 0},
    )
    components = ranking_components(0, 1500, [unknown], aggregate_availability([unknown]), ())
    assert components == {"distance": 10000, "availability": 0, "price": 0, "confidence": 0, "entrance": 0}


def test_rates_and_realtime_pipeline_into_ranking():
    summary, _ = resolve(rate(rate_type="TIME_BLOCK", base_amount=Decimal("15"), unit_minutes=30))
    zones = [zone(availability=avail("FULL", 0, 10), rate_summary=summary, confidence=0.9)]
    components = ranking_components(0, 500, zones, aggregate_availability(zones), ())
    assert components == {"distance": 10000, "availability": 0, "price": 6000, "confidence": 9000, "entrance": 0}


def lot(parking_id, distance, status="ALLOWED", score=None):
    item = {"id": parking_id, "distance_m": distance, "compatibility": {"status": status}}
    if score is not None:
        item["ranking_score_bp"] = score
    return item


def test_sort_key_tuples():
    assert sort_key(lot(12, 420, score=7120)) == (0, -7120, 420, 12)
    assert sort_key(lot(12, 420, "UNKNOWN")) == (1, 420, 12)
    # Unknown-only lots ignore any score that might be attached.
    assert sort_key(lot(12, 420, "UNKNOWN", score=9999)) == (1, 420, 12)
    assert sort_key(lot(12, 420, CompatibilityStatus.ALLOWED, score=0)) == (0, 0, 420, 12)


def test_sort_key_orders_confirmed_before_unknown_with_deterministic_ties():
    items = [
        lot(7, 100, "UNKNOWN"),
        lot(3, 500, score=7000),
        lot(4, 100, "UNKNOWN"),
        lot(9, 400, score=7000),
        lot(99, 50, "UNKNOWN"),
        lot(2, 400, score=7000),
        lot(50, 1400, score=8000),
    ]
    assert [item["id"] for item in sorted(items, key=sort_key)] == [50, 2, 9, 3, 99, 4, 7]


@pytest.mark.parametrize(
    "item",
    [
        lot(1, 10, "NOT_ALLOWED", score=100),
        lot(1, 10, None, score=100),
        {"id": 1, "distance_m": 10, "ranking_score_bp": 1},
        lot(1, 10),
        lot(1, 10, score=10001),
        lot(1, 10, score=-1),
        lot(1, 10, score=50.0),
        lot(1, 10, score=True),
        lot(True, 10, score=1),
        lot("1", 10, score=1),
        lot(1, -1, score=1),
        lot(1, 10.0, "UNKNOWN"),
    ],
)
def test_sort_key_rejects_invalid_items(item):
    with pytest.raises(ValueError):
        sort_key(item)
