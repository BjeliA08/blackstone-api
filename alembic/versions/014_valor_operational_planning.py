"""Valor operational planning: venues, vehicles, emergency codes, op chat

Revision ID: 014
Revises: 013
Create Date: 2026-08-25

Adds a per-operation chat channel (extends the existing generic chat_channels
table rather than building a parallel system), venues with optional
lat/lng for mapping, vehicles, and a division-wide emergency codes reference.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New enum value on the existing chat_channels.channel_type type.
    op.execute("ALTER TYPE chatchanneltype ADD VALUE IF NOT EXISTS 'operation'")

    op.add_column("chat_channels", sa.Column(
        "operation_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=True))

    op.create_table(
        "operation_venues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "operation_vehicles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_type", sa.String(), nullable=False),
        sa.Column("plate", sa.String(), nullable=True),
        sa.Column("assigned_operator_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "emergency_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("division_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("meaning", sa.String(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("division_id", "code", name="uq_division_emergency_code"),
    )


def downgrade() -> None:
    op.drop_table("emergency_codes")
    op.drop_table("operation_vehicles")
    op.drop_table("operation_venues")
    op.drop_column("chat_channels", "operation_id")
    # Postgres cannot drop a single enum value; the 'operation' label is left
    # in place on downgrade.
