import pytest
from alembic import command
from alembic.config import Config


def test_migrations_up_down():
    """
    Test that Alembic migrations can run up and down from the baseline.
    We just run it against the test database.
    """
    # Assuming we are running this inside a test environment with a valid database
    # For CI, this will use the service container DB.
    # It might fail if run locally without DB, but it fulfills the test coverage requirement.
    alembic_cfg = Config("migrations/alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")

    try:
        # Upgrade to head
        command.upgrade(alembic_cfg, "head")
        # Downgrade to base
        command.downgrade(alembic_cfg, "base")
        # Upgrade back to head to leave db in correct state
        command.upgrade(alembic_cfg, "head")
        assert True
    except Exception as e:
        pytest.fail(f"Migration test failed: {e}")
