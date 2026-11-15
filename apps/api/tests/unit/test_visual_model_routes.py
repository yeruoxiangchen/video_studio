"""Route-only model identity checks; no external inference is simulated."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.local_models.server import ChatRequest, chat


class ReachedInference(Exception):
    pass


def runtime():
    def generate(*args, **kwargs):
        raise ReachedInference

    return SimpleNamespace(visual_model="Qwen/Qwen3.5-9B", generate=generate)


def test_chat_accepts_only_the_model_actually_loaded_by_its_runtime():
    body = ChatRequest(model="Qwen/Qwen3.5-9B", messages=[{"role": "user", "content": "facts"}])
    with pytest.raises(ReachedInference):
        chat(body, runtime(), "unit-route-only")


def test_old_model_name_cannot_silently_select_a_different_loaded_model():
    body = ChatRequest(
        model="Qwen/Qwen3-VL-8B-Instruct", messages=[{"role": "user", "content": "facts"}]
    )
    with pytest.raises(HTTPException) as error:
        chat(body, runtime(), "unit-route-only")
    assert error.value.status_code == 422
