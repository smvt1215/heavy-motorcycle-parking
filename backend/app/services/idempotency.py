"""Idempotency-Key handling and the single commit for community writes.

The stored response is written in the same transaction as the business change,
so a replay can never observe one without the other. Records live 24 hours;
expiry is checked when read, without a background job.
"""

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.community import IDEMPOTENCY_TTL
from app.domain.errors import DiscoveryError
from app.models import IdempotencyRecord
from app.repositories.community_cases import CommunityCaseRepository

KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,128}$")
Handler = Callable[[], Awaitable[tuple[int, dict[str, Any]]]]


def request_digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def key_required() -> DiscoveryError:
    return DiscoveryError("IDEMPOTENCY_KEY_REQUIRED", "This operation requires an Idempotency-Key header.", 428)


def key_reused() -> DiscoveryError:
    return DiscoveryError("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used for a different request.", 422)


async def unit_of_work(session: AsyncSession, handler: Callable[[], Awaitable[Any]]) -> Any:
    """Run a write and commit once; any failure rolls back every partial change."""
    try:
        result = await handler()
        await session.commit()
        return result
    except BaseException:
        await session.rollback()
        raise


class IdempotencyService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CommunityCaseRepository(session)

    async def run(
        self,
        user_id: int,
        operation: str,
        key: str | None,
        payload: Any,
        now: datetime,
        handler: Handler,
        guard: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Replay a stored result, or run `guard` (e.g. rate limiting) and then `handler`.

        A replay never reaches the guard, so retries always return the original result.
        """
        if key is None:
            raise key_required()
        if not KEY_PATTERN.fullmatch(key):
            raise DiscoveryError(
                "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be 1-128 visible ASCII characters.", 422
            )
        digest = request_digest(payload)
        stored = await self._stored(user_id, operation, key, digest, now)
        if stored is not None:
            return stored
        try:
            if guard is not None:
                await guard()
            status, body = await handler()
            self.session.add(
                IdempotencyRecord(
                    user_id=user_id,
                    operation=operation,
                    idempotency_key=key,
                    request_sha256=digest,
                    response_status=status,
                    response_body=body,
                    created_at=now,
                    expires_at=now + IDEMPOTENCY_TTL,
                )
            )
            await self.session.commit()
        except IntegrityError:
            # A concurrent request with the same key committed first: return its result.
            await self.session.rollback()
            stored = await self._stored(user_id, operation, key, digest, now)
            if stored is None:
                raise
            return stored
        except BaseException:
            await self.session.rollback()
            # A concurrent request with the same key may have committed while this one
            # waited for the case lock and then failed (e.g. VERSION_CONFLICT): replay it.
            stored = await self._stored(user_id, operation, key, digest, now)
            if stored is not None:
                return stored
            raise
        return status, body

    async def _stored(self, user_id, operation, key, digest, now) -> tuple[int, dict] | None:
        record = await self.repo.idempotency_record(user_id, operation, key)
        if record is None:
            return None
        if record.expires_at <= now:
            await self.session.delete(record)
            await self.session.flush()
            return None
        if record.request_sha256 != digest:
            raise key_reused()
        return record.response_status, record.response_body
