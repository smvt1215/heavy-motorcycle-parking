from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker | None = None


def get_db_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url, echo=settings.debug)
    return _engine


def get_sessionmaker() -> async_sessionmaker:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_db_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session():
    maker = get_sessionmaker()
    async with maker() as session:
        yield session
