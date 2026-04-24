"""Tests for ParsedResume Pydantic schema."""
import pytest
from pydantic import ValidationError

from app.workers.ingest_worker import ParsedResume


def test_valid_minimal():
    data = {
        "skills": [],
        "experience_years": None,
        "education": [],
        "languages": [],
        "location": None,
        "seniority_level": None,
        "job_titles": [],
        "certifications": [],
        "phone": None,
        "address": None,
    }
    parsed = ParsedResume.model_validate(data)
    assert parsed.skills == []
    assert parsed.experience_years is None


def test_valid_full():
    data = {
        "skills": ["Python", "SQL"],
        "experience_years": 5,
        "education": [{"degree": "BSc", "institution": "MIT", "year": 2018}],
        "languages": ["English", "French"],
        "location": "Paris, FR",
        "seniority_level": "senior",
        "job_titles": ["Software Engineer"],
        "certifications": ["AWS SAA"],
        "phone": "+33612345678",
        "address": "12 Rue de la Paix, Paris",
    }
    parsed = ParsedResume.model_validate(data)
    assert parsed.seniority_level == "senior"
    assert parsed.education[0].degree == "BSc"
    assert parsed.education[0].year == 2018


def test_invalid_seniority_raises():
    data = {
        "skills": [],
        "experience_years": None,
        "education": [],
        "languages": [],
        "location": None,
        "seniority_level": "god-tier",
        "job_titles": [],
        "certifications": [],
        "phone": None,
        "address": None,
    }
    with pytest.raises(ValidationError):
        ParsedResume.model_validate(data)


def test_missing_required_field_raises():
    with pytest.raises(ValidationError):
        ParsedResume.model_validate({"skills": ["Python"]})
