"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-07-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operators",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("phone_number", sa.String(), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column("setup_code", sa.String(), nullable=True),
        sa.Column("discord_id", sa.String(), nullable=True),
        sa.Column("role", sa.Enum("operator", "director", "admin", name="operatorrole"), nullable=False, server_default="operator"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "sites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), unique=True, nullable=False),
        sa.Column("slot_count", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.create_table(
        "shifts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("site_id", UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("shift_name", sa.String(), nullable=False),
        sa.Column("status", sa.Enum("draft", "approved", name="shiftstatus"), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("shift_id", UUID(as_uuid=True), sa.ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("operator_id", UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("position", sa.String(), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.create_table(
        "check_ins",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("assignment_id", UUID(as_uuid=True), sa.ForeignKey("assignments.id"), nullable=False),
        sa.Column("operator_id", UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("scheduled_start", sa.Time(), nullable=False),
        sa.Column("scheduled_end", sa.Time(), nullable=False),
        sa.Column("actual_check_in", sa.DateTime(), nullable=True),
        sa.Column("actual_check_out", sa.DateTime(), nullable=True),
        sa.Column("status", sa.Enum("pending", "checked_in", "checked_out", name="checkinstatus"), nullable=False, server_default="pending"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    # Indexes for common query patterns
    op.create_index("ix_shifts_date", "shifts", ["date"])
    op.create_index("ix_shifts_site_date", "shifts", ["site_id", "date"])
    op.create_index("ix_assignments_shift", "assignments", ["shift_id"])
    op.create_index("ix_assignments_operator", "assignments", ["operator_id"])
    op.create_index("ix_check_ins_operator", "check_ins", ["operator_id"])
    op.create_index("ix_check_ins_assignment", "check_ins", ["assignment_id"])


def downgrade() -> None:
    op.drop_table("check_ins")
    op.drop_table("assignments")
    op.drop_table("shifts")
    op.drop_table("sites")
    op.drop_table("operators")
    op.execute("DROP TYPE IF EXISTS operatorrole")
    op.execute("DROP TYPE IF EXISTS shiftstatus")
    op.execute("DROP TYPE IF EXISTS checkinstatus")
