from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alembic_config_is_discoverable_from_backend_dir():
    """`alembic upgrade head` run from backend/ (as the README documents) must find its config."""
    alembic_ini = BACKEND_DIR / "alembic.ini"
    assert alembic_ini.is_file()
    cfg = Config(str(alembic_ini))
    assert Path(cfg.get_main_option("script_location")) == BACKEND_DIR / "migrations"


def test_migrations_up_down():
    """Alembic migrations run up, down to base, and up again against the test database."""
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))

    engine = create_engine(settings.database_url)
    try:
        # Test the M1 downgrade separately: it must drop its tables/types without
        # removing the PostGIS extension owned by the existing M0 baseline.
        command.upgrade(alembic_cfg, "head")
        assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names(schema="public"))
        assert inspect(engine).get_enums(schema="public")
        command.downgrade(alembic_cfg, "001_bootstrap")
        assert not set(Base.metadata.tables).intersection(inspect(engine).get_table_names(schema="public"))
        assert inspect(engine).get_enums(schema="public") == []
        with engine.connect() as connection:
            assert connection.execute(text("SELECT extname FROM pg_extension WHERE extname='postgis'")).scalar_one()
        command.upgrade(alembic_cfg, "head")
        command.downgrade(alembic_cfg, "base")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT extname FROM pg_extension WHERE extname='postgis'")).first() is None
        # Empty-database upgrade must work again, with no dangling enum objects.
        command.upgrade(alembic_cfg, "head")
        assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names(schema="public"))
    finally:
        engine.dispose()


def test_alembic_check_ignores_visible_extension_schemas(monkeypatch):
    """The real Alembic environment must not propose deleting extension tables."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA m1_extension_fixture"))
            connection.execute(text("CREATE TABLE m1_extension_fixture.extension_record (id INTEGER PRIMARY KEY)"))
        url = make_url(settings.database_url).update_query_dict(
            {"options": "-csearch_path=public,m1_extension_fixture"}
        )
        monkeypatch.setattr(settings, "database_url", url.render_as_string(hide_password=False))
        command.check(cfg)
        with engine.connect() as connection:
            assert inspect(connection).has_table("extension_record", schema="m1_extension_fixture")
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS m1_extension_fixture CASCADE"))
        engine.dispose()
