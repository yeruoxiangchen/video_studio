"""Actual installed compiler/tokenizer contract; no GPU or model inference."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from camcat.services.providers import SourceClipSemantics, source_visual_schema

pytestmark = [pytest.mark.integration]


@pytest.mark.parametrize("profile", ["canonical", "quality"])
def test_installed_grammar_rejects_invalid_enum_duplicate_keys_and_extra_properties(
    tmp_path, profile
):
    root = Path(os.environ.get("CAMCAT_STUDIO_DATA", "/data/zjr/camcat_studio"))
    python = root / (
        "quality-models-venv/bin/python" if profile == "quality" else "local-models-venv/bin/python"
    )
    if not python.is_file():
        pytest.skip("the installed local model compiler environment is required")
    weights = (
        root / "models/Qwen3.5-9B"
        if profile == "quality"
        else Path("/data/zjr/models/Qwen3-VL-8B-Instruct")
    )
    config = json.loads((weights / "config.json").read_text())
    valid = SourceClipSemantics(
        description="A person walks",
        scene="gallery",
        composition="wide shot",
        risk_score=0,
        temporal_evidence=["Person starts walking", "Person stops"],
        subject_motion="approaching",
    ).model_dump(mode="json")
    opening, closing = valid.pop("temporal_evidence")
    valid.update(opening_state=opening, closing_state=closing)
    schema = source_visual_schema()
    # Also protect callers whose older Pydantic schemas omitted additionalProperties.
    schema.pop("additionalProperties", None)
    packet = {
        "schema": schema,
        "valid": valid,
        "invalid": {**valid, "subject_motion": "walking"},
        "invalid_json": [
            json.dumps(valid)[:-1] + ',"subject_motion":"walking"}',
            json.dumps({**valid, "unexpected": "value"}),
            json.dumps({**valid, "description": "x" * 513}),
        ],
        "tokenizer": str(weights),
        "vocab_size": config["text_config"]["vocab_size"],
    }
    environment = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": "",
        "PYTHONPATH": str(Path.cwd()),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    result = subprocess.run(
        [str(python), "-B", "-m", "services.local_models.grammar_probe"],
        input=json.dumps(packet),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    (tmp_path / "compiler.stderr").write_text(result.stderr)
    assert result.returncode == 0, result.stderr[-2000:]
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    (tmp_path / "compiler.json").write_text(json.dumps(receipt, indent=2))
    assert receipt["kind"] == "grammar_contract_without_model_inference"
