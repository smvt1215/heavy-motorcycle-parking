"""Taipei TCMSV V2 adapter. TPE0003 mirrors a real official row; TPE9xxx rows are synthetic boundaries."""

import copy
import json
import math
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pyproj import Transformer

from app.ingestion.contracts import RecordError
from app.ingestion.taipei import TAIPEI_TZ, TaipeiParkingAdapter, parse_updatetime
from app.models.enums import ParkingSpaceType, RateParseStatus, RateType, RealtimeStatus, VehicleType

FIXTURES = Path(__file__).parent / "fixtures" / "taipei"
ADAPTER = TaipeiParkingAdapter()
YELLOW_RED = {VehicleType.LARGE_HEAVY}
PAYEX_0003 = "小型車：計時 30元/時，全程以半小時計，全日雙月票11,520元。"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def static_payload():
    return _load("alldesc_sample.json")


@pytest.fixture(scope="module")
def realtime_payload():
    return _load("allavailable_sample.json")


def _record(payload, external_id):
    return copy.deepcopy(next(r for r in payload["data"]["park"] if isinstance(r, dict) and r.get("id") == external_id))


def _zones(lot):
    return {zone.key: zone for zone in lot.zones}


def _fares(zone):
    return [rate for rate in zone.rates if rate.key.startswith("fare:")]


def _all_rates(lot):
    return [rate for zone in lot.zones for rate in zone.rates]


# --- envelope and timestamps -------------------------------------------------


def test_records_returns_park_rows(static_payload, realtime_payload):
    assert len(ADAPTER.records(static_payload)) == 7
    assert len(ADAPTER.records(realtime_payload)) == 6


@pytest.mark.parametrize(
    "payload", [None, [], "x", {}, {"data": []}, {"data": {}}, {"data": {"park": {}}}, {"data": {"park": None}}]
)
def test_bad_envelope_raises_record_error(payload):
    with pytest.raises(RecordError) as exc:
        ADAPTER.records(payload)
    assert exc.value.code == "INVALID_ENVELOPE"


def test_updatetime_cst_is_asia_taipei_not_us_central(static_payload):
    updated = ADAPTER.source_updated_at(static_payload)
    assert updated == datetime(2026, 10, 6, 0, 2, tzinfo=TAIPEI_TZ)
    assert updated.utcoffset() == timedelta(hours=8)
    assert updated.astimezone(UTC) == datetime(2026, 10, 5, 16, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        12345,
        "Wed Oct 06 00:02:00 CST 2026",
        "Tue Oct 06 00:02:00 EST 2026",
        "Tue Oct 06 00:02:00 2026",
        "Tue Oct 32 00:02:00 CST 2026",
        "Tue Oct 06 24:02:00 CST 2026",
        "tue oct 06 00:02:00 CST 2026",
        "2026-10-06 00:02:00",
        " Tue Oct 06 00:02:00 CST 2026",
    ],
)
def test_bad_updatetime_is_none(value):
    assert parse_updatetime(value) is None
    assert ADAPTER.source_updated_at({"data": {"UPDATETIME": value, "park": []}}) is None


@pytest.mark.parametrize("payload", [None, [], {"data": None}, {"data": {"park": []}}])
def test_missing_updatetime_is_none(payload):
    assert ADAPTER.source_updated_at(payload) is None


# --- real row TPE0003 -------------------------------------------------------------


def test_real_row_lot_fields_and_projection(static_payload):
    record = _record(static_payload, "TPE0003")
    lot = ADAPTER.normalize_static(record)
    assert (lot.external_id, lot.name, lot.district, lot.address) == (
        "TPE0003",
        "民生立體停車場",
        "松山區",
        "民生東路5段84號",
    )
    expected_lng, expected_lat = Transformer.from_crs(3826, 4326, always_xy=True).transform(306457.778, 2772361.517)
    assert math.isclose(lot.lat, expected_lat, abs_tol=1e-9)
    assert math.isclose(lot.lng, expected_lng, abs_tol=1e-9)
    # Not swapped: Songshan District is around 25.06N, 121.56E.
    assert 25.03 < lot.lat < 25.09
    assert 121.53 < lot.lng < 121.59


