"""Real roadside samples are distinct from synthetic policy edge cases."""

import json
from pathlib import Path

import pytest

from app.ingestion.cli import parser
from app.ingestion.contracts import RecordError
from app.ingestion.new_taipei_roadside import NewTaipeiRoadsideAdapter, charging_schedule
from app.models.enums import RealtimeStatus, VehicleType

ADAPTER = NewTaipeiRoadsideAdapter()
SAMPLES = json.loads((Path(__file__).parent / "fixtures/new_taipei/roadside_sample.json").read_text())


def motor(**kwargs):
    # Synthetic record: policy scope must not be claimed as a captured live motorcycle cell.
    return {
        **SAMPLES[0],
        "name": "機車停車位",
        "day": "每天",
        "hour": "00:00-24:00",
        "memo": "",
        "pay": "計次收費",
        "paycash": "20元/次",
        **kwargs,
    }


def test_real_sample_and_source_does_not_grant_heavy_or_guess_status_codes():
    assert parser().parse_args(["--city", "new_taipei_roadside"]).city == ADAPTER.city
    assert ADAPTER.source.feeds["static"].page_size == 1000
    assert ADAPTER.source.feeds["static"].max_pages == 100
    assert ADAPTER.source.feeds["static"].reconcile_min_ratio == 0.8
    assert ADAPTER.records({"pages": [SAMPLES]}) == SAMPLES
    for record in SAMPLES:
        lot = ADAPTER.normalize_static(record)
        assert lot.entrances == () and lot.district is None
        (zone,) = lot.zones
        assert zone.rules == () and zone.capacity == 1
        assert zone.large_heavy is None
        assert zone.space_type == "CAR_SHARED"
        assert ADAPTER.normalize_realtime(record).observations[0].status == RealtimeStatus.UNKNOWN


def test_synthetic_confirmed_ordinary_paid_motor_cell_has_separate_policy_rate():
    (zone,) = ADAPTER.normalize_static(motor()).zones
    assert zone.normal_heavy is True and zone.large_heavy is None
    assert zone.space_type == "MOTO_SHARED"
    (rule,) = zone.rules
    assert rule.large_heavy is True and rule.authority_priority == 200
    assert rule.schedule == {"timezone": "Asia/Taipei", "weekdays": list(range(7))}
    normal, large = zone.rates
    assert normal.vehicle == VehicleType.NORMAL_HEAVY
    assert large.vehicle == VehicleType.LARGE_HEAVY and large.parsed.base_amount == 30
    assert large.parsed.unit_minutes == 240
    assert "isnowcash" not in rule.evidence


@pytest.mark.parametrize(
    "pay,terms",
    [("免費", "30元/時"), ("計次收費", "30元/時"), ("計時收費", "20元/次"), ("計時收費", "免費"), ("", "30元/時")],
)
def test_contradictory_or_unknown_charging_mode_has_no_confirmed_posted_price(pay, terms):
    posted = ADAPTER.normalize_static(motor(pay=pay, paycash=terms)).zones[0].rates[0]
    assert posted.parsed.parse_status != "PARSED"
    assert posted.parsed.base_amount is None and posted.parsed.rate_type is None
    assert posted.parsed.raw_text == terms and posted.raw_payload["pay"] == pay


def test_long_display_name_preserves_full_identity_address_and_category():
    record = motor(roadname="路" * 100, name="類" * 64, cellid="格" * 64)
    lot = ADAPTER.normalize_static(record)
    assert len(lot.name) <= 200 and "…" in lot.name
    assert lot.name.endswith(f"{record['name']} {record['cellid']}")
    assert lot.address == record["roadname"] and lot.external_id == record["id"]


@pytest.mark.parametrize(
    "override",
    [
        {"pay": "免費", "isnowcash": "true"},
        {"pay": "", "paycash": "30元/4小時"},
        {"name": "身障機車停車位"},
        {"name": "時段性禁停停車位"},
        {"memo": "夜間禁停"},
        {"memo": None},
        {"countycode": "63000"},
    ],
)
def test_nonstandard_or_unknown_scope_has_no_policy_grant(override):
    assert ADAPTER.normalize_static(motor(**override)).zones[0].rules == ()
    if "memo" in override:
        assert ADAPTER.normalize_static(motor(**override)).zones[0].normal_heavy is None


@pytest.mark.parametrize("code", ["0", "1", "2", "3", None, "garbage"])
def test_unverified_parking_status_never_produces_numeric_observation(code):
    observation = ADAPTER.normalize_realtime(motor(parkingstatus=code)).observations[0]
    assert observation.available is observation.total is None
    assert observation.status == "UNKNOWN"


@pytest.mark.parametrize("override", [{"latitude": "0"}, {"longitude": True}, {"id": ""}, {"cellid": None}])
def test_invalid_identity_or_coordinates_fail_record(override):
    with pytest.raises(RecordError):
        ADAPTER.normalize_static(motor(**override))


@pytest.mark.parametrize(
    "day,hour,expected",
    [
        (
            "週一-週五",
            "07:00-20:00",
            {"timezone": "Asia/Taipei", "weekdays": [0, 1, 2, 3, 4], "start_time": "07:00", "end_time": "20:00"},
        ),
        (
            "週一-週六",
            "23:00-02:00",
            {"timezone": "Asia/Taipei", "weekdays": [0, 1, 2, 3, 4, 5], "start_time": "23:00", "end_time": "02:00"},
        ),
        ("平日", "07:00-20:00", None),
        ("每天", "7:00-20:00", None),
        ("每天", "00:00-00:00", None),
        ("每天", "24小時", None),
    ],
)
def test_schedule_exact_terms_only(day, hour, expected):
    assert charging_schedule({"day": day, "hour": hour}) == expected
