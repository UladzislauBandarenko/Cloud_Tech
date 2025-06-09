import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
from main import app, redis_client

@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        res = await ac.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

@pytest.mark.asyncio
@patch.object(redis_client, "dbsize", new_callable=AsyncMock)
async def test_metrics(mock_dbsize):
    mock_dbsize.return_value = 42
    async with AsyncClient(app=app, base_url="http://test") as ac:
        res = await ac.get("/metrics")
        assert res.status_code == 200
        assert res.json() == {"service": "user-service", "cached_keys": 42}
