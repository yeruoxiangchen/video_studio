"""Prepare native Pydantic inputs and check the installed model environment's grammar."""

from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
from pathlib import Path

from camcat.agent.contract import CommandProposal, command_proposal_schema


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    weights = json.loads((source / "infra/local-models.lock.json").read_text())["visual"][
        "physical_path"
    ]
    config = json.loads((Path(weights) / "config.json").read_text())
    valid = {
        "commands": [
            {
                "type": "insert_segment",
                "track_id": "main",
                "segment": {
                    "type": "video",
                    "segment_id": "cut",
                    "source_id": "uploaded",
                    "timeline_start_us": 0,
                    "source_start_us": 0,
                    "source_duration_us": 1_000_000,
                },
            }
        ]
    }
    CommandProposal.model_validate(valid)
    invalid = copy.deepcopy(valid)
    del invalid["commands"][0]["type"]
    packet = {
        "schema": command_proposal_schema(),
        "valid": valid,
        "invalid": invalid,
        "tokenizer": weights,
        "vocab_size": config["text_config"]["vocab_size"],
    }
    environment = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": "",
        "PYTHONPATH": str(source),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    process = subprocess.Popen(
        [
            str(root / "local-models-venv/bin/python"),
            "-B",
            "-m",
            "services.local_models.grammar_probe",
        ],
        cwd=source,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        output, errors = process.communicate(json.dumps(packet), timeout=120)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise SystemExit("Grammar compilation exceeded 120 seconds") from None
    if process.returncode:
        print(errors[-3000:])
        raise SystemExit(process.returncode)
    result = json.loads(output.strip().splitlines()[-1])
    (root / "checks/generation-grammar.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
