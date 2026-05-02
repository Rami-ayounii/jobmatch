# ADR 0006 — Phase 5: Human Approval Gate Complete

**Date:** 2026-05-02
**Status:** Accepted
**Branch:** master

## What was built

- `GET /invites` — lists tenant invites, optionally filtered by status
- `POST /invites/{id}/approve` — authenticated recruiter approval; dispatches send_worker
- `POST /invites/{id}/reject` — authenticated recruiter rejection with optional reason
- `send_worker.dispatch_invite` — Celery task; decrypts candidate email, calls Resend, marks sent/failed
- `InviteRepo.list_by_status`, `mark_sent`, `mark_failed` — new repo methods

## Safety invariants enforced

1. `send_worker` aborts if `status != "approved"` OR `approved_by IS NULL` (double check against DB manipulation)
2. `send_email` does NOT appear in the agent's `_TOOL_SCHEMAS` — verified by unit test
3. Adversarial test confirms send_worker is blocked for all non-approved states (awaiting_approval, rejected, sent, null approved_by)

## Trade-offs

- `dispatch_invite` is called immediately on approve (not via a queue-scan pattern). This is simpler and sufficient for v1 volumes.
- `contact_jsonb_encrypted` contains the candidate email — decryption happens inside the worker transaction to minimise the time PII is in memory.
- Email body is plain text from `draft_body`, converted to minimal HTML (newlines → `<br>`). No templating engine added.
- `rejected_reason` field is reused for `mark_failed` error messages (avoids adding a new column).
- Tests patch at source module paths (`app.db.session.AsyncSessionLocal`, `app.core.security.decrypt_bytes`) because `_dispatch` uses deferred imports to avoid circular imports at Celery worker startup.

## What was skipped / stubbed

- Resend webhook status updates (bounces, opens) — Phase 7+
- Retry policy for failed sends (Celery `max_retries`) — Phase 7+
- Per-invite email preview in dashboard — Phase 8

## What's next

Phase 6: Eval harness (gold set, Promptfoo, NDCG@5).
