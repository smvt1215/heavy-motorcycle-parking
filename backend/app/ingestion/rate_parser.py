"""Conservative parser for free-text Taiwan parking rate descriptions.

`parse_rate_text` returns PARSED only when the whole text (NFKC-normalized,
whitespace removed, one trailing "。" dropped) is exactly one of a small set of
fully anchored single-tariff phrases, e.g. 每小時30元, 30元/時, 每30分鐘15元,
每次50元, 免費, 每日100元, 月租3000元/月, or an hourly/time-block phrase followed by
an explicit 每日最高(收費)100元 daily cap.

Anything else (day/event qualifiers such as 平日/假日/活動/優惠, an unanchored
最高, free-minute or rounding clauses, several vehicles or tariffs, thousands
separators) is never PARSED and never yields monetary fields: PARTIALLY_PARSED
when the text visibly contains an amount, otherwise RAW_ONLY.

Negative, non-finite, over-precise (>2 decimals) or out-of-range NUMERIC(10,2)
amounts, zero units, empty text and non-string input are INVALID.
`raw_text` always preserves the original input string unchanged.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ingestion.contracts import ParsedRate
from app.models.enums import RateParseStatus, RateType

MAX_AMOUNT = Decimal("99999999.99")
MAX_MINUTES = 2_147_483_647

_NUM = r"[-+]?(?:[0-9]+(?:\.[0-9]+)?|(?i:nan|inf(?:inity)?))"
_AMOUNT = f"(?P<a>{_NUM})"
_CAP = f"(?P<cap>{_NUM})"
_CAP_SUFFIX = r"[,;、]?每日最高(?:收費)?<CAP>元"

_TIME_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("hour", r"每(?:1|一)?小時<A>元"),
    ("hour", r"每時<A>元"),
    ("hour", r"<A>元/(?:1|一)?小?時"),
    ("half", r"每半小時<A>元"),
    ("half", r"<A>元/半小時"),
    ("minutes", r"每(?P<m>[0-9]+)分(?:鐘)?<A>元"),
    ("minutes", r"<A>元/(?P<m>[0-9]+)分(?:鐘)?"),
    ("hours", r"每(?P<h>[0-9]+)小時<A>元"),
)

_FLAT_TEMPLATES: tuple[tuple[RateType, str], ...] = (
    (RateType.PER_ENTRY, r"每次<A>元"),
    (RateType.PER_ENTRY, r"<A>元/次"),
    (RateType.PER_ENTRY, r"計次<A>元"),
    (RateType.DAILY, r"每日<A>元"),
    (RateType.DAILY, r"每天<A>元"),
    (RateType.DAILY, r"<A>元/日"),
    (RateType.DAILY, r"<A>元/天"),
    (RateType.MONTHLY, r"月租<A>元(?:/月)?"),
    (RateType.MONTHLY, r"每月<A>元"),
    (RateType.MONTHLY, r"<A>元/月"),
)


def _compile(template: str) -> re.Pattern[str]:
    return re.compile(template.replace("<A>", _AMOUNT).replace("<CAP>", _CAP))


_TIME_PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = tuple(
    (kind, _compile(template + suffix), bool(suffix))
    for kind, template in _TIME_TEMPLATES
    for suffix in ("", _CAP_SUFFIX)
)
_FLAT_PATTERNS: tuple[tuple[RateType, re.Pattern[str]], ...] = tuple(
    (rate_type, _compile(template)) for rate_type, template in _FLAT_TEMPLATES
)
_FREE = re.compile(r"免費(?:停車)?")
_BAD_MONEY = re.compile(r"-[0-9]+(?:\.[0-9]+)?元|(?i:nan|inf(?:inity)?)元")
_MONEY_HINT = re.compile(r"[0-9]+(?:\.[0-9]+)?元|免費")


def strict_amount(text: str) -> Decimal | None:
    """Unsigned finite Decimal within NUMERIC(10,2) with at most 2 decimals, else None."""
    if not text or text[0] in "+-":
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT:
        return None
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        return None
    return amount


def _minutes(text: str, factor: int = 1) -> int | None:
    digits = text.lstrip("0") or "0"
    if len(digits) > 10:
        return None
    minutes = int(digits) * factor
    return minutes if 0 < minutes <= MAX_MINUTES else None


def _normalize(raw: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw)).removesuffix("。")


def _invalid(raw_text: str) -> ParsedRate:
    return ParsedRate(RateParseStatus.INVALID, raw_text)


def _time_rate(raw: str, kind: str, match: re.Match[str], capped: bool) -> ParsedRate:
    amount = strict_amount(match["a"])
    if kind == "hour":
        unit: int | None = 60
    elif kind == "half":
        unit = 30
    elif kind == "minutes":
        unit = _minutes(match["m"])
    else:
        unit = _minutes(match["h"], 60)
    cap = strict_amount(match["cap"]) if capped else None
    if amount is None or unit is None or (capped and cap is None):
        return _invalid(raw)
    return ParsedRate(
        RateParseStatus.PARSED,
        raw,
        rate_type=RateType.HOURLY if unit == 60 else RateType.TIME_BLOCK,
        base_amount=amount,
        unit_minutes=unit,
        daily_max_amount=cap,
    )


def parse_rate_text(raw: Any) -> ParsedRate:
    """Parse one free-text rate; ambiguity is never PARSED and money is never guessed."""
    if not isinstance(raw, str):
        return _invalid("" if raw is None else str(raw))
    if re.search(r"(?<=[0-9])\s+(?=[0-9])", unicodedata.normalize("NFKC", raw)):
        return ParsedRate(RateParseStatus.RAW_ONLY, raw)
    text = _normalize(raw)
    if not text:
        return _invalid(raw)
    if _FREE.fullmatch(text):
        return ParsedRate(RateParseStatus.PARSED, raw, rate_type=RateType.FREE)
    for kind, pattern, capped in _TIME_PATTERNS:
        match = pattern.fullmatch(text)
        if match is not None:
            return _time_rate(raw, kind, match, capped)
    for rate_type, pattern in _FLAT_PATTERNS:
        match = pattern.fullmatch(text)
        if match is not None:
            amount = strict_amount(match["a"])
            if amount is None:
                return _invalid(raw)
            return ParsedRate(RateParseStatus.PARSED, raw, rate_type=rate_type, base_amount=amount)
    if _BAD_MONEY.search(text):
        return _invalid(raw)
    status = RateParseStatus.PARTIALLY_PARSED if _MONEY_HINT.search(text) else RateParseStatus.RAW_ONLY
    return ParsedRate(status, raw)
