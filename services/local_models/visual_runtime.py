"""A separate official Transformers visual model, sharing the bounded generator."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from .generation import StructuredDecoder
from .geometry_tool import run_geometry, verify_models
from .geometry_tool import specification as geometry_specification
from .runtime import Runtime


def verify_visual_source(folder: Path, repository: str, revision: str) -> dict[str, Any]:
    receipt = json.loads((folder / "camcat-source.json").read_text())
    if receipt.get("repository") != repository or receipt.get("revision") != revision:
        raise ValueError("Visual model repository/revision differs from its pinned identity")
    for record in receipt["files"]:
        name = record["file"]
        if Path(name).name != name or not (folder / name).is_file():
            raise ValueError("Visual model download is incomplete")
        if (folder / name).stat().st_size != record["bytes"]:
            raise ValueError("Visual model download is incomplete")
    return dict(receipt)


class VisualRuntime(Runtime):
    def __init__(self) -> None:
        import torch
        from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
        from transformers.models.qwen3_5.modeling_qwen3_5 import is_fast_path_available

        self.torch = torch
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        self.root = Path(os.environ["CAMCAT_STUDIO_DATA"])
        self.source = Path(__file__).resolve().parents[2]
        geometry = geometry_specification()
        verify_models(self.root, geometry)
        if not (self.root / geometry["python"]).is_file():
            raise ValueError("Prepare the isolated CPU geometry runtime before visual startup")
        specification = json.loads((self.source / "infra/visual-model.lock.json").read_text())
        folder = self.root / specification["directory"]
        verify_visual_source(folder, specification["repository"], specification["revision"])
        self.visual_model = specification["repository"]
        self.visual_precision = specification["precision"]
        self.visual_device = 0
        self.video_options = dict(specification["video_options"])
        self.measure_background = True
        self.analysis_timeout_seconds = specification["analysis_timeout_seconds"]
        self.locks = {"visual": threading.Lock()}
        self.active_generation_deadline = None
        self.status_path = self.root / "checks/visual-model-server.json"
        self.status = json.loads(self.status_path.read_text())
        torch.cuda.set_per_process_memory_fraction(specification["gpu_memory_fraction"], 0)
        self.stamp("load_visual")
        self.processor = AutoProcessor.from_pretrained(folder, local_files_only=True)
        self.visual = Qwen3_5ForConditionalGeneration.from_pretrained(
            folder,
            local_files_only=True,
            dtype=torch.bfloat16,
            device_map={"": 0},
            attn_implementation="sdpa",
        ).eval()
        self.decoder = StructuredDecoder(
            self.processor.tokenizer, self.visual.config.text_config.vocab_size
        )
        self.status.update(
            visual=self.visual_model,
            revision=specification["revision"],
            precision=self.visual_precision,
            video_options=self.video_options,
            analysis_timeout_seconds=self.analysis_timeout_seconds,
            libraries={name: version(name) for name in ("torch", "transformers", "xgrammar")},
            optimized_linear_attention=bool(is_fast_path_available),
            geometry=geometry["revision"],
            source_sha256={
                name: hashlib.sha256((self.source / name).read_bytes()).hexdigest()
                for name in (
                    "services/local_models/visual_runtime.py",
                    "services/local_models/visual_server.py",
                    "services/local_models/runtime.py",
                    "services/local_models/server.py",
                    "services/local_models/generation.py",
                    "services/local_models/camera_motion.py",
                    "services/local_models/source_geometry.py",
                    "services/local_models/geometry_tool.py",
                    "infra/geometry.lock.json",
                    "infra/visual-model.lock.json",
                )
            },
        )
        self.stamp("ready", state="ready")

    def source_geometry(self, path: Path) -> dict[str, Any]:
        return run_geometry(path, self.root)

    def stamp(self, phase: str, *, state: str = "loading") -> None:
        self.status.update(state=state, phase=phase, time=datetime.now(UTC).isoformat())
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.status, indent=2) + "\n")
        temporary.replace(self.status_path)
