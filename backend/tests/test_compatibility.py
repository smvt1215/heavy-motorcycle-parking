import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from itertools import permutations, product

import pytest

from app.domain.parking import SPACE_TYPES, VEHICLES, ParkingFacts, Provenance, RuleFact, ZoneFacts
from app.domain.schedules import CalendarSnapshot
from app.models.enums import ParkingSpaceType, VehicleType
from app.services import (
    CompatibilityStatus,
    ParkingCompatibilityService,
    filter_nearby_zones,
    is_nearby_eligible,
    rollup_nearby_compatibility,
)
from tests.fixtures.parking import EVALUATION_AT, RULE_CASES

PERMISSION_FIELDS = {
    "GREEN": "green_plate_allowed",
    "WHITE": "white_plate_allowed",
    "YELLOW": "yellow_plate_allowed",
    "RED": "red_plate_allowed",
    "CAR": "car_allowed",
}
DEFAULT_ALLOWED = {
    "HEAVY_ONLY": {"YELLOW", "RED"},
    "MOTO_SHARED": {"GREEN", "WHITE", "YELLOW", "RED"},
    "CAR_SHARED": {"YELLOW", "RED", "CAR"},
    "LIGHT_MOTO_ONLY": {"GREEN", "WHITE"},
}


def rule(rule_id=1, permission=True, vehicle="RED", **fields):
    values = {
        "rule_id": rule_id,
        "parking_id": 10,
        "zone_id": 20,
        "rule_kind": "BASELINE",
        "authority_priority": 10,
        "provenance": Provenance(source_id=rule_id, source_type="GOVERNMENT", source_record_id=f"record-{rule_id}"),
        PERMISSION_FIELDS[vehicle]: permission,
    }
    return RuleFact(**{**values, **fields})


def evaluate(*rules, vehicle="RED", space_type="HEAVY_ONLY", at=EVALUATION_AT, calendar=None, zone_id=20):
    return ParkingCompatibilityService(calendar).evaluate(
        ParkingFacts(10, tuple(rules)), ZoneFacts(zone_id, 10, space_type), vehicle, at
    )


@pytest.mark.parametrize("space_type, vehicle", tuple(product(SPACE_TYPES, VEHICLES)))
def test_all_twenty_space_type_vehicle_defaults(space_type, vehicle):
    result = evaluate(vehicle=vehicle, space_type=space_type)
    expected = "ALLOWED" if vehicle in DEFAULT_ALLOWED[space_type] else "NOT_ALLOWED"
    assert result.status == expected
    assert result.reason == "space_type_default"
    assert result.space_type == space_type
    assert result.vehicle == vehicle
    assert result.confidence is None
    assert result.provenance == result.rule_ids == ()


@pytest.mark.parametrize("space_type, vehicle, permission", tuple(product(SPACE_TYPES, VEHICLES, (True, False, None))))
def test_explicit_three_state_permission_always_beats_classification(space_type, vehicle, permission):
    result = evaluate(rule(vehicle=vehicle, permission=permission), vehicle=vehicle, space_type=space_type)
    expected = "UNKNOWN" if permission is None else "ALLOWED" if permission else "NOT_ALLOWED"
    assert result.status == expected
    assert result.reason == ("unknown_permission" if permission is None else "explicit_vehicle_permission")
    assert result.rule_ids == (1,)


@pytest.mark.parametrize(
    "permissions, expected",
    [
        ((True, True), "ALLOWED"),
        ((False, False), "NOT_ALLOWED"),
        ((True, False), "UNKNOWN"),
        ((True, None), "UNKNOWN"),
        ((False, None), "UNKNOWN"),
        ((None, None), "UNKNOWN"),
        ((True, False, None), "UNKNOWN"),
    ],
)
@pytest.mark.parametrize("lower_permission", [True, False])
def test_every_highest_tier_fact_counts_and_lower_tier_never_overrides(permissions, expected, lower_permission):
    high = [rule(i + 1, permission=value, rule_kind="EXCEPTION") for i, value in enumerate(permissions)]
    lower = rule(99, permission=lower_permission, authority_priority=999)
    for ordering in permutations((*high, lower)):
        result = evaluate(*ordering)
        assert result.status == expected
        assert result.rule_ids == tuple(range(1, len(high) + 1))
        if expected == "UNKNOWN":
            assert result.confidence is None


