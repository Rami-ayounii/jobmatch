"""Create full schema: all 7 tables, extensions, RLS policies.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extensions (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── tenants ──────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("clerk_org_id", sa.Text, nullable=False, unique=True),
        sa.Column("monthly_cost_cap_usd", sa.Numeric(10, 4), nullable=False, server_default="50"),
        sa.Column("created_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
    )

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("clerk_user_id", sa.Text, nullable=False, unique=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False, server_default="recruiter"),
        sa.Column("created_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('recruiter', 'admin')", name="users_role_check"),
    )
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON users
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)

    # ── candidates ────────────────────────────────────────────────────────────
    op.create_table(
        "candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("full_name_hash", sa.Text, nullable=False),
        sa.Column("email_hash", sa.Text, nullable=False),
        sa.Column("contact_jsonb_encrypted", sa.LargeBinary, nullable=False),
        sa.Column("resume_text", sa.Text, nullable=False),
        sa.Column("structured_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("embedding", sa.Text, nullable=True),  # placeholder; altered below
        sa.Column("r2_key", sa.Text, nullable=False),
        sa.Column("consent_given_at", sa.TIMESTAMP, nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "full_name_hash", "email_hash", name="candidates_dedup_uq"),
    )
    # Replace placeholder embedding column with real vector type
    op.execute("ALTER TABLE candidates DROP COLUMN embedding")
    op.execute("ALTER TABLE candidates ADD COLUMN embedding vector(1536)")
    op.create_index("candidates_tenant_deleted_idx", "candidates", ["tenant_id", "deleted_at"])
    op.execute("""
        CREATE INDEX candidates_embedding_idx ON candidates
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
    """)
    op.execute("ALTER TABLE candidates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE candidates FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON candidates
        USING (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            AND deleted_at IS NULL
        )
    """)

    # ── jobs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description_text", sa.Text, nullable=False),
        sa.Column("structured_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("embedding", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("created_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('active', 'archived')", name="jobs_status_check"),
    )
    op.execute("ALTER TABLE jobs DROP COLUMN embedding")
    op.execute("ALTER TABLE jobs ADD COLUMN embedding vector(1536)")
    op.create_index("jobs_tenant_idx", "jobs", ["tenant_id"])
    op.execute("""
        CREATE INDEX jobs_embedding_idx ON jobs
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
    """)
    op.execute("ALTER TABLE jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE jobs FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON jobs
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)

    # ── matches ───────────────────────────────────────────────────────────────
    op.create_table(
        "matches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("triggered_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("shortlist_jsonb", JSONB, nullable=True),
        sa.Column("agent_trace_jsonb", JSONB, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("duration_s", sa.Numeric(8, 3), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP, nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="matches_status_check",
        ),
    )
    op.create_index("matches_tenant_job_idx", "matches", ["tenant_id", "job_id"])
    op.execute("ALTER TABLE matches ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE matches FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON matches
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)

    # ── invites ───────────────────────────────────────────────────────────────
    op.create_table(
        "invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("match_id", UUID(as_uuid=True), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("candidate_id", UUID(as_uuid=True), sa.ForeignKey("candidates.id"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("draft_subject", sa.Text, nullable=False),
        sa.Column("draft_body", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="awaiting_approval"),
        sa.Column("approved_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP, nullable=True),
        sa.Column("rejected_reason", sa.Text, nullable=True),
        sa.Column("resend_message_id", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('awaiting_approval', 'approved', 'rejected', 'sent', 'failed')",
            name="invites_status_check",
        ),
    )
    op.create_index("invites_tenant_status_idx", "invites", ["tenant_id", "status"])
    op.execute("ALTER TABLE invites ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invites FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON invites
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)

    # ── audit_events ──────────────────────────────────────────────────────────
    op.create_table(
        "audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("audit_events_tenant_type_idx", "audit_events", ["tenant_id", "event_type"])
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    # Tenants can SELECT and INSERT; no UPDATE or DELETE (immutable log)
    op.execute("""
        CREATE POLICY tenant_isolation ON audit_events
        FOR SELECT
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)
    op.execute("""
        CREATE POLICY tenant_insert ON audit_events
        FOR INSERT
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("invites")
    op.drop_table("matches")
    op.drop_table("jobs")
    op.drop_table("candidates")
    op.drop_table("users")
    op.drop_table("tenants")
