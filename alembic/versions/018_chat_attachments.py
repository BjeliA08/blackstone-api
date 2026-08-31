"""Chat message attachments (invoice uploads into Directors chat)

Revision ID: 018
Revises: 017
Create Date: 2026-08-31

Operators now upload their invoice as a file, tagged with site + period,
instead of the app computing one. It lands as an attachment message in the
existing Directors channel — no parallel storage system, same pattern as
every other chat extension this session (DMs, operation chat).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("attachment_key", sa.String(), nullable=True))
    op.add_column("chat_messages", sa.Column("attachment_filename", sa.String(), nullable=True))
    op.add_column("chat_messages", sa.Column(
        "attachment_site_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("sites.id"), nullable=True))
    op.add_column("chat_messages", sa.Column("attachment_period_month", sa.Integer(), nullable=True))
    op.add_column("chat_messages", sa.Column("attachment_period_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "attachment_period_year")
    op.drop_column("chat_messages", "attachment_period_month")
    op.drop_column("chat_messages", "attachment_site_id")
    op.drop_column("chat_messages", "attachment_filename")
    op.drop_column("chat_messages", "attachment_key")
