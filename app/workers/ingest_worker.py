"""Resume ingest worker — parse + embed on upload."""
from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging
import uuid
from typing import Literal

from pydantic import BaseModel, ValidationError

# Top-level imports so tests can patch app.workers.ingest_worker.<name>
from app.core.security import decrypt_bytes, encrypt_bytes
from app.db.repos import AuditRepo, CandidateRepo
from app.db.session import AsyncSessionLocal
from app.services import embeddings, llm, resume_parser, storage
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class EducationEntry(BaseModel):
    degree: str
    institution: str
    year: int | None = None


class ParsedResume(BaseModel):
    skills: list[str]
    experience_years: int | None
    education: list[EducationEntry]
    languages: list[str]
    location: str | None
    seniority_level: Literal["junior", "mid", "senior", "lead", "executive"] | None
    job_titles: list[str]
    certifications: list[str]
    phone: str | None
    address: str | None


def _load_prompt() -> str:
    return (
        importlib.resources.files("app.services.prompts")
        .joinpath("parse_resume.txt")
        .read_text(encoding="utf-8")
    )


async def _call_groq(resume_text: str, run_id: str, tenant_id: str) -> str:
    prompt = _load_prompt().replace("{resume_text}", resume_text)
    response = await llm.chat_completion(
        model="groq/llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        tenant_id=tenant_id,
        run_id=run_id,
        max_tokens=2048,
        temperature=0.0,
    )
    return response.choices[0].message.content


async def _parse_with_groq(
    resume_text: str, run_id: str, tenant_id: str
) -> ParsedResume:
    raw = await _call_groq(resume_text, run_id, tenant_id)
    return ParsedResume(**json.loads(raw))


async def _run_process_resume(run_id: str, candidate_id: str, tenant_id: str) -> None:
    candidate_uuid = uuid.UUID(candidate_id)
    tenant_uuid = uuid.UUID(tenant_id)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            candidate = await CandidateRepo(session).get(tenant_uuid, candidate_uuid)

    if candidate is None:
        logger.warning("ingest_worker candidate_not_found candidate_id=%s", candidate_id)
        return

    # Idempotency: skip if already parsed
    if candidate.embedding is not None:
        logger.info("ingest_worker already_parsed candidate_id=%s", candidate_id)
        return

    filename = candidate.r2_key.rsplit("/", 1)[-1]
    data = await storage.download_resume(candidate.r2_key)
    resume_text = await resume_parser.extract_text(data, filename)

    try:
        parsed = await _parse_with_groq(resume_text, run_id, tenant_id)
        structured: dict = parsed.model_dump()
        phone = parsed.phone
        address = parsed.address
    except (json.JSONDecodeError, ValidationError, Exception) as exc:
        logger.error("ingest_worker parse_error candidate_id=%s error=%s", candidate_id, exc)
        structured = {}
        phone = None
        address = None

    embedding = await embeddings.embed_text(resume_text)

    contact = json.loads(decrypt_bytes(candidate.contact_jsonb_encrypted))
    contact["phone"] = phone
    contact["address"] = address
    new_contact_encrypted = encrypt_bytes(json.dumps(contact).encode())

    async with AsyncSessionLocal() as session:
        async with session.begin():
            await CandidateRepo(session).update_after_parse(
                tenant_uuid,
                candidate_uuid,
                resume_text=resume_text,
                structured_jsonb=structured,
                embedding=embedding,
                contact_jsonb_encrypted=new_contact_encrypted,
            )
            await AuditRepo(session).log(
                tenant_uuid,
                "resume_parsed",
                entity_type="candidate",
                entity_id=candidate_uuid,
                payload={"run_id": run_id},
            )


async def _run_delete_r2_object(r2_key: str) -> None:
    await storage.delete_resume(r2_key)


@celery_app.task(name="ingest.process_resume", bind=True)
def process_resume(self: object, run_id: str, candidate_id: str, tenant_id: str) -> None:
    asyncio.run(_run_process_resume(run_id, candidate_id, tenant_id))


@celery_app.task(name="ingest.delete_r2_object", bind=True)
def delete_r2_object(self: object, r2_key: str) -> None:
    asyncio.run(_run_delete_r2_object(r2_key))
