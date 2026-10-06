"""Destination search: Google Places is a geocoding source only."""

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.domain.places import PlaceDestination, PlacesError, PlaceSuggestion
from app.main import create_app
from app.services.places import (
    AUTOCOMPLETE_FIELD_MASK,
    DETAILS_FIELD_MASK,
    DestinationSearchService,
    GooglePlacesClient,
)
from app.services.rate_limit import RedisRateLimiter

TOKEN = "0b6f5c1e-7a52-4c4f-9a7e-3d2b1c0a9f88"
TAIPEI_101_ID = "ChIJH56c2rarQjQRphD9gvC8BhI"
PARKING_KEYS = {"compatibility", "availability", "rate_summary", "rates", "zones", "realtime", "legal", "allowed"}


def client_for(handler, key="test-key"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GooglePlacesClient(key, http), http


def autocomplete_payload(*predictions):
    return {"suggestions": [{"placePrediction": item} for item in predictions]}


def prediction(place_id=TAIPEI_101_ID, main="台北101", secondary="台灣台北市信義區信義路五段7號"):
    return {
        "placeId": place_id,
        "text": {"text": f"{main}, {secondary}"},
        "structuredFormat": {"mainText": {"text": main}, "secondaryText": {"text": secondary}},
    }


DETAILS = {
    "id": TAIPEI_101_ID,
    "displayName": {"text": "台北101", "languageCode": "zh-TW"},
    "formattedAddress": "110台灣台北市信義區信義路五段7號",
    "location": {"latitude": 25.0339639, "longitude": 121.5644722},
}


async def test_autocomplete_sends_minimal_taiwan_scoped_session_request():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=autocomplete_payload(prediction()))

    client, http = client_for(handler)
    async with http:
        result = await client.autocomplete("台北101", TOKEN, (25.03, 121.56))

    assert result == [PlaceSuggestion(TAIPEI_101_ID, "台北101", "台灣台北市信義區信義路五段7號")]
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://places.googleapis.com/v1/places:autocomplete"
    assert request.headers["X-Goog-Api-Key"] == "test-key"
    assert request.headers["X-Goog-FieldMask"] == AUTOCOMPLETE_FIELD_MASK
    body = json.loads(request.content)
    assert body["input"] == "台北101"
    assert body["sessionToken"] == TOKEN
    assert body["includedRegionCodes"] == ["tw"]
    assert body["languageCode"] == "zh-TW"
    assert body["locationBias"]["circle"]["center"] == {"latitude": 25.03, "longitude": 121.56}


async def test_autocomplete_without_bias_omits_location_and_drops_malformed_predictions():
    def handler(request):
        assert "locationBias" not in json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "suggestions": [
                    {"queryPrediction": {"text": {"text": "台北"}}},
                    {"placePrediction": {"placeId": "bad id!", "text": {"text": "x"}}},
                    {"placePrediction": {"placeId": "A1", "structuredFormat": {"mainText": {"text": "  "}}}},
                    {"placePrediction": {"placeId": "A2", "text": {"text": "只有全文"}}},
                    "not-an-object",
                    *({"placePrediction": prediction(f"P{i}", f"地點{i}")} for i in range(6)),
                ]
            },
        )

    client, http = client_for(handler)
    async with http:
        result = await client.autocomplete("台北", TOKEN, None)

    assert result[0] == PlaceSuggestion("A2", "只有全文", None)
    assert [item.place_id for item in result] == ["A2", "P0", "P1", "P2", "P3"]


async def test_autocomplete_without_suggestions_is_empty_not_error():
    client, http = client_for(lambda request: httpx.Response(200, json={}))
    async with http:
        assert await client.autocomplete("zzzz", TOKEN, None) == []


async def test_details_closes_session_and_returns_coordinates_only():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=DETAILS)

    client, http = client_for(handler)
    async with http:
        place = await client.details(TAIPEI_101_ID, TOKEN)

    assert place == PlaceDestination(
        TAIPEI_101_ID, "台北101", "110台灣台北市信義區信義路五段7號", 25.0339639, 121.5644722
    )
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == f"/v1/places/{TAIPEI_101_ID}"
    assert request.url.params["sessionToken"] == TOKEN
    assert request.headers["X-Goog-FieldMask"] == DETAILS_FIELD_MASK


async def test_details_falls_back_to_address_for_missing_display_name():
    payload = {**DETAILS, "displayName": None}
    client, http = client_for(lambda request: httpx.Response(200, json=payload))
    async with http:
        place = await client.details(TAIPEI_101_ID, TOKEN)
    assert place.name == DETAILS["formattedAddress"]


