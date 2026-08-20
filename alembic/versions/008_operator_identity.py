"""operator identity: real names, licence, onboarding status, invite codes

Revision ID: 008
Revises: 007
Create Date: 2026-08-20

Splits full_name into first_name/last_name (full_name survives as a computed
hybrid property, so ordering and filtering on it still work in SQL).

Existing operators are grandfathered to `active` rather than
`profile_pending`. Forcing 41 people already working shifts to supply a
photo and licence before they can open the app would lock a live system on
a migration, which is not a decision a schema change should make. Their
missing licence data still shows up for Directors via the expiry status, so
it can be chased deliberately.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sa.Enum("invited", "profile_pending", "active", "deactivated",
            name="onboardingstatus").create(op.get_bind(), checkfirst=True)
    onboarding = postgresql.ENUM(
        "invited", "profile_pending", "active", "deactivated",
        name="onboardingstatus", create_type=False,
    )

    op.add_column("operators", sa.Column("first_name", sa.String(), nullable=True))
    op.add_column("operators", sa.Column("last_name", sa.String(), nullable=True))
    op.add_column("operators", sa.Column("security_licence_number", sa.String(), nullable=True))
    op.add_column("operators", sa.Column("security_licence_expiry", sa.Date(), nullable=True))
    op.add_column("operators", sa.Column("photo_key", sa.String(), nullable=True))
    op.add_column("operators", sa.Column("onboarding_status", onboarding,
                                         nullable=False, server_default="active"))
    op.add_column("operators", sa.Column("invited_by", UUID(as_uuid=True),
                                         sa.ForeignKey("operators.id"), nullable=True))
    op.add_column("operators", sa.Column("activated_at", sa.DateTime(), nullable=True))

    # Split on the first space: everything before is the first name, the rest
    # is the last name. Single-word names keep an empty last name.
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE operators
        SET first_name = split_part(trim(full_name), ' ', 1),
            last_name  = COALESCE(
                NULLIF(substr(trim(full_name), strpos(trim(full_name), ' ') + 1), trim(full_name)),
                ''
            )
    """))
    conn.execute(sa.text(
        "UPDATE operators SET activated_at = created_at WHERE activated_at IS NULL"
    ))

    op.alter_column("operators", "first_name", nullable=False)
    op.alter_column("operators", "last_name", nullable=False,
                    server_default=sa.text("''"))

    op.drop_column("operators", "full_name")

    op.create_table(
        "invite_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(), unique=True, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("intended_role", sa.String(), nullable=True),
        sa.Column("intended_site_access", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.create_index("ix_invite_codes_code", "invite_codes", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_invite_codes_code", "invite_codes")
    op.drop_table("invite_codes")

    op.add_column("operators", sa.Column("full_name", sa.String(), nullable=True))
    op.get_bind().execute(sa.text(
        "UPDATE operators SET full_name = trim(first_name || ' ' || COALESCE(last_name, ''))"
    ))
    op.alter_column("operators", "full_name", nullable=False)

    for col in ("activated_at", "invited_by", "onboarding_status", "photo_key",
                "security_licence_expiry", "security_licence_number",
                "last_name", "first_name"):
        op.drop_column("operators", col)
    sa.Enum(name="onboardingstatus").drop(op.get_bind(), checkfirst=True)
