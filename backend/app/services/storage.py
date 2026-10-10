"""S3-compatible object storage for community report photos."""

import asyncio
import logging
from typing import Protocol

from app.domain.errors import DiscoveryError

logger = logging.getLogger(__name__)


def storage_unavailable() -> DiscoveryError:
    return DiscoveryError("STORAGE_UNAVAILABLE", "Photo storage is temporarily unavailable.", 503)


class ObjectStorage(Protocol):
    async def put(self, key: str, body: bytes, content_type: str) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def get(self, key: str) -> bytes: ...


class S3ObjectStorage:
    """boto3 client calls run in a worker thread so the event loop stays free."""

    def __init__(self, client, bucket: str):
        self._client = client
        self._bucket = bucket

    @classmethod
    def from_settings(cls, settings) -> "S3ObjectStorage | None":
        if not settings.s3_bucket:
            return None
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value() if settings.s3_access_key_id else None,
            aws_secret_access_key=(
                settings.s3_secret_access_key.get_secret_value() if settings.s3_secret_access_key else None
            ),
        )
        return cls(client, settings.s3_bucket)

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        except Exception as exc:  # noqa: BLE001 - any storage failure is a 503 for the client
            logger.warning("Photo upload failed: %s", type(exc).__name__)
            raise storage_unavailable() from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def get(self, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
            return await asyncio.to_thread(response["Body"].read)
        except Exception as exc:  # noqa: BLE001 - any storage failure is a 503 for the client
            logger.warning("Photo read failed: %s", type(exc).__name__)
            raise storage_unavailable() from exc
