"""seed the valor_director role

Revision ID: 013
Revises: 012
Create Date: 2026-08-25

Valor Collective planning moves out of the regular Director portal into its
own, gated by this role instead of the general director/admin roles.
"""
from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

ROLE_NAME = "valor_director"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO roles (id, name) VALUES (gen_random_uuid(), :name) "
        "ON CONFLICT (name) DO NOTHING"
    ), {"name": ROLE_NAME})


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM roles WHERE name = '{ROLE_NAME}'"))
