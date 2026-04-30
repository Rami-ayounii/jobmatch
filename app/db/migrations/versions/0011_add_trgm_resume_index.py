"""add_trgm_index_on_resume_text

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-30
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS candidates_resume_trgm_idx "
        "ON candidates USING GIN (resume_text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_index("candidates_resume_trgm_idx", table_name="candidates")
