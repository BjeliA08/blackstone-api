"""App settings: PWA branding (icon, name, colors)

Revision ID: 020
Revises: 019
Create Date: 2026-09-15

Singleton table backing the installable PWA's manifest/icon branding —
uploaded via a new admin-only endpoint. Seeds exactly one row with defaults
matching the app's existing brass/near-black visual language.
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("app_icon_key", sa.String(), nullable=True),
        sa.Column("app_name", sa.String(), nullable=False, server_default="Blackstone Ops"),
        sa.Column("theme_color", sa.String(), nullable=False, server_default="#c9a15a"),
        sa.Column("background_color", sa.String(), nullable=False, server_default="#08090c"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO app_settings (id, app_name, theme_color, background_color) "
        "VALUES (:id, 'Blackstone Ops', '#c9a15a', '#08090c')"
    ), {"id": str(uuid.uuid4())})


def downgrade() -> None:
    op.drop_table("app_settings")
