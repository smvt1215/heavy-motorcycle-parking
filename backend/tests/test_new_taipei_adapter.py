"""New Taipei adapter: pure normalization of reduced official records and synthetic edge cases."""

import json
from pathlib import Path

import pytest

from app.ingestion.contracts import RecordError
from app.ingestion.new_taipei import NewTaipeiParkingAdapter
from app.ingestion.sources import NEW_TAIPEI
from app.models.enums import ParkingSpaceType, RateParseStatus, RateType, RealtimeStatus, VehicleType

FIXTURES = Path(__file__).parent / "fixtures" / "new_taipei"
ADAPTER = NewTaipeiParkingAdapter()


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def static_records():
    return {record["ID"]: record for record in ADAPTER.records(load("static_sample.json"))}


def lot(external_id="110014", **overrides):
    return ADAPTER.normalize_static({**static_records()[external_id], **overrides})


def zones(normalized):
    return {zone.key: zone for zone in normalized.zones}


def test_source_policy_is_independent_from_taipei():
    assert NEW_TAIPEI.key == "new_taipei" and NEW_TAIPEI.city_name == "新北市"
    static, realtime = NEW_TAIPEI.feeds["static"], NEW_TAIPEI.feeds["realtime"]
    assert static.code == "NEW_TAIPEI_STATIC" and realtime.code == "NEW_TAIPEI_REALTIME"
    assert static.page_size == realtime.page_size == 1000
    # No upstream update time exists, so freshness can only be fetch-based.
    assert realtime.freshness_uses_source_timestamp is False
    assert static.reconcile_min_ratio == 0.8
    assert ADAPTER.source is NEW_TAIPEI


def test_envelope_accepts_paged_or_plain_array_and_has_no_source_time():
    payload = load("static_sample.json")
    records = ADAPTER.records(payload)
    assert len(records) == 6
    assert ADAPTER.records(payload["pages"][0]) == records
    assert ADAPTER.records({"page_size": 1000, "pages": [[{"ID": "a"}], [{"ID": "b"}], []]}) == [
        {"ID": "a"},
        {"ID": "b"},
    ]
    assert ADAPTER.source_updated_at(payload) is None
    for bad in ({}, {"pages": {}}, {"pages": [{}]}, "x", None):
        with pytest.raises(RecordError) as error:
            ADAPTER.records(bad)
        assert error.value.code == "INVALID_ENVELOPE"


def test_official_lot_maps_center_district_and_car_zone_without_heavy_permission():
    normalized = lot("010056")
    assert normalized.external_id == "010056" and normalized.name == "遠東百貨停車場"
    assert normalized.district == "板橋區" and normalized.address == "板橋區中山路一段152號"
    assert 24.9 < normalized.lat < 25.1 and 121.4 < normalized.lng < 121.5
    assert normalized.entrances == ()
    car = zones(normalized)["car"]
    assert set(zones(normalized)) == {"car"}  # TOTALMOTOR=0 never becomes NOT_ALLOWED
    assert car.space_type == ParkingSpaceType.CAR_SHARED and car.capacity == 453
    assert car.car is True and car.yellow is None and car.red is None


def test_heavy_fee_creates_unknown_heavy_zone_with_vehicle_rates():
    normalized = lot("110014")
    heavy = zones(normalized)["heavy"]
    assert heavy.space_type == ParkingSpaceType.HEAVY_ONLY and heavy.capacity is None
    # A fee never grants legality: every permission stays NULL.
    assert (heavy.green, heavy.white, heavy.yellow, heavy.red, heavy.car) == (None,) * 5
    by_text = {}
    for rate in heavy.rates:
        by_text.setdefault(rate.parsed.raw_text, set()).add(rate.vehicle)
    assert by_text["重型機車計時20元"] == {VehicleType.YELLOW, VehicleType.RED}
    assert by_text["重型機車月租1500元"] == {VehicleType.YELLOW, VehicleType.RED}
    assert by_text[static_records()["110014"]["PAYEX"]] == {None}
    hourly = next(r for r in heavy.rates if r.parsed.raw_text == "重型機車計時20元")
    # 計時 states no time unit, so no hourly comparison value is guessed.
    assert hourly.parsed.parse_status == RateParseStatus.PARTIALLY_PARSED and hourly.parsed.base_amount is None
    monthly = next(r for r in heavy.rates if r.parsed.raw_text == "重型機車月租1500元")
    assert monthly.parsed.parse_status == RateParseStatus.PARSED
    assert monthly.parsed.rate_type == RateType.MONTHLY and str(monthly.parsed.base_amount) == "1500"


def test_motor_labels_never_leak_into_heavy_or_car_zones():
    normalized = lot("010152")
    by_zone = {key: {(r.vehicle, r.parsed.raw_text) for r in zone.rates} for key, zone in zones(normalized).items()}
    assert (VehicleType.GREEN, "機車計次10元") in by_zone["motor"]
    assert not any(text.startswith("機車") for _, text in by_zone["heavy"] if _ is not None)
    assert not any(text.startswith("重型機車") for vehicle, text in by_zone["motor"] if vehicle is not None)
    assert all(vehicle in (VehicleType.CAR, None) for vehicle, _ in by_zone["car"])
    motor = zones(normalized)["motor"]
    assert motor.green is motor.white is True and motor.yellow is None and motor.red is None


