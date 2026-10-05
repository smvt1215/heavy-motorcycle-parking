from pathlib import Path

from alembic import command
from alembic.config import Config

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

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
