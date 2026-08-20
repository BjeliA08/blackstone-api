"""chat channels, messages and read markers

Revision ID: 007
Revises: 006
Create Date: 2026-08-20

Channel membership is deliberately NOT stored. It is computed per request
from roles and site access so it stays correct automatically when someone's
role or site grant changes.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

SITE_CHANNELS = [
    ("shelter", "Shelter"),
    ("club101", "Club101"),
    ("starhall", "Starhall"),
]

GROUP_CHANNELS = [
    ("site-leads", "Site Leads", "site_leads"),
    ("directors", "Directors", "directors"),
    ("admin", "Admin", "admin"),
]


def upgrade() -> None:
    # Create the type once, then reference it with create_type=False so
    # create_table does not try to create it a second time.
    sa.Enum("site", "site_leads", "directors", "admin",
            name="chatchanneltype").create(op.get_bind(), checkfirst=True)
    channel_type = postgresql.ENUM(
        "site", "site_leads", "directors", "admin",
        name="chatchanneltype", create_type=False,
    )

    op.create_table(
        "chat_channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(), unique=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("channel_type", channel_type, nullable=False),
        sa.Column("site_id", UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("channel_id", UUID(as_uuid=True),
                  sa.ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operator_id", UUID(as_uuid=True),
                  sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "chat_reads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("channel_id", UUID(as_uuid=True),
                  sa.ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operator_id", UUID(as_uuid=True),
                  sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_read_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("channel_id", "operator_id", name="uq_chat_read"),
    )

    # History is read newest-first per channel, which is exactly this index.
    op.create_index("ix_chat_messages_channel_created", "chat_messages",
                    ["channel_id", "created_at"])
    op.create_index("ix_chat_reads_operator", "chat_reads", ["operator_id"])

    conn = op.get_bind()
    for slug, name in SITE_CHANNELS:
        site = conn.execute(sa.text("SELECT id FROM sites WHERE slug = :s"), {"s": slug}).fetchone()
        conn.execute(sa.text(
            "INSERT INTO chat_channels (id, slug, name, channel_type, site_id) "
            "VALUES (gen_random_uuid(), :slug, :name, 'site', :site) "
            "ON CONFLICT (slug) DO NOTHING"
        ), {"slug": slug, "name": name, "site": site[0] if site else None})

    for slug, name, ctype in GROUP_CHANNELS:
        conn.execute(sa.text(
            "INSERT INTO chat_channels (id, slug, name, channel_type) "
            f"VALUES (gen_random_uuid(), :slug, :name, '{ctype}') "
            "ON CONFLICT (slug) DO NOTHING"
        ), {"slug": slug, "name": name})


def downgrade() -> None:
    op.drop_index("ix_chat_reads_operator", "chat_reads")
    op.drop_index("ix_chat_messages_channel_created", "chat_messages")
    op.drop_table("chat_reads")
    op.drop_table("chat_messages")
    op.drop_table("chat_channels")
    sa.Enum(name="chatchanneltype").drop(op.get_bind(), checkfirst=True)