def test_real_row_center_is_not_entrance_and_entrance_access_unknown(static_payload):
    lot = ADAPTER.normalize_static(_record(static_payload, "TPE0003"))
    (entrance,) = lot.entrances
    assert (entrance.lat, entrance.lng, entrance.name) == (25.0585, 121.5596, "入口")
    assert entrance.heavy_access is None
    assert (lot.lat, lot.lng) != (entrance.lat, entrance.lng)


def test_real_row_zones_and_permissions(static_payload):
    lot = ADAPTER.normalize_static(_record(static_payload, "TPE0003"))
    zones = _zones(lot)
    assert list(zones) == ["car", "heavy"]  # totalmotor 0 omits the motor zone

    car = zones["car"]
    assert (car.space_type, car.capacity, car.car) == (ParkingSpaceType.CAR_SHARED, 457, True)
    assert (car.green, car.white, car.yellow, car.red) == (None, None, None, None)

    heavy = zones["heavy"]
    assert (heavy.space_type, heavy.capacity) == (ParkingSpaceType.HEAVY_ONLY, 3)
    assert (heavy.yellow, heavy.red) == (True, True)
    assert (heavy.green, heavy.white, heavy.car) == (None, None, None)


def test_real_row_structured_fare_is_partial_and_car_only(static_payload):
    record = _record(static_payload, "TPE0003")
    zones = _zones(ADAPTER.normalize_static(record))
    (fare,) = _fares(zones["car"])
    assert fare.vehicle is VehicleType.CAR
    assert fare.parsed.parse_status is RateParseStatus.PARTIALLY_PARSED
    assert fare.parsed.rate_type is RateType.TIME_BLOCK
    assert fare.parsed.base_amount == Decimal("30")
    assert fare.parsed.unit_minutes is None  # CUnit is undocumented: no unit asserted
    assert fare.parsed.daily_max_amount is None
    assert fare.parsed.rules == ()  # 00-24 is unrestricted
    assert fare.parsed.raw_text == PAYEX_0003
    assert fare.raw_payload["fare_rule"] == record["FareInfo"]["FareRule"][0]
    assert fare.raw_payload["payex"] == PAYEX_0003

    # C never maps to heavy vehicles; the heavy zone only sees unattributed payex.
    assert [rate.vehicle for rate in zones["heavy"].rates] == [None]


def test_real_row_payex_is_raw_only_unattributed_on_every_zone(static_payload):
    lot = ADAPTER.normalize_static(_record(static_payload, "TPE0003"))
    for zone in lot.zones:
        payex_rates = [rate for rate in zone.rates if rate.key.startswith("payex:")]
        assert len(payex_rates) == 1
        (rate,) = payex_rates
        assert rate.vehicle is None
        assert rate.parsed.parse_status is RateParseStatus.PARTIALLY_PARSED
        assert rate.parsed.raw_text == PAYEX_0003
        assert rate.parsed.base_amount is None
        assert rate.raw_payload == {"source_field": "payex", "payex": PAYEX_0003}
    assert all(rate.parsed.parse_status is not RateParseStatus.PARSED for rate in _all_rates(lot))


def test_simple_unlabelled_payex_is_parsed_without_guessing_vehicle(static_payload):
    record = _record(static_payload, "TPE0003")
    record["payex"] = "每小時30元"
    lot = ADAPTER.normalize_static(record)
    for zone in lot.zones:
        rate = next(rate for rate in zone.rates if rate.key.startswith("payex:"))
        assert rate.vehicle is None and rate.parsed.parse_status is RateParseStatus.PARSED
        assert rate.parsed.base_amount == Decimal("30")


@pytest.mark.parametrize(
    "field,value",
    [("id", "x" * 241), ("name", "x" * 201), ("area", "x" * 65), ("totalcar", "9" * 5000), ("tw97x", 10**500)],
)
def test_storage_bounds_are_record_failures(static_payload, field, value):
    record = _record(static_payload, "TPE0003")
    record[field] = value
    with pytest.raises(RecordError):
        ADAPTER.normalize_static(record)


# --- synthetic channels, duplicates, windows ---------------------------------------


