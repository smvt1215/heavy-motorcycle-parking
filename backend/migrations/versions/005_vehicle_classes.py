"""Development contract: class permissions and two rider vehicle classes.

No saved user preference/vehicle is migrated. Historical labels and plate-level
rules remain evidence; class permissions start NULL until a verified reimport.
Rates with plate-specific applicability remain unconfirmed (vehicle=NULL).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "005_vehicle_classes"
down_revision = "004_user_features"
branch_labels = None
depends_on = None

TABLE_COLUMNS = (("users", "preferred_vehicle"), ("user_vehicles", "vehicle_type"), ("parking_rates", "vehicle_type"))


def upgrade():
    op.add_column("parking_rules", sa.Column("normal_heavy_allowed", sa.Boolean(), nullable=True))
    op.add_column("parking_rules", sa.Column("large_heavy_allowed", sa.Boolean(), nullable=True))
    op.drop_constraint("ck_users_preferred_vehicle_heavy", "users", type_="check")
    for table, column in TABLE_COLUMNS:
        op.add_column(table, sa.Column(f"legacy_{column}", sa.String(16), nullable=True))
        op.execute(f"UPDATE {table} SET legacy_{column} = {column}::text")
        op.alter_column(table, column, type_=sa.Text(), postgresql_using=f"{column}::text", nullable=True)
        # CAR is an internal source-rate channel, never a public rider value.
        expression = "CASE WHEN vehicle_type = 'CAR' THEN 'CAR' ELSE NULL END" if table == "parking_rates" else "NULL"
        op.execute(f"UPDATE {table} SET {column} = {expression}")
    op.execute("DROP TYPE vehicle_type")
    postgresql.ENUM("NORMAL_HEAVY", "LARGE_HEAVY", "CAR", name="vehicle_type").create(op.get_bind())
    for table, column in TABLE_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=postgresql.ENUM(name="vehicle_type", create_type=False),
            postgresql_using=f"{column}::vehicle_type",
        )
    op.create_check_constraint(
        "ck_users_preferred_vehicle_heavy",
        "users",
        "preferred_vehicle IS NULL OR preferred_vehicle IN ('NORMAL_HEAVY', 'LARGE_HEAVY')",
    )


def downgrade():
    op.drop_constraint("ck_users_preferred_vehicle_heavy", "users", type_="check")
    for table, column in TABLE_COLUMNS:
        op.alter_column(table, column, type_=sa.Text(), postgresql_using=f"{column}::text")
        fallback = (
            f"CASE WHEN {column} = 'LARGE_HEAVY' THEN 'RED' "
            f"WHEN {column} = 'NORMAL_HEAVY' THEN 'WHITE' ELSE {column} END"
        )
        op.execute(f"UPDATE {table} SET {column} = COALESCE(legacy_{column}, {fallback})")
    op.execute("DROP TYPE vehicle_type")
    postgresql.ENUM("GREEN", "WHITE", "YELLOW", "RED", "CAR", name="vehicle_type").create(op.get_bind())
    for table, column in TABLE_COLUMNS:
        if table == "user_vehicles":
            # Newly-created class rows have a deterministic old representative;
            # historical rows restore their exact original label.
            op.alter_column(table, column, nullable=False)
        op.alter_column(
            table,
            column,
            type_=postgresql.ENUM(name="vehicle_type", create_type=False),
            postgresql_using=f"{column}::vehicle_type",
        )
        op.drop_column(table, f"legacy_{column}")
    # Old preferences supported only yellow/red. New normal preference is unset.
    op.execute("UPDATE users SET preferred_vehicle = NULL WHERE preferred_vehicle::text NOT IN ('YELLOW', 'RED')")
    op.create_check_constraint(
        "ck_users_preferred_vehicle_heavy",
        "users",
        "preferred_vehicle IS NULL OR preferred_vehicle IN ('YELLOW', 'RED')",
    )
    op.drop_column("parking_rules", "large_heavy_allowed")
    op.drop_column("parking_rules", "normal_heavy_allowed")
