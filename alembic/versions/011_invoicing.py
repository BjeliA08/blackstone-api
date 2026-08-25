"""contractor invoicing: rates, invoices, invoice line items

Revision ID: 011
Revises: 010
Create Date: 2026-08-24

Operators are contractors, not employees. This is invoicing and margin
visibility only — no tax remittance, no payroll. Line item rate is a
snapshot at generation time; a later rate change never touches history.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("operators", sa.Column("pay_rate", sa.Numeric(10, 2), nullable=True))
    op.add_column("operators", sa.Column("gst_number", sa.String(), nullable=True))
    op.add_column("operators", sa.Column(
        "gst_registered", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.add_column("sites", sa.Column("bill_rate", sa.Numeric(10, 2), nullable=True))

    sa.Enum("draft", "submitted", "approved", "paid", name="invoicestatus").create(
        op.get_bind(), checkfirst=True)
    invoice_status = postgresql.ENUM(
        "draft", "submitted", "approved", "paid", name="invoicestatus", create_type=False)

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("status", invoice_status, nullable=False, server_default="draft"),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("marked_paid_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("total_hours", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("gst_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("operator_id", "period_month", "period_year",
                            name="uq_operator_period_invoice"),
    )

    op.create_table(
        "invoice_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("shift_name", sa.String(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("hours", sa.Numeric(10, 2), nullable=False),
        sa.Column("rate", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("invoice_line_items")
    op.drop_table("invoices")
    sa.Enum(name="invoicestatus").drop(op.get_bind(), checkfirst=True)
    op.drop_column("sites", "bill_rate")
    op.drop_column("operators", "gst_registered")
    op.drop_column("operators", "gst_number")
    op.drop_column("operators", "pay_rate")
