"""SOS entries: Postgres-backed trespass/SOS registry

Revision ID: 021
Revises: 020
Create Date: 2026-09-15

Replaces the Google Sheets "SOS Registry" as this app's data source (the
Discord bot's own Sheets-based SOS flow is untouched and keeps running
independently). No data migration — starts empty.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None

SOS_STATUS = ["pending", "active", "expired", "rejected"]
SOS_LENGTH = ["7_days", "14_days", "30_days", "3_months", "6_months", "1_year", "indefinite"]


def upgrade() -> None:
    sa.Enum(*SOS_STATUS, name="sosstatus").create(op.get_bind(), checkfirst=True)
    sa.Enum(*SOS_LENGTH, name="soslength").create(op.get_bind(), checkfirst=True)
    status_enum = postgresql.ENUM(*SOS_STATUS, name="sosstatus", create_type=False)
    length_enum = postgresql.ENUM(*SOS_LENGTH, name="soslength", create_type=False)

    op.create_table(
        "sos_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="pending"),
        sa.Column("trespassed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("length", length_enum, nullable=False),
        sa.Column("date_posted", sa.Date(), nullable=False),
        sa.Column("calculated_end_date", sa.Date(), nullable=True),
        sa.Column("photo_key", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("sos_entries")
    sa.Enum(name="soslength").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="sosstatus").drop(op.get_bind(), checkfirst=True)
