"""add_auto_triage_policies

Revision ID: e9b4c2d1f702
Revises: d4e2a1f8b901
Create Date: 2026-11-18 11:52:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = "e9b4c2d1f702"
down_revision: Union[str, None] = "d4e2a1f8b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auto_triage_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("policy_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("label", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("mode", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_auto_triage_policies_policy_key"),
        "auto_triage_policies",
        ["policy_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_auto_triage_policies_policy_key"), table_name="auto_triage_policies")
    op.drop_table("auto_triage_policies")
