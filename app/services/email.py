"""Resend email client — used only by send_worker post human-approval.

SAFETY: send_email is intentionally NOT exposed as an agent tool.
"""
import logging

import resend

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_email(*, to: str, subject: str, html: str) -> str:
    """Send an email via Resend. Returns the message ID."""
    resend.api_key = settings.resend_api_key
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    result = resend.Emails.send(params)
    logger.info("email_sent to=%s message_id=%s", to, result.get("id"))
    return result.get("id", "")
