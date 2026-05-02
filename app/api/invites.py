"""Invite approval endpoints."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user
from app.db.repos import AuditRepo, InviteRepo
from app.db.session import get_db
from app.workers.send_worker import dispatch_invite

router = APIRouter(prefix="/invites", tags=["invites"])


class InviteOut(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    draft_subject: str
    draft_body: str
    status: str
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    rejected_reason: str | None = None
    created_at: datetime


class ApproveRequest(BaseModel):
    pass


class RejectRequest(BaseModel):
    reason: str | None = None


def _to_out(invite: Any) -> InviteOut:
    return InviteOut(
        id=invite.id,
        match_id=invite.match_id,
        candidate_id=invite.candidate_id,
        job_id=invite.job_id,
        draft_subject=invite.draft_subject,
        draft_body=invite.draft_body,
        status=invite.status,
        approved_by=invite.approved_by,
        approved_at=invite.approved_at,
        rejected_reason=invite.rejected_reason,
        created_at=invite.created_at,
    )


@router.get("", response_model=list[InviteOut])
async def list_invites(
    status: str | None = None,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[InviteOut]:
    invites = await InviteRepo(db).list_by_status(ctx.tenant_id, status)
    return [_to_out(inv) for inv in invites]


@router.post("/{invite_id}/approve", response_model=InviteOut)
async def approve_invite(
    invite_id: uuid.UUID,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InviteOut:
    repo = InviteRepo(db)
    invite = await repo.get(ctx.tenant_id, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Invite is not awaiting approval (current status: {invite.status})",
        )

    ok = await repo.approve(ctx.tenant_id, invite_id, ctx.user_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Approval failed — concurrent modification?")

    await AuditRepo(db).log(
        ctx.tenant_id,
        "invite_approved",
        actor_id=ctx.user_id,
        entity_type="invite",
        entity_id=invite_id,
    )

    dispatch_invite.delay(
        invite_id=str(invite_id),
        tenant_id=str(ctx.tenant_id),
    )

    invite = await repo.get(ctx.tenant_id, invite_id)
    return _to_out(invite)


@router.post("/{invite_id}/reject", response_model=InviteOut)
async def reject_invite(
    invite_id: uuid.UUID,
    body: RejectRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InviteOut:
    repo = InviteRepo(db)
    invite = await repo.get(ctx.tenant_id, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Invite is not awaiting approval (current status: {invite.status})",
        )

    ok = await repo.reject(ctx.tenant_id, invite_id, body.reason)
    if not ok:
        raise HTTPException(status_code=409, detail="Rejection failed — concurrent modification?")

    await AuditRepo(db).log(
        ctx.tenant_id,
        "invite_rejected",
        actor_id=ctx.user_id,
        entity_type="invite",
        entity_id=invite_id,
    )

    invite = await repo.get(ctx.tenant_id, invite_id)
    return _to_out(invite)
