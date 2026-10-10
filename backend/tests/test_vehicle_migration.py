"""Non-destructive development-contract migration with historical evidence."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import settings


def test_vehicle_classes_preserve_legacy_rows_without_migrating_preferences():
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(settings.database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, "004_user_features")
        with engine.begin() as conn:
            source = conn.execute(
                text(
                    "INSERT INTO data_sources(code,name,source_type) "
                    "VALUES ('vehicle-migration','migration','GOVERNMENT') RETURNING id"
                )
            ).scalar_one()
            user = conn.execute(
                text(
                    "INSERT INTO users(auth_subject, preferred_vehicle) "
                    "VALUES ('vehicle-migration', 'RED') RETURNING id"
                )
            ).scalar_one()
            conn.execute(text("INSERT INTO user_vehicles(user_id,vehicle_type) VALUES (:id,'YELLOW')"), {"id": user})
            lot = conn.execute(
                text(
                    "INSERT INTO parking_lots(name,location) "
                    "VALUES ('migration','SRID=4326;POINT(121.5 25)') RETURNING id"
                )
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO parking_rules(parking_id,yellow_plate_allowed,red_plate_allowed,"
                    "rule_kind,authority_priority,source_id) VALUES (:id,true,false,'BASELINE',100,:source)"
                ),
                {"id": lot, "source": source},
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT preferred_vehicle,legacy_preferred_vehicle FROM users WHERE id=:id"), {"id": user}
            ).one() == (None, "RED")
            assert conn.execute(
                text("SELECT vehicle_type,legacy_vehicle_type FROM user_vehicles WHERE user_id=:id"), {"id": user}
            ).one() == (None, "YELLOW")
            assert conn.execute(
                text(
                    "SELECT normal_heavy_allowed,large_heavy_allowed,yellow_plate_allowed,"
                    "red_plate_allowed FROM parking_rules WHERE parking_id=:id"
                ),
                {"id": lot},
            ).one() == (None, None, True, False)
        command.downgrade(cfg, "004_user_features")
        with engine.begin() as conn:
            assert (
                conn.execute(text("SELECT preferred_vehicle::text FROM users WHERE id=:id"), {"id": user}).scalar_one()
                == "RED"
            )
            assert (
                conn.execute(
                    text("SELECT vehicle_type::text FROM user_vehicles WHERE user_id=:id"), {"id": user}
                ).scalar_one()
                == "YELLOW"
            )
            conn.execute(text("DELETE FROM users WHERE id=:id"), {"id": user})
            conn.execute(text("DELETE FROM parking_lots WHERE id=:id"), {"id": lot})
            conn.execute(text("DELETE FROM data_sources WHERE id=:id"), {"id": source})
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()
