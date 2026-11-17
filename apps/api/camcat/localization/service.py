from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from botocore.exceptions import ClientError

from camcat.config import Settings
from camcat.domain.localization import (
    AudioWindow,
    LocalizationCue,
    fit_audio_window,
    render_srt,
    speech_cache_key,
    validate_translations,
)
from camcat.localization.contract import translation_response_schema
from camcat.localization.prompts import translation_instructions
from camcat.models import EditingSession, Job, JobKind, utcnow
from camcat.rendering.fingerprint import sha256_file
from camcat.repositories import JobRepository
from camcat.services.object_store import ObjectStore
from camcat.services.providers import ProviderError, QwenChatClient


class TranslationService:
    def __init__(self, settings: Settings, *, correlation_id: str | None = None) -> None:
        self.backend = settings.translation_backend
        if self.backend == "hymt" and not all(
            (
                settings.translation_base_url,
                settings.translation_api_key.get_secret_value(),
                settings.translation_model,
            )
        ):
            raise ValueError("专用翻译需要明确配置服务地址、密钥和模型")
        configured = settings.model_copy(
            update={
                "llm_base_url": settings.translation_base_url or settings.llm_base_url,
                "llm_api_key": settings.translation_api_key
                if settings.translation_api_key.get_secret_value()
                else settings.llm_api_key,
                "llm_model": settings.translation_model or settings.llm_model,
            }
        )
        self.client = QwenChatClient(configured)
        self.client.client.headers["X-Correlation-Id"] = correlation_id or uuid4().hex

    def translate(
        self,
        cues: list[LocalizationCue],
        *,
        language: str,
        glossary: dict[str, str],
        timing_feedback: dict[str, Any] | None = None,
    ) -> list[LocalizationCue]:
        payload = {
            "target_language": language,
            "glossary": glossary,
            "sentences": [
                {
                    "id": c.cue_id,
                    "source": c.source_text,
                    "current": c.text,
                    "window_seconds": c.duration_us / 1_000_000,
                }
                for c in cues
            ],
            "timing_feedback": timing_feedback,
        }
        user = json.dumps(payload, ensure_ascii=False)
        if len(user) > 28_000:
            raise ValueError("译制文本过长，请先缩短剪辑计划")
        if self.backend == "hymt":
            from camcat.localization.hymt import translate_hymt

            response = translate_hymt(
                self.client,
                cues,
                language=language,
                glossary=glossary,
                timing_feedback=timing_feedback,
            )
        else:
            response = self.client.json_completion(
                system=translation_instructions(language, timing_feedback),
                user=user,
                response_schema=translation_response_schema(cues),
                temperature=0,
            )
        translated = validate_translations(cues, response)
        if language.startswith("en") and any(
            "\u3400" <= character <= "\u9fff" for cue in translated for character in cue.text
        ):
            raise ValueError("英文译文仍含中文，请校订术语或重新提交翻译")
        return translated


class SpeechClient:
    def __init__(self, settings: Settings, *, correlation_id: str | None = None) -> None:
        self.settings = settings
        self.calls = 0
        self.correlation_id = correlation_id or uuid4().hex

    def synthesize(self, text: str, target: Path, *, voice: str) -> None:
        settings = self.settings
        if not all(
            (
                settings.tts_base_url,
                settings.tts_model,
                settings.tts_voice,
                settings.tts_api_key.get_secret_value(),
            )
        ):
            raise ValueError("配音服务尚未配置，请设置 CamCat 的 TTS 服务、模型、音色和密钥")
        if len(text) > 2000:
            raise ValueError("单条配音文本超过 2000 字符，请先拆分字幕")
        last: Exception | None = None
        for attempt in range(settings.provider_max_retries + 1):
            self.calls += 1
            try:
                with (
                    httpx.Client(
                        timeout=settings.provider_timeout_seconds, trust_env=False
                    ) as client,
                    client.stream(
                        "POST",
                        settings.tts_base_url + "/v1/audio/speech",
                        headers={
                            "Authorization": "Bearer " + settings.tts_api_key.get_secret_value(),
                            "X-Correlation-Id": self.correlation_id,
                        },
                        json={
                            "model": settings.tts_model,
                            "voice": voice,
                            "input": text,
                            "response_format": "wav",
                        },
                    ) as response,
                ):
                    if response.status_code == 429 or response.status_code >= 500:
                        raise ProviderError(f"配音服务暂时不可用，HTTP {response.status_code}")
                    if response.status_code != 200:
                        raise ValueError(f"配音服务拒绝请求，HTTP {response.status_code}")
                    size = 0
                    with target.open("wb") as stream:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > settings.tts_max_response_bytes:
                                raise ValueError("配音服务返回的音频超过大小上限")
                            stream.write(chunk)
                with target.open("rb") as stream:
                    header = stream.read(12)
                if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                    raise ValueError("配音服务必须返回有效的 WAV 音频")
                return
            except (httpx.TransportError, ProviderError) as error:
                last = error
                target.unlink(missing_ok=True)
                if attempt < settings.provider_max_retries:
                    time.sleep(0.25 * (2**attempt))
        raise ProviderError("配音服务连接失败或持续限流，请检查服务后重试任务") from last


