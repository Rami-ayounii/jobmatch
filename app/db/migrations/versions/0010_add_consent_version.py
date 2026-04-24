"""add_consent_version_to_candidates

Revision ID: 0010
Revises: 0002
Create Date: 2026-04-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("consent_version", sa.Text(), nullable=False, server_default="v1"),
    )


def downgrade() -> None:
    op.drop_column("candidates", "consent_version")
