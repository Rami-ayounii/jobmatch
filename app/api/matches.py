"""Match pipeline endpoints — implemented in Phase 4."""
from fastapi import APIRouter

router = APIRouter(prefix="/matches", tags=["matches"])
