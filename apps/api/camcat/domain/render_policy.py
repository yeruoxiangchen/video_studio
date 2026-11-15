from pydantic import BaseModel, ConfigDict


class RenderingPolicy(BaseModel):
    """Versioned user choices, independent of encoder quality."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    preserve_color: bool = False
    preserve_audio: bool = False
