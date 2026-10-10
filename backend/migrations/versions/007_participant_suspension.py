"""Moderator suspension of community participants.

Revision ID: 007_participant_suspension
Revises: 006_community_verification

A suspended participant cannot submit cases, stances or photos, and their
active stances stop counting toward corroboration or objections. Additive;
the downgrade drops only these columns and their constraint.
"""

import sqlalchemy as sa
from alembic import op

revision = "007_participant_suspension"
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


def downgrade() -> None:
    op.drop_constraint("ck_community_participants_suspension_has_reason", "community_participants", type_="check")
    op.drop_column("community_participants", "suspended_reason")
    op.drop_column("community_participants", "suspended_at")
