import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.cache import get_redis
from app.config import settings
from app.domain.errors import DiscoveryError
from app.middleware import RequestBodyLimit
from app.routers.community import dev_router
from app.routers.community import router as community_router
from app.routers.health import router as health_router
from app.routers.parking import router as parking_router
from app.routers.places import router as places_router
from app.services.cursors import CursorCodec
from app.services.places import GooglePlacesClient
from app.services.rate_limit import RedisRateLimiter
from app.services.storage import S3ObjectStorage


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if app.state.places_http is not None:
        await app.state.places_http.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title="Heavy Motorcycle Parking API",
        description="重機停車通 API",
        version=settings.app_version,
        docs_url="/api/docs" if settings.environment != "PROD" else None,
        redoc_url="/api/redoc" if settings.environment != "PROD" else None,
    )
    key = settings.cursor_signing_key
    app.state.cursor_codec = CursorCodec(
        key.get_secret_value().encode() if key is not None else secrets.token_bytes(32)
    )
    app.state.holiday_calendar = None
    app.state.clock = lambda: datetime.now(UTC)
    places_key = settings.google_places_api_key
    app.state.places_http = (
        httpx.AsyncClient(timeout=settings.places_timeout_seconds, follow_redirects=False) if places_key else None
    )
    app.state.places_gateway = (
        GooglePlacesClient(places_key.get_secret_value(), app.state.places_http) if places_key else None
    )
    app.state.object_storage = S3ObjectStorage.from_settings(settings)
    app.state.places_rate_limiter = RedisRateLimiter(
        get_redis, settings.places_rate_limit_per_minute, prefix="ratelimit"
    )

    @app.middleware("http")
    async def capture_request_time(request: Request, call_next):
        request.state.received_at = request.app.state.clock()
        return await call_next(request)

    @app.exception_handler(DiscoveryError)
    async def discovery_error(request: Request, exc: DiscoveryError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo arbitrary query input or non-JSON exception contexts.
        fields = [
            {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]} for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid request parameters.",
                    "details": {"fields": fields},
                }
            },
        )

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(parking_router, prefix="/api/v1")
    app.include_router(places_router, prefix="/api/v1")
    app.include_router(community_router, prefix="/api/v1")
    if settings.environment == "DEV":
        app.include_router(dev_router, prefix="/api/v1")
    # Multipart bodies are spooled before handlers run; cap them at the ASGI boundary.
    app.add_middleware(
        RequestBodyLimit,
        max_bytes=settings.report_photo_max_bytes + 64 * 1024,
        path_pattern=r"/api/v1/reports/[0-9]+/photos",
    )
    return app


app = create_app()
