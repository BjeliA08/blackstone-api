"""site lifecycle: type, dates, description and status

Revision ID: 009
Revises: 008
Create Date: 2026-08-20

Status is derived from the dates on every read — only `archived` is ever
written here, by an Admin retiring a site. Storing a computed status would
mean a site silently going stale until something remembered to recalculate it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sa.Enum("permanent", "temporary", name="sitetype").create(op.get_bind(), checkfirst=True)
    sa.Enum("active", "upcoming", "ended", "archived", name="sitestatus").create(
        op.get_bind(), checkfirst=True)

    site_type = postgresql.ENUM("permanent", "temporary", name="sitetype", create_type=False)
    site_status = postgresql.ENUM("active", "upcoming", "ended", "archived",
                                  name="sitestatus", create_type=False)

    op.add_column("sites", sa.Column("site_type", site_type, nullable=False,
                                     server_default="permanent"))
    op.add_column("sites", sa.Column("starts_on", sa.Date(), nullable=True))
    op.add_column("sites", sa.Column("ends_on", sa.Date(), nullable=True))
    op.add_column("sites", sa.Column("description", sa.String(), nullable=True))
    # Only ever set to 'archived' by hand; everything else is computed on read.
    op.add_column("sites", sa.Column("status", site_status, nullable=False,
                                     server_default="active"))


def downgrade() -> None:
    for col in ("status", "description", "ends_on", "starts_on", "site_type"):
        op.drop_column("sites", col)
    sa.Enum(name="sitestatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="sitetype").drop(op.get_bind(), checkfirst=True)
