"""Durable removal of private objects recorded in `storage_deletion_outbox`.

Usage (retry everything still pending):
    python -m app.services.storage_outbox
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StorageDeletion
from app.services.storage import ObjectStorage

logger = logging.getLogger(__name__)


async def drain(session: AsyncSession, storage: ObjectStorage | None, now: datetime, ids: list[int] | None = None):
    """Delete pending objects; successes are completed, failures stay pending with an attempt count.

    Returns (completed, still_pending). Without storage nothing is attempted.
    """
    statement = select(StorageDeletion).where(StorageDeletion.completed_at.is_(None))
    if ids is not None:
        statement = statement.where(StorageDeletion.id.in_(ids))
    rows = list((await session.scalars(statement.order_by(StorageDeletion.id).with_for_update(skip_locked=True))).all())
    if storage is None:
        await session.rollback()
        return 0, len(rows)
    completed = 0
    for row in rows:
        row.attempts += 1
        try:
            await storage.delete(row.storage_key)
        except Exception as exc:  # noqa: BLE001 - keep the request durable and retry later
            row.last_error = type(exc).__name__[:200]
            logger.warning("Object deletion failed (outbox %s): %s", row.id, row.last_error)
            continue
        row.completed_at = max(now, row.requested_at)
        row.last_error = None
        completed += 1
    await session.commit()
    return completed, len(rows) - completed


async def _main() -> None:
    from app.config import settings
    from app.db import get_db_engine, get_sessionmaker
    from app.services.storage import S3ObjectStorage

    async with get_sessionmaker()() as session:
        completed, pending = await drain(session, S3ObjectStorage.from_settings(settings), datetime.now(UTC))
    await get_db_engine().dispose()
    print(f"completed={completed} pending={pending}")


if __name__ == "__main__":
    asyncio.run(_main())
