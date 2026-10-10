"""Community moderation: participant suspension and durable photo-object deletion.

Revision ID: 007_community_moderation
Revises: 006_community_verification

A suspended participant cannot submit cases, stances or photos, and their
active stances stop counting toward corroboration or objections.

Deleting evidence clears the photo row in the same transaction that records the
object key in `storage_deletion_outbox`; the object is removed afterwards and
retried until it succeeds, so a transient storage failure never orphans a
private image. Additive; the downgrade drops only these columns and the table.
"""

import sqlalchemy as sa
from alembic import op

revision = "007_community_moderation"
down_revision = "006_community_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("community_participants", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("community_participants", sa.Column("suspended_reason", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_community_participants_suspension_has_reason",
        "community_participants",
        "(suspended_at IS NULL) = (suspended_reason IS NULL)",
    )
    op.create_table(
        "storage_deletion_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_storage_deletion_outbox"),
        sa.UniqueConstraint("storage_key", name="uq_storage_deletion_outbox_storage_key"),
        sa.CheckConstraint("attempts >= 0", name="ck_storage_deletion_outbox_attempts_nonnegative"),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= requested_at",
            name="ck_storage_deletion_outbox_completed_after_requested",
        ),
    )
    op.create_index(
        "ix_storage_deletion_outbox_pending",
        "storage_deletion_outbox",
        ["requested_at"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_storage_deletion_outbox_pending", table_name="storage_deletion_outbox")
    op.drop_table("storage_deletion_outbox")
    op.drop_constraint("ck_community_participants_suspension_has_reason", "community_participants", type_="check")
    op.drop_column("community_participants", "suspended_reason")
    op.drop_column("community_participants", "suspended_at")
