"""Fixtures for the control-plane tests: a store and DB under tmp, a client body."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.modules.control_plane.schemas import ProjectCreate, TrackedPromptCreate
from src.modules.control_plane.store import ProjectStore
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

NOW = datetime(2026, 9, 17, 9, tzinfo=UTC)
CLIENT = {
    "brand_name": "GEP",
    "aliases": ["GEP SMART"],
    "domains": ["gep.com"],
    "competitor_domains": ["coupa.com"],
    "lob": "Procurement Software",
    "seed_keywords": ["procurement software"],
}


@pytest.fixture
def store(tmp_path) -> ProjectStore:
    return ProjectStore(tmp_path / "cp.sqlite")


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    return TimeSeriesDB(tmp_path / "cp.sqlite")


@pytest.fixture
def project(store):
    return store.create_project(ProjectCreate(name="GEP procurement", client=CLIENT))


@pytest.fixture
def prompts(store, project):
    return [
        store.add_prompt(
            project.id, TrackedPromptCreate(prompt_text="What is the best procurement software?")
        ),
        store.add_prompt(
            project.id,
            TrackedPromptCreate(
                prompt_text="Is GEP SMART good for source to pay?", important=True, interval="2d"
            ),
        ),
    ]
