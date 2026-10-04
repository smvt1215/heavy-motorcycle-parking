import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_version(async_client: AsyncClient):
    response = await async_client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "environment" in data


@pytest.mark.asyncio
async def test_health(async_client: AsyncClient):
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
    assert "redis" in data