@pytest.mark.parametrize(
    "winning, losing",
    [
        (
            {"zone_id": 20, "rule_kind": "BASELINE", "authority_priority": -100},
            {"zone_id": None, "rule_kind": "EXCEPTION", "authority_priority": 999},
        ),
        ({"rule_kind": "EXCEPTION", "authority_priority": -100}, {"rule_kind": "BASELINE", "authority_priority": 999}),
        ({"authority_priority": 20}, {"authority_priority": 10}),
        ({"authority_priority": -10}, {"authority_priority": -20}),
    ],
)
@pytest.mark.parametrize("winning_permission", [True, False, None])
def test_precedence_is_scope_then_kind_then_configured_authority(winning, losing, winning_permission):
    expected = "UNKNOWN" if winning_permission is None else "ALLOWED" if winning_permission else "NOT_ALLOWED"
    result = evaluate(
        rule(1, permission=winning_permission, **winning), rule(2, permission=not winning_permission, **losing)
    )
    assert result.status == expected
    assert result.rule_ids == (1,)


def test_m1_equal_authority_conflict_fixture_is_consumed_without_collapsing_evidence():
    rules = []
    for index, case in enumerate(RULE_CASES, start=1):
        fields = {key: value for key, value in case.items() if key != "scope"}
        rules.append(rule(index, zone_id=None if case["scope"] == "lot" else 20, **fields))
    for ordering in permutations(rules):
        result = evaluate(*ordering)
        assert result.status == "UNKNOWN"
        assert result.reason == "conflicting_permissions"
        assert result.rule_ids == (3, 4, 5)
        assert [source.source_id for source in result.provenance] == [3, 4, 5]


@pytest.mark.parametrize("permissions", [(True, False), (True, None), (False, None)])
def test_recency_confidence_ids_and_order_do_not_resolve_legality(permissions):
    facts = (
        rule(
            1000,
            permission=permissions[0],
            confidence=1.0,
            provenance=Provenance(1, fetched_at=EVALUATION_AT + timedelta(days=1)),
        ),
        rule(
            -1000,
            permission=permissions[1],
            confidence=0.0,
            provenance=Provenance(2, fetched_at=EVALUATION_AT - timedelta(days=365)),
        ),
    )
    assert evaluate(*facts).status == evaluate(*reversed(facts)).status == "UNKNOWN"
    assert evaluate(*facts).to_dict() == evaluate(*reversed(facts)).to_dict()


@pytest.mark.parametrize(
    "fields",
    [
        {"active": False},
        {"parking_id": 11},
        {"zone_id": 21},
        {"effective_from": EVALUATION_AT + timedelta(seconds=1)},
        {"effective_to": EVALUATION_AT},
        {"schedule": {"weekdays": [6]}},
    ],
)
def test_known_inapplicable_rule_does_not_override_lower_candidate(fields):
    high = rule(1, permission=False, rule_kind="EXCEPTION", **fields)
    result = evaluate(high, rule(2, permission=True))
    assert result.status == "ALLOWED"
    assert result.rule_ids == (2,)


@pytest.mark.parametrize("fields", [{"active": False}, {"zone_id": 21}, {"effective_to": EVALUATION_AT}])
def test_irrelevant_rule_with_bad_schedule_is_ignored_before_parsing(fields):
    assert evaluate(rule(schedule={"unsupported": True}, **fields)).reason == "space_type_default"


def test_effective_dates_use_absolute_half_open_interval():
    fact = rule(effective_from=EVALUATION_AT, effective_to=EVALUATION_AT + timedelta(hours=1))
    assert evaluate(fact, at=EVALUATION_AT - timedelta(microseconds=1)).rule_ids == ()
    assert evaluate(fact, at=EVALUATION_AT).rule_ids == (1,)
    assert evaluate(fact, at=EVALUATION_AT + timedelta(hours=1) - timedelta(microseconds=1)).rule_ids == (1,)
    assert evaluate(fact, at=EVALUATION_AT + timedelta(hours=1)).rule_ids == ()
    assert (
        evaluate(fact, at=EVALUATION_AT.astimezone(timezone(timedelta(hours=-7)))).to_dict() == evaluate(fact).to_dict()
    )


@pytest.mark.parametrize("schedule", [{"day_type": "HOLIDAY"}, {"timezone": "UTC"}, {"unsupported": True}])
@pytest.mark.parametrize("permission", [True, False, None])
def test_unresolved_highest_schedule_is_unknown_without_lower_or_default_fallback(schedule, permission):
    result = evaluate(rule(1, rule_kind="EXCEPTION", permission=permission, schedule=schedule), rule(2))
    assert result.status == "UNKNOWN"
    assert result.reason == "schedule_unknown"
    assert result.rule_ids == (1,)


