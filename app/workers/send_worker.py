"""Send worker — post-approval email dispatch via Resend.

SAFETY INVARIANT: This worker MUST verify status == "approved" AND
approved_by IS NOT NULL before sending any email. This check must
never be removed or weakened.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _dispatch(invite_id: str, tenant_id: str) -> None:
    from sqlalchemy import text

    from app.core.security import decrypt_bytes
    from app.db.repos import AuditRepo, CandidateRepo, InviteRepo
    from app.db.session import AsyncSessionLocal
    from app.services.email import send_email

    tid = uuid.UUID(tenant_id)
    iid = uuid.UUID(invite_id)

    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})

            repo = InviteRepo(db)
            invite = await repo.get(tid, iid)

            # SAFETY INVARIANT — abort if gate not passed
            if invite is None:
                logger.error("send_worker invite_not_found invite_id=%s", invite_id)
                return
            if invite.status != "approved" or invite.approved_by is None:
                logger.error(
                    "send_worker refused invite_id=%s status=%s approved_by=%s",
                    invite_id,
                    invite.status,
                    invite.approved_by,
                )
                return

            # Fetch candidate to get their email address
            candidate = await CandidateRepo(db).get(tid, invite.candidate_id)
            if candidate is None:
                logger.error(
                    "send_worker candidate_not_found invite_id=%s candidate_id=%s",
                    invite_id,
                    invite.candidate_id,
                )
                await repo.mark_failed(tid, iid, "candidate not found")
                return

            try:
                contact = json.loads(decrypt_bytes(candidate.contact_jsonb_encrypted))
                to_email: str = contact["email"]
            except Exception as exc:
                logger.error(
                    "send_worker decrypt_error invite_id=%s error=%s", invite_id, exc
                )
                await repo.mark_failed(tid, iid, f"decrypt error: {exc}"[:500])
                return

            try:
                message_id = await send_email(
                    to=to_email,
                    subject=invite.draft_subject,
                    html=invite.draft_body.replace("\n", "<br>"),
                )
                await repo.mark_sent(tid, iid, message_id)
                await AuditRepo(db).log(
                    tid,
                    "invite_sent",
                    entity_type="invite",
                    entity_id=iid,
                    payload={"resend_message_id": message_id},
                )
                logger.info("send_worker sent invite_id=%s message_id=%s", invite_id, message_id)
            except Exception as exc:
                logger.error(
                    "send_worker send_error invite_id=%s error=%s", invite_id, exc
                )
                await repo.mark_failed(tid, iid, str(exc)[:500])


@celery_app.task(name="send.dispatch_invite", bind=True)
def dispatch_invite(
    self: object,
    invite_id: str,
    tenant_id: str,
) -> None:
    asyncio.run(_dispatch(invite_id, tenant_id))
