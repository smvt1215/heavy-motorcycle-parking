"""Public wire contracts and endpoint orchestration with explicit preloaded facts."""

import base64
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.discovery import EntranceFact, LotSnapshot, RateFact, RateRuleFact, RealtimeFact, ZoneSnapshot
from app.domain.errors import DiscoveryError
from app.domain.parking import ParkingFacts, Provenance, RuleFact, ZoneFacts
from app.main import create_app
from app.routers.parking import get_discovery_service
from app.schemas.parking import NearbyQuery
from app.services.cursors import CursorCodec, query_fingerprint
from app.services.discovery import ParkingDiscoveryService

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
KEY = b"m3-test-signing-key-with-32-bytes!!"
BASE_FIELDS = {"zone_id", "name", "space_type", "capacity", "compatibility", "rate_summary", "availability"}


def source(source_id=1, **fields):
    return Provenance(source_id, "GOVERNMENT", fetched_at=NOW, **fields)


def make_lot(parking_id=1, permissions=(True,), space_types=None, distance=0, **rule_fields):
    space_types = space_types or ("HEAVY_ONLY",) * len(permissions)
    rules = []
    zones = []
    for index, (permission, kind) in enumerate(zip(permissions, space_types, strict=True)):
        zone_id = parking_id * 100 + index
        zones.append(ZoneSnapshot(ZoneFacts(zone_id, parking_id, kind), capacity=20))
        rules.append(
            RuleFact(
                zone_id,
                parking_id,
                zone_id,
                "BASELINE",
                1,
                source(),
                large_heavy_allowed=permission,
                normal_heavy_allowed=permission,
                **rule_fields,
            )
        )
    return LotSnapshot(
        ParkingFacts(parking_id, tuple(rules)), f"Lot {parking_id}", 25.03, 121.56, tuple(zones), distance_m=distance
    )


class MemoryRepository:
    def __init__(self, lots=()):
        self.lots = lots
        self.queries = []

    async def nearby(self, lat, lng, radius):
        self.queries.append((lat, lng, radius))
        return self.lots

    async def detail(self, parking_id):
        return next((lot for lot in self.lots if lot.facts.parking_id == parking_id), None)


@pytest.fixture
async def api():
    app = create_app()
    app.state.clock = lambda: NOW
    repository = MemoryRepository()
    app.dependency_overrides[get_discovery_service] = lambda: ParkingDiscoveryService(repository, CursorCodec(KEY))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        yield client, repository, app


def query(**fields):
    return {"lat": 25.03, "lng": 121.56, "vehicle": "LARGE_HEAVY", **fields}


@pytest.mark.parametrize("vehicle", ["LARGE_HEAVY", "NORMAL_HEAVY"])
async def test_nearby_tristate_rollup_and_light_exclusion(api, vehicle):
    client, repository, _ = api
    repository.lots = (
        make_lot(1, (True, None, False)),
        make_lot(2, (None,)),
        make_lot(3, (False,)),
        make_lot(4, (True,), ("LIGHT_MOTO_ONLY",)),
    )
    default = (await client.get("/api/v1/parking/nearby", params=query(vehicle=vehicle))).json()
    assert {lot["id"] for lot in default["items"]} == ({1} if vehicle == "LARGE_HEAVY" else {1, 4})
    assert [zone["compatibility"]["status"] for zone in default["items"][0]["zones"]] == ["ALLOWED"]
    included = (await client.get("/api/v1/parking/nearby", params=query(vehicle=vehicle, include_unknown=True))).json()
    assert {lot["id"] for lot in included["items"]} == ({1, 2} if vehicle == "LARGE_HEAVY" else {1, 2, 4})
    assert included["items"][0]["compatibility"]["status"] == "ALLOWED"
    assert [zone["compatibility"]["status"] for zone in included["items"][0]["zones"]] == ["ALLOWED", "UNKNOWN"]
    assert included["items"][-1]["compatibility"]["status"] == "UNKNOWN"
    assert included["items"][-1]["ranking_score_bp"] is None
    assert repository.queries[0] == (25.03, 121.56, 1500)


