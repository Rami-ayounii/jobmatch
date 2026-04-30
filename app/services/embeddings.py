"""Embedding service using OpenAI text-embedding-3-small via LiteLLM."""
import logging

import litellm

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDING_DIM = 1536


async def embed_text(text: str) -> list[float]:
    response = await litellm.aembedding(model=EMBEDDING_MODEL, input=[text])
    return response.data[0]["embedding"]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    response = await litellm.aembedding(model=EMBEDDING_MODEL, input=texts)
    return [item["embedding"] for item in response.data]
