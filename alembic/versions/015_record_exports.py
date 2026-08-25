"""Site Records: record_exports table

Revision ID: 015
Revises: 014
Create Date: 2026-08-25

Site Records is an aggregation layer over data that already exists
(shifts, check_ins, invoices, compliance fields) — this is the only new
table it needs. Exports are immutable: a generated PDF is retained and
never regenerated, since the underlying data can drift after the fact.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "record_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("sections_included", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("file_key", sa.String(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("include_rates", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("record_exports")
