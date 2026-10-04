import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.cache import get_redis
from app.config import settings
from app.db import get_db_engine
from app.schemas.health import HealthResponse, VersionResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    db_status = "ok"
    redis_status = "ok"

    try:
        engine = get_db_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "error"

    try:
        redis_client = get_redis()
        await redis_client.ping()
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        redis_status = "error"

    overall_status = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

    return HealthResponse(status=overall_status, database=db_status, redis=redis_status)


@router.get("/version", response_model=VersionResponse)
async def version_check():
    return VersionResponse(version=settings.app_version, environment=settings.environment)