def test_channel_mapping_dedup_and_conflicts(static_payload):
    lot = ADAPTER.normalize_static(_record(static_payload, "TPE9001"))
    zones = _zones(lot)
    assert list(zones) == ["car", "motor", "heavy"]
    assert (zones["car"].capacity, zones["motor"].capacity, zones["heavy"].capacity) == (20, 40, 5)
    assert (zones["motor"].green, zones["motor"].white, zones["motor"].yellow) == (True, True, None)

    car_fares = _fares(zones["car"])
    assert {rate.vehicle for rate in car_fares} == {VehicleType.CAR}
    timed = sorted(r.parsed.base_amount for r in car_fares if r.parsed.rate_type is RateType.TIME_BLOCK)
    assert timed == [Decimal("40"), Decimal("50")]  # exact duplicate removed, conflicting fare kept
    assert len({rate.key for rate in zones["car"].rates}) == len(zones["car"].rates)

    motor_fares = _fares(zones["motor"])
    assert {rate.vehicle for rate in motor_fares} == {VehicleType.NORMAL_HEAVY, VehicleType.NORMAL_HEAVY}

    heavy_fares = _fares(zones["heavy"])
    assert {rate.vehicle for rate in heavy_fares} == YELLOW_RED
    assert len(heavy_fares) == 3  # One class rate each: CM, HM monthly, HM invalid-window
    assert len({rate.key for rate in zones["heavy"].rates}) == len(zones["heavy"].rates)


def test_bus_and_charging_rules_are_excluded(static_payload):
    lot = ADAPTER.normalize_static(_record(static_payload, "TPE9001"))
    amounts = {rate.parsed.base_amount for rate in _all_rates(lot)}
    assert Decimal("100") not in amounts  # T (bus)
    assert Decimal("8") not in amounts  # RateType 9 (charging)


def test_cm_overnight_window_and_rate_types(static_payload):
    zones = _zones(ADAPTER.normalize_static(_record(static_payload, "TPE9001")))
    (cm_car,) = [r for r in _fares(zones["car"]) if r.raw_payload["fare_rule"]["ParkingType"] == "CM"]
    assert cm_car.parsed.rate_type is RateType.PER_ENTRY
    assert cm_car.parsed.base_amount == Decimal("60")
    (rule,) = cm_car.parsed.rules
    assert (rule.start_time, rule.end_time, rule.amount) == (time(20, 0), time(8, 0), Decimal("60"))

    monthly = [r for r in _fares(zones["heavy"]) if r.parsed.rate_type is RateType.MONTHLY]
    assert {r.vehicle for r in monthly} == YELLOW_RED
    assert all(r.parsed.rules == () and r.parsed.base_amount == Decimal("1500") for r in monthly)


def test_invalid_window_is_invalid_with_raw_evidence(static_payload):
    zones = _zones(ADAPTER.normalize_static(_record(static_payload, "TPE9001")))
    invalid = [r for r in _fares(zones["heavy"]) if r.parsed.parse_status is RateParseStatus.INVALID]
    assert {r.vehicle for r in invalid} == YELLOW_RED
    for rate in invalid:
        assert rate.parsed.base_amount is None and rate.parsed.rate_type is None and rate.parsed.rules == ()
        assert rate.parsed.raw_text == "汽車每小時40元；大型重機每小時20元，假日另計。"
        assert rate.raw_payload["fare_rule"]["ChargeableSTime"] == "25"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("18", "24", (time(18, 0), time(0, 0))),
        ("08:30", "17:00", (time(8, 30), time(17, 0))),
        (7, 19, (time(7, 0), time(19, 0))),
        ("00", "24", None),
    ],
)
def test_chargeable_windows(static_payload, start, end, expected):
    record = _record(static_payload, "TPE0003")
    record["FareInfo"]["FareRule"][0].update(ChargeableSTime=start, ChargeableETime=end)
    (fare,) = _fares(_zones(ADAPTER.normalize_static(record))["car"])
    assert fare.parsed.parse_status is RateParseStatus.PARTIALLY_PARSED
    if expected is None:
        assert fare.parsed.rules == ()
    else:
        (rule,) = fare.parsed.rules
        assert (rule.start_time, rule.end_time) == expected


