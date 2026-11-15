"""Small MSR-VTT pilot using unchanged Qwen encoder, sampling and MMEB scorer."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts.public_benchmark_protocol import (
    SOURCE,
    BenchmarkRun,
    sha256,
    validate_rankings,
    verify_receipts,
    write_json,
)


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"]) / "benchmarks"
    run = BenchmarkRun(root, "retrieval")
    try:
        verify_receipts(root)
        config = json.loads((SOURCE / "eval/public_pilot_v1.json").read_text())["msrvtt"]
        subset = json.loads((root / "msrvtt-pilot.json").read_text())
        run.report.update(
            benchmark=subset["benchmark"],
            scope=subset["scope"],
            query_count=len(subset["queries"]),
            candidate_count=len(subset["candidates"]),
            selection_seed=subset["seed"],
            dataset_revision=subset["revision"],
            input_manifest_sha256=sha256(root / "msrvtt-pilot.json"),
            runner_sha256=sha256(Path(__file__)),
            official_source_revision=config["official_source_revision"],
            sampling={
                "num_frames": config["num_frames"],
                "max_frames_saved": config["max_frames_saved"],
            },
        )
        raw = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
        devices = [[x.strip() for x in row.split(",")] for row in raw.splitlines()]
        device = next(row for row in devices if int(row[0]) == config["gpu"])
        if int(device[2]) < 22528:
            raise RuntimeError("Pilot GPU is no longer idle; no other GPU will be taken")
        os.environ.update(
            CUDA_VISIBLE_DEVICES=device[0],
            CUDA_DEVICE_ORDER="PCI_BUS_ID",
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
        )
        run.report.update(
            gpu=int(device[0]),
            gpu_uuid=device[1],
            gpu_memory_budget_mib=config["gpu_memory_budget_mib"],
        )

        vendor = SOURCE / "third_party/qwen3_vl"
        receipt = json.loads((vendor / "UPSTREAM.json").read_text())
        for relative, expected in receipt["files"].items():
            if sha256(vendor / relative) != expected:
                raise RuntimeError("Vendored official Qwen file changed: " + relative)
        sys.path.insert(0, str(vendor))
        import numpy as np
        import torch
        from src.models.qwen3_vl_embedding import Qwen3VLEmbedder

        from services.local_models.protocol import StrictVisionErrors, normalize_mrl

        vision = importlib.import_module("src.evaluation.mmeb_v2.utils.vision_utils.vision_utils")
        metrics_module = importlib.import_module("src.evaluation.mmeb_v2.utils.eval_utils.metrics")
        logging.getLogger("src.models.qwen3_vl_embedding").addHandler(StrictVisionErrors())
        torch.cuda.set_per_process_memory_fraction(config["gpu_memory_budget_mib"] / int(device[3]))
        model_path = Path(os.environ["CAMCAT_STUDIO_DATA"]) / "models/Qwen3-VL-Embedding-8B"
        model_source = json.loads((model_path / "camcat-source.json").read_text())
        model_lock = json.loads((SOURCE / "infra/local-models.lock.json").read_text())["embedding"]
        if model_source["revision"] != model_lock["revision"]:
            raise ValueError("Embedding model revision differs from the product lock")
        attention = "sdpa"
        run.save(
            phase="load_model",
            model=model_lock,
            precision="bfloat16",
            attention=attention,
            protocol_notes=[
                "Public test_1k subset: 20 queries / 100 videos; "
                "full-set leaderboard numbers are not comparable.",
                "Official 64-frame sampler, instructions, encoder and metric code; "
                "batch size 1 and SDPA instead of upstream FlashAttention 2.",
                "Both 4096D reference and product 2048D MRL are computed "
                "from the same actual model outputs.",
            ],
            versions={
                name: importlib.metadata.version(name)
                for name in ("torch", "transformers", "numpy", "qwen-vl-utils")
            },
        )
        model = Qwen3VLEmbedder(
            str(model_path), torch_dtype=torch.bfloat16, attn_implementation=attention
        )
        call_log = run.output / "encoder-calls.jsonl"

        def encode(item: dict, role: str, identifier: str):
            started = time.monotonic()
            with torch.inference_mode():
                vector = model.process([item])[0].float().cpu().numpy()
            if (
                vector.shape != (4096,)
                or not np.isfinite(vector).all()
                or np.linalg.norm(vector) <= 0
            ):
                raise ValueError("Invalid model vector")
            record = {
                "role": role,
                "id": identifier,
                "seconds": round(time.monotonic() - started, 4),
                "dimensions": 4096,
                "peak_gpu_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
            }
            with call_log.open("a") as out:
                out.write(json.dumps(record) + "\n")
            return vector

        candidate_vectors = []
        for index, candidate in enumerate(subset["candidates"], 1):
            path = Path(candidate["path"])
            if sha256(path) != candidate["sha256"]:
                raise ValueError("Candidate checksum changed: " + candidate["video_id"])
            frames = root / "data/msrvtt/frames64" / candidate["video_id"]
            vision.save_frames(str(path), str(frames), config["max_frames_saved"])
            selected = vision.process_video_frames(str(frames), num_frames=config["num_frames"])
            if not selected:
                raise ValueError("Official preprocessing yielded no video frames")
            candidate_vectors.append(encode({"video": selected}, "video", candidate["video_id"]))
            run.save(phase="encode_videos", completed_videos=index)
        query_vectors = []
        for index, query in enumerate(subset["queries"], 1):
            query_vectors.append(
                encode(
                    {"text": query["text"], "instruction": query["instruction"]},
                    "query",
                    query["query_id"],
                )
            )
            run.save(phase="encode_queries", completed_queries=index)
        candidate_vectors = np.stack(candidate_vectors)
        query_vectors = np.stack(query_vectors)
        np.savez(
            run.output / "embeddings4096.npz", candidates=candidate_vectors, queries=query_vectors
        )
        scorer = metrics_module.RankingMetrics(["hit", "recall", "mrr", "ndcg"])
        ids = [c["video_id"] for c in subset["candidates"]]
        results = {}
        for dimension in config["dimensions"]:
            if dimension == 2048:
                candidates = np.asarray(
                    [normalize_mrl(v.tolist()) for v in candidate_vectors], dtype=np.float32
                )
                queries = np.asarray(
                    [normalize_mrl(v.tolist()) for v in query_vectors], dtype=np.float32
                )
            else:
                candidates, queries = candidate_vectors, query_vectors
            scores = queries @ candidates.T
            indices = np.argsort(-scores, axis=1, kind="stable")
            rankings = {
                query["query_id"]: [ids[i] for i in row]
                for query, row in zip(subset["queries"], indices, strict=True)
            }
            validate_rankings(subset, rankings)
            records = [
                {
                    "query_id": query["query_id"],
                    "prediction": rankings[query["query_id"]],
                    "label": query["labels"],
                    "rel_scores": None,
                }
                for query in subset["queries"]
            ]
            metric = scorer.evaluate(records)
            details = [
                {
                    "query_id": q["query_id"],
                    "text": q["text"],
                    "label": q["labels"],
                    "positive_rank": rankings[q["query_id"]].index(q["labels"][0]) + 1,
                    "top5": rankings[q["query_id"]][:5],
                }
                for q in subset["queries"]
            ]
            write_json(run.output / f"predictions-{dimension}.json", records)
            write_json(run.output / f"per-query-{dimension}.json", details)
            results[str(dimension)] = metric
        # Score calibration is confined to the evaluator; it is never sent to the encoder.
        perfect = [
            {
                "prediction": q["labels"] + [v for v in ids if v not in q["labels"]],
                "label": q["labels"],
                "rel_scores": None,
            }
            for q in subset["queries"]
        ]
        negative = [
            {
                "prediction": [v for v in ids if v not in q["labels"]] + q["labels"],
                "label": q["labels"],
                "rel_scores": None,
            }
            for q in subset["queries"]
        ]
        calibration = {"oracle": scorer.evaluate(perfect), "wrong_last": scorer.evaluate(negative)}
        assert (
            calibration["oracle"]["recall@1"] == 1 and calibration["wrong_last"]["recall@10"] == 0
        )
        run.save(
            state="completed",
            phase="completed",
            metrics=results,
            scorer_calibration=calibration,
            peak_gpu_mib=round(torch.cuda.max_memory_allocated() / 1024**2, 2),
        )
    except Exception as error:
        run.save(
            state="failed", phase="failed", error=str(error)[:1000], error_type=type(error).__name__
        )
        raise


if __name__ == "__main__":
    main()
