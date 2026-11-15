from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from camcat.domain.commands import (
    AddAudioSegment,
    AddTrack,
    DomainEditCommand,
    RegisterSource,
    RemoveTrack,
    SetLocalizationMetadata,
    SetVideoAudioVolume,
    UpdateTextSegment,
)
from camcat.domain.localization import (
    LocalizationCue,
    LocalizationMetadata,
    collect_cues,
    cue_fingerprint,
)
from camcat.domain.project import (
    AudioSegment,
    AudioTrack,
    EditingProjectV2,
    MediaSourceRef,
    TextSegment,
    TextStyle,
    TextTrack,
    VideoTrack,
)

TEXT_TRACK = "localization-subtitles"
AUDIO_TRACK = "localization-dialogue"


class SpeechArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    cue_id: str
    source_id: str
    storage_key: str
    duration_us: int = Field(gt=0)
    retention_class: Literal["transient_4h", "generated"]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def translation_commands(
    project: EditingProjectV2,
    *,
    cues: list[LocalizationCue],
    source_track_id: str,
    language: str,
    source_version: int,
    job_id: str,
    glossary: dict[str, str] | None = None,
) -> list[DomainEditCommand]:
    if source_track_id == TEXT_TRACK:
        raise ValueError("请从原文字幕轨提交翻译，译文可以在翻译完成后单独编辑")
    original = next((t for t in project.timeline.tracks if t.track_id == source_track_id), None)
    if not isinstance(original, TextTrack):
        raise ValueError("原文字幕轨不存在")
    previous = project.metadata.get("localization", {})
    had_dubbing = any(t.track_id == AUDIO_TRACK for t in project.timeline.tracks)
    saved_volumes = previous.get("original_audio_volumes", {}) if had_dubbing else {}
    original_volumes = {
        segment.segment_id: float(saved_volumes.get(segment.segment_id, segment.audio_volume))
        for track in project.timeline.tracks
        if isinstance(track, VideoTrack)
        for segment in track.segments
    }
    commands: list[DomainEditCommand] = [
        RemoveTrack(track_id=t.track_id)
        for t in project.timeline.tracks
        if t.track_id in {TEXT_TRACK, AUDIO_TRACK}
    ]
    translated_segments = []
    lineage: dict[str, str] = {}
    for cue in cues:
        segment_id = "localized-" + hashlib.sha256(cue.cue_id.encode()).hexdigest()[:20]
        translated_segments.append(
            TextSegment(
                segment_id=segment_id,
                start_us=cue.start_us,
                duration_us=cue.duration_us,
                text=cue.text,
                style=TextStyle(y=0.90),
            )
        )
        lineage[segment_id] = cue.source_text
    commands.append(
        AddTrack(
            track=TextTrack(
                track_id=TEXT_TRACK,
                name=f"译文字幕 · {language}",
                segments=translated_segments,
            )
        )
    )
    for segment in original.segments:
        commands.append(UpdateTextSegment(segment_id=segment.segment_id, y=0.78))
    if had_dubbing:
        commands.extend(
            SetVideoAudioVolume(segment_id=segment_id, audio_volume=volume)
            for segment_id, volume in original_volumes.items()
        )
    commands.append(
        SetLocalizationMetadata(
            metadata=LocalizationMetadata(
                language=language,
                source_track_id=source_track_id,
                target_track_id=TEXT_TRACK,
                source_version=source_version,
                source_text_by_id=lineage,
                original_audio_volumes=original_volumes,
                translation_job_id=job_id,
                glossary=glossary or {},
                translated_source_hash=cue_fingerprint(
                    collect_cues(project, track_id=source_track_id)
                ),
            )
        )
    )
    return commands


