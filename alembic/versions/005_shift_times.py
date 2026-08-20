"""shift start/end times on site_shifts

Revision ID: 005
Revises: 004
Create Date: 2026-08-20

Shifts carry their real hours so operators can see what they are committing
to, and so the optional "not before / not after" limits read against a known
window. Overnight crosses midnight (end <= start), which the existing
duration/overlap helpers already handle.
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

SHELTER_TIMES = {
    "Morning":   ("07:00", "15:00"),
    "Evening":   ("15:00", "23:00"),
    "Overnight": ("23:00", "07:00"),
    "Parkade":   ("06:00", "16:30"),
}


def upgrade() -> None:
    op.add_column("site_shifts", sa.Column("start_time", sa.Time(), nullable=True))
    op.add_column("site_shifts", sa.Column("end_time", sa.Time(), nullable=True))

    conn = op.get_bind()
    site = conn.execute(sa.text("SELECT id FROM sites WHERE slug = 'shelter'")).fetchone()
    if site:
        for name, (start, end) in SHELTER_TIMES.items():
            conn.execute(sa.text(
                "UPDATE site_shifts SET start_time = CAST(:s AS time), end_time = CAST(:e AS time) "
                "WHERE site_id = :site AND shift_name = :name"
            ), {"s": start, "e": end, "site": site[0], "name": name})


def downgrade() -> None:
    op.drop_column("site_shifts", "end_time")
    op.drop_column("site_shifts", "start_time")
