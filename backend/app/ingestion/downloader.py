"""Bounded downloads with explicit timeout/retry policy and actual fetch instants."""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.ingestion.sources import FeedPolicy


class DownloadError(RuntimeError):
    def __init__(self, code: str, message: str, *, evidence: Any = None, fetched_at: datetime | None = None):
        self.code = code
        self.evidence = evidence
        self.fetched_at = fetched_at
        super().__init__(message)


@dataclass(frozen=True)
class Download:
    payload: Any
    fetched_at: datetime


class ParkingDownloader:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        attempts: int = 4,
        timeout: float = 30,
        backoff_seconds: float = 1,
        max_bytes: int = 20_000_000,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if attempts < 1 or timeout <= 0 or backoff_seconds < 0 or max_bytes < 1:
            raise ValueError("Invalid downloader limits")
        self.client = client
        self.attempts = attempts
        self.timeout = timeout
        self.backoff_seconds = backoff_seconds
        self.max_bytes = max_bytes
        self.sleep = sleep
        self.clock = clock

    async def fetch(self, policy: FeedPolicy) -> Download:
        if policy.page_size is None:
            return await self._fetch_one(policy.url, None)
        return await self._fetch_pages(policy)

    async def _fetch_pages(self, policy: FeedPolicy) -> Download:
        """Fetch every page; any failed or malformed page fails the whole snapshot.

        The envelope keeps each page verbatim so raw evidence shows page boundaries.
        The snapshot's fetch instant is the *first* page receipt, so freshness never
        looks newer than the oldest data in the snapshot.
        """
        pages: list[Any] = []
        fetched_at = None
        for page in range(policy.max_pages):
            download = await self._fetch_one(policy.url, {"page": page, "size": policy.page_size})
            fetched_at = fetched_at or download.fetched_at
            if not isinstance(download.payload, list):
                raise DownloadError(
                    "INVALID_PAGE",
                    f"Page {page} is not a JSON array",
                    evidence={"page": page, "payload": download.payload},
                    fetched_at=fetched_at,
                )
            pages.append(download.payload)
            if len(download.payload) < policy.page_size:
                # The receipt of the final page completes the snapshot.
                return Download({"page_size": policy.page_size, "pages": pages}, fetched_at)
        raise DownloadError(
            "PAGE_LIMIT_EXCEEDED",
            f"Source still returned full pages after {policy.max_pages} pages",
            fetched_at=fetched_at,
        )

    async def _fetch_one(self, url: str, params: dict[str, Any] | None) -> Download:
        for attempt in range(self.attempts):
            try:
                async with self.client.stream("GET", url, params=params, timeout=self.timeout) as response:
                    if response.status_code == 429 or 500 <= response.status_code < 600:
                        raise DownloadError("HTTP_RETRYABLE", f"Source HTTP {response.status_code}")
                    if response.status_code != 200:
                        raise DownloadError("HTTP_ERROR", f"Source HTTP {response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            raise DownloadError("PAYLOAD_TOO_LARGE", "Source response exceeds configured limit")
                    fetched_at = self.clock().astimezone(UTC)
                    return Download(decode_json(bytes(body), fetched_at), fetched_at)
            except (httpx.TransportError, DownloadError) as exc:
                retryable = isinstance(exc, httpx.TransportError) or exc.code == "HTTP_RETRYABLE"
                if not retryable or attempt == self.attempts - 1:
                    if isinstance(exc, DownloadError):
                        raise
                    raise DownloadError("TRANSPORT_ERROR", type(exc).__name__) from exc
                await self.sleep(self.backoff_seconds * 2**attempt)
        raise AssertionError("Retry loop must return or raise")


def _invalid_constant(value: str):
    raise ValueError(f"Non-JSON constant: {value}")


def decode_json(body: bytes, fetched_at: datetime):
    try:
        return json.loads(body, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise DownloadError(
            "INVALID_JSON",
            "Source response is not valid JSON",
            evidence={"body_base64": base64.b64encode(body).decode("ascii")},
            fetched_at=fetched_at,
        ) from exc
