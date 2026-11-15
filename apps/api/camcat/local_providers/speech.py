from __future__ import annotations

import hmac
import io
import json
import threading
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SpeechRequest(BaseModel):
    model: Literal["piper"]
    voice: str
    input: str = Field(min_length=1, max_length=2000)
    response_format: Literal["wav"] = "wav"

    @field_validator("input")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("speech text is empty")
        return value


class SpeechSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAMCAT_SPEECH_")
    model_path: Path
    api_key: SecretStr = Field(min_length=24)
    voice: str = "en_US-lessac-medium"
    revision: str = "1"
    threads: int = Field(default=2, ge=1, le=8)


class SpeechEngine:
    def __init__(self, settings: SpeechSettings) -> None:
        import onnxruntime as ort
        from piper import PiperVoice
        from piper.config import PiperConfig

        config = json.loads(Path(str(settings.model_path) + ".json").read_text())
        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.threads
        options.inter_op_num_threads = 1
        self.voice = PiperVoice(
            config=PiperConfig.from_dict(config),
            session=ort.InferenceSession(
                str(settings.model_path), sess_options=options, providers=["CPUExecutionProvider"]
            ),
        )
        self.lock = threading.Lock()
        self.settings = settings

    def synthesize(self, text: str) -> bytes:
        if not self.lock.acquire(blocking=False):
            raise HTTPException(429, "Speech engine is busy; retry later")
        try:
            output = io.BytesIO()
            with wave.open(output, "wb") as stream:
                self.voice.synthesize_wav(text, stream)
            return output.getvalue()
        finally:
            self.lock.release()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = SpeechSettings()  # type: ignore[call-arg]
    application.state.engine = SpeechEngine(settings)
    yield
    del application.state.engine


app = FastAPI(title="CamCat local speech provider", lifespan=lifespan)


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    engine: SpeechEngine = request.app.state.engine
    return {
        "status": "ready",
        "model": "piper",
        "voice": engine.settings.voice,
        "revision": engine.settings.revision,
        "languages": ["en"],
        "device": "cpu",
    }


@app.post("/v1/audio/speech")
def speech(
    body: SpeechRequest, request: Request, authorization: str = Header(default="")
) -> Response:
    engine: SpeechEngine = request.app.state.engine
    if not hmac.compare_digest(
        authorization, "Bearer " + engine.settings.api_key.get_secret_value()
    ):
        raise HTTPException(401, "Unauthorized")
    if body.voice != engine.settings.voice:
        raise HTTPException(422, "Requested voice is unavailable")
    return Response(engine.synthesize(body.input), media_type="audio/wav")
