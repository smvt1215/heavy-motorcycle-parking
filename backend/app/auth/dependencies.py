from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import AccessTokenService, AuthenticatedUser
from app.db import get_session

# auto_error=False: missing/non-Bearer credentials reach our handler, which returns the
# stable 401 UNAUTHENTICATED envelope instead of FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth", description="Opaque access token")


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthenticatedUser:
    token = credentials.credentials if credentials is not None else None
    return await AccessTokenService(session).authenticate(token, request.state.received_at)


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
