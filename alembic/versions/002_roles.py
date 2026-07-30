"""role management tables

Revision ID: 002
Revises: 001
Create Date: 2026-07-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

ROLE_NAMES = [
    "admin",
    "director",
    "security_operator",
    "shelter_site_lead",
    "club101_site_lead",
    "starhall_site_lead",
]

SITE_LEAD_SLUG_MAP = {
    "shelter_site_lead": "shelter",
    "club101_site_lead": "club101",
    "starhall_site_lead": "starhall",
}


def upgrade() -> None:
    # ── New tables ────────────────────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(), unique=True, nullable=False),
    )

    op.create_table(
        "operator_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operator_id", UUID(as_uuid=True),
                  sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("assigned_by", UUID(as_uuid=True),
                  sa.ForeignKey("operators.id"), nullable=True),
        sa.UniqueConstraint("operator_id", "role_id", name="uq_operator_role"),
    )

    op.create_table(
        "site_access",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operator_id", UUID(as_uuid=True),
                  sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("granted_by", UUID(as_uuid=True),
                  sa.ForeignKey("operators.id"), nullable=True),
        sa.UniqueConstraint("operator_id", "site_id", name="uq_operator_site"),
    )

    op.create_index("ix_operator_roles_operator", "operator_roles", ["operator_id"])
    op.create_index("ix_site_access_operator", "site_access", ["operator_id"])

    # ── Seed roles ────────────────────────────────────────────────────────────
    conn = op.get_bind()
    for name in ROLE_NAMES:
        conn.execute(sa.text(
            "INSERT INTO roles (id, name) VALUES (gen_random_uuid(), :name) ON CONFLICT (name) DO NOTHING"
        ), {"name": name})

    # ── Seed admin operator (Andriy Bjeli) ───────────────────────────────────
    # Look up by discord_id first, then fall back to full_name
    result = conn.execute(sa.text(
        "SELECT id FROM operators WHERE discord_id = '275476078474149889' "
        "OR full_name ILIKE 'andriy bjeli' LIMIT 1"
    )).fetchone()

    if result:
        andriy_id = result[0]
    else:
        andriy_id = conn.execute(sa.text(
            "INSERT INTO operators (id, full_name, phone_number, role, active) "
            "VALUES (gen_random_uuid(), 'Andriy Bjeli', 'ADMIN_ANDRIY', 'admin', true) "
            "RETURNING id"
        )).fetchone()[0]

    # Assign admin role
    admin_role_id = conn.execute(
        sa.text("SELECT id FROM roles WHERE name = 'admin'")
    ).fetchone()[0]

    conn.execute(sa.text(
        "INSERT INTO operator_roles (id, operator_id, role_id) "
        "VALUES (gen_random_uuid(), :op, :role) ON CONFLICT (operator_id, role_id) DO NOTHING"
    ), {"op": andriy_id, "role": admin_role_id})

    # Grant all three site accesses
    for slug in ("shelter", "club101", "starhall"):
        site_row = conn.execute(
            sa.text("SELECT id FROM sites WHERE slug = :slug"), {"slug": slug}
        ).fetchone()
        if site_row:
            conn.execute(sa.text(
                "INSERT INTO site_access (id, operator_id, site_id) "
                "VALUES (gen_random_uuid(), :op, :site) ON CONFLICT (operator_id, site_id) DO NOTHING"
            ), {"op": andriy_id, "site": site_row[0]})


def downgrade() -> None:
    op.drop_index("ix_site_access_operator", "site_access")
    op.drop_index("ix_operator_roles_operator", "operator_roles")
    op.drop_table("site_access")
    op.drop_table("operator_roles")
    op.drop_table("roles")
