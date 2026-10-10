"""Reviewed official policies; scope evidence never comes from counts or prices."""

import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from importlib.resources import files
from typing import Any

from app.ingestion.contracts import NormalizedRate, NormalizedRule, NormalizedZone, ParsedRate, ParsedRateRule
from app.models.enums import ParkingSpaceType, RateParseStatus, RateType, RuleKind, VehicleType


@dataclass(frozen=True)
class OfficialPolicy:
    code: str
    name: str
    url: str
    effective_from: datetime
    published_at: datetime
    scope: str
    authority_priority: int = 200


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


TAIPEI_ROADSIDE = OfficialPolicy(
    "TAIPEI_ROADSIDE_LARGE_20261006",
    "臺北公有路邊收費機車格大重政策",
    "https://pma.gov.taipei/News_Content.aspx?n=E43F2E5AE1223B3D&s=1190C312983454BA&sms=78D644F2755ACCAA",
    _instant("2026-10-06T00:00:00+08:00"),
    _instant("2026-09-30T00:00:00+08:00"),
    "臺北市公有路邊已公告收費一般機車格；一般機車可停時段；排除特殊禁停、未收費與路外",
)
NEW_TAIPEI_ROADSIDE = OfficialPolicy(
    "NEW_TAIPEI_ROADSIDE_LARGE_20260701",
    "新北路邊收費機車格大重政策",
    "https://www.traffic.ntpc.gov.tw/home.jsp?act=be4f48068b2b0031&dataserno=99345bcf365b8f6ed7a6315705335638&id=54fa46e9e522dde4&mserno=39e5192ff77897e0ae099c1886ca9b09",
    _instant("2026-07-01T00:00:00+08:00"),
    _instant("2026-06-29T00:00:00+08:00"),
    "新北市公有路邊收費一般機車格；排除特殊禁停與路外；每4小時計次30元",
)
NEW_TAIPEI_PUBLIC_CAR = OfficialPolicy(
    "NEW_TAIPEI_MANAGED_PUBLIC_CAR",
    "新北交通局轄管公有路外汽車格大重停放政策",
    "https://www.traffic.ntpc.gov.tw/home.jsp?act=be4f48068b2b0031&dataserno=dac9467f3186024b02ee3214e1ab7dcd&id=148",
    # Announcement confirms existing law; no unsupported earlier effective date is asserted.
    _instant("2025-03-21T00:00:00+08:00"),
    _instant("2025-03-21T00:00:00+08:00"),
    "交通局轄管公有路外場站汽車區；須匹配已核對場站ID/名稱/行政區/地址；不適用私營或一般機車區",
)
POLICIES = {p.code: p for p in (TAIPEI_ROADSIDE, NEW_TAIPEI_ROADSIDE, NEW_TAIPEI_PUBLIC_CAR)}
MANAGED_FACILITIES = json.loads(
    files("app.ingestion").joinpath("evidence/new_taipei_managed_facilities.json").read_text(encoding="utf-8")
)


def managed_facility(record: dict[str, Any]) -> dict | None:
    matches = [
        entry
        for entry in MANAGED_FACILITIES["facilities"]
        if (record.get("ID"), record.get("NAME"), record.get("AREA"), record.get("ADDRESS"))
        == (entry["external_id"], entry["name"], entry["district"], entry["address"])
    ]
    return matches[0] if len(matches) == 1 else None


def policy_rule(
    policy: OfficialPolicy, zone: NormalizedZone, *, schedule=None, evidence=None, scope_effective_from=None
) -> NormalizedRule:
    return NormalizedRule(
        key=policy.code,
        policy_code=policy.code,
        normal_heavy=zone.normal_heavy,
        large_heavy=True,
        rule_kind=RuleKind.BASELINE,
        authority_priority=policy.authority_priority,
        effective_from=max(policy.effective_from, scope_effective_from)
        if scope_effective_from
        else policy.effective_from,
        schedule=schedule,
        evidence={"scope": policy.scope, **(evidence or {})},
    )


@dataclass(frozen=True)
class RoadsideScope:
    city: str
    public: bool | None
    paid: bool | None
    ordinary_motorcycle: bool | None
    special_restriction: bool | None
    schedule: dict[str, Any] | None
    evidence: dict[str, Any]


def apply_roadside_policy(zone: NormalizedZone, scope: RoadsideScope) -> NormalizedZone:
    """The caller must confirm scope from documented source fields or reviewed evidence.

    Unknown schedule blocks permission conservatively via the existing engine.
    A fee amount, capacity, current-charge flag or lot name alone proves no scope.
    """
    policy = {"taipei": TAIPEI_ROADSIDE, "new_taipei": NEW_TAIPEI_ROADSIDE}.get(scope.city)
    if policy is None or not (
        zone.space_type == ParkingSpaceType.MOTO_SHARED
        and scope.public is True
        and scope.paid is True
        and scope.ordinary_motorcycle is True
        and scope.special_restriction is False
    ):
        return zone
    schedule = scope.schedule if scope.schedule is not None else {"unresolved_roadside_schedule": True}
    rule = policy_rule(policy, zone, schedule=schedule, evidence=scope.evidence)
    rates = zone.rates
    if scope.city == "new_taipei":
        # Officially a repeated per-entry charge, not a linear hourly comparison.
        rate = NormalizedRate(
            key=policy.code,
            vehicle=VehicleType.LARGE_HEAVY,
            parsed=ParsedRate(
                RateParseStatus.PARSED,
                "每4小時計次30元（依公告收費時段）",
                RateType.PER_ENTRY,
                Decimal(30),
                240,
                rules=(ParsedRateRule(schedule=schedule),),
            ),
            policy_code=policy.code,
            effective_from=policy.effective_from,
            raw_payload={"policy_url": policy.url, "scope_evidence": scope.evidence},
        )
        rates = (*rates, rate)
    return replace(zone, rules=(*zone.rules, rule), rates=rates)
