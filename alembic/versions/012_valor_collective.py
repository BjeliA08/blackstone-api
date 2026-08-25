"""Valor Collective: divisions, CP roster, operations, client profiles

Revision ID: 012
Revises: 011
Create Date: 2026-08-25

A separate division sitting alongside site-based operations, not inside
them. division_operators is a separate grant from site_access — no operator
is CP-qualified by default. threat_notes on operations is Director/Admin
only, enforced in the schema layer, not here.
"""
import uuid as uuid_module

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sa.Enum("planning", "confirmed", "active", "completed", "cancelled",
           name="operationstatus").create(op.get_bind(), checkfirst=True)
    operation_status = postgresql.ENUM(
        "planning", "confirmed", "active", "completed", "cancelled",
        name="operationstatus", create_type=False)

    op.create_table(
        "divisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
    )

    op.create_table(
        "division_operators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("division_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cp_qualifications", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("added_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("operator_id", "division_id", name="uq_operator_division"),
    )

    op.create_table(
        "operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("division_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("operation_name", sa.String(), nullable=False),
        sa.Column("status", operation_status, nullable=False, server_default="planning"),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("threat_notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "operation_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_name", sa.String(), nullable=False),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "client_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("headline", sa.String(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("skills", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("years_experience", sa.Integer(), nullable=True),
        sa.Column("photo_key", sa.String(), nullable=True),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    divisions = sa.table(
        "divisions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
    )
    op.bulk_insert(divisions, [{
        "id": uuid_module.uuid4(),
        "slug": "valor-collective",
        "name": "Valor Collective",
        "description": "Close protection services division",
    }])


def downgrade() -> None:
    op.drop_table("client_profiles")
    op.drop_table("operation_roles")
    op.drop_table("operations")
    op.drop_table("division_operators")
    op.drop_table("divisions")
    sa.Enum(name="operationstatus").drop(op.get_bind(), checkfirst=True)
