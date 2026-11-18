"""add_user_court_auto_fetch_mode

Revision ID: d4e2a1f8b901
Revises: ac65c9d85c61
Create Date: 2026-11-18 10:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = "d4e2a1f8b901"
down_revision: Union[str, None] = "ac65c9d85c61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "court_auto_fetch_mode",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "court_auto_fetch_mode")
