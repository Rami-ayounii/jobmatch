"""Resume ingest worker — parse + embed on upload."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class EducationEntry(BaseModel):
    degree: str
    institution: str
    year: int | None = None


class ParsedResume(BaseModel):
    skills: list[str]
    experience_years: int | None
    education: list[EducationEntry]
    languages: list[str]
    location: str | None
    seniority_level: Literal["junior", "mid", "senior", "lead", "executive"] | None
    job_titles: list[str]
    certifications: list[str]
    phone: str | None
    address: str | None


@celery_app.task(name="ingest.process_resume", bind=True)
def process_resume(self: object, run_id: str, candidate_id: str, tenant_id: str) -> None:
    raise NotImplementedError("Implemented in Task 9")


@celery_app.task(name="ingest.delete_r2_object", bind=True)
def delete_r2_object(self: object, r2_key: str) -> None:
    raise NotImplementedError("Implemented in Task 10")
