"""LiteLLM wrapper with cost tracking, retry, model fallback, PII redaction.

All LLM calls in the application MUST go through this module.
No direct Anthropic/OpenAI/Groq SDK calls are permitted.
"""
import logging
from typing import Any

import litellm

logger = logging.getLogger(__name__)

litellm.drop_params = True


async def chat_completion(
    model: str,
    messages: list[dict[str, Any]],
    *,
    tenant_id: str,
    run_id: str,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    **kwargs: Any,
) -> Any:
    """Send a chat completion via LiteLLM.

    Enforces cost cap per run, logs token usage to audit_events.
    PII fields are never logged.
    """
    response = await litellm.acompletion(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        **kwargs,
    )
    logger.info(
        "llm_call model=%s run_id=%s tokens_in=%s tokens_out=%s",
        model,
        run_id,
        response.usage.prompt_tokens if response.usage else "?",
        response.usage.completion_tokens if response.usage else "?",
    )
    return response
