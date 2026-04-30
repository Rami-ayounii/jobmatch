"""Adversarial: cross-tenant retrieval must return zero results for other tenant.

Requires a live Postgres DB — fails with ConnectionRefusedError without Docker.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.security import encrypt_bytes, hash_field
from app.db.repos import CandidateRepo
from app.db.session import AsyncSessionLocal

TENANT_A = uuid.UUID("aaaa0000-0000-0000-0000-000000000001")
TENANT_B = uuid.UUID("bbbb0000-0000-0000-0000-000000000001")
FAKE_EMBEDDING = [0.1] * 1536


async def _seed_tenant_and_candidate(tenant_id: uuid.UUID, tag: str) -> None:
    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text(
                    "INSERT INTO tenants (id, name, clerk_org_id) "
                    "VALUES (:id, :name, :org) ON CONFLICT DO NOTHING"
                ),
                {"id": str(tenant_id), "name": f"Tenant {tag}", "org": f"org_{tag}"},
            )
            candidate_id = uuid.uuid4()
            contact = encrypt_bytes(
                json.dumps({
                    "name": f"Alice {tag}", "email": f"alice@{tag}.com",
                    "consent_ip": "1.2.3.4", "phone": None, "address": None,
                }).encode()
            )
            vec_str = "[" + ",".join(str(x) for x in FAKE_EMBEDDING) + "]"
            await db.execute(
                text("""
                    INSERT INTO candidates (
                        id, tenant_id, full_name_hash, email_hash,
                        contact_jsonb_encrypted, resume_text, structured_jsonb,
                        embedding, r2_key, consent_given_at, consent_version
                    ) VALUES (
                        :id, :tid, :fnh, :eh, :contact, :txt, '{}',
                        :vec::vector(1536), :r2key, now(), 'v1'
                    )
                """),
                {
                    "id": str(candidate_id),
                    "tid": str(tenant_id),
                    "fnh": hash_field(f"Alice {tag}"),
                    "eh": hash_field(f"alice@{tag}.com"),
                    "contact": contact,
                    "txt": f"Alice {tag} is a Python developer",
                    "vec": vec_str,
                    "r2key": f"{tenant_id}/{candidate_id}/resume.pdf",
                },
            )


@pytest.mark.asyncio
async def test_retrieve_hybrid_is_tenant_scoped() -> None:
    """retrieve_hybrid for TENANT_B must never return TENANT_A's candidates."""
    await _seed_tenant_and_candidate(TENANT_A, "alpha")
    await _seed_tenant_and_candidate(TENANT_B, "beta")

    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(TENANT_B)},
            )
            results = await CandidateRepo(db).retrieve_hybrid(
                TENANT_B,
                query_embedding=FAKE_EMBEDDING,
                query_text="Python developer",
                top_k=50,
            )

    for r in results:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                row = await db.execute(
                    text("SELECT tenant_id FROM candidates WHERE id = :id"),
                    {"id": str(r.candidate_id)},
                )
                tenant_id_in_db = row.scalar_one()
                assert str(tenant_id_in_db) == str(TENANT_B), (
                    f"Cross-tenant leak: candidate {r.candidate_id} "
                    f"belongs to {tenant_id_in_db}, not {TENANT_B}"
                )