def audio_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    seconds = float(json.loads(result.stdout)["format"]["duration"])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("配音音频时长无效")
    return seconds


def normalize_speech(
    source: Path, target: Path, *, window_us: int, max_speed: float
) -> AudioWindow:
    try:
        fit = fit_audio_window(
            natural_seconds=audio_duration(source), window_us=window_us, max_speed=max_speed
        )
    except ValueError as error:
        raise ValueError("配音超出时间窗，请缩短译文或延长字幕") from error
    samples = (window_us * 48000 + 999999) // 1_000_000
    process = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(source),
            "-af",
            # Trim on the output sample clock, after resampling. Trimming on the
            # Piper/input clock can lose one output sample for fractional windows.
            f"aresample=48000,atempo={fit.speed:.12g},apad,atrim=end_sample={samples}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if process.returncode:
        raise ValueError("音频解码或时长适配失败")
    actual = round(audio_duration(target) * 1_000_000)
    if actual < window_us or actual - window_us > 2000:
        raise ValueError("配音适配后的实际时长与字幕时间窗不一致")
    return fit


class LocalizationService:
    def __init__(self, settings: Settings, store: ObjectStore) -> None:
        self.settings, self.store = settings, store

    def run(self, job: Job, jobs: JobRepository) -> dict[str, Any]:
        started = time.monotonic()
        if job.expires_at and job.expires_at <= utcnow():
            raise ValueError("原片处理会话已过期，请重新上传原片")
        session_id = UUID(str(job.payload["session_id"]))
        session = jobs.db.get(EditingSession, session_id)
        if session is None or session.owner_id != job.owner_id or session.deleted_at:
            raise LookupError("剪辑会话不存在")
        if session.expired_at or (job.expires_at and job.expires_at <= utcnow()):
            raise ValueError("原片处理会话已过期，请重新上传原片")
        cues = [LocalizationCue.model_validate(c) for c in job.payload["cues"]]
        language = str(job.payload["language"])
        temporary = session.transient_expires_at is not None
        prefix = ("temporary" if temporary else "generated") + f"/localization/{session_id}"
        folder = Path(self.settings.runtime_dir) / "jobs" / str(job.id)
        folder.mkdir(parents=True, exist_ok=True)
        jobs.checkpoint(job, "localization_started", {"cue_count": len(cues), "language": language})
        result: dict[str, Any] = {
            "language": language,
            "source_version": job.payload["source_version"],
            "source_track_id": job.payload["source_track_id"],
        }
        if job.kind == JobKind.TRANSLATE_SUBTITLES:
            cues = TranslationService(self.settings, correlation_id=str(job.id)).translate(
                cues, language=language, glossary=dict(job.payload.get("glossary", {}))
            )
            jobs.update_progress(job, 0.8)
        else:
            result.update(self._dub(job, jobs, cues, language, prefix, folder, temporary))
            cues = [LocalizationCue.model_validate(c) for c in result["cues"]]
        subtitle = folder / "subtitles.srt"
        subtitle.write_text(render_srt(cues), encoding="utf-8")
        subtitle_key = f"{prefix}/jobs/{job.id}/subtitles.srt"
        self.store.upload_file(subtitle, subtitle_key, "application/x-subrip; charset=utf-8")
        result.update(
            cues=[cue.model_dump(mode="json") for cue in cues],
            subtitle_key=subtitle_key,
            elapsed_seconds=time.monotonic() - started,
        )
        jobs.checkpoint(
            job, "localization_ready", {"cue_count": len(cues), "subtitle_key": subtitle_key}
        )
        return result

    def _dub(
        self,
        job: Job,
        jobs: JobRepository,
        cues: list[LocalizationCue],
        language: str,
        prefix: str,
        folder: Path,
        temporary: bool,
    ) -> dict[str, Any]:
        settings = self.settings
        voice = str(job.payload["voice"])
        if language not in settings.tts_languages or voice != settings.tts_voice:
            raise ValueError("当前配音服务不支持所选语言或音色")
        speech = SpeechClient(settings, correlation_id=str(job.id))
        audio: list[dict[str, Any]] = []
        final_cues: list[LocalizationCue] = []
        new_characters = 0
        for index, original in enumerate(cues):
            cue = original
            jobs.checkpoint(job, "dubbing", {"completed": index, "total": len(cues)})
            attempts = []
            for attempt in range(2):
                natural_path = folder / f"speech-{index}-{attempt}.wav"
                if language.startswith("en") and any("\u3400" <= c <= "\u9fff" for c in cue.text):
                    raise ValueError("字幕仍含中文，请先完成英文翻译并保存")
                cache = speech_cache_key(
                    text=cue.text,
                    language=language,
                    model=settings.tts_model,
                    voice=voice,
                    provider=settings.tts_base_url,
                    revision=settings.tts_revision,
                )
                raw_key = f"{prefix}/cache/{cache}.wav"
                hit = self._download_cached(raw_key, natural_path)
                if not hit:
                    speech.synthesize(cue.text, natural_path, voice=voice)
                    audio_duration(natural_path)
                    self.store.upload_file(natural_path, raw_key, "audio/wav")
                    new_characters += len(cue.text)
                natural = audio_duration(natural_path)
                attempts.append({"cache_hit": hit, "natural_seconds": natural})
                try:
                    fit = fit_audio_window(
                        natural_seconds=natural,
                        window_us=cue.duration_us,
                        max_speed=settings.tts_max_speed,
                    )
                    break
                except ValueError:
                    if attempt == 1 or not job.payload.get("allow_duration_rewrite"):
                        raise ValueError(
                            f"第 {index + 1} 句配音超出时间窗，请缩短译文或允许一次时长调整"
                        ) from None
                    revision_key = f"{prefix}/jobs/{job.id}/rewrite-{index}.json"
                    saved = self.store.read_json(revision_key, None)
                    if saved is None:
                        # Persist intent first so retries cannot repeat an uncertain rewrite.
                        self.store.write_json(revision_key, {"state": "attempted"})
                        rewritten = TranslationService(
                            settings, correlation_id=str(job.id)
                        ).translate(
                            [cue],
                            language=language,
                            glossary={},
                            timing_feedback={
                                "natural_seconds": natural,
                                "window_seconds": cue.duration_us / 1_000_000,
                                "maximum_speed": settings.tts_max_speed,
                            },
                        )[0]
                        saved = {"state": "complete", "text": rewritten.text}
                        self.store.write_json(revision_key, saved)
                    if not isinstance(saved, dict) or saved.get("state") != "complete":
                        raise ValueError("上次时长改写未完成，请保存新版本后重新提交") from None
                    cue = cue.model_copy(update={"text": str(saved["text"])})
            normalized = folder / f"cue-{index}.wav"
            fit = normalize_speech(
                natural_path,
                normalized,
                window_us=cue.duration_us,
                max_speed=settings.tts_max_speed,
            )
            actual = round(audio_duration(normalized) * 1_000_000)
            storage_key = f"{prefix}/jobs/{job.id}/cue-{index}.wav"
            self.store.upload_file(normalized, storage_key, "audio/wav")
            audio.append(
                {
                    "cue_id": cue.cue_id,
                    "source_id": f"dub-{job.id.hex}-{index}",
                    "storage_key": storage_key,
                    "duration_us": actual,
                    "retention_class": "transient_4h" if temporary else "generated",
                    "sha256": sha256_file(normalized),
                    "natural_seconds": natural,
                    "window_seconds": cue.duration_us / 1_000_000,
                    "speed": fit.speed,
                    "padding_seconds": fit.padding_seconds,
                    "cache_hit": all(a["cache_hit"] for a in attempts),
                    "duration_rewritten": cue.text != original.text,
                }
            )
            final_cues.append(cue)
            jobs.update_progress(job, 0.1 + 0.75 * (index + 1) / len(cues))
        return {
            "audio": audio,
            "cues": [c.model_dump(mode="json") for c in final_cues],
            "voice": voice,
            "tts_model": settings.tts_model,
            "tts_revision": settings.tts_revision,
            "tts_new_characters": new_characters,
            "tts_api_calls": speech.calls,
            "cache_hits": sum(a["cache_hit"] for a in audio),
        }

    def _download_cached(self, key: str, target: Path) -> bool:
        try:
            self.store.download_file(key, target)
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) not in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                raise
            return False
        audio_duration(target)
        return True
