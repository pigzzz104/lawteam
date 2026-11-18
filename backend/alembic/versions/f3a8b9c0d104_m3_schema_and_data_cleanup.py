"""m3_schema_and_data_cleanup

Revision ID: f3a8b9c0d104
Revises: e9b4c2d1f702
Create Date: 2026-11-18 19:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = "f3a8b9c0d104"
down_revision: Union[str, None] = "e9b4c2d1f702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "default_deadline_offsets_days",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "default_schedule_offsets_minutes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    op.add_column(
        "cases",
        sa.Column("charge_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )

    # Migrate legacy free-text criminal charge names placed in cases.crime_type into cases.charge_name
    op.execute(
        sa.text(
            """
            UPDATE cases
            SET charge_name = COALESCE(charge_name, crime_type),
                crime_type = '一般犯罪'
            WHERE UPPER(case_category::text) = 'CRIMINAL'
              AND crime_type IS NOT NULL
              AND crime_type NOT IN ('一般犯罪', '流窜/多次/结伙作案')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE cases
            SET crime_type = '一般犯罪'
            WHERE UPPER(case_category::text) = 'CRIMINAL'
              AND (crime_type IS NULL OR crime_type = '')
            """
        )
    )

    op.add_column("deadlines", sa.Column("notify_owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_deadlines_notify_owner_id_users",
        "deadlines",
        "users",
        ["notify_owner_id"],
        ["id"],
    )

    op.add_column(
        "reminder_deliveries",
        sa.Column(
            "sent_user_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "reminder_deliveries",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("'0'"),
        ),
    )
    op.add_column(
        "reminder_deliveries",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reminder_deliveries", "next_retry_at")
    op.drop_column("reminder_deliveries", "retry_count")
    op.drop_column("reminder_deliveries", "sent_user_ids")

    op.drop_constraint("fk_deadlines_notify_owner_id_users", "deadlines", type_="foreignkey")
    op.drop_column("deadlines", "notify_owner_id")

    op.drop_column("cases", "charge_name")
    op.drop_column("users", "default_schedule_offsets_minutes")
    op.drop_column("users", "default_deadline_offsets_days")