def test_uncertain_lower_tier_cannot_change_confirmed_higher_tier():
    result = evaluate(rule(1, rule_kind="EXCEPTION"), rule(2, permission=False, schedule={"day_type": "HOLIDAY"}))
    assert result.status == "ALLOWED"
    assert result.rule_ids == (1,)


def test_uncertain_schedule_in_tied_highest_tier_remains_unknown():
    result = evaluate(rule(1), rule(2, schedule={"day_type": "HOLIDAY"}))
    assert result.status == "UNKNOWN"
    assert result.rule_ids == (1, 2)


@pytest.mark.parametrize("holiday, expected, winners", [(True, "NOT_ALLOWED", (1,)), (False, "ALLOWED", (2,))])
def test_holiday_exception_uses_supplied_calendar(holiday, expected, winners):
    high = rule(1, permission=False, rule_kind="EXCEPTION", schedule={"day_type": "HOLIDAY"})
    result = evaluate(high, rule(2), calendar=CalendarSnapshot({EVALUATION_AT.date(): holiday}))
    assert result.status == expected
    assert result.rule_ids == winners


def test_confirmed_confidence_is_descriptive_conservative_and_nullable():
    assert evaluate(rule(1, confidence=0.9), rule(2, confidence=0.3)).confidence == 0.3
    assert evaluate(rule(1, confidence=0.9), rule(2, confidence=None)).confidence is None
    assert evaluate(rule(1, confidence=0.0)).confidence == 0.0
    assert evaluate(rule(1, confidence=1.0), rule(2, permission=False, confidence=0.9)).confidence is None


def test_json_result_keeps_all_winning_provenance_and_null_fields():
    source = Provenance(7, "OPERATOR", "same-upstream-rule", fetched_at=EVALUATION_AT, verified_at=None)
    result = evaluate(
        rule(1, provenance=source), rule(2, permission=False, provenance=source), rule(3, zone_id=None, confidence=1.0)
    )
    data = json.loads(json.dumps(result.to_dict(), allow_nan=False))
    assert data["status"] == "UNKNOWN"
    assert data["vehicle"] == "RED"
    assert data["evaluation_at"] == "2026-10-05T04:00:00Z"
    assert data["rule_ids"] == [1, 2]
    assert len(data["provenance"]) == 2
    assert data["provenance"][0]["source_record_id"] == "same-upstream-rule"
    assert data["provenance"][0]["fetched_at"] == data["evaluation_at"]
    assert data["provenance"][0]["source_updated_at"] is None
    assert data["provenance"][0]["verified_at"] is None
    assert data["confidence"] is None
    with pytest.raises(FrozenInstanceError):
        result.status = CompatibilityStatus.ALLOWED


@pytest.mark.parametrize("value", [None, "", "red", "BOAT", True, 1])
def test_vehicle_must_be_explicit_and_supported(value):
    with pytest.raises(ValueError, match="vehicle"):
        evaluate(vehicle=value)


def test_input_instant_and_zone_ownership_are_required():
    with pytest.raises(ValueError, match="offset-aware"):
        evaluate(at=datetime(2026, 10, 5))
    with pytest.raises(ValueError, match="belong"):
        ParkingCompatibilityService().evaluate(ParkingFacts(10), ZoneFacts(20, 11, "HEAVY_ONLY"), "RED", EVALUATION_AT)


def test_persisted_vehicle_enum_can_be_normalized_without_inferring_vehicle():
    result = evaluate(vehicle=VehicleType.RED)
    assert result.vehicle == "RED"
    assert type(result.vehicle) is str
    zone = ZoneFacts(20, 10, ParkingSpaceType.HEAVY_ONLY)
    assert type(zone.space_type) is str


