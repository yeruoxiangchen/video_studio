from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from camcat.config import Settings
from camcat.database import get_db
from camcat.domain.commands import SetLocalizationMetadata
from camcat.domain.localization import collect_cues
from camcat.domain.project import EditingProjectV2, TextTrack
from camcat.domain.state_patch import PatchConflict
from camcat.localization.delivery import (
    approval_metadata,
    build_delivery_archive,
    delivery_status,
    source_review_hints,
)
from camcat.localization.delivery_files import DeliveryFileResponse
from camcat.localization.operations import (
    apply_localization,
    current_localization,
    enqueue_localization,
)
from camcat.localization.plans import (
    TEXT_TRACK,
    validate_dubbed_state,
    validate_translation_state,
)
from camcat.localization.schemas import (
    ApplyLocalizationRequest,
    ConfirmLocalizationReview,
    DubRequest,
    LocalizationContext,
    SubtitleTrackInfo,
    TranslateRequest,
)
from camcat.localization.source_draft import source_draft_commands
from camcat.models import Job, JobKind, utcnow
from camcat.repositories import StateRepository
from camcat.schemas import EditingSessionResponse, JobResponse
from camcat.services.object_store import ObjectStore


def create_localization_router(
    settings: Settings,
    owner_dependency: Callable[..., str],
    job_response: Callable[[Job], JobResponse],
    session_response: Callable[..., EditingSessionResponse],
    object_store: ObjectStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/editing/sessions", tags=["localization"])

    def current(session_id: UUID, db: Session, owner_id: str) -> tuple[Any, Any, EditingProjectV2]:
        return current_localization(session_id, db, owner_id)

    @router.post("/{session_id}/localization/prepare", response_model=EditingSessionResponse)
    def prepare_source(
        session_id: UUID,
        request: ApplyLocalizationRequest,
        db: Session = Depends(get_db),
        owner_id: str = Depends(owner_dependency),
    ) -> EditingSessionResponse:
        session, version, project = current(session_id, db, owner_id)
        if request.base_version != version.version:
            raise PatchConflict(
                expected_version=request.base_version, current_version=version.version
            )
        try:
            commands = source_draft_commands(project)
            if commands:
                version, _ = StateRepository(db).apply_commands(
                    session_id=session_id,
                    owner_id=owner_id,
                    base_version=request.base_version,
                    commands=commands,
                    actor=owner_id,
                    reason="载入原片与识别字幕，直接译制",
                )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return session_response(session, version=version.version, document=version.document)

    @router.get("/{session_id}/localization", response_model=LocalizationContext)
    def context(
        session_id: UUID,
        source_track_id: str | None = None,
        db: Session = Depends(get_db),
        owner_id: str = Depends(owner_dependency),
    ) -> LocalizationContext:
        session, version, project = current(session_id, db, owner_id)
        tracks = [track for track in project.timeline.tracks if isinstance(track, TextTrack)]
        metadata = project.metadata.get("localization", {})
        if not isinstance(metadata, dict):
            metadata = {}
        source_id = str(
            source_track_id
            or metadata.get("source_track_id")
            or (tracks[0].track_id if tracks else "")
        )
        if source_track_id is not None and not any(
            t.track_id == source_track_id and t.track_id != TEXT_TRACK for t in tracks
        ):
            raise HTTPException(422, "请选择有效的原文字幕轨")
        target_id = str(metadata.get("target_track_id") or "")
        selected = target_id if any(t.track_id == target_id for t in tracks) else source_id
        selected_track = next((track for track in tracks if track.track_id == selected), None)
        cues = (
            collect_cues(project, track_id=selected, check_overlap=False)
            if selected_track and selected_track.segments
            else []
        )
        recent = db.scalars(
            select(Job)
            .where(
                Job.owner_id == owner_id,
                Job.kind.in_([JobKind.TRANSLATE_SUBTITLES, JobKind.DUB_SUBTITLES]),
                Job.payload["session_id"].astext == str(session_id),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(20)
        ).all()
        outdated = False
        try:
            validate_dubbed_state(project)
        except ValueError:
            outdated = True
        translation_outdated = False
        try:
            validate_translation_state(project)
        except ValueError:
            translation_outdated = True
        latest_render = db.scalar(
            select(Job)
            .where(
                Job.owner_id == owner_id,
                Job.kind == JobKind.RENDER,
                Job.payload["session_id"].astext == str(session_id),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        delivery = delivery_status(
            project,
            version=version.version,
            render_job=job_response(latest_render) if latest_render else None,
            render_version=int(latest_render.payload["version"]) if latest_render else None,
            session_id=str(session_id),
        )
        return LocalizationContext(
            base_version=version.version,
            source_track_id=source_id or None,
            target_track_id=target_id or None,
            language=metadata.get("language"),
            tracks=[
                SubtitleTrackInfo(
                    track_id=t.track_id,
                    name=t.name,
                    cue_count=len(t.segments),
                    is_translation=t.track_id == TEXT_TRACK,
                )
                for t in tracks
            ],
            cues=cues,
            jobs=[job_response(job) for job in recent],
            tts_configured=bool(
                settings.tts_base_url
                and settings.tts_api_key.get_secret_value()
                and settings.tts_model
                and settings.tts_voice
            ),
            tts_voice=settings.tts_voice or None,
            tts_languages=settings.tts_languages,
            audio_outdated=outdated,
            translation_outdated=translation_outdated,
            source_cues=collect_cues(project, track_id=source_id, check_overlap=False)
            if source_id and any(t.track_id == source_id and t.segments for t in tracks)
            else [],
            glossary=metadata.get("glossary", {}),
            review_hints=source_review_hints(project),
            delivery=delivery,
            expires_at=session.transient_expires_at.isoformat()
            if session.transient_expires_at
            else None,
        )

    def enqueue(
        session_id: UUID,
        request: TranslateRequest | DubRequest,
        db: Session,
        owner_id: str,
    ) -> JobResponse:
        return job_response(enqueue_localization(session_id, request, db, owner_id, settings))

    @router.post(
        "/{session_id}/localization/translate", response_model=JobResponse, status_code=202
    )
    def translate(
        session_id: UUID,
        request: TranslateRequest,
        db: Session = Depends(get_db),
        owner_id: str = Depends(owner_dependency),
    ) -> JobResponse:
        return enqueue(session_id, request, db, owner_id)

    @router.post("/{session_id}/localization/dub", response_model=JobResponse, status_code=202)
    def dub(
        session_id: UUID,
        request: DubRequest,
        db: Session = Depends(get_db),
        owner_id: str = Depends(owner_dependency),
    ) -> JobResponse:
        return enqueue(session_id, request, db, owner_id)

    @router.post("/{session_id}/localization/{job_id}/apply", response_model=EditingSessionResponse)
    def apply(
        session_id: UUID,
        job_id: UUID,
        request: ApplyLocalizationRequest,
        db: Session = Depends(get_db),
        owner_id: str = Depends(owner_dependency),
    ) -> EditingSessionResponse:
        session, after = apply_localization(session_id, job_id, request, db, owner_id, settings)
        return session_response(session, version=after.version, document=after.document)

    @router.post("/{session_id}/localization/review", response_model=EditingSessionResponse)
    def confirm_review(
        session_id: UUID,
        request: ConfirmLocalizationReview,
        db: Session = Depends(get_db),
        owner_id: str = Depends(owner_dependency),
    ) -> EditingSessionResponse:
        session, version, project = current(session_id, db, owner_id)
        if request.base_version != version.version:
            raise PatchConflict(
                expected_version=request.base_version, current_version=version.version
            )
        if delivery_status(project, version=version.version).state in {
            "ready_to_render",
            "ready",
            "rendering",
        }:
            return session_response(session, version=version.version, document=version.document)
        try:
            metadata = approval_metadata(
                project, version=version.version, reviewer=owner_id, at=utcnow().isoformat()
            )
            after, _ = StateRepository(db).apply_commands(
                session_id=session_id,
                owner_id=owner_id,
                base_version=request.base_version,
                commands=[SetLocalizationMetadata(metadata=metadata)],
                actor=owner_id,
                reason="确认本版本原文、译文与配音审校",
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return session_response(session, version=after.version, document=after.document)

    @router.get("/{session_id}/localization/delivery/{job_id}", response_class=DeliveryFileResponse)
    def download_delivery(
        session_id: UUID,
        job_id: UUID,
        base_version: int,
        db: Session = Depends(get_db),
        owner_id: str = Depends(owner_dependency),
    ) -> DeliveryFileResponse:
        _, version, project = current(session_id, db, owner_id)
        if base_version != version.version:
            raise PatchConflict(expected_version=base_version, current_version=version.version)
        job = db.scalar(
            select(Job).where(
                Job.id == job_id, Job.owner_id == owner_id, Job.kind == JobKind.RENDER
            )
        )
        if job is None or job.payload.get("session_id") != str(session_id):
            raise HTTPException(404, "交付视频不存在")
        status = delivery_status(
            project,
            version=version.version,
            render_job=job_response(job),
            render_version=int(job.payload["version"]),
            session_id=str(session_id),
        )
        if status.state != "ready":
            raise HTTPException(422, "；".join(status.blockers) or "请导出当前已审校版本的交付视频")
        assert job.result and job.finished_at
        key = str(job.result["output_key"])
        root = Path(settings.runtime_dir) / "delivery-tmp"
        root.mkdir(parents=True, exist_ok=True)
        temporary = TemporaryDirectory(dir=root, prefix="package-")
        try:
            if object_store.size_bytes(key) > settings.delivery_max_bytes:
                raise HTTPException(413, "交付视频超过大小限制，请降低导出码率或缩短视频")
            video, archive = (
                Path(temporary.name) / "video.mp4",
                Path(temporary.name) / "delivery.zip",
            )
            object_store.download_file(key, video)
            build_delivery_archive(
                project,
                version=version.version,
                video=video,
                output=archive,
                delivery_id=str(job.id),
                rendered_at=job.finished_at.isoformat(),
                maximum_bytes=settings.delivery_max_bytes,
                expected_sha256=str(job.result["output_sha256"]),
            )
            return DeliveryFileResponse(
                temporary=temporary,
                path=archive,
                filename=f"video-delivery-v{version.version}.zip",
            )
        except ClientError as error:
            temporary.cleanup()
            raise HTTPException(503, "交付文件暂时无法读取，请稍后重试或重新导出") from error
        except ValueError as error:
            temporary.cleanup()
            raise HTTPException(422, str(error)) from error
        except BaseException:
            temporary.cleanup()
            raise

    return router