@pytest.mark.parametrize(
    ("start", "end"), [("08", "08"), ("24", "08"), ("08", ""), ("", "08"), ("8a", "20"), ("24:30", "08"), (True, 8)]
)
def test_invalid_windows_never_abort_lot(static_payload, start, end):
    record = _record(static_payload, "TPE0003")
    record["FareInfo"]["FareRule"][0].update(ChargeableSTime=start, ChargeableETime=end)
    (fare,) = _fares(_zones(ADAPTER.normalize_static(record))["car"])
    assert fare.parsed.parse_status is RateParseStatus.INVALID
    assert fare.parsed.base_amount is None


@pytest.mark.parametrize("value", [-30, "-30", "30.555", "abc", True, float("nan"), 100000000])
def test_invalid_structured_amount_is_invalid(static_payload, value):
    record = _record(static_payload, "TPE0003")
    record["FareInfo"]["FareRule"][0]["ParkingRates"] = value
    (fare,) = _fares(_zones(ADAPTER.normalize_static(record))["car"])
    assert fare.parsed.parse_status is RateParseStatus.INVALID
    assert fare.parsed.base_amount is None


def test_unknown_rate_type_is_raw_only(static_payload):
    record = _record(static_payload, "TPE0003")
    record["FareInfo"]["FareRule"][0]["RateType"] = "42"
    (fare,) = _fares(_zones(ADAPTER.normalize_static(record))["car"])
    assert fare.parsed.parse_status is RateParseStatus.RAW_ONLY
    assert fare.parsed.rate_type is None and fare.parsed.base_amount is None


def test_missing_count_with_channel_creates_unknown_zone(static_payload):
    lot = ADAPTER.normalize_static(_record(static_payload, "TPE9002"))
    zones = _zones(lot)
    assert list(zones) == ["car", "motor"]  # heavy "0" omitted; CM never creates heavy
    motor = zones["motor"]
    assert motor.capacity is None
    assert (motor.green, motor.white, motor.yellow, motor.red, motor.car) == (None,) * 5
    assert {rate.vehicle for rate in motor.rates} == {VehicleType.NORMAL_HEAVY, VehicleType.NORMAL_HEAVY}
    car_rates = zones["car"].rates
    assert [rate.vehicle for rate in car_rates] == [VehicleType.CAR]
    # Empty payex: no unattributed payex rate; structured raw_text falls back to the rule itself.
    assert json.loads(car_rates[0].parsed.raw_text) == car_rates[0].raw_payload["fare_rule"]
    assert car_rates[0].raw_payload["payex"] is None
    assert not any(rate.vehicle in YELLOW_RED for rate in _all_rates(lot))


def test_missing_heavy_count_with_hm_channel_keeps_permission_unknown(static_payload):
    record = _record(static_payload, "TPE0003")
    del record["totallargemotor"]
    record["FareInfo"]["FareRule"].append(
        {"ParkingType": "HM", "RateType": "1", "ChargeableSTime": "00", "ChargeableETime": "24", "ParkingRates": 20}
    )
    heavy = _zones(ADAPTER.normalize_static(record))["heavy"]
    assert heavy.capacity is None
    assert (heavy.yellow, heavy.red) == (None, None)
    assert {rate.vehicle for rate in _fares(heavy)} == YELLOW_RED


def test_generic_channels_never_create_or_permit_heavy(static_payload):
    record = _record(static_payload, "TPE0003")
    del record["totallargemotor"]
    record["FareInfo"]["FareRule"].append(
        {"ParkingType": "CM", "RateType": "1", "ChargeableSTime": "00", "ChargeableETime": "24", "ParkingRates": 40}
    )
    lot = ADAPTER.normalize_static(record)
    assert "heavy" not in _zones(lot)
    assert not any(rate.vehicle in YELLOW_RED for rate in _all_rates(lot))


def test_zero_heavy_count_with_hm_channel_omits_zone(static_payload):
    record = _record(static_payload, "TPE0003")
    record["totallargemotor"] = "0"
    record["FareInfo"]["FareRule"].append({"ParkingType": "HM", "RateType": "1", "ParkingRates": 20})
    assert "heavy" not in _zones(ADAPTER.normalize_static(record))


