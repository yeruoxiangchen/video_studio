from __future__ import annotations

import json
import logging
import math
import os
import queue
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from .asr_contract import ASR_MODEL as ASR_MODEL
from .asr_contract import public_transcript
from .budget import check_input_budget
from .generation import (
    GenerationLease,
    GenerationTimeout,
    GenerationTiming,
    StructuredDecoder,
    chat_generation_options,
    final_json_content,
)
from .protocol import RerankRequest, StrictVisionErrors, normalize_mrl
from .validation import ModelOutputError

VISUAL_MODEL = "Qwen/Qwen3-VL-8B-Instruct"


def structured(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
    value, end = json.JSONDecoder().raw_decode(cleaned)
    if not isinstance(value, dict) or cleaned[end:].strip() not in {"", "```"}:
        raise ValueError("Local model did not return one JSON object")
    return value


class AsrProcess:
    def __init__(self, source: Path) -> None:
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.lock = threading.Lock()
        self.process = subprocess.Popen(
            [
                str(Path(os.environ["CAMCAT_STUDIO_DATA"]) / "asr-venv/bin/python"),
                "-B",
                "-u",
                "-m",
                "services.local_models.asr_worker",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=source,
        )

        def read() -> None:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                if line.startswith("CAMCAT_ASR_JSON "):
                    self.events.put(json.loads(line.removeprefix("CAMCAT_ASR_JSON ")))
            self.events.put({"event": "exited"})

        threading.Thread(target=read, daemon=True).start()
        event = self.events.get(timeout=600)
        if event.get("event") != "ready":
            self.close()
            raise RuntimeError(
                "Local multilingual ASR failed to initialize: " + str(event.get("error_type"))
            )
        self.ready = {name: value for name, value in event.items() if name != "event"}

    def transcribe(self, path: Path) -> dict[str, Any]:
        with self.lock:
            if self.process.poll() is not None:
                raise RuntimeError("Local ASR process exited")
            request_id = uuid.uuid4().hex
            assert self.process.stdin is not None
            self.process.stdin.write(
                json.dumps({"path": str(path), "request_id": request_id}) + "\n"
            )
            self.process.stdin.flush()
            event = self.events.get(timeout=180)
            if event.get("event") == "error" and event.get("code") == "asr_invalid_input":
                raise ValueError("Invalid audio input")
            if event.get("event") != "result" or event.get("request_id") != request_id:
                raise RuntimeError(
                    "Local ASR failed: " + str(event.get("error_type", "process unavailable"))
                )
            return public_transcript(event)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class Runtime:
    def __init__(self) -> None:
        import torch
        from src.models.qwen3_vl_embedding import Qwen3VLEmbedder
        from src.models.qwen3_vl_reranker import Qwen3VLReranker
        from transformers import AutoProcessor, BitsAndBytesConfig, Qwen3VLForConditionalGeneration

        self.torch = torch
        # The upstream INFO messages include source URLs. Keep provider logs to
        # safe metadata and surface upstream warnings/errors without that traffic.
        logging.getLogger("qwen_vl_utils.vision_process").setLevel(logging.WARNING)
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        self.root = Path(os.environ["CAMCAT_STUDIO_DATA"])
        self.source = Path(__file__).resolve().parents[2]
        self.status_path = self.root / "checks/local-models-server.json"
        self.status = json.loads(self.status_path.read_text())
        visual_role = next(row for row in self.status["gpus"] if row["role"] == "visual")
        self.visual_device = int(visual_role["cuda_device"])
        self.visual_precision = str(visual_role["precision"])
        self.active_generation_deadline: float | None = None
        self.locks = {role: threading.Lock() for role in ("embedding", "reranker", "visual")}
        if self.visual_device == 1:
            self.locks["visual"] = self.locks["reranker"]
        for name in ("src.models.qwen3_vl_embedding", "src.models.qwen3_vl_reranker"):
            logger = logging.getLogger(name)
            logger.handlers = [StrictVisionErrors()]
            logger.propagate = False
        for device in sorted({int(row["cuda_device"]) for row in self.status["gpus"]}):
            torch.cuda.set_per_process_memory_fraction(0.86, device)
        options = {
            "torch_dtype": torch.bfloat16,
            "attn_implementation": "sdpa",
            "local_files_only": True,
            "low_cpu_mem_usage": True,
        }
        limits = {
            "max_length": 8192,
            "min_pixels": 4096,
            "max_pixels": 262144,
            "total_pixels": 2097152,
            "fps": 1.0,
            "max_frames": 16,
        }
        self.stamp("load_embedding")
        with torch.cuda.device(0):
            self.embedding = Qwen3VLEmbedder(
                str(self.root / "models/Qwen3-VL-Embedding-8B"), **limits, **options
            )
        self.stamp("load_reranker")
        with torch.cuda.device(1):
            self.reranker = Qwen3VLReranker(
                str(self.root / "models/Qwen3-VL-Reranker-8B"),
                **limits,
                **options,
                device_map={"": 1},
                quantization_config=BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=torch.bfloat16,
                ),
            )
        self.stamp("load_visual")
        path = "/data/zjr/models/Qwen3-VL-8B-Instruct"
        self.processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        quantization = (
            {
                "quantization_config": BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=torch.bfloat16,
                )
            }
            if self.visual_precision == "nf4"
            else {}
        )
        with torch.cuda.device(self.visual_device):
            self.visual = Qwen3VLForConditionalGeneration.from_pretrained(
                path,
                local_files_only=True,
                dtype=torch.bfloat16,
                device_map={"": self.visual_device},
                attn_implementation="sdpa",
                **quantization,
            ).eval()
        self.decoder = StructuredDecoder(
            self.processor.tokenizer, self.visual.config.text_config.vocab_size
        )
        self.stamp("load_asr")
        self.asr = AsrProcess(self.source)
        self.status["asr_runtime"] = self.asr.ready
        self.status["libraries"] = {
            name: version(name) for name in ("torch", "transformers", "xgrammar")
        }
        self.stamp("ready", state="ready")

    def stamp(self, phase: str, *, state: str = "loading") -> None:
        self.status.update(phase=phase, state=state, time=datetime.now(UTC).isoformat())
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.status, indent=2) + "\n")
        temporary.replace(self.status_path)

    def embed(self, item: dict[str, Any]) -> list[float]:
        with self.locks["embedding"], self.torch.cuda.device(0), self.torch.inference_mode():
            vectors = self.embedding.process([item])
            return normalize_mrl(vectors[0].float().cpu().tolist())

    def rerank(self, request: RerankRequest) -> list[float]:
        scores: list[float] = []
        with (
            tempfile.TemporaryDirectory(dir=self.root / "tmp") as temporary,
            self.locks["reranker"],
            self.torch.cuda.device(1),
            self.torch.inference_mode(),
        ):
            folder = Path(temporary)
            query = request.query.materialize(folder, "query")
            for offset in range(0, len(request.documents), 2):
                values = self.reranker.process(
                    {
                        "query": query,
                        "instruction": request.instruction,
                        "documents": [
                            doc.materialize(folder, f"document-{offset + index}")
                            for index, doc in enumerate(request.documents[offset : offset + 2])
                        ],
                        "fps": 1.0,
                        "max_frames": 16,
                    }
                )
                scores.extend(float(value) for value in values)
        if len(scores) != len(request.documents) or not all(math.isfinite(v) for v in scores):
            raise RuntimeError("Official reranker returned invalid candidate scores")
        return scores

    def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        timeout_seconds: float = 180,
        request_id: str = "",
        enable_thinking: bool = False,
    ) -> dict[str, Any]:
        from qwen_vl_utils import process_vision_info

        queued_at = time.monotonic()
        with GenerationLease(self.locks["visual"], timeout_seconds) as lease:
            self.active_generation_deadline = lease.deadline
            metrics: dict[str, Any] = {
                "queue_seconds": round(time.monotonic() - queued_at, 4),
                "precision": self.visual_precision,
            }
            try:
                preprocess_at = time.monotonic()
                with self.torch.cuda.device(self.visual_device), self.torch.inference_mode():
                    images, video_inputs, kwargs = process_vision_info(
                        messages,
                        image_patch_size=16,
                        return_video_metadata=True,
                        return_video_kwargs=True,
                    )
                    videos: list[Any] | None = None
                    metadata: list[Any] | None = None
                    if video_inputs:
                        raw_videos, raw_metadata = zip(*video_inputs, strict=True)
                        videos, metadata = list(raw_videos), list(raw_metadata)
                        if getattr(self, "measure_background", False):
                            from .camera_motion import background_motion, with_background_evidence

                            measured = [background_motion(video) for video in videos]
                            messages = with_background_evidence(messages, measured)
                            metrics["background_motion"] = measured
                    text = self.processor.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=True,
                        enable_thinking=enable_thinking,
                    )
                    inputs = self.processor(
                        text=[text],
                        images=images,
                        videos=videos,
                        video_metadata=metadata,
                        do_resize=False,
                        return_tensors="pt",
                        **kwargs,
                    )
                    length = inputs["input_ids"].shape[1]
                    metrics["prompt_tokens"] = length
                    logging.getLogger("uvicorn.error").info(
                        "generation_input prompt_tokens=%d max_new_tokens=%d", length, max_tokens
                    )
                    check_input_budget(length)
                    inputs = inputs.to(f"cuda:{self.visual_device}")
                    metrics["preprocess_seconds"] = round(time.monotonic() - preprocess_at, 4)
                    generation = chat_generation_options(self.processor.tokenizer, temperature)
                    grammar_at = time.monotonic()
                    processor = self.decoder.logits_processor(schema, reasoning=enable_thinking)
                    metrics["grammar_seconds"] = round(time.monotonic() - grammar_at, 4)
                    generate_at = time.monotonic()
                    generation_timing = GenerationTiming()
                    output = self.visual.generate(
                        **inputs,
                        max_new_tokens=max_tokens,
                        logits_processor=[processor],
                        max_time=lease.remaining(),
                        streamer=generation_timing,
                        **generation,
                    )
                    metrics.update(generation_timing.metrics())
                    metrics["generation_seconds"] = round(time.monotonic() - generate_at, 4)
                    tokens = output[0, length:]
                    metrics["completion_tokens"] = len(tokens)
                    metrics["total_seconds"] = round(time.monotonic() - queued_at, 4)
                    content = self.processor.decode(tokens, skip_special_tokens=True)
                    logging.getLogger("uvicorn.error").info(
                        "generation_output request_id=%s metrics=%s",
                        request_id,
                        json.dumps(metrics),
                    )
                    lease.remaining()
                    try:
                        content = final_json_content(content, reasoning=enable_thinking)
                        parsed = structured(content)
                    except (ValueError, TypeError):
                        raise ModelOutputError([{"path": [], "validator": "json_object"}]) from None
                    return {
                        "parsed": parsed,
                        "content": content,
                        "prompt_tokens": length,
                        "completion_tokens": len(tokens),
                        "finish_reason": "length" if len(tokens) >= max_tokens else "stop",
                        "timings": metrics,
                    }
            except (GenerationTimeout, ModelOutputError) as error:
                metrics["total_seconds"] = round(time.monotonic() - queued_at, 4)
                error.metrics = metrics
                raise
            finally:
                self.active_generation_deadline = None
