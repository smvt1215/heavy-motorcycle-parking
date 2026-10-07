"""M8 user features: report vocabulary, roles, preferred vehicle and access tokens.

Revision ID: 004_user_features
Revises: 003_ingestion_identity

Report enums are replaced with the v1 issue vocabulary. Existing rows are mapped
conservatively (anything without an exact counterpart becomes OTHER / PENDING), and
the downgrade maps back the same way.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "004_user_features"
down_revision = "003_ingestion_identity"
branch_labels = None
depends_on = None

NEW_TYPES = (
    "PARKING_ALLOWED",
    "PARKING_NOT_ALLOWED",
    "WRONG_SPACE_TYPE",
    "WRONG_RATE",
    "WRONG_AVAILABILITY",
    "WRONG_ENTRANCE",
    "CLOSED",
    "PLATE_RECOGNITION_FAILED",
    "GATE_SENSOR_FAILED",
    "OTHER",
)
OLD_TYPES = (
    "PERMISSION_CORRECTION",
    "RATE_CORRECTION",
    "AVAILABILITY_CORRECTION",
    "ENTRANCE_CORRECTION",
    "LOCATION_CORRECTION",
    "CLOSURE",
    "OTHER",
)
NEW_STATUSES = ("PENDING", "VERIFIED", "REJECTED", "SUPERSEDED")
OLD_STATUSES = ("PENDING", "UNDER_REVIEW", "ACCEPTED", "REJECTED")

TYPE_UP = {
    "RATE_CORRECTION": "WRONG_RATE",
    "AVAILABILITY_CORRECTION": "WRONG_AVAILABILITY",
    "ENTRANCE_CORRECTION": "WRONG_ENTRANCE",
    "CLOSURE": "CLOSED",
}
TYPE_DOWN = {new: old for old, new in TYPE_UP.items()}
STATUS_UP = {"UNDER_REVIEW": "PENDING", "ACCEPTED": "VERIFIED"}
STATUS_DOWN = {"VERIFIED": "ACCEPTED", "SUPERSEDED": "REJECTED"}


def _case(column: str, mapping: dict[str, str], targets: tuple[str, ...], fallback: str) -> str:
    branches = " ".join(f"WHEN {column}::text = '{old}' THEN '{new}'" for old, new in mapping.items())
    passthrough = ", ".join(f"'{label}'" for label in targets)
    return f"CASE {branches} WHEN {column}::text IN ({passthrough}) THEN {column}::text ELSE '{fallback}' END"


def _swap_enum(name: str, column: str, labels: tuple[str, ...], mapping: dict[str, str], fallback: str) -> None:
    temporary = f"{name}_v2"
    sa.Enum(*labels, name=temporary).create(op.get_bind())
    if column == "status":
        op.execute("ALTER TABLE user_reports ALTER COLUMN status DROP DEFAULT")
    op.execute(
        f"ALTER TABLE user_reports ALTER COLUMN {column} TYPE {temporary} "
        f"USING ({_case(column, mapping, labels, fallback)})::{temporary}"
    )
    op.execute(f"DROP TYPE {name}")
    op.execute(f"ALTER TYPE {temporary} RENAME TO {name}")
    if column == "status":
        op.execute("ALTER TABLE user_reports ALTER COLUMN status SET DEFAULT 'PENDING'")


def upgrade() -> None:
    _swap_enum("user_report_type", "report_type", NEW_TYPES, TYPE_UP, "OTHER")
    _swap_enum("user_report_status", "status", NEW_STATUSES, STATUS_UP, "PENDING")

    op.add_column("users", sa.Column("role", sa.String(16), server_default=sa.text("'USER'"), nullable=False))
    op.add_column(
        "users",
        sa.Column("preferred_vehicle", postgresql.ENUM(name="vehicle_type", create_type=False), nullable=True),
    )
    op.create_check_constraint("ck_users_role", "users", "role IN ('USER', 'MODERATOR')")
    op.create_check_constraint(
        "ck_users_preferred_vehicle_heavy",
        "users",
        "preferred_vehicle IS NULL OR preferred_vehicle IN ('YELLOW', 'RED')",
    )

    op.add_column("user_reports", sa.Column("resolved_by_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_user_reports_resolved_by_user_id_users",
        "user_reports",
        "users",
        ["resolved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "access_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_access_tokens_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_access_tokens_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_access_tokens_expires_after_created"),
        sa.PrimaryKeyConstraint("id", name="pk_access_tokens"),
    )
    op.create_index("ix_access_tokens_user_id", "access_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_access_tokens_user_id", table_name="access_tokens")
    op.drop_table("access_tokens")
    op.drop_constraint("fk_user_reports_resolved_by_user_id_users", "user_reports", type_="foreignkey")
    op.drop_column("user_reports", "resolved_by_user_id")
    op.drop_constraint("ck_users_preferred_vehicle_heavy", "users", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "preferred_vehicle")
    op.drop_column("users", "role")
    _swap_enum("user_report_status", "status", OLD_STATUSES, STATUS_DOWN, "PENDING")
    _swap_enum("user_report_type", "report_type", OLD_TYPES, TYPE_DOWN, "OTHER")
