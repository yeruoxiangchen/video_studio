"""Pure metadata checks; model inference is covered separately with real weights."""

import json

import pytest

from services.local_models.visual_runtime import verify_visual_source


def test_a_different_model_revision_is_rejected_before_loading(tmp_path):
    (tmp_path / "camcat-source.json").write_text(
        json.dumps(
            {
                "repository": "Qwen/Qwen3.5-9B",
                "revision": "other",
                "files": [],
            }
        )
    )
    with pytest.raises(ValueError, match="revision"):
        verify_visual_source(tmp_path, "Qwen/Qwen3.5-9B", "pinned")


def test_a_truncated_download_is_rejected_before_loading(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"short")
    (tmp_path / "camcat-source.json").write_text(
        json.dumps(
            {
                "repository": "Qwen/Qwen3.5-9B",
                "revision": "pinned",
                "files": [{"file": "model.safetensors", "bytes": 100}],
            }
        )
    )
    with pytest.raises(ValueError, match="incomplete"):
        verify_visual_source(tmp_path, "Qwen/Qwen3.5-9B", "pinned")