@pytest.mark.parametrize(
    "payload",
    [
        {**DETAILS, "location": None},
        {**DETAILS, "location": {"latitude": 25.03}},
        {**DETAILS, "location": {"latitude": "25.03", "longitude": 121.5}},
        {**DETAILS, "location": {"latitude": 95, "longitude": 121.5}},
        {**DETAILS, "location": {"latitude": True, "longitude": 121.5}},
        {"id": TAIPEI_101_ID, "location": DETAILS["location"]},
        [],
    ],
)
async def test_details_rejects_payloads_without_trustworthy_coordinates(payload):
    client, http = client_for(lambda request: httpx.Response(200, json=payload))
    async with http:
        with pytest.raises(PlacesError) as error:
            await client.details(TAIPEI_101_ID, TOKEN)
    assert error.value.code == "PLACES_UPSTREAM_ERROR"


@pytest.mark.parametrize(
    ("response", "code", "status"),
    [
        (httpx.Response(404, json={"error": {}}), "PLACE_NOT_FOUND", 404),
        (httpx.Response(403, json={"error": {}}), "PLACES_UPSTREAM_ERROR", 502),
        (httpx.Response(429, json={"error": {}}), "PLACES_UPSTREAM_ERROR", 502),
        (httpx.Response(500, text="boom"), "PLACES_UPSTREAM_ERROR", 502),
        (httpx.Response(200, text="not json"), "PLACES_UPSTREAM_ERROR", 502),
        (httpx.Response(200, json={"suggestions": {}}), "PLACES_UPSTREAM_ERROR", 502),
    ],
)
async def test_upstream_failures_map_to_stable_errors(response, code, status):
    client, http = client_for(lambda request: response)
    async with http:
        with pytest.raises(PlacesError) as error:
            await client.autocomplete("台北101", TOKEN, None)
    assert (error.value.code, error.value.status_code) == (code, status)


async def test_transport_timeout_is_upstream_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    client, http = client_for(handler)
    async with http:
        with pytest.raises(PlacesError) as error:
            await client.details(TAIPEI_101_ID, TOKEN)
    assert error.value.code == "PLACES_UPSTREAM_ERROR"


async def test_invalid_place_id_never_reaches_upstream():
    def handler(request):
        raise AssertionError("must not be called")

    client, http = client_for(handler)
    async with http:
        with pytest.raises(PlacesError) as error:
            await client.details("../places:autocomplete", TOKEN)
    assert error.value.code == "PLACE_NOT_FOUND"


async def test_unconfigured_service_reports_unavailable():
    with pytest.raises(PlacesError) as error:
        await DestinationSearchService(None).autocomplete("台北101", TOKEN, None)
    assert (error.value.code, error.value.status_code) == ("PLACES_UNAVAILABLE", 503)


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.error = None

    async def autocomplete(self, text, session_token, bias):
        self.calls.append(("autocomplete", text, session_token, bias))
        if self.error:
            raise self.error
        return [] if text == "查無此地" else [PlaceSuggestion(TAIPEI_101_ID, "台北101", "信義區")]

    async def details(self, place_id, session_token):
        self.calls.append(("details", place_id, session_token))
        if self.error:
            raise self.error
        return PlaceDestination(place_id, "台北101", None, 25.0339639, 121.5644722)


class AllowList:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.keys = []

    async def allow(self, key):
        self.keys.append(key)
        return self.allowed


@pytest.fixture
def app_with_gateway():
    app = create_app()
    gateway = FakeGateway()
    app.state.places_gateway = gateway
    app.state.places_rate_limiter = AllowList()
    return app, gateway


async def call(app, path, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.get(f"/api/v1/places{path}", params=params)


async def test_autocomplete_endpoint_returns_geocoding_suggestions_only(app_with_gateway):
    app, gateway = app_with_gateway
    response = await call(app, "/autocomplete", input=" 台北101 ", session_token=TOKEN, lat=25.03, lng=121.56)

    assert response.status_code == 200
    assert response.json() == {
        "suggestions": [{"place_id": TAIPEI_101_ID, "primary_text": "台北101", "secondary_text": "信義區"}],
        "attribution": "GOOGLE",
    }
    assert gateway.calls == [("autocomplete", "台北101", TOKEN, (25.03, 121.56))]
    assert not PARKING_KEYS & set(response.json()["suggestions"][0])


async def test_autocomplete_endpoint_empty_result(app_with_gateway):
    app, _ = app_with_gateway
    response = await call(app, "/autocomplete", input="查無此地", session_token=TOKEN)
    assert response.status_code == 200
    assert response.json()["suggestions"] == []


async def test_details_endpoint_returns_destination_without_parking_facts(app_with_gateway):
    app, gateway = app_with_gateway
    response = await call(app, f"/{TAIPEI_101_ID}", session_token=TOKEN)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "place_id": TAIPEI_101_ID,
        "name": "台北101",
        "address": None,
        "location": {"lat": 25.0339639, "lng": 121.5644722},
        "attribution": "GOOGLE",
    }
    assert not PARKING_KEYS & set(body)
    assert gateway.calls == [("details", TAIPEI_101_ID, TOKEN)]


