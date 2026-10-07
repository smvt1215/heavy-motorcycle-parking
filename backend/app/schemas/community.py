"""Wire contracts for profile, favorites and community reports."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models import ReportStatus, ReportType
from app.schemas.parking import Location, PublicVehicle, WireModel

ResolvedStatus = Literal["VERIFIED", "REJECTED", "SUPERSEDED"]


class MeResponse(WireModel):
    id: int
    display_name: str | None
    email: str | None
    role: Literal["USER", "MODERATOR"]
    # A client-side default; selected-vehicle endpoints still require explicit `vehicle`.
    preferred_vehicle: PublicVehicle | None


class VehiclePreference(WireModel):
    vehicle: PublicVehicle | None


class FavoriteCreate(WireModel):
    parking_id: int = Field(gt=0)


class FavoriteItem(WireModel):
    parking_id: int
    name: str
    location: Location
    created_at: datetime


class FavoritesResponse(WireModel):
    items: list[FavoriteItem]


class ReportCreate(WireModel):
    parking_id: int = Field(gt=0)
    zone_id: int | None = Field(default=None, gt=0)
    report_type: ReportType
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("description")
    @classmethod
    def blank_is_none(cls, value):
        return value.strip() or None if value is not None else None


class ReportStatusUpdate(WireModel):
    status: ResolvedStatus


class ReportProvenance(WireModel):
    """Community evidence stays separately attributable from official sources."""

    source_type: Literal["COMMUNITY"] = "COMMUNITY"


class PublicReport(WireModel):
    id: int
    parking_id: int
    zone_id: int | None
    report_type: ReportType
    status: ReportStatus
    # Free text is only published after moderation verifies it.
    description: str | None
    photo_count: int
    created_at: datetime
    resolved_at: datetime | None
    provenance: ReportProvenance = ReportProvenance()


class PublicReportsResponse(WireModel):
    items: list[PublicReport]


class OwnReport(PublicReport):
    pass


class OwnReportsResponse(WireModel):
    items: list[OwnReport]


class PhotoResponse(WireModel):
    id: int
    report_id: int
    content_type: str
    byte_size: int
    width: int
    height: int
    created_at: datetime


class DevSessionRequest(WireModel):
    subject: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9:._@-]+$")
    display_name: str | None = Field(default=None, max_length=100)


class SessionResponse(WireModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime
