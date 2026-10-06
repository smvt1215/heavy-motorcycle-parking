from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from app.domain.places import rate_limited
from app.schemas.error import ErrorResponse
from app.schemas.parking import Location
from app.schemas.places import (
    PLACE_ID_PATTERN,
    AutocompleteQuery,
    AutocompleteResponse,
    PlaceDestinationResponse,
    PlaceDetailsQuery,
    PlaceSuggestionResponse,
)
from app.services.places import DestinationSearchService

router = APIRouter(prefix="/places", tags=["places"])


def get_destination_service(request: Request) -> DestinationSearchService:
    return DestinationSearchService(request.app.state.places_gateway)


async def enforce_places_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    if not await request.app.state.places_rate_limiter.allow(f"places:{client}"):
        raise rate_limited()


Service = Annotated[DestinationSearchService, Depends(get_destination_service)]
ERRORS = {
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@router.get(
    "/autocomplete",
    response_model=AutocompleteResponse,
    responses=ERRORS,
    dependencies=[Depends(enforce_places_rate_limit)],
)
async def autocomplete(query: Annotated[AutocompleteQuery, Query()], service: Service):
    suggestions = await service.autocomplete(query.input, query.session_token, query.bias)
    return AutocompleteResponse(
        suggestions=[
            PlaceSuggestionResponse(
                place_id=item.place_id, primary_text=item.primary_text, secondary_text=item.secondary_text
            )
            for item in suggestions
        ]
    )


@router.get(
    "/{place_id}",
    response_model=PlaceDestinationResponse,
    responses={**ERRORS, 404: {"model": ErrorResponse}},
    dependencies=[Depends(enforce_places_rate_limit)],
)
async def place_details(
    place_id: Annotated[str, Path(pattern=PLACE_ID_PATTERN)],
    query: Annotated[PlaceDetailsQuery, Query()],
    service: Service,
):
    place = await service.details(place_id, query.session_token)
    return PlaceDestinationResponse(
        place_id=place.place_id,
        name=place.name,
        address=place.address,
        location=Location(lat=place.lat, lng=place.lng),
    )
