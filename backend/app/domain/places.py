"""Destination geocoding facts.

Google Places only answers *where* a destination is. These types deliberately carry no
parking legality, rate or availability information; parking facts come from our own data.
"""

import math
from dataclasses import dataclass

from app.domain.errors import DiscoveryError


class PlacesError(DiscoveryError):
    """Stable destination-search error exposed through the common error envelope."""


def places_unavailable() -> PlacesError:
    return PlacesError("PLACES_UNAVAILABLE", "Destination search is not configured.", 503)


def places_upstream_error() -> PlacesError:
    return PlacesError("PLACES_UPSTREAM_ERROR", "Destination search is temporarily unavailable.", 502)


def place_not_found() -> PlacesError:
    return PlacesError("PLACE_NOT_FOUND", "The selected destination was not found.", 404)


def rate_limited() -> PlacesError:
    return PlacesError("RATE_LIMITED", "Too many destination searches. Please retry shortly.", 429)


@dataclass(frozen=True, slots=True)
class PlaceSuggestion:
    place_id: str
    primary_text: str
    secondary_text: str | None


@dataclass(frozen=True, slots=True)
class PlaceDestination:
    place_id: str
    name: str
    address: str | None
    lat: float
    lng: float

    def __post_init__(self):
        if not (
            math.isfinite(self.lat) and math.isfinite(self.lng) and -90 <= self.lat <= 90 and -180 <= self.lng <= 180
        ):
            raise ValueError("Destination coordinates must be finite WGS84 values")
