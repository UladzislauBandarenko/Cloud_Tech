import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
from main import app, encrypt, decrypt, redis_client


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_encrypt_decrypt():
    secret = "my secret"
    token = encrypt(secret)
    assert isinstance(token, str)
    assert decrypt(token) == secret


@pytest.mark.asyncio
@patch.object(redis_client, "dbsize", new_callable=AsyncMock)
async def test_metrics(mock_dbsize):
    mock_dbsize.return_value = 42
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/metrics")
        assert response.status_code == 200
        assert response.json()["cached_keys"] == 42


@pytest.mark.asyncio
@patch.object(redis_client, "ping", new_callable=AsyncMock)
async def test_startup_event(mock_ping):
    await app.router.startup()
    mock_ping.assert_awaited_once()


@pytest.mark.asyncio
@patch.object(redis_client, "close", new_callable=AsyncMock)
async def test_shutdown_event(mock_close):
    await app.router.shutdown()
    mock_close.assert_awaited_once()
