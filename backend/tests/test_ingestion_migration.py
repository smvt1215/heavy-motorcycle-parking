"""M4 migration must preserve the existing M1 baseline and restore it on downgrade."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import settings


def test_m4_upgrade_downgrade_preserves_existing_zone_identity():
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(settings.database_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "002_core_schema")
    try:
        with engine.begin() as connection:
            lot = connection.execute(
                text(
                    "INSERT INTO parking_lots(name,location) VALUES ('M4 migration fixture',"
                    "ST_SetSRID(ST_MakePoint(121.56,25.03),4326)::geography) RETURNING id"
                )
            ).scalar_one()
            zone = connection.execute(
                text("INSERT INTO parking_zones(parking_id,space_type) VALUES (:lot,'HEAVY_ONLY') RETURNING id"),
                {"lot": lot},
            ).scalar_one()
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT source_id,external_id FROM parking_zones WHERE id=:zone"), {"zone": zone}
            ).one() == (None, None)
        command.downgrade(cfg, "002_core_schema")
        assert "source_id" not in {column["name"] for column in inspect(engine).get_columns("parking_zones")}
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT parking_id FROM parking_zones WHERE id=:zone"), {"zone": zone}
                ).scalar_one()
                == lot
            )
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM parking_lots WHERE name='M4 migration fixture'"))
        engine.dispose()
