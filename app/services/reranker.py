"""BGE reranker singleton (local CPU) via sentence-transformers.

Heavy CPU work runs via asyncio.to_thread to avoid blocking the event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder("BAAI/bge-reranker-base")
        logger.info("BGE reranker loaded")
    return _reranker


async def rerank(query: str, passages: list[str]) -> list[float]:
    """Return reranker scores for each passage (higher = more relevant)."""
    def _score() -> list[float]:
        model = _get_reranker()
        pairs = [(query, p) for p in passages]
        return model.predict(pairs).tolist()

    return await asyncio.to_thread(_score)
