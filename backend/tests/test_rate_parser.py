from decimal import Decimal

import pytest

from app.ingestion.rate_parser import parse_rate_text
from app.models.enums import RateParseStatus, RateType


@pytest.mark.parametrize("text", ["每小時3 0元", "每３ ０分鐘15元", "每" + "9" * 5000 + "分鐘15元"])
def test_internal_numeric_spaces_and_oversized_units_are_not_confirmed(text):
    rate = parse_rate_text(text)
    assert rate.parse_status != RateParseStatus.PARSED and rate.base_amount is None


PARSED = RateParseStatus.PARSED
UNCERTAIN = {RateParseStatus.PARTIALLY_PARSED, RateParseStatus.RAW_ONLY}


def _assert_no_money(parsed):
    assert parsed.rate_type is None
    assert parsed.base_amount is None
    assert parsed.unit_minutes is None
    assert parsed.free_minutes is None
    assert parsed.daily_max_amount is None
    assert parsed.rules == ()


@pytest.mark.parametrize(
    ("text", "rate_type", "amount", "unit", "cap"),
    [
        ("每小時30元", RateType.HOURLY, "30", 60, None),
        ("30元/時", RateType.HOURLY, "30", 60, None),
        ("30元/小時", RateType.HOURLY, "30", 60, None),
        ("每時25元", RateType.HOURLY, "25", 60, None),
        ("每小時12.50元", RateType.HOURLY, "12.50", 60, None),
        ("每30分鐘15元", RateType.TIME_BLOCK, "15", 30, None),
        ("15元/30分鐘", RateType.TIME_BLOCK, "15", 30, None),
        ("每半小時20元", RateType.TIME_BLOCK, "20", 30, None),
        ("每2小時50元", RateType.TIME_BLOCK, "50", 120, None),
        ("每60分鐘40元", RateType.HOURLY, "40", 60, None),
        ("每次50元", RateType.PER_ENTRY, "50", None, None),
        ("50元/次", RateType.PER_ENTRY, "50", None, None),
        ("每日100元", RateType.DAILY, "100", None, None),
        ("月租3000元/月", RateType.MONTHLY, "3000", None, None),
        ("每月3000元", RateType.MONTHLY, "3000", None, None),
        ("每小時30元，每日最高收費100元", RateType.HOURLY, "30", 60, "100"),
        ("每小時30元每日最高100元", RateType.HOURLY, "30", 60, "100"),
        ("每30分鐘15元；每日最高收費120元", RateType.TIME_BLOCK, "15", 30, "120"),
    ],
)
def test_fully_anchored_simple_patterns_are_parsed(text, rate_type, amount, unit, cap):
    parsed = parse_rate_text(text)
    assert parsed.parse_status is PARSED
    assert parsed.raw_text == text
    assert parsed.rate_type is rate_type
    assert parsed.base_amount == Decimal(amount)
    assert isinstance(parsed.base_amount, Decimal)
    assert parsed.unit_minutes == unit
    assert parsed.daily_max_amount == (Decimal(cap) if cap else None)
    assert parsed.free_minutes is None


def test_free_is_parsed_without_inventing_amounts():
    parsed = parse_rate_text("免費")
    assert parsed.parse_status is PARSED
    assert parsed.rate_type is RateType.FREE
    assert parsed.base_amount is None
    assert parsed.unit_minutes is None


def test_full_width_and_whitespace_normalized_but_raw_text_preserved():
    raw = "  每小時３０元。 "
    parsed = parse_rate_text(raw)
    assert parsed.parse_status is PARSED
    assert parsed.base_amount == Decimal("30")
    assert parsed.raw_text == raw


@pytest.mark.parametrize(
    "text",
    [
        "平日每小時30元",
        "假日每小時40元",
        "活動期間每次100元",
        "優惠每小時20元",
        "最高每日100元",
        "每日最高100元",
        "每小時30元，假日每小時40元",
        "汽車每小時40元，機車每次20元",
        "前30分鐘免費，之後每小時30元",
        "每小時30元，每日最高100元（限平日）",
        "首小時30元",
        "每小時30元起",
        "30元/時(未滿半小時以半小時計)",
        "小型車：計時 30元/時，全程以半小時計，全日雙月票11,520元。",
        "3,000元/月",
        "每小時30",
        "依現場公告",
        "每小時٣٠元",
    ],
)
def test_ambiguous_text_is_never_parsed_and_money_is_never_guessed(text):
    parsed = parse_rate_text(text)
    assert parsed.parse_status in UNCERTAIN
    assert parsed.raw_text == text
    _assert_no_money(parsed)


def test_uncertain_status_reflects_visible_amount():
    assert parse_rate_text("平日每小時30元").parse_status is RateParseStatus.PARTIALLY_PARSED
    assert parse_rate_text("依現場公告").parse_status is RateParseStatus.RAW_ONLY
    assert parse_rate_text("每小時٣٠元").parse_status is RateParseStatus.RAW_ONLY


@pytest.mark.parametrize(
    "text",
    [
        "每小時-30元",
        "-30元/時",
        "每小時+30元",
        "每小時NaN元",
        "每小時nan元",
        "每小時inf元",
        "每小時Infinity元",
        "每小時30.555元",
        "每0分鐘10元",
        "每0小時10元",
        "每小時100000000元",
        "每次-5元",
        "每小時30元，每日最高-1元",
        "平日每小時-30元",
        "",
        "   ",
    ],
)
def test_invalid_amounts_and_units_are_invalid(text):
    parsed = parse_rate_text(text)
    assert parsed.parse_status is RateParseStatus.INVALID
    assert parsed.raw_text == text
    _assert_no_money(parsed)


@pytest.mark.parametrize(("raw", "raw_text"), [(None, ""), (float("nan"), "nan"), (30, "30"), (True, "True")])
def test_non_string_input_is_invalid(raw, raw_text):
    parsed = parse_rate_text(raw)
    assert parsed.parse_status is RateParseStatus.INVALID
    assert parsed.raw_text == raw_text
    _assert_no_money(parsed)


def test_numeric_10_2_upper_bound_is_accepted():
    parsed = parse_rate_text("每次99999999.99元")
    assert parsed.parse_status is PARSED
    assert parsed.base_amount == Decimal("99999999.99")
