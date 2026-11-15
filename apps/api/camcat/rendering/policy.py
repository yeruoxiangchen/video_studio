from typing import Literal

from camcat.domain.project import EditingProjectV2
from camcat.domain.render_policy import RenderingPolicy
from camcat.timeline.schemas import RenderProfile


def render_profile_for_project(
    project: EditingProjectV2, quality: Literal["preview", "standard", "high"]
) -> RenderProfile:
    policy = RenderingPolicy.model_validate(project.metadata.get("render_policy") or {})
    crf, preset = {
        "preview": (28, "ultrafast"),
        "standard": (20, "veryfast"),
        "high": (16, "medium"),
    }[quality]
    profile = RenderProfile(crf=crf, preset=preset)
    changes: dict[str, float | bool] = {}
    if policy.preserve_color:
        changes.update(color_contrast=1.0, color_saturation=1.0, color_gamma=1.0)
    if policy.preserve_audio:
        changes["normalize_loudness"] = False
    return profile.model_copy(update=changes)
