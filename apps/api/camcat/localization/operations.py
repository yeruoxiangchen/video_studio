from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from camcat.config import Settings
from camcat.domain.localization import LocalizationCue, collect_cues
from camcat.domain.project import EditingProjectV2, TextTrack
from camcat.domain.reducer import reduce_commands
from camcat.domain.state_patch import PatchConflict, VersionedState
from camcat.localization.agent import LocalizationAction, dubbing_request
from camcat.localization.plans import (
    TEXT_TRACK,
    SpeechArtifact,
    dubbing_commands,
    translation_commands,
    validate_translation_state,
)
from camcat.localization.schemas import (
    ApplyLocalizationRequest,
    DubRequest,
    TranslateRequest,
)
from camcat.models import Job, JobKind, JobStatus, utcnow
from camcat.repositories import JobRepository, StateRepository


def current_localization(
    session_id: UUID, db: Session, owner_id: str
) -> tuple[Any, Any, EditingProjectV2]:
    session, version = StateRepository(db).current(session_id, owner_id=owner_id)
    if session.expired_at or (
        session.transient_expires_at and session.transient_expires_at <= utcnow()
    ):
        raise HTTPException(410, "原片会话已过期，请重新上传原片")
    return session, version, EditingProjectV2.model_validate(version.document)


def enqueue_localization(
    session_id: UUID,
    request: TranslateRequest | DubRequest,
    db: Session,
    owner_id: str,
    settings: Settings,
    *,
    automation: dict[str, Any] | None = None,
    commit: bool = True,
    snapshot: tuple[Any, Any, EditingProjectV2] | None = None,
) -> Job:
    session, version, project = snapshot or current_localization(session_id, db, owner_id)
    if version.version != request.base_version:
        raise PatchConflict(expected_version=request.base_version, current_version=version.version)
    if isinstance(request, TranslateRequest):
        kind = JobKind.TRANSLATE_SUBTITLES
        track_id = request.source_track_id or next(
            (
                t.track_id
                for t in project.timeline.tracks
                if isinstance(t, TextTrack) and t.track_id != TEXT_TRACK
            ),
            "",
        )
        if track_id == TEXT_TRACK:
            raise HTTPException(422, "请选择原文字幕轨进行翻译")
        language = request.target_language
        extra: dict[str, Any] = {
            "glossary": request.glossary,
            "provider_revision": settings.translation_model or settings.llm_model,
        }
    else:
        kind = JobKind.DUB_SUBTITLES
        try:
            validate_translation_state(project)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        metadata = project.metadata.get("localization", {})
        if not isinstance(metadata, dict) or not metadata.get("target_track_id"):
            raise HTTPException(422, "请先翻译字幕并将结果写入时间线")
        track_id = str(metadata["target_track_id"])
        language = str(metadata["language"])
        voice = request.voice or settings.tts_voice
        if not all(
            (
                settings.tts_base_url,
                settings.tts_api_key.get_secret_value(),
                settings.tts_model,
                settings.tts_voice,
            )
        ):
            raise HTTPException(422, "配音服务尚未配置，请配置 TTS 服务后再提交")
        if language not in settings.tts_languages or voice != settings.tts_voice:
            raise HTTPException(422, "当前音色不支持译文语言，请选择匹配的配音服务")
        extra = {
            "voice": voice,
            "allow_duration_rewrite": request.allow_duration_rewrite,
            "original_audio_volume": request.original_audio_volume,
            "provider_revision": settings.tts_revision,
            "tts_model": settings.tts_model,
        }
    try:
        cues = collect_cues(project, track_id=track_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    payload = {
        "session_id": str(session_id),
        "source_version": version.version,
        "source_track_id": track_id,
        "language": language,
        "cues": [cue.model_dump(mode="json") for cue in cues],
        **extra,
        **({"agent_action": automation} if automation else {}),
    }
    key = hashlib.sha256(
        json.dumps(
            {"owner": owner_id, "kind": kind.value, **payload},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    job = JobRepository(db).enqueue(
        owner_id=owner_id,
        kind=kind,
        payload=payload,
        idempotency_key="localization:" + key,
        expires_at=session.transient_expires_at,
        commit=commit,
    )
    return job


def apply_localization(
    session_id: UUID,
    job_id: UUID,
    request: ApplyLocalizationRequest,
    db: Session,
    owner_id: str,
    settings: Settings,
) -> tuple[Any, VersionedState]:
    session, version, project = current_localization(session_id, db, owner_id)
    job = db.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.owner_id == owner_id,
            Job.kind.in_([JobKind.TRANSLATE_SUBTITLES, JobKind.DUB_SUBTITLES]),
        )
    )
    if job is None or job.payload.get("session_id") != str(session_id):
        raise HTTPException(404, "译制任务不存在")
    if job.status != JobStatus.SUCCEEDED or not job.result:
        raise HTTPException(409, "请等待任务成功后再将结果写入时间线")
    if job.result.get("applied_version"):
        return session, version
    if (
        request.base_version != version.version
        or int(job.payload["source_version"]) != version.version
    ):
        raise PatchConflict(
            expected_version=int(job.payload["source_version"]), current_version=version.version
        )
    cues = [LocalizationCue.model_validate(cue) for cue in job.result["cues"]]
    try:
        if job.kind == JobKind.TRANSLATE_SUBTITLES:
            commands = translation_commands(
                project,
                cues=cues,
                source_track_id=job.payload["source_track_id"],
                language=job.payload["language"],
                source_version=version.version,
                job_id=str(job.id),
                glossary=job.payload.get("glossary", {}),
            )
        else:
            commands = dubbing_commands(
                project,
                cues=cues,
                audio=[SpeechArtifact.model_validate(a) for a in job.result["audio"]],
                language=job.payload["language"],
                source_version=version.version,
                job_id=str(job.id),
                original_audio_volume=float(job.payload["original_audio_volume"]),
            )
        automation = job.payload.get("agent_action")
        if (
            isinstance(automation, dict)
            and automation.get("action") == "translate_and_dub"
            and job.kind == JobKind.TRANSLATE_SUBTITLES
        ):
            action = LocalizationAction.model_validate(automation)
            future = reduce_commands(project, commands).project
            next_job = enqueue_localization(
                session_id,
                dubbing_request(action, version=version.version + 1),
                db,
                owner_id,
                settings,
                automation={**automation, "action": "dub"},
                commit=False,
                snapshot=(
                    session,
                    VersionedState(
                        session_id=str(session_id),
                        version=version.version + 1,
                        document=future.model_dump(mode="json"),
                    ),
                    future,
                ),
            )
            job.result = {**job.result, "next_job_id": str(next_job.id)}
        # Commit the applied marker and the new version in the same CAS transaction.
        job.result = {**job.result, "applied_version": version.version + 1}
        after, _ = StateRepository(db).apply_commands(
            session_id=session_id,
            owner_id=owner_id,
            base_version=request.base_version,
            commands=commands,
            actor=owner_id,
            reason=f"Apply localization job {job.id}",
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(422, str(error)) from error
    return session, after
