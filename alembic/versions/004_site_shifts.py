"""per-site shift configuration + site-scoped availability entries

Revision ID: 004
Revises: 003
Create Date: 2026-08-20

Availability is captured per site, because sites do not run the same shifts:
Shelter runs Morning/Evening/Overnight/Parkade while Club101 and Starhall
run a single Event Night. Shift names move into a director-editable
site_shifts table rather than being hard-coded.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

# Seeded from the shift names actually in use on the live scheduling sheet.
SEED = {
    "shelter": ["Morning", "Evening", "Overnight", "Parkade"],
    "club101": ["Event Night"],
    "starhall": ["Event Night"],
}


def upgrade() -> None:
    op.create_table(
        "site_shifts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("site_id", UUID(as_uuid=True),
                  sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shift_name", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.UniqueConstraint("site_id", "shift_name", name="uq_site_shift_name"),
    )
    op.create_index("ix_site_shifts_site", "site_shifts", ["site_id"])

    conn = op.get_bind()
    for slug, names in SEED.items():
        row = conn.execute(sa.text("SELECT id FROM sites WHERE slug = :slug"), {"slug": slug}).fetchone()
        if not row:
            continue
        for i, name in enumerate(names):
            conn.execute(sa.text(
                "INSERT INTO site_shifts (id, site_id, shift_name, sort_order) "
                "VALUES (gen_random_uuid(), :site, :name, :ord) "
                "ON CONFLICT (site_id, shift_name) DO NOTHING"
            ), {"site": row[0], "name": name, "ord": i})

    # Site-scope existing availability entries. Anything already stored used
    # Shelter's shift names, so it belongs to Shelter.
    op.add_column("availability_entries", sa.Column("site_id", UUID(as_uuid=True), nullable=True))

    shelter = conn.execute(sa.text("SELECT id FROM sites WHERE slug = 'shelter'")).fetchone()
    if shelter:
        conn.execute(sa.text("UPDATE availability_entries SET site_id = :s WHERE site_id IS NULL"),
                     {"s": shelter[0]})

    # Any row still unscoped has no site to belong to — drop it rather than
    # leave availability that cannot be attributed.
    conn.execute(sa.text("DELETE FROM availability_entries WHERE site_id IS NULL"))

    op.alter_column("availability_entries", "site_id", nullable=False)
    op.create_foreign_key("fk_availability_entries_site", "availability_entries",
                          "sites", ["site_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_availability_entries_site", "availability_entries", ["site_id"])


def downgrade() -> None:
    op.drop_index("ix_availability_entries_site", "availability_entries")
    op.drop_constraint("fk_availability_entries_site", "availability_entries", type_="foreignkey")
    op.drop_column("availability_entries", "site_id")
    op.drop_index("ix_site_shifts_site", "site_shifts")
    op.drop_table("site_shifts")
