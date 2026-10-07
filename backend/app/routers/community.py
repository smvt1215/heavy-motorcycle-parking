from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Path, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.auth.tokens import AccessTokenService
from app.config import settings
from app.db import get_session
from app.domain.errors import not_found
from app.schemas.community import (
    DevSessionRequest,
    FavoriteCreate,
    FavoriteItem,
    FavoritesResponse,
    MeResponse,
    OwnReport,
    OwnReportsResponse,
    PhotoResponse,
    PublicReport,
    PublicReportsResponse,
    ReportCreate,
    ReportStatusUpdate,
    SessionResponse,
    VehiclePreference,
)
from app.schemas.error import ErrorResponse
from app.schemas.parking import Location
from app.services.community import ProfileService, ReportService, ReportView

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]
ParkingID = Annotated[int, Path(gt=0)]
ReportID = Annotated[int, Path(gt=0)]
AUTH = {401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
OWNED = {**AUTH, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}


def profile_service(session: Session) -> ProfileService:
    return ProfileService(session)


def report_service(request: Request, session: Session) -> ReportService:
    return ReportService(session, request.app.state.object_storage, settings.report_photo_max_bytes)


Profiles = Annotated[ProfileService, Depends(profile_service)]
Reports = Annotated[ReportService, Depends(report_service)]


def _me(user) -> MeResponse:
    return MeResponse(
        id=user.id,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        preferred_vehicle=user.preferred_vehicle,
    )


def _public(view: ReportView) -> PublicReport:
    report = view.report
    return PublicReport(
        id=report.id,
        parking_id=report.parking_id,
        zone_id=report.zone_id,
        report_type=report.report_type,
        status=report.status,
        description=report.description if report.status == "VERIFIED" else None,
        photo_count=view.photo_count,
        created_at=report.created_at,
        resolved_at=report.resolved_at,
    )


def _own(view: ReportView) -> OwnReport:
    return OwnReport(**{**_public(view).model_dump(), "description": view.report.description})


# --- session -----------------------------------------------------------------------------


@router.post(
    "/auth/dev-session",
    response_model=SessionResponse,
    tags=["auth"],
    include_in_schema=settings.environment == "DEV",
    responses={404: {"model": ErrorResponse}},
)
async def dev_session(request: Request, body: DevSessionRequest, session: Session):
    """DEV only: issue a token for a local test subject. Disabled (404) elsewhere."""
    if settings.environment != "DEV":
        raise not_found("NOT_FOUND", "Not found.")
    tokens = AccessTokenService(session)
    user = await tokens.ensure_user(f"dev:{body.subject}", display_name=body.display_name)
    token, expires_at = await tokens.issue(
        user, request.state.received_at, timedelta(days=settings.access_token_ttl_days)
    )
    await session.commit()
    return SessionResponse(access_token=token, expires_at=expires_at)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"], responses=AUTH)
async def logout(request: Request, user: CurrentUser, session: Session):
    await AccessTokenService(session).revoke(user.token_hash, request.state.received_at)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- profile and favorites ---------------------------------------------------------------


@router.get("/me", response_model=MeResponse, tags=["me"], responses=AUTH)
async def me(user: CurrentUser, profiles: Profiles):
    return _me(await profiles.me(user))


@router.put("/me/vehicle", response_model=MeResponse, tags=["me"], responses=AUTH)
async def set_vehicle(body: VehiclePreference, user: CurrentUser, profiles: Profiles):
    return _me(await profiles.set_vehicle(user, body.vehicle))


@router.get("/me/reports", response_model=OwnReportsResponse, tags=["reports"], responses=AUTH)
async def my_reports(user: CurrentUser, reports: Reports):
    return OwnReportsResponse(items=[_own(view) for view in await reports.mine(user)])


@router.get("/favorites", response_model=FavoritesResponse, tags=["favorites"], responses=AUTH)
async def favorites(user: CurrentUser, profiles: Profiles):
    rows = await profiles.favorites(user)
    return FavoritesResponse(
        items=[
            FavoriteItem(
                parking_id=row.parking_id,
                name=row.name,
                location=Location(lat=row.lat, lng=row.lng),
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@router.post(
    "/favorites",
    status_code=status.HTTP_201_CREATED,
    tags=["favorites"],
    responses={**AUTH, 200: {"description": "Already a favorite"}, 404: {"model": ErrorResponse}},
)
async def add_favorite(body: FavoriteCreate, user: CurrentUser, profiles: Profiles, response: Response):
    created = await profiles.add_favorite(user, body.parking_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return {"parking_id": body.parking_id}


@router.delete(
    "/favorites/{parking_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["favorites"],
    responses={**AUTH, 404: {"model": ErrorResponse}},
)
async def remove_favorite(parking_id: ParkingID, user: CurrentUser, profiles: Profiles):
    await profiles.remove_favorite(user, parking_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- community reports -------------------------------------------------------------------


@router.get(
    "/parking/{parking_id}/reports",
    response_model=PublicReportsResponse,
    tags=["reports"],
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def parking_reports(parking_id: ParkingID, reports: Reports):
    return PublicReportsResponse(items=[_public(view) for view in await reports.for_parking(parking_id)])


@router.post(
    "/reports",
    response_model=OwnReport,
    status_code=status.HTTP_201_CREATED,
    tags=["reports"],
    responses={**AUTH, 404: {"model": ErrorResponse}},
)
async def create_report(body: ReportCreate, user: CurrentUser, reports: Reports):
    view = await reports.create(user, body.parking_id, body.zone_id, body.report_type, body.description)
    return _own(view)


@router.post(
    "/reports/{report_id}/photos",
    response_model=PhotoResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["reports"],
    responses={**OWNED, 409: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def upload_photo(report_id: ReportID, user: CurrentUser, reports: Reports, file: Annotated[UploadFile, File()]):
    data = await file.read(settings.report_photo_max_bytes + 1)
    photo = await reports.add_photo(user, report_id, data)
    return PhotoResponse(
        id=photo.id,
        report_id=photo.report_id,
        content_type=photo.content_type,
        byte_size=photo.byte_size,
        width=photo.width,
        height=photo.height,
        created_at=photo.created_at,
    )


@router.patch(
    "/reports/{report_id}/status",
    response_model=OwnReport,
    tags=["reports"],
    responses={**OWNED, 409: {"model": ErrorResponse}},
)
async def set_report_status(
    request: Request, report_id: ReportID, body: ReportStatusUpdate, user: CurrentUser, reports: Reports
):
    view = await reports.set_status(user, report_id, body.status, request.state.received_at)
    return _own(view)