@pytest.mark.parametrize("suffix", ["", "/rates", "/realtime"])
async def test_specialized_zones_have_same_base_and_zone_scoped_evidence(api, suffix):
    client, repository, _ = api
    lot = make_lot(1, (True, None))
    rate = RateFact(7, 100, "LARGE_HEAVY", "HOURLY", "PARSED", source(2), base_amount=Decimal(20), unit_minutes=60)
    rt = RealtimeFact(8, 100, "AVAILABLE", 3, 10, source(3))
    entrance = EntranceFact(9, "Entry", 25.031, 121.561, "UNKNOWN", source(4))
    repository.lots = (
        replace(lot, zones=(replace(lot.zones[0], rates=(rate,), realtime=rt), lot.zones[1]), entrances=(entrance,)),
    )
    response = await client.get(f"/api/v1/parking/1{suffix}", params={"vehicle": "LARGE_HEAVY", "at": NOW.isoformat()})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["evaluation_at"] == "2026-10-05T04:00:00Z"
    first, other = data["zones"]
    assert first.keys() >= BASE_FIELDS and other.keys() >= BASE_FIELDS
    assert first["compatibility"]["provenance"]["source_id"] == 1
    assert first["rate_summary"]["provenance"]["source_id"] == 2
    assert type(first["rate_summary"]["comparison_hourly_rate_twd"]) is int
    assert first["availability"]["provenance"]["source_id"] == 3
    assert first["availability"]["provenance"]["fetched_at"] is not None
    assert other["availability"] is None and other["rate_summary"] is None
    assert other["compatibility"]["status"] == "UNKNOWN"
    if suffix == "/rates":
        assert first["rates"][0]["rate_id"] == 7 and other["rates"] == []
    if not suffix:
        assert data["location"] == {"lat": 25.03, "lng": 121.56}
        assert data["entrances"][0]["location"] != data["location"]
        assert data["entrances"][0]["heavy_motorcycle_access"] == "UNKNOWN"
        assert data["entrances"][0]["provenance"]["source_id"] == 4


@pytest.mark.parametrize("permissions", [(True, False), (True, None), (False, None), (None, None)])
async def test_highest_rule_ties_preserve_every_source_in_api(api, permissions):
    client, repository, _ = api
    lot = make_lot()
    top = tuple(
        RuleFact(i + 10, 1, 100, "EXCEPTION", 100, source(i + 2), large_heavy_allowed=permission)
        for i, permission in enumerate(permissions)
    )
    repository.lots = (replace(lot, facts=replace(lot.facts, rules=lot.facts.rules + top)),)
    data = (await client.get("/api/v1/parking/1", params={"vehicle": "LARGE_HEAVY"})).json()["zones"][0]
    assert data["compatibility"]["status"] == "UNKNOWN"
    assert data["compatibility"]["provenance"] is None
    assert [record["provenance"]["source_id"] for record in data["compatibility"]["rule_evidence"]] == [2, 3]


@pytest.mark.parametrize("suffix", ["", "/rates", "/realtime"])
async def test_selected_time_pins_rules_but_not_realtime_freshness(api, suffix):
    client, repository, _ = api
    past = NOW - timedelta(days=3)
    lot = make_lot(effective_from=NOW - timedelta(days=1))
    realtime = RealtimeFact(1, 100, "AVAILABLE", 3, 20, source())
    repository.lots = (replace(lot, zones=(replace(lot.zones[0], realtime=realtime),)),)
    data = (
        await client.get(f"/api/v1/parking/1{suffix}", params={"vehicle": "LARGE_HEAVY", "at": past.isoformat()})
    ).json()
    assert data["evaluation_at"] == "2026-10-02T04:00:00Z"
    assert data["zones"][0]["compatibility"]["reason"] == "space_type_default"
    assert data["zones"][0]["availability"]["freshness"]["status"] == "FRESH"


