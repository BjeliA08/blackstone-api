"""partial fallback coverage on availability entries

Revision ID: 006
Revises: 005
Create Date: 2026-08-20

An operator available for two consecutive shifts who cannot work the whole
second one offers "partial fallback" coverage: usable only when no full
candidate exists for that slot.
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    coverage_type = sa.Enum("full", "partial_fallback", name="coveragetype")
    coverage_type.create(op.get_bind(), checkfirst=True)
    op.add_column("availability_entries", sa.Column(
        "coverage_type", coverage_type, nullable=False, server_default="full"
    ))


def downgrade() -> None:
    op.drop_column("availability_entries", "coverage_type")
    sa.Enum(name="coveragetype").drop(op.get_bind(), checkfirst=True)
