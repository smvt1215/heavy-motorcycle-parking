"""Operator CLI for access tokens until identity-provider sign-in issues them.

    python -m app.auth.cli issue --subject apple:000123 --role MODERATOR --days 7
    python -m app.auth.cli revoke-user --subject apple:000123

The token is printed once and never stored in plaintext.
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.auth.tokens import AccessTokenService
from app.db import get_db_engine, get_sessionmaker
from app.models import AccessToken, User, UserRole


def parser() -> argparse.ArgumentParser:
    arguments = argparse.ArgumentParser(description="Issue or revoke opaque API access tokens")
    commands = arguments.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue")
    issue.add_argument("--subject", required=True)
    issue.add_argument("--role", choices=[role.value for role in UserRole])
    issue.add_argument("--days", type=int, default=30)
    revoke = commands.add_parser("revoke-user")
    revoke.add_argument("--subject", required=True)
    return arguments


async def run(options: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    try:
        async with get_sessionmaker()() as session, session.begin():
            tokens = AccessTokenService(session)
            if options.command == "issue":
                if not 1 <= options.days <= 365:
                    raise ValueError("--days must be between 1 and 365")
                role = UserRole(options.role) if options.role else None
                user = await tokens.ensure_user(options.subject, role=role)
                token, expires_at = await tokens.issue(user, now, timedelta(days=options.days))
                print(f"{token}\nexpires_at={expires_at.isoformat()}")
            else:
                user_id = await session.scalar(select(User.id).where(User.auth_subject == options.subject))
                if user_id is None:
                    raise ValueError("Unknown subject")
                result = await session.execute(
                    update(AccessToken)
                    .where(AccessToken.user_id == user_id, AccessToken.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
                print(f"revoked={result.rowcount}")
    finally:
        await get_db_engine().dispose()
    return 0


def main() -> None:
    arguments = parser()
    options = arguments.parse_args()
    try:
        raise SystemExit(asyncio.run(run(options)))
    except ValueError as exc:
        arguments.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
