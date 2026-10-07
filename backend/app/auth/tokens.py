"""Opaque access tokens.

Tokens are random 256-bit values with a recognizable prefix. Only their SHA-256
digest is stored, so a database leak does not reveal usable credentials. Identity
provider sign-in (Apple/Google/email) is a separate concern that ends by issuing
one of these tokens; provider internals are not part of the parking API contract.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import unauthenticated
from app.models import AccessToken, User, UserRole

TOKEN_PREFIX = "hmp_"
TOKEN_PATTERN = re.compile(r"^hmp_[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True)
class AuthenticatedUser:
    """Identity derived only from a validated token; never from request data."""

    id: int
    role: str
    token_hash: str

    @property
    def is_moderator(self) -> bool:
        return self.role == UserRole.MODERATOR


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AccessTokenService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_user(
        self,
        subject: str,
        *,
        display_name: str | None = None,
        email: str | None = None,
        role: UserRole | None = None,
    ) -> User:
        """Find or create the local profile for an identity-provider subject."""
        user = (await self.session.scalars(select(User).where(User.auth_subject == subject))).one_or_none()
        if user is None:
            user = User(auth_subject=subject, role=role or UserRole.USER)
            self.session.add(user)
        elif role is not None:
            user.role = role
        if display_name is not None:
            user.display_name = display_name
        if email is not None:
            user.email = email
        await self.session.flush()
        return user

    async def issue(self, user: User, now: datetime, ttl: timedelta) -> tuple[str, datetime]:
        token = new_token()
        expires_at = now + ttl
        self.session.add(
            AccessToken(user_id=user.id, token_hash=token_digest(token), created_at=now, expires_at=expires_at)
        )
        await self.session.flush()
        return token, expires_at

    async def authenticate(self, token: str | None, now: datetime) -> AuthenticatedUser:
        if token is None or not TOKEN_PATTERN.fullmatch(token):
            raise unauthenticated()
        digest = token_digest(token)
        row = (
            await self.session.execute(
                select(AccessToken.expires_at, AccessToken.revoked_at, User.id, User.role)
                .join(User, User.id == AccessToken.user_id)
                .where(AccessToken.token_hash == digest)
            )
        ).one_or_none()
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            raise unauthenticated()
        return AuthenticatedUser(row.id, row.role, digest)

    async def revoke(self, token_hash: str, now: datetime) -> None:
        await self.session.execute(
            update(AccessToken)
            .where(AccessToken.token_hash == token_hash, AccessToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