def test_service_import_has_no_orm_http_or_ui_dependency():
    script = (
        "import sys; import app.services; "
        "assert 'sqlalchemy' not in sys.modules; assert 'fastapi' not in sys.modules; "
        "assert not any(name.startswith('app.models') for name in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)


def test_duplicate_rule_identity_is_rejected_instead_of_overwriting_conflicting_facts():
    with pytest.raises(ValueError, match="unique"):
        evaluate(rule(1), rule(1, permission=False))


@pytest.mark.parametrize("vehicle", ["YELLOW", "RED"])
@pytest.mark.parametrize("permission", [True, False, None])
def test_light_moto_only_is_never_exposed_in_normal_heavy_search(vehicle, permission):
    result = evaluate(rule(vehicle=vehicle, permission=permission), vehicle=vehicle, space_type="LIGHT_MOTO_ONLY")
    assert not is_nearby_eligible(result)
    assert not is_nearby_eligible(result, include_unknown=True)


@pytest.mark.parametrize(
    "permission, default, with_unknown", [(True, True, True), (False, False, False), (None, False, True)]
)
def test_nearby_tri_state_policy(permission, default, with_unknown):
    result = evaluate(rule(permission=permission))
    assert is_nearby_eligible(result) is default
    assert is_nearby_eligible(result, include_unknown=True) is with_unknown
    assert result.status == ("UNKNOWN" if permission is None else "ALLOWED" if permission else "NOT_ALLOWED")


def test_nearby_rollup_is_derived_from_returned_zones_only():
    confirmed = evaluate(zone_id=20)
    unknown = evaluate(rule(zone_id=21, permission=None), zone_id=21)
    denied = evaluate(rule(zone_id=22, permission=False), zone_id=22)
    assert filter_nearby_zones((unknown, denied, confirmed)) == (confirmed,)
    returned = filter_nearby_zones((unknown, denied, confirmed), include_unknown=True)
    assert returned == (unknown, confirmed)
    assert rollup_nearby_compatibility(returned) == "ALLOWED"
    assert returned[0].status == "UNKNOWN"
    assert rollup_nearby_compatibility(filter_nearby_zones((unknown, denied))) is None
    assert rollup_nearby_compatibility(filter_nearby_zones((unknown, denied), include_unknown=True)) == "UNKNOWN"
    assert rollup_nearby_compatibility(()) is None


@pytest.mark.parametrize("flag", [None, "true", "false", 1, 0])
def test_unknown_filter_flag_must_be_boolean(flag):
    with pytest.raises(ValueError, match="boolean"):
        is_nearby_eligible(evaluate(), include_unknown=flag)
    with pytest.raises(ValueError, match="boolean"):
        filter_nearby_zones((), include_unknown=flag)


def test_rollup_rejects_prohibited_zones_or_mixed_evaluation_contexts():
    with pytest.raises(ValueError, match="filtered"):
        rollup_nearby_compatibility((evaluate(rule(permission=False)),))
    confirmed = evaluate()
    for other in (
        replace(confirmed, parking_id=11),
        replace(confirmed, vehicle="YELLOW"),
        replace(confirmed, evaluation_at=EVALUATION_AT + timedelta(seconds=1)),
    ):
        with pytest.raises(ValueError, match="one lot"):
            rollup_nearby_compatibility((confirmed, other))


@pytest.mark.parametrize(
    "fields",
    [
        {"red_plate_allowed": 1},
        {"red_plate_allowed": "true"},
        {"active": None},
        {"authority_priority": True},
        {"authority_priority": None},
        {"authority_priority": 1.5},
        {"rule_kind": "LATEST"},
        {"confidence": True},
        {"confidence": float("nan")},
        {"confidence": float("inf")},
        {"confidence": -0.1},
        {"confidence": 1.1},
        {"effective_from": datetime(2026, 10, 5)},
        {"effective_from": EVALUATION_AT, "effective_to": EVALUATION_AT},
    ],
)
def test_invalid_normalized_rule_facts_are_rejected_before_evaluation(fields):
    with pytest.raises(ValueError):
        rule(**fields)


@pytest.mark.parametrize("field", ["rule_id", "parking_id", "zone_id"])
@pytest.mark.parametrize("value", ["20", True, 20.0])
def test_rule_ids_cannot_silently_change_rule_scope(field, value):
    with pytest.raises(ValueError, match="integer ID"):
        rule(**{field: value})


@pytest.mark.parametrize("value", ["20", True, 20.0])
def test_all_context_ids_and_source_ids_require_actual_integers(value):
    constructors = [
        lambda: ParkingFacts(value),
        lambda: ZoneFacts(value, 10, "HEAVY_ONLY"),
        lambda: ZoneFacts(20, value, "HEAVY_ONLY"),
        lambda: Provenance(value),
    ]
    for construct in constructors:
        with pytest.raises(ValueError, match="integer ID"):
            construct()


def test_rollup_rejects_light_moto_only_heavy_zones_before_projecting_lot_legality():
    light = evaluate(rule(permission=True), space_type="LIGHT_MOTO_ONLY")
    with pytest.raises(ValueError, match="filtered"):
        rollup_nearby_compatibility((light,))


def test_rule_must_supply_typed_provenance():
    with pytest.raises(ValueError, match="Provenance"):
        rule(provenance=None)
