"""Agent tools for the reasoner node. All tools are read-only or scope-limited."""
from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    success: bool
    data: Any
    error: str | None = None