def test_free_motor_fee_is_parsed_for_green_white_only():
    motor = zones(lot("010189"))["motor"]
    assert motor.capacity == 50
    free = [r for r in motor.rates if r.vehicle is not None]
    assert {r.vehicle for r in free} == {VehicleType.GREEN, VehicleType.WHITE}
    assert all(r.parsed.rate_type == RateType.FREE for r in free)


@pytest.mark.parametrize(
    ("payex", "expected_vehicle_rates"),
    [
        ("身障車月租1650元;大型車計時50元;", set()),
        ("每小時30元", set()),
        ("小型車計時50元;小型車計時50元;", {"小型車計時50元"}),
        ("小型車計時50元；小型車計時60元；", {"小型車計時50元", "小型車計時60元"}),
    ],
)
def test_unsupported_labels_and_duplicate_segments(payex, expected_vehicle_rates):
    car = zones(lot("010056", PAYEX=payex))["car"]
    assert {r.parsed.raw_text for r in car.rates if r.vehicle is not None} == expected_vehicle_rates
    assert sum(r.vehicle is None for r in car.rates) == 1


def test_missing_counts_use_fee_channels_and_zero_counts_win():
    missing = lot("110052", TOTALCAR="", TOTALMOTOR=None)
    assert zones(missing)["car"].capacity is None and zones(missing)["car"].car is None
    assert zones(missing)["motor"].capacity is None and zones(missing)["motor"].green is None
    zero = lot("110052", TOTALCAR="0", TOTALMOTOR="0")
    assert set(zones(zero)) == {"heavy"}
    nothing = lot("010056", TOTALCAR="0", PAYEX="")
    assert nothing.zones == ()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"ID": " "}, "MISSING_ID"),
        ({"NAME": None}, "MISSING_NAME"),
        ({"NAME": 7}, "INVALID_NAME"),
        ({"AREA": "區" * 65}, "INVALID_AREA"),
        ({"TOTALCAR": "-1"}, "INVALID_CAPACITY"),
        ({"TOTALCAR": "1.5"}, "INVALID_CAPACITY"),
        ({"TOTALMOTOR": True}, "INVALID_CAPACITY"),
        ({"TW97X": "0", "TW97Y": "0"}, "INVALID_COORDINATES"),
        ({"TW97X": ""}, "INVALID_COORDINATES"),
    ],
)
def test_invalid_static_fields_fail_only_the_record(overrides, code):
    with pytest.raises(RecordError) as error:
        lot("110014", **overrides)
    assert error.value.code == code


def test_non_object_record_fails():
    with pytest.raises(RecordError) as error:
        ADAPTER.normalize_static(["010056"])
    assert error.value.code == "INVALID_RECORD"


@pytest.mark.parametrize(
    ("value", "status", "available"),
    [
        ("18", RealtimeStatus.AVAILABLE, 18),
        ("0", RealtimeStatus.FULL, 0),
        (5, RealtimeStatus.AVAILABLE, 5),
        ("-9", RealtimeStatus.UNKNOWN, None),
        (-9, RealtimeStatus.UNKNOWN, None),
        ("", RealtimeStatus.UNKNOWN, None),
        (None, RealtimeStatus.UNKNOWN, None),
    ],
)
def test_realtime_car_only_with_sentinel(value, status, available):
    record = {"ID": "110014"} if value is None else {"ID": "110014", "AVAILABLECAR": value}
    normalized = ADAPTER.normalize_realtime(record)
    assert normalized.external_id == "110014"
    (observation,) = normalized.observations
    assert observation.zone_key == "car" and observation.total is None
    assert (observation.status, observation.available) == (status, available)


@pytest.mark.parametrize("value", ["-1", "1.0", "abc", True, 2.5])
def test_invalid_realtime_value_fails_record(value):
    with pytest.raises(RecordError) as error:
        ADAPTER.normalize_realtime({"ID": "110014", "AVAILABLECAR": value})
    assert error.value.code == "INVALID_AVAILABILITY"


def test_record_key_and_raw_rate_text_never_raise():
    assert ADAPTER.record_key({"ID": " 010056 "}) == "010056"
    assert ADAPTER.record_key({"id": "x"}) is None and ADAPTER.record_key("x") is None
    assert ADAPTER.raw_rate_text({"PAYEX": "小型車計時60元;"}) == "小型車計時60元;"
    assert ADAPTER.raw_rate_text({"PAYEX": 1}) is None and ADAPTER.raw_rate_text(None) is None


def test_fixture_realtime_records_normalize():
    records = ADAPTER.records(load("realtime_sample.json"))
    statuses = {r["ID"]: ADAPTER.normalize_realtime(r).observations[0].status for r in records}
    assert statuses["010056"] == RealtimeStatus.UNKNOWN and statuses["110052"] == RealtimeStatus.AVAILABLE
