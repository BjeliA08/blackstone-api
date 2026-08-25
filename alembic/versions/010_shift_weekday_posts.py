"""site_shifts.weekday_posts: per-weekday post-count override

Revision ID: 010
Revises: 009
Create Date: 2026-08-24

Some sites (Club101, Starhall) need a different number of operators on a
Friday than a Tuesday. Null keeps the existing flat site.slot_count behavior
for every site that has never touched this.
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("site_shifts", sa.Column("weekday_posts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("site_shifts", "weekday_posts")
