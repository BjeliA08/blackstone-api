"""Direct messages between operators

Revision ID: 017
Revises: 016
Create Date: 2026-08-31

Extends the existing generic chat_channels table with a new 'direct'
channel type, same as 'operation' did in 014 — no parallel messaging system.
dm_operator_a_id/dm_operator_b_id (a < b, always) map a pair of operators to
exactly one channel regardless of who starts the conversation.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE chatchanneltype ADD VALUE IF NOT EXISTS 'direct'")

    op.add_column("chat_channels", sa.Column(
        "dm_operator_a_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=True))
    op.add_column("chat_channels", sa.Column(
        "dm_operator_b_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=True))
    op.create_unique_constraint(
        "uq_dm_pair", "chat_channels", ["dm_operator_a_id", "dm_operator_b_id"])


def downgrade() -> None:
    op.drop_constraint("uq_dm_pair", "chat_channels", type_="unique")
    op.drop_column("chat_channels", "dm_operator_b_id")
    op.drop_column("chat_channels", "dm_operator_a_id")
    # Postgres cannot drop a single enum value; the 'direct' label is left
    # in place on downgrade.
