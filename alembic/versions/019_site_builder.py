"""Site Builder: shift pattern templates, positions, features

Revision ID: 019
Revises: 018
Create Date: 2026-08-31

Extends the existing site_shifts table (adds slot_count, based_on_template_id)
rather than introducing a parallel "site_shift_patterns" table — shift
names/times/slot-counts already live there and are read consistently
everywhere that matters (scheduler, check-in, invoicing); a second table for
the same concept would just create two systems to keep in sync.

New tables: shift_pattern_templates (seed library), site_positions,
site_position_assignments, site_features.

Seeds every existing site with a default "Security Operator" position and a
full site_features row-set (enabled=true for all keys except sos, which
matches today's hardcoded Shelter-only gate — only Shelter gets sos=true).
Also links Shelter's and Club101's existing site_shifts rows to the closest
matching seeded template, informationally.
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

FEATURE_KEYS = [
    "sos", "chat", "invoicing", "camera_monitoring",
    "records_export", "availability_submission", "check_in_check_out",
]


def upgrade() -> None:
    sa.Enum(*FEATURE_KEYS, name="sitefeaturekey").create(op.get_bind(), checkfirst=True)
    feature_key_enum = postgresql.ENUM(*FEATURE_KEYS, name="sitefeaturekey", create_type=False)

    op.create_table(
        "shift_pattern_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("default_shifts", sa.JSON(), nullable=False),
    )

    op.add_column("site_shifts", sa.Column("slot_count", sa.Integer(), nullable=True))
    op.add_column("site_shifts", sa.Column(
        "based_on_template_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("shift_pattern_templates.id"), nullable=True))

    op.create_table(
        "site_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_default_position", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "site_position_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("shift_pattern_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("site_shifts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("position_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("site_positions.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("shift_pattern_id", "slot_index", name="uq_site_position_slot"),
    )

    op.create_table(
        "site_features",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True),
                 sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature_key", feature_key_enum, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("site_id", "feature_key", name="uq_site_feature"),
    )

    conn = op.get_bind()

    # --- Seed the template library -----------------------------------------
    templates = [
        {
            "id": str(uuid.uuid4()),
            "name": "Day/Evening/Overnight + Parkade",
            "description": "Shelter's current pattern — three round-the-clock shifts plus a daytime parkade post.",
            "default_shifts": [
                {"name": "Morning", "start_time": "07:00", "end_time": "15:00", "default_slot_count": 3},
                {"name": "Evening", "start_time": "15:00", "end_time": "23:00", "default_slot_count": 3},
                {"name": "Overnight", "start_time": "23:00", "end_time": "07:00", "default_slot_count": 3},
                {"name": "Parkade", "start_time": "06:00", "end_time": "16:30", "default_slot_count": 1},
            ],
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Single Shift",
            "description": "One daily shift covering standard business hours — for a site with one steady post.",
            "default_shifts": [
                {"name": "Day Shift", "start_time": "08:00", "end_time": "20:00", "default_slot_count": 1},
            ],
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Event Night (6-position)",
            "description": "Club101's current pattern — one event-night shift staffed by multiple distinct positions.",
            "default_shifts": [
                {"name": "Event Night", "start_time": "20:00", "end_time": "02:00", "default_slot_count": 6},
            ],
        },
        {
            "id": str(uuid.uuid4()),
            "name": "3x8hr Standard Rotation",
            "description": "Three even 8-hour shifts covering a full day.",
            "default_shifts": [
                {"name": "Shift 1", "start_time": "00:00", "end_time": "08:00", "default_slot_count": 1},
                {"name": "Shift 2", "start_time": "08:00", "end_time": "16:00", "default_slot_count": 1},
                {"name": "Shift 3", "start_time": "16:00", "end_time": "00:00", "default_slot_count": 1},
            ],
        },
        {
            "id": str(uuid.uuid4()),
            "name": "4x6hr Rotation",
            "description": "Four even 6-hour shifts covering a full day.",
            "default_shifts": [
                {"name": "Shift 1", "start_time": "00:00", "end_time": "06:00", "default_slot_count": 1},
                {"name": "Shift 2", "start_time": "06:00", "end_time": "12:00", "default_slot_count": 1},
                {"name": "Shift 3", "start_time": "12:00", "end_time": "18:00", "default_slot_count": 1},
                {"name": "Shift 4", "start_time": "18:00", "end_time": "00:00", "default_slot_count": 1},
            ],
        },
    ]
    import json as _json
    for t in templates:
        conn.execute(sa.text(
            "INSERT INTO shift_pattern_templates (id, name, description, default_shifts) "
            "VALUES (:id, :name, :description, :default_shifts)"
        ), {**t, "default_shifts": _json.dumps(t["default_shifts"])})

    day_evening_overnight_id = templates[0]["id"]
    event_night_id = templates[2]["id"]

    # --- Seed every existing site with a default position + full feature set ---
    sites = conn.execute(sa.text("SELECT id, slug FROM sites")).fetchall()
    for site_id, slug in sites:
        conn.execute(sa.text(
            "INSERT INTO site_positions (id, site_id, name, is_default_position, sort_order) "
            "VALUES (:id, :site_id, 'Security Operator', true, 0)"
        ), {"id": str(uuid.uuid4()), "site_id": site_id})

        for key in FEATURE_KEYS:
            enabled = True if key != "sos" else (slug == "shelter")
            conn.execute(sa.text(
                "INSERT INTO site_features (id, site_id, feature_key, enabled) "
                "VALUES (:id, :site_id, :key, :enabled)"
            ), {"id": str(uuid.uuid4()), "site_id": site_id, "key": key, "enabled": enabled})

    # --- Link Shelter's and Club101's existing shifts to their template, informationally ---
    conn.execute(sa.text(
        "UPDATE site_shifts SET based_on_template_id = :tid "
        "WHERE site_id IN (SELECT id FROM sites WHERE slug = 'shelter')"
    ), {"tid": day_evening_overnight_id})
    conn.execute(sa.text(
        "UPDATE site_shifts SET based_on_template_id = :tid "
        "WHERE site_id IN (SELECT id FROM sites WHERE slug = 'club101')"
    ), {"tid": event_night_id})


def downgrade() -> None:
    op.drop_table("site_features")
    op.drop_table("site_position_assignments")
    op.drop_table("site_positions")
    op.drop_column("site_shifts", "based_on_template_id")
    op.drop_column("site_shifts", "slot_count")
    op.drop_table("shift_pattern_templates")
    sa.Enum(name="sitefeaturekey").drop(op.get_bind(), checkfirst=True)