def test_lot_without_supported_zones_is_kept_without_invented_zone(static_payload):
    lot = ADAPTER.normalize_static(_record(static_payload, "TPE9003"))
    assert lot.zones == ()
    assert lot.entrances == ()
    assert 22 <= lot.lat <= 26.5 and 119 <= lot.lng <= 123


@pytest.mark.parametrize("value", [12, "12", "0012"])
def test_valid_capacity_values(static_payload, value):
    record = _record(static_payload, "TPE0003")
    record["totallargemotor"] = value
    assert _zones(ADAPTER.normalize_static(record))["heavy"].capacity == 12


@pytest.mark.parametrize("value", [True, False, -1, 1.5, 3.0, "1.5", "-1", "１２", " 12", "", "abc", 2**31])
def test_invalid_capacity_is_record_failure(static_payload, value):
    record = _record(static_payload, "TPE0003")
    record["totallargemotor"] = value
    with pytest.raises(RecordError) as exc:
        ADAPTER.normalize_static(record)
    assert exc.value.code == "INVALID_CAPACITY"


def test_fixture_malformed_records(static_payload):
    with pytest.raises(RecordError) as capacity:
        ADAPTER.normalize_static(_record(static_payload, "TPE9004"))
    assert capacity.value.code == "INVALID_CAPACITY"
    with pytest.raises(RecordError) as coords:
        ADAPTER.normalize_static(_record(static_payload, "TPE9005"))
    assert coords.value.code == "INVALID_COORDINATES"
    with pytest.raises(RecordError) as not_record:
        ADAPTER.normalize_static(static_payload["data"]["park"][-1])
    assert not_record.value.code == "INVALID_RECORD"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("id", None, "MISSING_ID"),
        ("id", "  ", "MISSING_ID"),
        ("name", "", "MISSING_NAME"),
        ("tw97x", None, "INVALID_COORDINATES"),
        ("tw97x", "abc", "INVALID_COORDINATES"),
        ("tw97x", True, "INVALID_COORDINATES"),
        ("tw97y", "NaN", "INVALID_COORDINATES"),
        ("tw97y", "9999999", "INVALID_COORDINATES"),
    ],
)
def test_required_lot_fields(static_payload, field, value, code):
    record = _record(static_payload, "TPE0003")
    record[field] = value
    with pytest.raises(RecordError) as exc:
        ADAPTER.normalize_static(record)
    assert exc.value.code == code


def test_coordinate_fields_are_not_swapped_or_replaced_by_entrance(static_payload):
    record = _record(static_payload, "TPE0003")
    record["tw97x"], record["tw97y"] = record["tw97y"], record["tw97x"]
    with pytest.raises(RecordError) as exc:
        ADAPTER.normalize_static(record)
    assert exc.value.code == "INVALID_COORDINATES"


def test_invalid_entrances_are_kept_with_null_coordinates(static_payload):
    lot = ADAPTER.normalize_static(_record(static_payload, "TPE9001"))
    by_name = {entrance.name: entrance for entrance in lot.entrances}
    assert (by_name["主入口"].lat, by_name["主入口"].lng) == (25.033, 121.543)
    assert (by_name["後門"].lat, by_name["後門"].lng) == (None, None)
    assert (by_name["欄位對調"].lat, by_name["欄位對調"].lng) == (None, None)
    assert all(entrance.heavy_access is None for entrance in lot.entrances)
    assert len({entrance.key for entrance in lot.entrances}) == 3


@pytest.mark.parametrize("value", [None, "bad", [], {"EntrancecoordInfo": "bad"}])
def test_malformed_entrance_container_does_not_abort_lot(static_payload, value):
    record = _record(static_payload, "TPE0003")
    record["EntranceCoord"] = value
    assert ADAPTER.normalize_static(record).entrances == ()


def test_duplicate_entrances_are_deduplicated(static_payload):
    record = _record(static_payload, "TPE0003")
    info = record["EntranceCoord"]["EntrancecoordInfo"]
    info.append(dict(info[0]))
    assert len(ADAPTER.normalize_static(record).entrances) == 1


