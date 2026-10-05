import os

import pytest
from httpx import AsyncClient

from app.routers import health as health_router

# CI provides real Postgres/Redis services and sets this flag so connectivity regressions fail the gate.
REQUIRE_SERVICES = os.getenv("HEALTH_REQUIRE_SERVICES", "").lower() in {"1", "true", "yes"}


@pytest.mark.asyncio
async def test_version(async_client: AsyncClient):
    response = await async_client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "environment" in data


@pytest.mark.asyncio
@pytest.mark.skipif(not REQUIRE_SERVICES, reason="set HEALTH_REQUIRE_SERVICES=1 with Postgres/Redis running")
async def test_health_ok_with_live_services(async_client: AsyncClient):
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}


class _FailingEngine:
    def connect(self):
        raise ConnectionError("database unreachable")


class _FailingRedis:
    async def ping(self):
        raise ConnectionError("redis unreachable")


@pytest.mark.asyncio
async def test_health_degraded_when_dependencies_fail(async_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(health_router, "get_db_engine", lambda: _FailingEngine())
    monkeypatch.setattr(health_router, "get_redis", lambda: _FailingRedis())

    response = await async_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "database": "error", "redis": "error"}
