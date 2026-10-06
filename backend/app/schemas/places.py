"""Destination search wire contracts. No parking facts are present by design."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.parking import Location, WireModel

SESSION_TOKEN_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
PLACE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,512}$"
Attribution = Literal["GOOGLE"]


class AutocompleteQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1, max_length=100)
    session_token: str = Field(pattern=SESSION_TOKEN_PATTERN)
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)

    @model_validator(mode="after")
    def bias_pair(self):
        if not self.input.strip():
            raise ValueError("input must contain non-whitespace characters")
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng must be supplied together")
        return self

    @property
    def bias(self) -> tuple[float, float] | None:
        return (self.lat, self.lng) if self.lat is not None and self.lng is not None else None


class PlaceDetailsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_token: str = Field(pattern=SESSION_TOKEN_PATTERN)


class PlaceSuggestionResponse(WireModel):
    place_id: str
    primary_text: str
    secondary_text: str | None


class AutocompleteResponse(WireModel):
    suggestions: list[PlaceSuggestionResponse]
    attribution: Attribution = "GOOGLE"


class PlaceDestinationResponse(WireModel):
    place_id: str
    name: str
    address: str | None
    location: Location
    attribution: Attribution = "GOOGLE"