def test_normalization_is_deterministic_and_order_independent(static_payload):
    record = _record(static_payload, "TPE9001")
    first = ADAPTER.normalize_static(record)
    assert ADAPTER.normalize_static(copy.deepcopy(record)) == first
    reordered = copy.deepcopy(record)
    reordered["FareInfo"]["FareRule"].reverse()
    keys = {(zone.key, rate.key) for zone in first.zones for rate in zone.rates}
    assert {(z.key, r.key) for z in ADAPTER.normalize_static(reordered).zones for r in z.rates} == keys


def test_record_key(static_payload):
    assert ADAPTER.record_key(_record(static_payload, "TPE0003")) == "TPE0003"
    assert ADAPTER.record_key("not-a-record") is None
    assert ADAPTER.record_key({"id": 3}) is None


# --- realtime --------------------------------------------------------------------


def _observations(realtime):
    return {obs.zone_key: obs for obs in realtime.observations}


def test_real_realtime_row(realtime_payload):
    realtime = ADAPTER.normalize_realtime(_record(realtime_payload, "TPE0003"))
    assert realtime.external_id == "TPE0003"
    obs = _observations(realtime)
    assert (obs["car"].status, obs["car"].available) == (RealtimeStatus.AVAILABLE, 15)
    assert (obs["motor"].status, obs["motor"].available) == (RealtimeStatus.UNKNOWN, None)
    assert (obs["heavy"].status, obs["heavy"].available) == (RealtimeStatus.AVAILABLE, 2)
    assert all(o.total is None for o in realtime.observations)


def test_realtime_strings_zero_and_bus_ignored(realtime_payload):
    obs = _observations(ADAPTER.normalize_realtime(_record(realtime_payload, "TPE9001")))
    assert set(obs) == {"car", "motor", "heavy"}
    assert (obs["car"].status, obs["car"].available) == (RealtimeStatus.FULL, 0)
    assert (obs["motor"].status, obs["motor"].available) == (RealtimeStatus.AVAILABLE, 12)
    assert (obs["heavy"].status, obs["heavy"].available) == (RealtimeStatus.UNKNOWN, None)


def test_realtime_missing_and_sentinel_are_unknown(realtime_payload):
    obs = _observations(ADAPTER.normalize_realtime(_record(realtime_payload, "TPE9002")))
    assert all(o.status is RealtimeStatus.UNKNOWN and o.available is None for o in obs.values())


def test_heavy_never_derived_from_generic_counts():
    obs = _observations(ADAPTER.normalize_realtime({"id": "X", "availablecar": 10, "availablemotor": 10}))
    assert (obs["heavy"].status, obs["heavy"].available) == (RealtimeStatus.UNKNOWN, None)


@pytest.mark.parametrize("external_id", ["TPE9007", "TPE9008", "TPE9009"])
def test_fixture_invalid_realtime_rows(realtime_payload, external_id):
    with pytest.raises(RecordError) as exc:
        ADAPTER.normalize_realtime(_record(realtime_payload, external_id))
    assert exc.value.code == "INVALID_AVAILABILITY"


@pytest.mark.parametrize("value", [1.5, 2.0, -9.0, True, False, -1, -10, "-1", "1.0", "", " 3", "abc", "３"])
def test_invalid_realtime_values(value):
    with pytest.raises(RecordError) as exc:
        ADAPTER.normalize_realtime({"id": "X", "availableheavymotor": value})
    assert exc.value.code == "INVALID_AVAILABILITY"


@pytest.mark.parametrize("record", [None, "x", [], {"availablecar": 1}, {"id": ""}])
def test_invalid_realtime_records(record):
    with pytest.raises(RecordError):
        ADAPTER.normalize_realtime(record)


def test_realtime_never_closed(realtime_payload):
    for external_id in ("TPE0003", "TPE9001", "TPE9002"):
        realtime = ADAPTER.normalize_realtime(_record(realtime_payload, external_id))
        assert RealtimeStatus.CLOSED not in {o.status for o in realtime.observations}
