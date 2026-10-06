"""Add source-owned zone identity and complete import envelope evidence.

Revision ID: 003_ingestion_identity
Revises: 002_core_schema
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "003_ingestion_identity"
down_revision = "002_core_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column("freshness_uses_source_timestamp", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("parking_zones", sa.Column("source_id", sa.Integer(), nullable=True))
    op.add_column("parking_zones", sa.Column("external_id", sa.String(255), nullable=True))
    op.add_column("parking_zones", sa.Column("source_active", sa.Boolean(), nullable=True))
    op.create_foreign_key(
        "fk_parking_zones_source_id_data_sources",
        "parking_zones",
        "data_sources",
        ["source_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_parking_zones_source_id_external_id", "parking_zones", ["source_id", "external_id"])
    op.create_check_constraint(
        "ck_parking_zones_external_id_requires_source", "parking_zones", "external_id IS NULL OR source_id IS NOT NULL"
    )
    op.add_column("raw_import_batches", sa.Column("feed_kind", sa.String(32), nullable=True))
    op.add_column("raw_import_batches", sa.Column("raw_payload", JSONB(), nullable=True))
    op.add_column("raw_import_batches", sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("raw_import_batches", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ("fetched_at", "source_updated_at", "raw_payload", "feed_kind"):
        op.drop_column("raw_import_batches", column)
    op.drop_constraint("ck_parking_zones_external_id_requires_source", "parking_zones", type_="check")
    op.drop_constraint("uq_parking_zones_source_id_external_id", "parking_zones", type_="unique")
    op.drop_constraint("fk_parking_zones_source_id_data_sources", "parking_zones", type_="foreignkey")
    op.drop_column("parking_zones", "external_id")
    op.drop_column("parking_zones", "source_id")
    op.drop_column("parking_zones", "source_active")
    op.drop_column("data_sources", "freshness_uses_source_timestamp")