def dubbing_commands(
    project: EditingProjectV2,
    *,
    cues: list[LocalizationCue],
    audio: list[SpeechArtifact],
    language: str,
    source_version: int,
    job_id: str,
    original_audio_volume: float,
) -> list[DomainEditCommand]:
    by_id = {item.cue_id: item for item in audio}
    if len(by_id) != len(audio) or set(by_id) != {cue.cue_id for cue in cues}:
        raise ValueError("配音结果缺少字幕或包含重复编号")
    metadata = LocalizationMetadata.model_validate(project.metadata.get("localization", {}))
    active_track = next(
        (t for t in project.timeline.tracks if t.track_id == metadata.target_track_id), None
    )
    if not isinstance(active_track, TextTrack):
        raise ValueError("请先翻译字幕并将结果写入时间线")
    if {s.segment_id for s in active_track.segments} != set(by_id):
        raise ValueError("配音对应的字幕轨已改变，请重新提交任务")
    had_dubbing = any(track.track_id == AUDIO_TRACK for track in project.timeline.tracks)
    original_volumes = (
        metadata.original_audio_volumes
        if had_dubbing
        else {
            segment.segment_id: segment.audio_volume
            for track in project.timeline.tracks
            if isinstance(track, VideoTrack)
            for segment in track.segments
        }
    )
    commands: list[DomainEditCommand] = [
        RemoveTrack(track_id=AUDIO_TRACK)
        for track in project.timeline.tracks
        if track.track_id == AUDIO_TRACK
    ]
    commands.append(
        AddTrack(
            track=AudioTrack(track_id=AUDIO_TRACK, name=f"译制配音 · {language}", role="dialogue")
        )
    )
    for cue in cues:
        speech = by_id[cue.cue_id]
        if speech.duration_us < cue.duration_us:
            raise ValueError("生成音频短于已验证时间窗")
        commands.append(
            RegisterSource(
                source=MediaSourceRef(
                    source_id=speech.source_id,
                    origin="generated_audio",
                    storage_key=speech.storage_key,
                    retention_class=speech.retention_class,
                    metadata={
                        "localization_job_id": job_id,
                        "cue_id": cue.cue_id,
                        **({"sha256": speech.sha256} if speech.sha256 else {}),
                    },
                )
            )
        )
        commands.append(
            AddAudioSegment(
                track_id=AUDIO_TRACK,
                segment=AudioSegment(
                    segment_id="audio-" + speech.source_id,
                    source_id=speech.source_id,
                    timeline_start_us=cue.start_us,
                    timeline_duration_us=cue.duration_us,
                    source_start_us=0,
                    source_duration_us=speech.duration_us,
                ),
            )
        )
        commands.append(UpdateTextSegment(segment_id=cue.cue_id, text=cue.text))
    for track in project.timeline.tracks:
        if isinstance(track, VideoTrack):
            commands.extend(
                SetVideoAudioVolume(
                    segment_id=s.segment_id,
                    audio_volume=original_audio_volume * original_volumes.get(s.segment_id, 1),
                )
                for s in track.segments
            )
    commands.append(
        SetLocalizationMetadata(
            metadata=metadata.model_copy(
                update={
                    "dubbing_job_id": job_id,
                    "dubbed_cues_hash": cue_fingerprint(cues),
                    "original_audio_volumes": original_volumes,
                }
            )
        )
    )
    return commands


def validate_dubbed_state(project: EditingProjectV2) -> None:
    validate_translation_state(project)
    raw = project.metadata.get("localization")
    if not isinstance(raw, dict) or not raw.get("dubbed_cues_hash"):
        return
    if not any(track.track_id == AUDIO_TRACK for track in project.timeline.tracks):
        return
    metadata = LocalizationMetadata.model_validate(raw)
    current = collect_cues(project, track_id=metadata.target_track_id)
    if cue_fingerprint(current) != metadata.dubbed_cues_hash:
        raise ValueError("字幕或时间窗已修改，请重新配音并写入时间线后再导出")


def validate_translation_state(project: EditingProjectV2) -> None:
    raw = project.metadata.get("localization")
    if not isinstance(raw, dict) or not raw.get("translated_source_hash"):
        return
    current = collect_cues(project, track_id=str(raw["source_track_id"]))
    if cue_fingerprint(current) != raw["translated_source_hash"]:
        raise ValueError("原文字幕或时间已修改，请重新翻译并写入时间线")
