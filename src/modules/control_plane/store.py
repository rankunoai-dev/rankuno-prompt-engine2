"""SQLite persistence for projects and tracked prompts.

Configuration rows are stored as one JSON blob per row (validated on the way in
and out by the `StrictModel`s), because the shape is analyst-facing and will
keep growing; a column per field would mean a migration per UI checkbox.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.core.logger import get_logger
from src.modules.control_plane.schemas import (
    Project,
    ProjectCreate,
    ProjectUpdate,
    TrackedPrompt,
    TrackedPromptCreate,
    TrackedPromptUpdate,
)
from src.modules.prompt_tracking.inputs import parse_prompt_lines
from src.modules.prompt_tracking.schemas import prompt_id_for

__all__ = ["ProjectStore"]

_logger = get_logger("modules.control_plane.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_prompts (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    prompt_id  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_prompts_project ON project_prompts(project_id);
"""

_PROMPT_META = {"id", "project_id", "prompt_id", "created_at", "updated_at"}
_PROJECT_META = {"id", "created_at", "updated_at"}


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class ProjectStore:
    """CRUD for projects and their prompts in one SQLite file."""

    def __init__(self, path: Path) -> None:
        """Open (creating if needed) the store at `path`."""
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- projects ------------------------------------------------------------

    def list_projects(self) -> list[Project]:
        """All projects, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, payload, created_at, updated_at FROM projects "
                "ORDER BY created_at DESC, id"
            ).fetchall()
        return [_project(row) for row in rows]

    def get_project(self, project_id: str) -> Project:
        """One project, or `KeyError`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, payload, created_at, updated_at FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return _project(row)

    def create_project(self, body: ProjectCreate) -> Project:
        """Insert a project and return it."""
        now = _now()
        project = Project(id=_new_id(), created_at=now, updated_at=now, **body.model_dump())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    project.id,
                    project.name,
                    project.model_dump_json(exclude=_PROJECT_META),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        _logger.info("project_created", extra={"project_id": project.id, "name": project.name})
        return project

    def update_project(self, project_id: str, body: ProjectUpdate) -> Project:
        """Apply the supplied fields and return the updated project."""
        current = self.get_project(project_id)
        changes = body.model_dump(exclude_unset=True)
        project = Project.model_validate({**current.model_dump(), **changes, "updated_at": _now()})
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET name = ?, payload = ?, updated_at = ? WHERE id = ?",
                (
                    project.name,
                    project.model_dump_json(exclude=_PROJECT_META),
                    project.updated_at.isoformat(),
                    project_id,
                ),
            )
        # A changed LOB changes every prompt's stable id.
        if "client" in changes and current.client.lob != project.client.lob:
            for prompt in self.list_prompts(project_id):
                self._write_prompt(
                    prompt.model_copy(
                        update={"prompt_id": prompt_id_for(project.client.lob, prompt.prompt_text)}
                    )
                )
        return project

    def delete_project(self, project_id: str) -> None:
        """Delete a project and its prompts. Tracking history is kept."""
        self.get_project(project_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM project_prompts WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        _logger.info("project_deleted", extra={"project_id": project_id})

    # -- prompts -------------------------------------------------------------

    def list_prompts(self, project_id: str) -> list[TrackedPrompt]:
        """Prompts of a project: important first, then oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, project_id, prompt_id, payload, created_at, updated_at "
                "FROM project_prompts WHERE project_id = ? ORDER BY created_at, id",
                (project_id,),
            ).fetchall()
        prompts = [_prompt(row) for row in rows]
        return sorted(prompts, key=lambda p: (not p.important, p.created_at, p.id))

    def get_prompt(self, project_id: str, tracked_id: str) -> TrackedPrompt:
        """One prompt, or `KeyError`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, project_id, prompt_id, payload, created_at, updated_at "
                "FROM project_prompts WHERE project_id = ? AND id = ?",
                (project_id, tracked_id),
            ).fetchone()
        if row is None:
            raise KeyError(tracked_id)
        return _prompt(row)

    def add_prompt(self, project_id: str, body: TrackedPromptCreate) -> TrackedPrompt:
        """Add one prompt. Duplicate text (case-insensitive) returns the existing row."""
        project = self.get_project(project_id)
        for existing in self.list_prompts(project_id):
            if existing.prompt_text.lower() == body.prompt_text.lower():
                return existing
        now = _now()
        prompt = TrackedPrompt(
            id=_new_id(),
            project_id=project_id,
            prompt_id=prompt_id_for(project.client.lob, body.prompt_text),
            created_at=now,
            updated_at=now,
            **body.model_dump(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO project_prompts (id, project_id, prompt_id, payload, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    prompt.id,
                    project_id,
                    prompt.prompt_id,
                    prompt.model_dump_json(exclude=_PROMPT_META),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return prompt

    def import_prompts(self, project_id: str, text: str) -> list[TrackedPrompt]:
        """Add prompts from a prompts-file body (`prompt | keyword | subtopic` lines)."""
        return [
            self.add_prompt(
                project_id,
                TrackedPromptCreate(
                    prompt_text=custom.prompt_text,
                    keyword=custom.keyword,
                    subtopic=custom.subtopic,
                ),
            )
            for custom in parse_prompt_lines(text)
        ]

    def update_prompt(
        self, project_id: str, tracked_id: str, body: TrackedPromptUpdate
    ) -> TrackedPrompt:
        """Apply the supplied fields; `clear_overrides` resets interval/engines/samples."""
        current = self.get_prompt(project_id, tracked_id)
        changes = body.model_dump(exclude_unset=True, exclude={"clear_overrides"})
        if body.clear_overrides:
            changes.update({"interval": None, "engines": None, "samples_per_engine": None})
        prompt = TrackedPrompt.model_validate(
            {**current.model_dump(), **changes, "updated_at": _now()}
        )
        if prompt.prompt_text != current.prompt_text:
            project = self.get_project(project_id)
            prompt = prompt.model_copy(
                update={"prompt_id": prompt_id_for(project.client.lob, prompt.prompt_text)}
            )
        self._write_prompt(prompt)
        return prompt

    def delete_prompt(self, project_id: str, tracked_id: str) -> None:
        """Remove a prompt from the project. Its history stays in the store."""
        self.get_prompt(project_id, tracked_id)
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM project_prompts WHERE project_id = ? AND id = ?",
                (project_id, tracked_id),
            )

    # -- internals -----------------------------------------------------------

    def _write_prompt(self, prompt: TrackedPrompt) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE project_prompts SET prompt_id = ?, payload = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    prompt.prompt_id,
                    prompt.model_dump_json(exclude=_PROMPT_META),
                    prompt.updated_at.isoformat(),
                    prompt.id,
                ),
            )


def _project(row: tuple[object, ...]) -> Project:
    base = ProjectCreate.model_validate_json(str(row[1]))
    return Project(
        id=str(row[0]),
        created_at=datetime.fromisoformat(str(row[2])),
        updated_at=datetime.fromisoformat(str(row[3])),
        **base.model_dump(),
    )


def _prompt(row: tuple[object, ...]) -> TrackedPrompt:
    base = TrackedPromptCreate.model_validate_json(str(row[3]))
    return TrackedPrompt(
        id=str(row[0]),
        project_id=str(row[1]),
        prompt_id=str(row[2]),
        created_at=datetime.fromisoformat(str(row[4])),
        updated_at=datetime.fromisoformat(str(row[5])),
        **base.model_dump(),
    )
