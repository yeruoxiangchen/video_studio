"""Prepare the already-selected FLEURS recordings from their pinned original archive."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scripts.localization_eval_metrics import extract_selected_audio
from scripts.prepare_public_benchmarks import fetch, hf_files
from scripts.public_benchmark_protocol import SOURCE, sha256, write_json


def prepare() -> Path:
    data = Path(os.environ["CAMCAT_STUDIO_DATA"])
    config = json.loads((SOURCE / "eval/localization_public_pilot_v1.json").read_text())
    spec = config["dataset"]
    root = data / "benchmarks/localization_public_v1"
    status = root / "preparation.json"
    rows = [json.loads(t) for t in Path(config["inputs"]).read_text().splitlines() if t.strip()]
    refs = [json.loads(t) for t in Path(config["references"]).read_text().splitlines() if t.strip()]
    ids = config["selection"]["sentence_ids"]
    if len(ids) != 20 or len(set(ids)) != 20 or [r["sentence_id"] for r in rows] != ids:
        raise ValueError("Public evaluation requires the original twenty unique sample IDs")
    if [r["sentence_id"] for r in refs] != ids:
        raise ValueError("References must cover exactly the selected source samples")
    manifest = root / "prepared-inputs.json"
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        if (
            saved.get("revision") == spec["revision"]
            and [r["sentence_id"] for r in saved.get("samples", [])] == ids
            and saved.get("references_sha256") == sha256(Path(config["references"]))
            and all(
                Path(r["audio_path"]).is_file()
                and sha256(Path(r["audio_path"])) == r["audio_sha256"]
                for r in saved["samples"]
            )
        ):
            write_json(
                status,
                {
                    "state": "ready",
                    "phase": "ready",
                    "samples": 20,
                    "manifest": str(manifest),
                    "verified_prepared_cache": True,
                },
            )
            print("Verified selected FLEURS recordings ready; original archive is not rescanned.")
            return manifest
    archive_name = "data/cmn_hans_cn/audio/test.tar.gz"
    write_json(status, {"state": "running", "phase": "archive_metadata", "selected": 20})
    identity = hf_files(spec["repository"], spec["revision"], [archive_name])[archive_name]
    archive = root / "raw/test.tar.gz"
    url = f"https://huggingface.co/datasets/{spec['repository']}/resolve/{spec['revision']}/{archive_name}"
    write_json(status, {"state": "running", "phase": "download_audio", **identity})
    fetch(url, archive, expected=identity["sha256"])
    write_json(status, {"state": "running", "phase": "extract_selected", "selected": 20})
    wanted = {r["source_audio_file"] for r in rows}
    if len(wanted) != 20:
        raise ValueError("One distinct source recording is required per selected sentence")
    extract_selected_audio(archive, wanted, root / "audio")
    records = []
    for row in rows:
        audio = root / "audio" / row["source_audio_file"]
        records.append({**row, "audio_path": str(audio), "audio_sha256": sha256(audio)})
    write_json(
        manifest,
        {
            "repository": spec["repository"],
            "revision": spec["revision"],
            "license": spec["license"],
            "archive_sha256": identity["sha256"],
            "archive_bytes": identity["bytes"],
            "source_url": url,
            "samples": records,
            "references": config["references"],
            "references_sha256": sha256(Path(config["references"])),
        },
    )
    write_json(
        status, {"state": "ready", "phase": "ready", "samples": 20, "manifest": str(manifest)}
    )
    print("Pinned FLEURS audio ready: 20 selected recordings; references remain scoring-only.")
    return manifest


if __name__ == "__main__":
    prepare()
