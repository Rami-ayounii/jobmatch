"""Candidate endpoints."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user
from app.core.security import encrypt_bytes, hash_field
from app.db.repos import AuditRepo, CandidateRepo, MatchRepo
from app.db.session import get_db
from app.services import storage
from app.workers.ingest_worker import delete_r2_object, process_resume

router = APIRouter(prefix="/candidates", tags=["candidates"])

_MAX_FILE_BYTES = 5 * 1024 * 1024
_ALLOWED_EXTENSIONS = {"pdf", "docx", "doc"}


class CandidateResponse(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime


def _validate_file(file: UploadFile, data: bytes) -> None:
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported file type: .{ext}")
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 5 MB limit")


@router.post("", status_code=202, response_model=CandidateResponse)
async def upload_candidate(
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    consent_given_at: Annotated[datetime, Form()],
    consent_version: Annotated[str, Form()],
    consent_ip: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CandidateResponse:
    data = await file.read()
    _validate_file(file, data)

    full_name_hash = hash_field(name)
    email_hash = hash_field(email)
    repo = CandidateRepo(db)

    existing = await repo.find_by_hashes(ctx.tenant_id, full_name_hash, email_hash)
    if existing is not None:
        stat = "processing" if existing.embedding is None else "ready"
        return CandidateResponse(
            id=existing.id, status=stat, created_at=existing.created_at
        )

    contact_blob = json.dumps({
        "name": name.strip(),
        "email": email.strip().lower(),
        "consent_ip": consent_ip,
        "phone": None,
        "address": None,
    }).encode()

    candidate_id = uuid.uuid4()
    r2_key = await storage.upload_resume(
        str(ctx.tenant_id), str(candidate_id), data, file.filename or "resume"
    )

    candidate = await repo.create(
        ctx.tenant_id,
        full_name_hash=full_name_hash,
        email_hash=email_hash,
        contact_jsonb_encrypted=encrypt_bytes(contact_blob),
        resume_text="",
        structured_jsonb={},
        r2_key=r2_key,
        consent_given_at=consent_given_at,
        consent_version=consent_version,
    )

    process_resume.delay(str(uuid.uuid4()), str(candidate.id), str(ctx.tenant_id))

    await AuditRepo(db).log(
        ctx.tenant_id,
        "candidate_uploaded",
        actor_id=ctx.user_id,
        entity_type="candidate",
        entity_id=candidate.id,
    )

    return CandidateResponse(
        id=candidate.id, status="processing", created_at=candidate.created_at
    )


@router.get("/{candidate_id}", response_model=CandidateResponse)
async def get_candidate(
    candidate_id: uuid.UUID,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CandidateResponse:
    info = await CandidateRepo(db).get_status(ctx.tenant_id, candidate_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return CandidateResponse(**info)


@router.delete("/{candidate_id}", status_code=204)
async def delete_candidate(
    candidate_id: uuid.UUID,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    repo = CandidateRepo(db)
    candidate = await repo.get(ctx.tenant_id, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    r2_key = candidate.r2_key
    deleted = await repo.soft_delete(ctx.tenant_id, candidate_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Candidate not found")

    await MatchRepo(db).anonymize_candidate(ctx.tenant_id, candidate_id)
    delete_r2_object.delay(r2_key)

    await AuditRepo(db).log(
        ctx.tenant_id,
        "candidate_deleted",
        actor_id=ctx.user_id,
        entity_type="candidate",
        entity_id=candidate_id,
    )
