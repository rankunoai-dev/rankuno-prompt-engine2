"""Control plane: projects, prompts, platform and interval configuration, and a local UI.

A *project* is one client engagement: the client profile (never injected into
prompts), the platforms to track, the default run interval and the tracked
prompts. Prompts can be starred as important and can override the interval,
platforms and sample count. Due work is derived from the time-series store, so
no separate schedule state is kept per prompt.
"""

from src.modules.control_plane.schemas import (
    Project,
    ProjectCreate,
    ProjectUpdate,
    TrackedPrompt,
    TrackedPromptCreate,
    TrackedPromptUpdate,
)
from src.modules.control_plane.store import ProjectStore

__all__ = [
    "Project",
    "ProjectCreate",
    "ProjectStore",
    "ProjectUpdate",
    "TrackedPrompt",
    "TrackedPromptCreate",
    "TrackedPromptUpdate",
]
