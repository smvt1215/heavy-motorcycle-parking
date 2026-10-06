import base64
from datetime import UTC, datetime

import httpx
import pytest

from app.ingestion.downloader import DownloadError, ParkingDownloader
from app.ingestion.sources import STATIC

NOW = datetime(2026, 10, 5, 16, 2, tzinfo=UTC)


@pytest.mark.parametrize("failures", [(503, 429), (httpx.ReadTimeout, httpx.ConnectError)])
async def test_retries_with_exponential_backoff_and_explicit_timeout(failures):
    requests, delays = [], []

    def respond(request):
        requests.append(request)
        assert request.extensions["timeout"]["read"] == 7
        if len(requests) <= len(failures):
            failure = failures[len(requests) - 1]
            if isinstance(failure, int):
                return httpx.Response(failure)
            raise failure("simulated", request=request)
        return httpx.Response(200, json={"data": {"park": []}})

    async def sleep(delay):
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        download = await ParkingDownloader(client, timeout=7, sleep=sleep, clock=lambda: NOW).fetch(STATIC)
    assert download.payload == {"data": {"park": []}}
    assert download.fetched_at == NOW
    assert len(requests) == 3 and delays == [1, 2]


@pytest.mark.parametrize("status", [400, 401, 404, 302])
async def test_permanent_http_errors_do_not_retry(status):
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DownloadError, match=f"HTTP {status}") as caught:
            await ParkingDownloader(client).fetch(STATIC)
    assert count == 1 and caught.value.code == "HTTP_ERROR"


async def test_exhausted_retries_and_backoff_limit():
    calls, delays = [], []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("simulated", request=request)

    async def sleep(delay):
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DownloadError) as caught:
            await ParkingDownloader(client, attempts=3, sleep=sleep).fetch(STATIC)
    assert caught.value.code == "TRANSPORT_ERROR"
    assert len(calls) == 3 and delays == [1, 2]


@pytest.mark.parametrize("body", [b"not json", b'{"bad":NaN}', b"\xff"])
async def test_invalid_json_keeps_exact_raw_bytes(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as client:
        with pytest.raises(DownloadError) as caught:
            await ParkingDownloader(client, clock=lambda: NOW).fetch(STATIC)
    assert caught.value.code == "INVALID_JSON"
    assert caught.value.fetched_at == NOW
    assert base64.b64decode(caught.value.evidence["body_base64"]) == body


async def test_download_payload_limit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 20))
    ) as client:
        with pytest.raises(DownloadError) as caught:
            await ParkingDownloader(client, max_bytes=10).fetch(STATIC)
    assert caught.value.code == "PAYLOAD_TOO_LARGE"
