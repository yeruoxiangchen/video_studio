"""Serve actual visual inference using the existing authenticated provider routes."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from .server import analyze, authorize, chat
from .visual_runtime import VisualRuntime


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    if len(os.environ.get("CAMCAT_LOCAL_MODELS_API_KEY", "")) < 24:
        raise RuntimeError("A private local-model key is required")
    application.state.runtime = VisualRuntime()
    yield


app = FastAPI(title="CamCat local visual model", lifespan=lifespan)
app.add_api_route("/v1/chat/completions", chat, methods=["POST"], dependencies=[Depends(authorize)])
app.add_api_route("/v1/analyze", analyze, methods=["POST"], dependencies=[Depends(authorize)])


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    if runtime.active_generation_deadline and time.monotonic() > runtime.active_generation_deadline:
        raise HTTPException(503, "Visual generation exceeded its time budget")
    return {**runtime.status, "status": "ready"}
