"""Authenticated opaque keyset cursors for sort_version=1."""

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.errors import DiscoveryError
from app.domain.parking import absolute_instant

SORT_VERSION = 1
CURSOR_VERSION = 1


def query_fingerprint(query: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(query, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid encoding")
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(raw) != value:
        raise ValueError("Noncanonical encoding")
    return raw


@dataclass(frozen=True)
class CursorContext:
    evaluation_at: datetime
    key: tuple[int, ...]


class CursorCodec:
    def __init__(self, secret: bytes):
        if len(secret) < 32:
            raise ValueError("Cursor signing key must contain at least 32 bytes")
        self._secret = secret

    def encode(self, fingerprint: str, evaluation_at: datetime, key: tuple[int, ...]) -> str:
        payload = {
            "v": CURSOR_VERSION,
            "sort": SORT_VERSION,
            "query": fingerprint,
            "at": absolute_instant(evaluation_at).isoformat(),
            "key": list(key),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return _encode(raw) + "." + _encode(hmac.digest(self._secret, raw, "sha256"))

    def decode(self, token: str, fingerprint: str, at: datetime | None = None) -> CursorContext:
        try:
            if not isinstance(token, str) or len(token) > 4096:
                raise ValueError("Invalid token size")
            body, signature = token.split(".")
            raw = _decode(body)
            if not hmac.compare_digest(hmac.digest(self._secret, raw, "sha256"), _decode(signature)):
                raise ValueError("Invalid signature")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {"v", "sort", "query", "at", "key"}:
                raise ValueError("Invalid payload")
            if type(payload["v"]) is not int or type(payload["sort"]) is not int:
                raise ValueError("Invalid versions")
            if payload["v"] != CURSOR_VERSION or payload["sort"] != SORT_VERSION:
                raise DiscoveryError("CURSOR_VERSION_UNSUPPORTED", "The cursor version is unsupported.")
            instant = absolute_instant(datetime.fromisoformat(payload["at"]))
            key = payload["key"]
            if not isinstance(key, list) or any(type(part) is not int for part in key):
                raise ValueError("Invalid sort keys")
            if not (
                (len(key) == 4 and key[0] == 0 and -10000 <= key[1] <= 0 and key[2] >= 0 and key[3] > 0)
                or (len(key) == 3 and key[0] == 1 and key[1] >= 0 and key[2] > 0)
            ):
                raise ValueError("Invalid sort tuple")
            if not isinstance(payload["query"], str):
                raise ValueError("Invalid query fingerprint")
        except (ValueError, TypeError, KeyError, binascii.Error, UnicodeDecodeError) as exc:
            raise DiscoveryError("INVALID_CURSOR", "The pagination cursor is invalid.") from exc
        if payload["query"] != fingerprint or (at is not None and absolute_instant(at) != instant):
            raise DiscoveryError("CURSOR_QUERY_MISMATCH", "The cursor does not match this query.")
        return CursorContext(instant, tuple(key))
