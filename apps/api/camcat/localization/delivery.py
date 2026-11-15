"""Version-bound editorial signoff and standard customer delivery artifacts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from camcat.domain.localization import LocalizationMetadata, collect_cues, render_srt
from camcat.domain.project import EditingProjectV2
from camcat.localization.plans import AUDIO_TRACK, validate_dubbed_state, validate_translation_state
from camcat.localization.schemas import DeliveryStatus, SourceReviewHint
from camcat.schemas import JobResponse

REVIEW_FIELDS = {"reviewed_version", "reviewed_content_hash", "reviewed_at", "reviewed_by"}


def review_fingerprint(project: EditingProjectV2) -> str:
    document = project.model_dump(mode="json")
    metadata = LocalizationMetadata.model_validate(project.metadata["localization"])
    document["metadata"]["localization"] = metadata.model_dump(mode="json", exclude=REVIEW_FIELDS)
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def delivery_status(
    project: EditingProjectV2,
    *,
    version: int,
    render_job: JobResponse | None = None,
    render_version: int | None = None,
    session_id: str = "",
) -> DeliveryStatus:
    raw = project.metadata.get("localization")
    if not isinstance(raw, dict) or not raw.get("target_track_id"):
        return DeliveryStatus(
            state="needs_translation", blockers=["请先校对原文、翻译并写入时间线"]
        )
    metadata = LocalizationMetadata.model_validate(raw)
    try:
        validate_translation_state(project)
        if not metadata.translated_source_hash:
            raise ValueError("请重新翻译并应用，以保存本次交付对应的原文版本")
    except ValueError as error:
        return DeliveryStatus(state="needs_translation", blockers=[str(error)])
    try:
        collect_cues(project, track_id=metadata.target_track_id)
        validate_dubbed_state(project)
        if not metadata.dubbed_cues_hash or not any(
            t.track_id == AUDIO_TRACK for t in project.timeline.tracks
        ):
            raise ValueError("请生成配音、试听并写入时间线")
    except ValueError as error:
        return DeliveryStatus(state="needs_dubbing", blockers=[str(error)])
    if metadata.reviewed_version != version or metadata.reviewed_content_hash != review_fingerprint(
        project
    ):
        return DeliveryStatus(
            state="needs_review",
            blockers=["请核对原文、译文和配音，确认本版本的交付审校"],
            reviewed_version=metadata.reviewed_version,
        )
    result = DeliveryStatus(state="ready_to_render", reviewed_version=version)
    if render_job is None or render_version != version:
        return result
    result.render_job = render_job
    if render_job.status in {"queued", "running"}:
        result.state = "rendering"
    elif render_job.status in {"failed", "cancelled", "dead_letter"}:
        result.state = "failed"
        result.blockers = [render_job.error or "导出未完成，请重新生成交付视频"]
    elif render_job.status == "succeeded":
        if (
            not render_job.result
            or not render_job.result.get("output_key")
            or render_job.result.get("full_decode_succeeds") is not True
        ):
            result.state = "failed"
            result.blockers = ["成片验证未完成，请重新导出交付视频"]
            return result
        result.state = "ready"
        result.package_download_url = (
            f"/api/v1/editing/sessions/{session_id}/localization/delivery/"
            f"{render_job.job_id}?base_version={version}"
        )
    return result


def approval_metadata(
    project: EditingProjectV2, *, version: int, reviewer: str, at: str
) -> LocalizationMetadata:
    status = delivery_status(project, version=version)
    if status.state in {"needs_translation", "needs_dubbing"}:
        raise ValueError("；".join(status.blockers))
    return LocalizationMetadata.model_validate(project.metadata["localization"]).model_copy(
        update={
            "reviewed_version": version + 1,
            "reviewed_content_hash": review_fingerprint(project),
            "reviewed_by": reviewer,
            "reviewed_at": at,
        }
    )


def source_review_hints(project: EditingProjectV2) -> list[SourceReviewHint]:
    hints: list[SourceReviewHint] = []
    segments = project.metadata.get("source_segments", [])
    if not isinstance(segments, list):
        return hints
    for segment in segments:
        evidence = segment.get("transcript_evidence", {})
        quality = evidence.get("quality", {})
        if not quality.get("review_recommended"):
            continue
        threshold = float(quality.get("review_threshold", 0.6))
        words = []
        for cue in evidence.get("segments", []):
            for word in cue.get("words", []):
                probability = word.get("probability")
                if (
                    isinstance(probability, (int, float))
                    and math.isfinite(probability)
                    and probability < threshold
                ):
                    text = str(word.get("word", "")).strip()
                    if text and text not in words:
                        words.append(text)
        hints.append(
            SourceReviewHint(
                filename=str(segment.get("filename", "原片")),
                start_seconds=float(segment.get("start_time", 0)),
                end_seconds=float(segment.get("end_time", 0)),
                low_confidence_word_count=int(quality.get("low_confidence_word_count", len(words))),
                words=words[:10],
            )
        )
    return hints


def build_delivery_archive(
    project: EditingProjectV2,
    *,
    version: int,
    video: Path,
    output: Path,
    delivery_id: str,
    rendered_at: str,
    maximum_bytes: int,
    expected_sha256: str | None = None,
) -> None:
    if delivery_status(project, version=version).state != "ready_to_render":
        raise ValueError("当前版本尚未完成交付审校")
    if video.stat().st_size > maximum_bytes:
        raise ValueError("交付视频超过大小限制，请降低导出码率或缩短视频")
    if expected_sha256:
        digest = hashlib.sha256()
        with video.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise ValueError("成片校验与已验证导出不一致，请重新导出")
    metadata = LocalizationMetadata.model_validate(project.metadata["localization"])
    source = collect_cues(project, track_id=metadata.source_track_id)
    target = collect_cues(project, track_id=metadata.target_track_id)
    bilingual = [c.model_copy(update={"text": c.source_text + "\n" + c.text}) for c in target]
    terms = io.StringIO(newline="")
    writer = csv.writer(terms)
    writer.writerow(["source", "translation"])
    writer.writerows(sorted(metadata.glossary.items()))
    manifest = {
        "delivery_id": delivery_id,
        "project_title": project.title,
        "version": version,
        "language": metadata.language,
        "review_status": "confirmed",
        "reviewed_at": metadata.reviewed_at,
        "rendered_at": rendered_at,
        "video_file": "video.mp4",
        "subtitle_files": ["source.srt", "translation.srt", "bilingual.srt"],
        "cue_count": len(target),
        "video_bytes": video.stat().st_size,
    }
    files = {
        "source.srt": render_srt(source).encode(),
        "translation.srt": render_srt(target).encode(),
        "bilingual.srt": render_srt(bilingual).encode(),
        "glossary.csv": terms.getvalue().encode("utf-8-sig"),
        "delivery.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(),
    }
    date = datetime.fromisoformat(rendered_at)
    stamp = (date.year, date.month, date.day, date.hour, date.minute, date.second)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        with (
            video.open("rb") as stream,
            archive.open(
                zipfile.ZipInfo("video.mp4", date_time=stamp), "w", force_zip64=True
            ) as entry,
        ):
            shutil.copyfileobj(stream, entry, length=1024 * 1024)
        for name, content in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=stamp), content)
