from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.parking import ParkingRepository
from app.schemas.error import ErrorResponse
from app.schemas.parking import (
    DetailResponse,
    NearbyQuery,
    NearbyResponse,
    RatesResponse,
    RealtimeResponse,
    VehicleQuery,
)
from app.services.discovery import ParkingDiscoveryService

router = APIRouter(prefix="/parking", tags=["parking"])


def get_discovery_service(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    return ParkingDiscoveryService(
        ParkingRepository(session), request.app.state.cursor_codec, request.app.state.holiday_calendar
    )


Service = Annotated[ParkingDiscoveryService, Depends(get_discovery_service)]
ParkingID = Annotated[int, Path(gt=0)]
ERRORS = {400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
DETAIL_ERRORS = {**ERRORS, 404: {"model": ErrorResponse}}


@router.get("/nearby", response_model=NearbyResponse, responses=ERRORS)
async def nearby(request: Request, query: Annotated[NearbyQuery, Query()], service: Service):
    return await service.nearby(query, request.state.received_at)


@router.get("/{parking_id}", response_model=DetailResponse, responses=DETAIL_ERRORS)
async def detail(request: Request, parking_id: ParkingID, query: Annotated[VehicleQuery, Query()], service: Service):
    return await service.selected(parking_id, query.vehicle, query.at, request.state.received_at, "detail")


@router.get("/{parking_id}/rates", response_model=RatesResponse, responses=DETAIL_ERRORS)
async def rates(request: Request, parking_id: ParkingID, query: Annotated[VehicleQuery, Query()], service: Service):
    return await service.selected(parking_id, query.vehicle, query.at, request.state.received_at, "rates")


@router.get("/{parking_id}/realtime", response_model=RealtimeResponse, responses=DETAIL_ERRORS)
async def realtime(request: Request, parking_id: ParkingID, query: Annotated[VehicleQuery, Query()], service: Service):
    return await service.selected(parking_id, query.vehicle, query.at, request.state.received_at, "realtime")
