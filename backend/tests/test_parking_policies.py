"""Reviewed policy scope and temporal resolution through the existing rule engine."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.domain.parking import ParkingFacts, Provenance, RuleFact, ZoneFacts
from app.ingestion.contracts import NormalizedZone
from app.ingestion.new_taipei import NewTaipeiParkingAdapter
from app.ingestion.policies import (
    MANAGED_FACILITIES,
    NEW_TAIPEI_PUBLIC_CAR,
    NEW_TAIPEI_ROADSIDE,
    TAIPEI_ROADSIDE,
    RoadsideScope,
    apply_roadside_policy,
    managed_facility,
)
from app.models.enums import ParkingSpaceType, RateType
from app.services.compatibility import ParkingCompatibilityService

ZONE = NormalizedZone("motor", "一般機車", ParkingSpaceType.MOTO_SHARED, 1, normal_heavy=True)
SCOPE = RoadsideScope(
    "taipei",
    True,
    True,
    True,
    False,
    {"start_time": "07:00", "end_time": "20:00"},
    {"review": "synthetic scope fixture"},
)


def facts(zone):
    baseline = RuleFact(1, 1, 1, "BASELINE", 100, Provenance(1), normal_heavy_allowed=zone.normal_heavy)
    rules = tuple(
        RuleFact(
            i + 2,
            1,
            1,
            str(r.rule_kind),
            r.authority_priority,
            Provenance(2),
            normal_heavy_allowed=r.normal_heavy,
            large_heavy_allowed=r.large_heavy,
            effective_from=r.effective_from,
            schedule=r.schedule,
        )
        for i, r in enumerate(zone.rules)
    )
    return ParkingFacts(1, (baseline, *rules))


def status(zone, at, vehicle="LARGE_HEAVY", extras=()):
    parking = facts(zone)
    parking = replace(parking, rules=(*parking.rules, *extras))
    return ParkingCompatibilityService().evaluate(parking, ZoneFacts(1, 1, str(zone.space_type)), vehicle, at).status


@pytest.mark.parametrize("city,policy", [("taipei", TAIPEI_ROADSIDE), ("new_taipei", NEW_TAIPEI_ROADSIDE)])
def test_policy_boundary_uses_taipei_midnight_and_requires_confirmed_schedule(city, policy):
    zone = apply_roadside_policy(ZONE, replace(SCOPE, city=city, schedule={}))
    assert status(zone, policy.effective_from - timedelta(microseconds=1)) == "UNKNOWN"
    assert status(zone, policy.effective_from) == "ALLOWED"
    assert status(zone, policy.effective_from, "NORMAL_HEAVY") == "ALLOWED"
    assert zone.rules[0].evidence["scope"] == policy.scope
    assert zone.rules[0].policy_code == policy.code


@pytest.mark.parametrize(
    "field,value",
    [
        ("public", False),
        ("public", None),
        ("paid", False),
        ("paid", None),
        ("ordinary_motorcycle", False),
        ("ordinary_motorcycle", None),
        ("special_restriction", True),
        ("special_restriction", None),
        ("city", "private"),
    ],
)
def test_policy_does_not_expand_scope(field, value):
    assert apply_roadside_policy(ZONE, replace(SCOPE, **{field: value})) == ZONE


def test_unknown_schedule_does_not_expose_lower_tier_grant():
    zone = apply_roadside_policy(ZONE, replace(SCOPE, schedule=None))
    assert status(zone, datetime.fromisoformat("2026-10-07T09:00:00+08:00")) == "UNKNOWN"


def test_scheduled_grant_outside_motorcycle_parking_hours_is_unknown():
    zone = apply_roadside_policy(ZONE, SCOPE)
    for hour, expected in [(6, "UNKNOWN"), (7, "ALLOWED"), (19, "ALLOWED"), (20, "UNKNOWN")]:
        assert status(zone, datetime.fromisoformat(f"2026-10-07T{hour:02}:00:00+08:00")) == expected


@pytest.mark.parametrize("permission", [False, None])
def test_tied_conflict_and_unknown_remain_unknown(permission):
    zone = apply_roadside_policy(ZONE, replace(SCOPE, schedule={}))
    conflict = RuleFact(99, 1, 1, "BASELINE", 200, Provenance(99), large_heavy_allowed=permission)
    assert status(zone, TAIPEI_ROADSIDE.effective_from, extras=(conflict,)) == "UNKNOWN"


def test_special_zone_exception_overrides_policy():
    zone = apply_roadside_policy(ZONE, replace(SCOPE, schedule={}))
    ban = RuleFact(99, 1, 1, "EXCEPTION", 10, Provenance(99), large_heavy_allowed=False)
    assert status(zone, TAIPEI_ROADSIDE.effective_from, extras=(ban,)) == "NOT_ALLOWED"


def test_new_taipei_policy_charge_is_repeated_entry_not_hourly_guess():
    zone = apply_roadside_policy(ZONE, replace(SCOPE, city="new_taipei"))
    (rate,) = zone.rates
    assert rate.parsed.rate_type == RateType.PER_ENTRY
    assert rate.parsed.base_amount == 30 and rate.parsed.unit_minutes == 240
    assert rate.policy_code == NEW_TAIPEI_ROADSIDE.code
    assert rate.effective_from == NEW_TAIPEI_ROADSIDE.effective_from
    assert rate.raw_payload["policy_url"] == NEW_TAIPEI_ROADSIDE.url
    assert apply_roadside_policy(ZONE, SCOPE).rates == ()  # No unconfirmed Taipei rate classification.


def test_managed_roster_is_unique_and_exact_scope_tuple_required():
    entries = MANAGED_FACILITIES["facilities"]
    assert len(entries) == len({e["external_id"] for e in entries}) == 55
    assert len(MANAGED_FACILITIES["unmatched"]) == 16
    for e in entries:
        record = dict(ID=e["external_id"], NAME=e["name"], AREA=e["district"], ADDRESS=e["address"])
        assert managed_facility(record) == e
        for key in record:
            assert managed_facility({**record, key: "unverified"}) is None
    assert NEW_TAIPEI_PUBLIC_CAR.effective_from.isoformat() == "2025-03-21T00:00:00+08:00"


def test_managed_public_permission_only_on_car_and_never_copies_rate_or_count():
    e = next(e for e in MANAGED_FACILITIES["facilities"] if e["external_id"] == "010152")
    record = dict(
        ID=e["external_id"],
        NAME=e["name"],
        AREA=e["district"],
        ADDRESS=e["address"],
        TW97X="297000",
        TW97Y="2770000",
        TOTALCAR="314",
        TOTALMOTOR="22",
        PAYEX="機車計次10元;重型機車每小時20元;",
    )
    adapter = NewTaipeiParkingAdapter()
    zones = {z.key: z for z in adapter.normalize_static(record).zones}
    assert len(zones["car"].rules) == 1 and zones["car"].capacity == 314
    assert zones["car"].space_type == ParkingSpaceType.CAR_SHARED
    assert zones["motor"].rules == zones["heavy"].rules == ()
    assert zones["heavy"].capacity is None
    assert not any(r.vehicle is not None for r in zones["car"].rates)
    assert all(not z.rules for z in adapter.normalize_static({**record, "ADDRESS": "moved"}).zones)


@pytest.mark.parametrize(
    "space_type", [ParkingSpaceType.CAR_SHARED, ParkingSpaceType.HEAVY_ONLY, ParkingSpaceType.LIGHT_MOTO_ONLY]
)
def test_roadside_scope_cannot_grant_another_zone_category(space_type):
    other = replace(ZONE, space_type=space_type)
    assert apply_roadside_policy(other, SCOPE) == other


def test_public_car_grant_does_not_predate_verified_facility_scope():
    e = next(e for e in MANAGED_FACILITIES["facilities"] if e["external_id"] == "010152")
    record = dict(
        ID=e["external_id"],
        NAME=e["name"],
        AREA=e["district"],
        ADDRESS=e["address"],
        TW97X="297000",
        TW97Y="2770000",
        TOTALCAR="314",
        TOTALMOTOR="0",
    )
    (zone,) = NewTaipeiParkingAdapter().normalize_static(record).zones
    roster_at = datetime.fromisoformat(MANAGED_FACILITIES["roster_published_at"])
    assert zone.rules[0].effective_from == roster_at
    assert status(zone, NEW_TAIPEI_PUBLIC_CAR.effective_from) == "UNKNOWN"
    assert status(zone, roster_at - timedelta(microseconds=1)) == "UNKNOWN"
    assert status(zone, roster_at) == "ALLOWED"
