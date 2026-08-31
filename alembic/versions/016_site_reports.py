"""Site Reports: Narcan/incident/ejection/EPS/EMS reporting

Revision ID: 016
Revises: 015
Create Date: 2026-08-31

Shelter-only for now, enforced in the router (app/routers/site_reports.py)
via a slug allowlist, not a schema constraint — extending to other sites
later doesn't need a migration. Category-specific fields live in the
`details` JSON column rather than as columns, since categories are expected
to grow (see app/models.py SiteReport docstring).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sa.Enum(
        "narcan_administration", "incident_report", "ejection", "eps_call", "ems_call",
        name="sitereportcategory",
    ).create(op.get_bind(), checkfirst=True)
    category_enum = postgresql.ENUM(
        "narcan_administration", "incident_report", "ejection", "eps_call", "ems_call",
        name="sitereportcategory", create_type=False,
    )

    op.create_table(
        "site_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("site_reports")
    sa.Enum(name="sitereportcategory").drop(op.get_bind(), checkfirst=True)
