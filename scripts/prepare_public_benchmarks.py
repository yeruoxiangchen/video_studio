"""Fetch only the frozen public pilot inputs; retain unchanged upstream verifiers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from scripts.public_benchmark_protocol import SOURCE, select_msrvtt_pilot, sha256, write_json

ROOT = Path(os.environ["CAMCAT_STUDIO_DATA"]) / "benchmarks"


def fetch(url: str, path: Path, *, expected: str | None = None) -> None:
    if path.is_file() and expected and sha256(path) == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "CamCat-public-pilot"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
                size = 0
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > 1024 * 1024 * 1024:
                        raise ValueError("Selected pilot file exceeded 1 GiB")
                    out.write(block)
            if expected and sha256(partial) != expected:
                raise ValueError("Public input checksum mismatch: " + path.name)
            partial.replace(path)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.5 * 2**attempt)


def pin_upstream_files(config: dict[str, Any]) -> dict[str, str]:
    receipts = {}
    for project, tree_name, directory in [
        ("PhiloLabs/agentic-vbench", "agentic_tree.json", "agentic-pinned"),
        ("QwenLM/Qwen3-VL-Embedding", "qwen_tree.json", "qwen-pinned"),
    ]:
        revision = (
            config["agentic"]["revision"]
            if directory == "agentic-pinned"
            else config["msrvtt"]["official_source_revision"]
        )
        tree_path = ROOT / "metadata" / tree_name
        if not tree_path.is_file():
            fetch(
                f"https://api.github.com/repos/{project}/git/trees/{revision}?recursive=1",
                tree_path,
            )
        tree = json.loads(tree_path.read_text())
        if tree["sha"] != revision or tree.get("truncated"):
            raise ValueError("Incomplete or unexpected upstream tree")
        blobs = {x["path"]: x["sha"] for x in tree["tree"] if x["type"] == "blob"}
        if directory == "agentic-pinned":
            paths = ["README.md", "LICENSE", "docs/VERIFIER_DESIGN.md"]
            for task in config["agentic"]["tasks"]:
                base = f"tasks/agentic_vbench_{task['family']}/{task['id']}/"
                paths.extend(p for p in blobs if p.startswith(base))
        else:
            paths = [
                "README.md",
                "LICENSE",
                "scripts/evaluation/mmeb_v2/video.yaml",
                "scripts/evaluation/mmeb_v2/video_retrieval.yaml",
            ]
        for path in paths:
            target = ROOT / "upstream" / directory / path
            if not target.is_file():
                fetch(f"https://raw.githubusercontent.com/{project}/{revision}/{path}", target)
            raw = target.read_bytes()
            blob_hash = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if blob_hash != blobs[path]:
                raise ValueError("Modified official source: " + path)
            receipts[str(target.relative_to(ROOT))] = sha256(target)
    return receipts


def hf_files(repository: str, revision: str, paths: list[str]) -> dict[str, Any]:
    from huggingface_hub import HfApi

    records = HfApi(token=False).get_paths_info(
        repository, paths, repo_type="dataset", revision=revision
    )
    result = {}
    for record in records:
        lfs = record.lfs
        if not lfs:
            raise ValueError("Expected LFS content hash for selected public media")
        result[record.path] = {"bytes": record.size, "sha256": lfs.sha256}
    if set(result) != set(paths):
        raise ValueError("Selected benchmark media are missing from the pinned source")
    return result


def main() -> None:
    config_path = SOURCE / "eval/public_pilot_v1.json"
    config = json.loads(config_path.read_text())
    ROOT.mkdir(parents=True, exist_ok=True)
    write_json(ROOT / "preparation.json", {"state": "running", "pid": os.getpid()})
    try:
        receipts = pin_upstream_files(config)
        spec = config["msrvtt"]
        annotation = ROOT / "metadata" / spec["annotation"]
        if not annotation.is_file():
            fetch(
                f"https://huggingface.co/datasets/{spec['repository']}/resolve/{spec['revision']}/{spec['annotation']}",
                annotation,
            )
        subset = select_msrvtt_pilot(
            json.loads(annotation.read_text()),
            queries=spec["query_count"],
            candidates=spec["candidate_count"],
            seed=spec["selection_seed"],
        )
        paths = ["raw_videos/" + c["filename"] for c in subset["candidates"]]
        files = hf_files(spec["repository"], spec["revision"], paths)
        subset.update(
            repository=spec["repository"],
            revision=spec["revision"],
            annotation_sha256=sha256(annotation),
        )
        for candidate in subset["candidates"]:
            remote = "raw_videos/" + candidate["filename"]
            candidate.update(files[remote])
            candidate["source_url"] = (
                f"https://huggingface.co/datasets/{spec['repository']}/resolve/{spec['revision']}/{remote}"
            )
            candidate["path"] = str(ROOT / "data/msrvtt/videos" / candidate["filename"])
        write_json(ROOT / "msrvtt-pilot.json", subset)

        def video(candidate: dict[str, Any]) -> str:
            fetch(candidate["source_url"], Path(candidate["path"]), expected=candidate["sha256"])
            return candidate["video_id"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            for done, _ in enumerate(pool.map(video, subset["candidates"]), 1):
                if done % 10 == 0:
                    print(
                        json.dumps({"phase": "msrvtt_download", "done": done, "total": len(paths)}),
                        flush=True,
                    )
        tasks = []
        from huggingface_hub import HfApi

        lock_path = ROOT / "agentic-data-lock.json"
        locks = json.loads(lock_path.read_text()) if lock_path.is_file() else {}
        for task in config["agentic"]["tasks"]:
            repository = task["dataset"]
            if repository not in locks:
                info = HfApi(token=False).dataset_info(repository)
                card = info.card_data.to_dict() if info.card_data else {}
                locks[repository] = {
                    "revision": info.sha,
                    "license": card.get("license"),
                    "card": card,
                }
                write_json(lock_path, locks)
            revision = locks[repository]["revision"]
            file = hf_files(repository, revision, [task["archive"]])[task["archive"]]
            archive = ROOT / "data/agentic" / task["family"] / "materials.zip"
            url = (
                f"https://huggingface.co/datasets/{repository}/resolve/{revision}/{task['archive']}"
            )
            print(json.dumps({"phase": "agentic_download", "task": task["id"], **file}), flush=True)
            fetch(url, archive, expected=file["sha256"])
            materials = archive.parent / "materials"
            materials.mkdir(exist_ok=True)
            with zipfile.ZipFile(archive) as package:
                media = [
                    m
                    for m in package.infolist()
                    if not m.is_dir() and re.fullmatch(r"\d+\.mp4", Path(m.filename).name)
                ]
                names = {Path(m.filename).name for m in media}
                if names != {f"{i}.mp4" for i in range(1, task["clip_count"] + 1)} or len(
                    media
                ) != len(names):
                    raise ValueError("Task archive does not contain the expected candidate clips")
                for item in media:
                    target = materials / Path(item.filename).name
                    if not target.is_file() or target.stat().st_size != item.file_size:
                        with package.open(item) as inp, target.open("wb") as out:
                            while block := inp.read(1024 * 1024):
                                out.write(block)
            tasks.append(
                {
                    **task,
                    "dataset_revision": revision,
                    "source_url": url,
                    "archive_sha256": file["sha256"],
                    "materials": str(materials),
                    "media": [
                        {"file": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)}
                        for p in sorted(materials.glob("*.mp4"))
                    ],
                }
            )
        write_json(
            ROOT / "agentic-pilot.json",
            {"tasks": tasks, "source_revision": config["agentic"]["revision"]},
        )
        write_json(ROOT / "upstream-receipts.json", receipts)
        result = {
            "state": "ready",
            "config_sha256": sha256(config_path),
            "queries": len(subset["queries"]),
            "candidates": len(subset["candidates"]),
            "editing_tasks": len(tasks),
        }
        write_json(ROOT / "preparation.json", result)
        print(json.dumps(result), flush=True)
    except Exception as error:
        write_json(ROOT / "preparation.json", {"state": "failed", "error": str(error)[:600]})
        raise


if __name__ == "__main__":
    main()
