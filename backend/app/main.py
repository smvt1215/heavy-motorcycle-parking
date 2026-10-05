import secrets
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.domain.errors import DiscoveryError
from app.routers.health import router as health_router
from app.routers.parking import router as parking_router
from app.services.cursors import CursorCodec


def create_app() -> FastAPI:
    app = FastAPI(
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

    @app.middleware("http")
    async def capture_request_time(request: Request, call_next):
        request.state.received_at = request.app.state.clock()
        return await call_next(request)

    @app.exception_handler(DiscoveryError)
    async def discovery_error(request: Request, exc: DiscoveryError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

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
    return app


app = create_app()
