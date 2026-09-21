"""CRUD tests for projects and tracked prompts."""

from __future__ import annotations

import pytest

from src.integrations.schemas import Engine
from src.modules.control_plane.schemas import (
    ProjectCreate,
    ProjectUpdate,
    TrackedPromptCreate,
    TrackedPromptUpdate,
)
from src.modules.prompt_tracking.schemas import ClientProfile, prompt_id_for
from tests.modules.control_plane.conftest import CLIENT


class TestProjects:
    def test_create_get_list(self, store, project):
        assert project.name == "GEP procurement"
        assert project.engines == list(Engine)
        assert project.interval == "daily"
        assert store.get_project(project.id) == project
        assert [p.id for p in store.list_projects()] == [project.id]

    def test_update_partial_and_interval_validation(self, store, project):
        updated = store.update_project(
            project.id, ProjectUpdate(interval="2d", engines=[Engine.PERPLEXITY], enabled=False)
        )
        assert updated.interval == "2d"
        assert updated.engines == [Engine.PERPLEXITY]
        assert updated.enabled is False
        assert updated.client == project.client
        assert updated.updated_at >= project.updated_at
        with pytest.raises(ValueError):
            ProjectUpdate(interval="fortnightly")
        with pytest.raises(ValueError):
            ProjectCreate(name="x", client=CLIENT, interval="sometimes")

    def test_delete_removes_prompts(self, store, project, prompts):
        store.delete_project(project.id)
        with pytest.raises(KeyError):
            store.get_project(project.id)
        assert store.list_prompts(project.id) == []

    def test_missing_project_is_key_error(self, store):
        with pytest.raises(KeyError):
            store.get_project("nope")
        with pytest.raises(KeyError):
            store.update_project("nope", ProjectUpdate(name="x"))
        with pytest.raises(KeyError):
            store.delete_project("nope")

    def test_lob_change_keeps_prompt_ids(self, store, project, prompts):
        """A LOB rename must not re-key prompts: it would orphan the whole history."""
        before = {p.id: p.prompt_id for p in store.list_prompts(project.id)}
        new_client = ClientProfile(**{**CLIENT, "lob": "Sourcing"})
        store.update_project(project.id, ProjectUpdate(client=new_client))
        for prompt in store.list_prompts(project.id):
            assert prompt.prompt_id == before[prompt.id]
            assert prompt.prompt_id != prompt_id_for("Sourcing", prompt.prompt_text)


class TestPrompts:
    def test_add_lists_important_first_and_dedupes(self, store, project, prompts):
        listed = store.list_prompts(project.id)
        assert listed[0].important is True
        assert listed[0].interval == "2d"
        duplicate = store.add_prompt(
            project.id, TrackedPromptCreate(prompt_text="WHAT IS THE BEST PROCUREMENT SOFTWARE?")
        )
        assert duplicate.id == prompts[0].id
        assert len(store.list_prompts(project.id)) == 2

    def test_prompt_text_is_kept_verbatim(self, store, project):
        prompt = store.add_prompt(
            project.id, TrackedPromptCreate(prompt_text="  which   erp is best for procurement  ")
        )
        assert prompt.prompt_text == "which erp is best for procurement"
        assert prompt.prompt_id == prompt_id_for(project.client.lob, prompt.prompt_text)

    def test_import_from_text(self, store, project):
        added = store.import_prompts(
            project.id, "# c\nOne prompt here | procurement software | Sub\nAnother one\n"
        )
        assert [p.prompt_text for p in added] == ["One prompt here", "Another one"]
        assert added[0].keyword == "procurement software"
        assert added[0].subtopic == "Sub"

    def test_update_overrides_and_clear(self, store, project, prompts):
        pid = prompts[0].id
        updated = store.update_prompt(
            project.id,
            pid,
            TrackedPromptUpdate(important=True, engines=[Engine.GEMINI], samples_per_engine=5),
        )
        assert updated.important is True
        assert updated.engines == [Engine.GEMINI]
        assert updated.samples_per_engine == 5
        cleared = store.update_prompt(project.id, pid, TrackedPromptUpdate(clear_overrides=True))
        assert cleared.engines is None
        assert cleared.interval is None
        assert cleared.samples_per_engine is None
        assert cleared.important is True

    def test_update_text_keeps_prompt_id(self, store, project, prompts):
        """Fixing a typo must not detach the prompt's tracking history."""
        original = prompts[0].prompt_id
        updated = store.update_prompt(
            project.id, prompts[0].id, TrackedPromptUpdate(prompt_text="new text here")
        )
        assert updated.prompt_text == "new text here"
        assert updated.prompt_id == original
        assert updated.prompt_id != prompt_id_for(project.client.lob, "new text here")
        # and it survives the round trip through storage
        assert store.get_prompt(project.id, prompts[0].id).prompt_id == original

    def test_delete_and_missing(self, store, project, prompts):
        store.delete_prompt(project.id, prompts[0].id)
        assert len(store.list_prompts(project.id)) == 1
        with pytest.raises(KeyError):
            store.get_prompt(project.id, prompts[0].id)
        with pytest.raises(KeyError):
            store.delete_prompt(project.id, "nope")
        with pytest.raises(KeyError):
            store.add_prompt("no-project", TrackedPromptCreate(prompt_text="abc def"))

    def test_invalid_interval_override_rejected(self):
        with pytest.raises(ValueError):
            TrackedPromptCreate(prompt_text="abc def", interval="whenever")