@pytest.mark.parametrize(
    "params",
    [
        {"input": "台北101"},
        {"input": "台北101", "session_token": "not-a-uuid"},
        {"input": "   ", "session_token": TOKEN},
        {"input": "", "session_token": TOKEN},
        {"input": "x" * 101, "session_token": TOKEN},
        {"input": "台北101", "session_token": TOKEN, "lat": 25.03},
        {"input": "台北101", "session_token": TOKEN, "lat": 91, "lng": 121},
        {"input": "台北101", "session_token": TOKEN, "unexpected": "1"},
    ],
)
async def test_autocomplete_validation_errors_do_not_call_google(app_with_gateway, params):
    app, gateway = app_with_gateway
    response = await call(app, "/autocomplete", **params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert gateway.calls == []


async def test_details_rejects_invalid_place_id_and_token(app_with_gateway):
    app, gateway = app_with_gateway
    assert (await call(app, "/bad%20id", session_token=TOKEN)).status_code == 422
    assert (await call(app, f"/{TAIPEI_101_ID}")).status_code == 422
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (PlacesError("PLACE_NOT_FOUND", "missing", 404), 404),
        (PlacesError("PLACES_UPSTREAM_ERROR", "down", 502), 502),
    ],
)
async def test_gateway_errors_use_common_error_envelope(app_with_gateway, error, status):
    app, gateway = app_with_gateway
    gateway.error = error
    response = await call(app, f"/{TAIPEI_101_ID}", session_token=TOKEN)
    assert response.status_code == status
    assert response.json() == {"error": {"code": error.code, "message": error.message}}


async def test_unconfigured_endpoint_returns_places_unavailable():
    app = create_app()
    app.state.places_gateway = None
    app.state.places_rate_limiter = AllowList()
    response = await call(app, "/autocomplete", input="台北101", session_token=TOKEN)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PLACES_UNAVAILABLE"


async def test_rate_limited_client_gets_429_before_google(app_with_gateway):
    app, gateway = app_with_gateway
    app.state.places_rate_limiter = AllowList(allowed=False)
    response = await call(app, "/autocomplete", input="台北101", session_token=TOKEN)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert gateway.calls == []


async def test_openapi_documents_places_endpoints(app_with_gateway):
    app, _ = app_with_gateway
    paths = app.openapi()["paths"]
    assert "/api/v1/places/autocomplete" in paths
    assert "/api/v1/places/{place_id}" in paths


class FakePipeline:
    def __init__(self, store, fail):
        self.store = store
        self.fail = fail
        self.ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))

    async def execute(self):
        if self.fail:
            raise ConnectionError("redis down")
        key = self.ops[0][1]
        self.store[key] = self.store.get(key, 0) + 1
        return [self.store[key], True]


class FakeRedis:
    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail

    def pipeline(self, transaction=True):
        return FakePipeline(self.store, self.fail)


async def test_redis_rate_limiter_counts_per_key_window():
    redis = FakeRedis()
    limiter = RedisRateLimiter(lambda: redis, limit=2)
    assert [await limiter.allow("places:1.2.3.4") for _ in range(3)] == [True, True, False]
    assert await limiter.allow("places:5.6.7.8") is True


async def test_redis_rate_limiter_fails_open():
    limiter = RedisRateLimiter(lambda: FakeRedis(fail=True), limit=1)
    assert await limiter.allow("places:1.2.3.4") is True


def test_blank_places_key_is_unset_and_secret_is_redacted():
    assert Settings(_env_file=None, google_places_api_key="  ").google_places_api_key is None
    config = Settings(_env_file=None, google_places_api_key="places-secret")
    assert "places-secret" not in repr(config)


def test_configured_key_creates_gateway(monkeypatch):
    from app import main

    monkeypatch.setattr(
        main.settings,
        "google_places_api_key",
        Settings(_env_file=None, google_places_api_key="k").google_places_api_key,
    )
    app = main.create_app()
    assert isinstance(app.state.places_gateway, GooglePlacesClient)
