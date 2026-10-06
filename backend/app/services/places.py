"""Google Places API (New) geocoding adapter.

The adapter resolves an address/POI to coordinates only. It requests the minimal field
masks needed for that purpose and never reads or returns parking information. Search text
is never logged.
"""

import logging
import re
from typing import Any, Protocol

import httpx

from app.domain.places import (
    PlaceDestination,
    PlaceSuggestion,
    place_not_found,
    places_unavailable,
    places_upstream_error,
)

logger = logging.getLogger(__name__)

PLACES_BASE_URL = "https://places.googleapis.com/v1"
AUTOCOMPLETE_FIELD_MASK = (
    "suggestions.placePrediction.placeId,"
    "suggestions.placePrediction.text.text,"
    "suggestions.placePrediction.structuredFormat"
)
DETAILS_FIELD_MASK = "id,displayName,formattedAddress,location"
LANGUAGE_CODE = "zh-TW"
REGION_CODE = "tw"
BIAS_RADIUS_M = 20_000.0
MAX_SUGGESTIONS = 5
PLACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


class PlacesGateway(Protocol):
    async def autocomplete(
        self, text: str, session_token: str, bias: tuple[float, float] | None
    ) -> list[PlaceSuggestion]: ...

    async def details(self, place_id: str, session_token: str) -> PlaceDestination: ...


class GooglePlacesClient:
    """Thin HTTP client; [http] must not follow redirects to other hosts."""

    def __init__(self, api_key: str, http: httpx.AsyncClient, base_url: str = PLACES_BASE_URL):
        self._api_key = api_key
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def autocomplete(
        self, text: str, session_token: str, bias: tuple[float, float] | None
    ) -> list[PlaceSuggestion]:
        body: dict[str, Any] = {
            "input": text,
            "sessionToken": session_token,
            "languageCode": LANGUAGE_CODE,
            "regionCode": REGION_CODE,
            "includedRegionCodes": [REGION_CODE],
        }
        if bias is not None:
            body["locationBias"] = {
                "circle": {"center": {"latitude": bias[0], "longitude": bias[1]}, "radius": BIAS_RADIUS_M}
            }
        data = await self._request("POST", "/places:autocomplete", AUTOCOMPLETE_FIELD_MASK, json=body)
        suggestions = data.get("suggestions", [])
        if not isinstance(suggestions, list):
            raise places_upstream_error()
        parsed = [item for raw in suggestions if (item := _suggestion(raw)) is not None]
        return parsed[:MAX_SUGGESTIONS]

    async def details(self, place_id: str, session_token: str) -> PlaceDestination:
        if not PLACE_ID_PATTERN.fullmatch(place_id):
            raise place_not_found()
        data = await self._request(
            "GET",
            f"/places/{place_id}",
            DETAILS_FIELD_MASK,
            params={"languageCode": LANGUAGE_CODE, "regionCode": REGION_CODE, "sessionToken": session_token},
        )
        destination = _destination(data, place_id)
        if destination is None:
            logger.warning("Places details response was missing required fields")
            raise places_upstream_error()
        return destination

    async def _request(self, method: str, path: str, field_mask: str, **kwargs) -> dict[str, Any]:
        headers = {"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": field_mask}
        try:
            response = await self._http.request(method, f"{self._base_url}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("Places request failed: %s", type(exc).__name__)
            raise places_upstream_error() from exc
        if response.status_code == 404:
            raise place_not_found()
        if response.status_code != 200:
            logger.warning("Places request returned HTTP %s", response.status_code)
            raise places_upstream_error()
        try:
            data = response.json()
        except ValueError as exc:
            raise places_upstream_error() from exc
        if not isinstance(data, dict):
            raise places_upstream_error()
        return data


def _text(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("text")
    return value.strip() or None if isinstance(value, str) else None


def _suggestion(raw: Any) -> PlaceSuggestion | None:
    """Drop malformed predictions rather than presenting partial destinations."""
    prediction = raw.get("placePrediction") if isinstance(raw, dict) else None
    if not isinstance(prediction, dict):
        return None
    place_id = prediction.get("placeId")
    if not isinstance(place_id, str) or not PLACE_ID_PATTERN.fullmatch(place_id):
        return None
    structured = prediction.get("structuredFormat")
    structured = structured if isinstance(structured, dict) else {}
    primary = _text(structured.get("mainText")) or _text(prediction.get("text"))
    if primary is None:
        return None
    return PlaceSuggestion(place_id, primary, _text(structured.get("secondaryText")))


def _destination(data: dict[str, Any], requested_id: str) -> PlaceDestination | None:
    location = data.get("location")
    if not isinstance(location, dict):
        return None
    lat, lng = location.get("latitude"), location.get("longitude")
    if not all(isinstance(value, int | float) and not isinstance(value, bool) for value in (lat, lng)):
        return None
    place_id = data.get("id") if isinstance(data.get("id"), str) else requested_id
    address = _text(data.get("formattedAddress"))
    name = _text(data.get("displayName")) or address
    if name is None or not PLACE_ID_PATTERN.fullmatch(place_id):
        return None
    try:
        return PlaceDestination(place_id, name, address, float(lat), float(lng))
    except ValueError:
        return None


class DestinationSearchService:
    def __init__(self, gateway: PlacesGateway | None):
        self._gateway = gateway

    def _require(self) -> PlacesGateway:
        if self._gateway is None:
            raise places_unavailable()
        return self._gateway

    async def autocomplete(
        self, text: str, session_token: str, bias: tuple[float, float] | None
    ) -> list[PlaceSuggestion]:
        return await self._require().autocomplete(text.strip(), session_token, bias)

    async def details(self, place_id: str, session_token: str) -> PlaceDestination:
        return await self._require().details(place_id, session_token)
