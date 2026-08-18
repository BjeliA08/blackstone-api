"""operator availability submission tables

Revision ID: 003
Revises: 002
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "availability_periods",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("opens_at", sa.DateTime(), nullable=False),
        sa.Column("closes_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.Enum("draft", "open", "closed", name="availabilitystatus"),
                  nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "availability_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operator_id", UUID(as_uuid=True),
                  sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_id", UUID(as_uuid=True),
                  sa.ForeignKey("availability_periods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.UniqueConstraint("operator_id", "period_id", name="uq_operator_period"),
    )

    op.create_table(
        "availability_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("submission_id", UUID(as_uuid=True),
                  sa.ForeignKey("availability_submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("shift_name", sa.String(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("earliest_start", sa.Time(), nullable=True),
        sa.Column("latest_end", sa.Time(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
    )

    op.create_index("ix_availability_submissions_period", "availability_submissions", ["period_id"])
    op.create_index("ix_availability_submissions_operator", "availability_submissions", ["operator_id"])
    op.create_index("ix_availability_entries_submission", "availability_entries", ["submission_id"])


def downgrade() -> None:
    op.drop_index("ix_availability_entries_submission", "availability_entries")
    op.drop_index("ix_availability_submissions_operator", "availability_submissions")
    op.drop_index("ix_availability_submissions_period", "availability_submissions")
    op.drop_table("availability_entries")
    op.drop_table("availability_submissions")
    op.drop_table("availability_periods")
    op.execute("DROP TYPE IF EXISTS availabilitystatus")