async def test_cursor_walk_equal_ties_pins_time_and_crosses_unknown_group(api):
    client, repository, app = api
    repository.lots = tuple(make_lot(i, (True if i < 6 else None,), distance=100) for i in range(8, 0, -1))
    params = query(limit=2, include_unknown=True)
    seen = []
    for page_index in range(4):
        response = await client.get("/api/v1/parking/nearby", params=params)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["evaluation_at"] == "2026-10-05T04:00:00Z"
        seen.extend(item["id"] for item in data["items"])
        assert data["page"]["has_more"] == (page_index < 3)
        params["cursor"] = data["page"]["next_cursor"]
        app.state.clock = lambda: NOW + timedelta(seconds=30)
    assert seen == list(range(1, 9))
    assert params["cursor"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"lat": 25.04},
        {"radius": 1000},
        {"vehicle": "NORMAL_HEAVY"},
        {"include_unknown": True},
        {"limit": 2},
        {"at": (NOW + timedelta(seconds=1)).isoformat()},
    ],
)
async def test_cursor_query_mismatch_never_restarts(api, change):
    client, repository, _ = api
    repository.lots = (make_lot(1), make_lot(2))
    params = query(limit=1)
    first = (await client.get("/api/v1/parking/nearby", params=params)).json()
    response = await client.get(
        "/api/v1/parking/nearby", params={**params, **change, "cursor": first["page"]["next_cursor"]}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CURSOR_QUERY_MISMATCH"


async def test_equivalent_absolute_at_matches_cursor(api):
    client, repository, _ = api
    repository.lots = (make_lot(1), make_lot(2))
    params = query(limit=1, at=NOW.isoformat())
    first = (await client.get("/api/v1/parking/nearby", params=params)).json()
    response = await client.get(
        "/api/v1/parking/nearby",
        params={**params, "cursor": first["page"]["next_cursor"], "at": "2026-10-05T12:00:00+08:00"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == 2


@pytest.mark.parametrize("token", ["broken", "a.b", "a.b.c", "eyJ2IjoxfQ.invalid"])
async def test_invalid_cursor_stable_error(api, token):
    response = await api[0].get("/api/v1/parking/nearby", params=query(cursor=token))
    assert response.status_code == 400 and response.json()["error"]["code"] == "INVALID_CURSOR"


def signed_payload(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def encode(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return encode(raw) + "." + encode(hmac.digest(KEY, raw, "sha256"))


@pytest.mark.parametrize("field", ["v", "sort"])
def test_cursor_unsupported_version(field):
    payload = {"v": 1, "sort": 1, "query": "fingerprint", "at": NOW.isoformat(), "key": [0, -5000, 20, 1]}
    payload[field] = 2
    with pytest.raises(DiscoveryError) as exc:
        CursorCodec(KEY).decode(signed_payload(payload), "fingerprint")
    assert exc.value.code == "CURSOR_VERSION_UNSUPPORTED"


@pytest.mark.parametrize(
    "key", [[True, -1, 0, 1], [0, 1, 0, 1], [1, -1, 1], [0, -10001, 0, 1], [0, -1, 0, 0], [2, 0, 1], [0, -1, 1]]
)
def test_cursor_rejects_invalid_authenticated_sort_keys(key):
    with pytest.raises(DiscoveryError) as exc:
        CursorCodec(KEY).decode(
            signed_payload({"v": 1, "sort": 1, "query": "q", "at": NOW.isoformat(), "key": key}), "q"
        )
    assert exc.value.code == "INVALID_CURSOR"


def test_cursor_tamper_and_foreign_signing_key():
    codec = CursorCodec(KEY)
    token = codec.encode("q", NOW, (0, -5000, 2, 1))
    assert codec.decode(token, "q").key == (0, -5000, 2, 1)
    body, signature = token.split(".")
    for changed in (body + "A." + signature, body + "." + signature[:-1] + ("A" if signature[-1] != "A" else "B")):
        with pytest.raises(DiscoveryError):
            codec.decode(changed, "q")
    with pytest.raises(DiscoveryError):
        CursorCodec(b"different-key-that-also-has-32-bytes").decode(token, "q")


@pytest.mark.parametrize(
    "fields",
    [
        {"vehicle": "GREEN"},
        {"space_type": "LIGHT_MOTO_ONLY"},
        {"lat": 91},
        {"lng": -181},
        {"lat": "nan"},
        {"radius": 0},
        {"radius": 5001},
        {"limit": 101},
        {"limit": 0},
        {"hourly_rate_max_twd": -1},
        {"at": "2026-10-05T12:00:00"},
        {"at": "1234567890"},
        {"price_max": 10},
    ],
)
async def test_nearby_query_validation_error_envelope(api, fields):
    response = await api[0].get("/api/v1/parking/nearby", params=query(**fields))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("path", ["nearby?lat=25&lng=121", "1", "1/rates", "1/realtime"])
async def test_vehicle_required_all_selected_endpoints(api, path):
    response = await api[0].get("/api/v1/parking/" + path)
    assert response.status_code == 422


@pytest.mark.parametrize("suffix", ["", "/rates", "/realtime"])
async def test_unknown_parking_not_found(api, suffix):
    response = await api[0].get(f"/api/v1/parking/999{suffix}", params={"vehicle": "LARGE_HEAVY"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PARKING_NOT_FOUND"


def test_openapi_shared_zone_contract_and_explicit_queries():
    spec = create_app().openapi()
    for suffix in ("nearby", "{parking_id}", "{parking_id}/rates", "{parking_id}/realtime"):
        operation = spec["paths"]["/api/v1/parking/" + suffix]["get"]
        assert next(p for p in operation["parameters"] if p["name"] == "vehicle")["required"] is True
        assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    schemas = spec["components"]["schemas"]
    for name in ("CommonZone", "RatesZone"):
        assert set(schemas[name]["required"]) >= BASE_FIELDS
    assert "rates" in schemas["RatesZone"]["required"]


def test_query_fingerprint_effective_defaults():
    first = NearbyQuery(**query())
    explicit = NearbyQuery(**query(radius=1500, limit=20, include_unknown=False))
    assert query_fingerprint(first.model_dump(exclude={"at", "cursor"})) == query_fingerprint(
        explicit.model_dump(exclude={"at", "cursor"})
    )


async def test_unresolved_rate_rule_blocks_daily_cap_filter_and_marks_raw_evidence(api):
    client, repository, _ = api
    lot = make_lot()
    rate = RateFact(
        7,
        100,
        "LARGE_HEAVY",
        "HOURLY",
        "PARSED",
        source(),
        base_amount=Decimal(20),
        unit_minutes=60,
        daily_max_amount=Decimal(100),
        rules=(RateRuleFact(1, day_type="HOLIDAY"),),
    )
    repository.lots = (replace(lot, zones=(replace(lot.zones[0], rates=(rate,)),)),)
    data = (await client.get("/api/v1/parking/1/rates", params={"vehicle": "LARGE_HEAVY"})).json()
    zone = data["zones"][0]
    assert zone["rate_summary"]["daily_max_twd"] is None
    assert zone["rates"][0]["applicability"] == "UNKNOWN"
    assert (await client.get("/api/v1/parking/nearby", params=query(daily_max_required=True))).json()["items"] == []


@pytest.mark.parametrize("vehicle", ["GREEN", "WHITE", "YELLOW", "RED", "CAR"])
@pytest.mark.parametrize("path", ["nearby", "1", "1/rates", "1/realtime"])
async def test_legacy_vehicle_labels_are_rejected_everywhere(api, vehicle, path):
    response = await api[0].get(f"/api/v1/parking/{path}", params=query(vehicle=vehicle))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_openapi_only_exposes_new_rider_vehicle_classes():
    spec = create_app().openapi()
    for suffix in ("/nearby", "/{parking_id}", "/{parking_id}/rates", "/{parking_id}/realtime"):
        params = spec["paths"][f"/api/v1/parking{suffix}"]["get"]["parameters"]
        vehicle = next(p for p in params if p["name"] == "vehicle")
        assert vehicle["schema"]["enum"] == ["NORMAL_HEAVY", "LARGE_HEAVY"]
