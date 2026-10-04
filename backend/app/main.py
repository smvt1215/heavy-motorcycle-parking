from fastapi import FastAPI

from app.config import settings
from app.routers.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Heavy Motorcycle Parking API",
        description="重機停車通 API",
        version=settings.app_version,
        docs_url="/api/docs" if settings.environment != "PROD" else None,
        redoc_url="/api/redoc" if settings.environment != "PROD" else None,
    )
    app.include_router(health_router, prefix="/api/v1")
    return app


app = create_app()
