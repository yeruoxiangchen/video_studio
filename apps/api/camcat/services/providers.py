from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from camcat.config import Settings
from camcat.services.telemetry import provider_attempt, safe_timings, safe_usage


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retryable = retryable
        self.status_code = status_code
        self.details = details or {}
        self.error_code = str(self.details.get("code", "provider_error"))
        super().__init__(message)


class ProviderOutputError(ProviderError):
    def __init__(self, message: str, *, violations: list[dict[str, Any]] | None = None) -> None:
        self.violations = (violations or [])[:5]
        super().__init__(
            message,
            retryable=False,
            status_code=422,
            details={"code": "model_output_invalid", "violations": self.violations},
        )

    def correction(self) -> str:
        return (
            " Your previous output did not satisfy the JSON contract. Correct these fields: "
            + json.dumps(self.violations, ensure_ascii=False)
            + ". Follow the supplied schema exactly, with its enum values and field names. "
            "Return one complete valid JSON object with no duplicate keys."
        )


class _ProviderClient:
    def __init__(self, *, base_url: str, api_key: str, settings: Settings) -> None:
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        self.max_retries = settings.provider_max_retries
        self.client = httpx.Client(
            timeout=httpx.Timeout(settings.provider_timeout_seconds),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            with provider_attempt(getattr(self, "model", ""), path, attempt + 1, kwargs) as receipt:
                headers = {**(kwargs.get("headers") or {}), "X-Request-ID": receipt["request_id"]}
                try:
                    response = self.client.request(
                        method, f"{self.base_url}{path}", **{**kwargs, "headers": headers}
                    )
                except httpx.TransportError as exc:
                    receipt.update(error_type=type(exc).__name__, retryable=True)
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    time.sleep(0.25 * (2**attempt))
                    continue
                receipt["status_code"] = response.status_code
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = ProviderError(
                        f"transient provider response {response.status_code}",
                        status_code=response.status_code,
                    )
                    if attempt < self.max_retries:
                        time.sleep(0.25 * (2**attempt))
                        continue
                    break
                if response.is_error:
                    try:
                        detail = response.json().get("detail", {})
                    except (ValueError, AttributeError):
                        detail = {}
                    if isinstance(detail, dict):
                        receipt["error_code"] = detail.get("code")
                        if isinstance(detail.get("metrics"), dict):
                            receipt["timings"] = safe_timings(detail["metrics"])
                    if isinstance(detail, dict) and detail.get("code") == "model_output_invalid":
                        raise ProviderOutputError(
                            "Model output did not satisfy the requested JSON contract",
                            violations=detail.get("violations", []),
                        )
                    raise ProviderError(
                        f"provider response {response.status_code}",
                        retryable=False,
                        status_code=response.status_code,
                        details=detail if isinstance(detail, dict) else {},
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ProviderOutputError("provider did not return a JSON object") from exc
                if not isinstance(payload, dict):
                    raise ProviderOutputError("provider returned a non-object JSON response")
                if isinstance(payload.get("usage"), dict):
                    receipt["usage"] = safe_usage(payload["usage"])
                if isinstance(payload.get("camcat_metrics"), dict):
                    receipt["timings"] = safe_timings(payload["camcat_metrics"])
                choices = payload.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    receipt["finish_reason"] = choices[0].get("finish_reason")
                return payload
        raise ProviderError(str(last_error)) from last_error

    def healthcheck(self) -> None:
        self.request("GET", "/health")


class QwenEmbeddingClient(_ProviderClient):
    """Canonical gateway client using real Qwen3-VL text and visual inputs."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key.get_secret_value(),
            settings=settings,
        )
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension

    def embed_text(self, text: str) -> list[float]:
        return self._embed(text=text)

    def embed_image(self, path: Path, *, text: str | None = None) -> list[float]:
        return self._embed(text=text, media_field="image", media_path=path)

    def embed_video(self, path: Path, *, text: str | None = None) -> list[float]:
        return self._embed(text=text, media_field="video", media_path=path)

    def _embed(
        self,
        *,
        text: str | None = None,
        media_field: str | None = None,
        media_path: Path | None = None,
    ) -> list[float]:
        data = {"model": self.model, "dimensions": str(self.dimension)}
        if text:
            data["text"] = text
        if media_field == "video":
            data["fps"] = str(self.settings.embedding_video_fps)
        files: dict[str, tuple[str, Any, str]] = {}
        if media_field and media_path:
            media_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
            with media_path.open("rb") as stream:
                files[media_field] = (media_path.name, stream, media_type)
                payload = self.request("POST", "/v1/embeddings", data=data, files=files)
        else:
            # A (None, value) file tuple forces multipart/form-data for text-only queries too.
            multipart_fields = {key: (None, value) for key, value in data.items()}
            payload = self.request("POST", "/v1/embeddings", files=multipart_fields)
        return self._extract(payload)

    def _extract(self, payload: dict[str, Any]) -> list[float]:
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ProviderError("embedding provider must return exactly one embedding")
        raw_vector = data[0].get("embedding")
        if not isinstance(raw_vector, list):
            raise ProviderError("embedding provider must return exactly one embedding")
        vector = [float(value) for value in raw_vector]
        if len(vector) != self.dimension:
            raise ProviderError(
                f"embedding dimension mismatch: expected {self.dimension}, got {len(vector)}"
            )
        if not any(value != 0 for value in vector):
            raise ProviderError("embedding provider returned a zero vector")
        return vector


class QwenRerankerClient(_ProviderClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.reranker_base_url,
            api_key=settings.reranker_api_key.get_secret_value(),
            settings=settings,
        )
        self.model = settings.reranker_model

    def rerank(self, query: dict[str, Any], documents: list[dict[str, Any]]) -> list[float]:
        payload = self.request(
            "POST",
            "/v1/rerank",
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
                "instruction": "Rank source clips for the user's requested video edit.",
            },
        )
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != len(documents):
            raise ProviderError("reranker returned an invalid result count")
        scores = [0.0] * len(documents)
        seen: set[int] = set()
        for position, item in enumerate(results):
            if not isinstance(item, dict):
                raise ProviderError("reranker returned an invalid result item")
            index = int(item.get("index", position))
            if index < 0 or index >= len(documents) or index in seen:
                raise ProviderError("reranker returned invalid or duplicate indices")
            seen.add(index)
            raw_score = item.get("relevance_score", item.get("score"))
            if not isinstance(raw_score, (int, float)):
                raise ProviderError("reranker result is missing a numeric score")
            scores[index] = float(raw_score)
        return scores


class QwenChatClient(_ProviderClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            settings=settings,
        )
        self.model = settings.llm_model
        self.json_schema_enabled = settings.llm_json_schema_enabled
        self.source_evidence_enabled = settings.source_evidence_enabled

    def json_completion(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        for attempt in range(2):
            try:
                return self._json_completion_once(
                    system=system,
                    user=user,
                    response_schema=response_schema,
                    temperature=temperature,
                )
            except ProviderError as exc:
                if (
                    not isinstance(exc, ProviderOutputError)
                    and str(exc) != "LLM did not return valid structured JSON"
                ) or attempt == 1:
                    raise
                system += (
                    exc.correction()
                    if isinstance(exc, ProviderOutputError)
                    else (
                        " Your previous response was not valid JSON. Return a JSON object only. "
                        "Do not use markdown fences or literal backslash-n outside string values."
                    )
                )
        raise AssertionError("unreachable")

    def _json_completion_once(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        response_format: dict[str, Any] = {"type": "json_object"}
        if self.json_schema_enabled and response_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "camcat_commands",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        payload = self.request(
            "POST",
            "/v1/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": response_format,
                "temperature": temperature,
            },
        )
        try:
            content = payload["choices"][0]["message"]["content"]
            result = parse_structured_content(str(content))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderOutputError("LLM did not return valid structured JSON") from exc
        return result


class SegmentSemantics(BaseModel):
    """Validated visual facts returned by the real multimodal analysis gateway."""

    description: str = Field(min_length=1, max_length=4000)
    scene: str = Field(min_length=1, max_length=512)
    actions: list[str] = Field(default_factory=list, max_length=30)
    people: list[str] = Field(default_factory=list, max_length=30)
    composition: str = Field(min_length=1, max_length=512)
    tags: list[str] = Field(default_factory=list, max_length=50)
    event_type: str | None = Field(default=None, max_length=128)
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_labels: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("actions", "people", "tags", "risk_labels")
    @classmethod
    def clean_strings(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip()[:128] for value in values if str(value).strip()]
        return list(dict.fromkeys(cleaned))


class SourceClipSemantics(SegmentSemantics):
    """Concise visual evidence for original-file selection and ordering."""

    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=512)
    environment: Literal["indoor", "outdoor", "mixed", "uncertain"] = "uncertain"
    scene: str = Field(min_length=1, max_length=128)
    composition: str = Field(min_length=1, max_length=256)
    actions: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list, max_length=6)
    people: list[Annotated[str, Field(max_length=96)]] = Field(default_factory=list, max_length=6)
    tags: list[Annotated[str, Field(max_length=24)]] = Field(default_factory=list, max_length=6)
    risk_labels: list[Annotated[str, Field(max_length=48)]] = Field(
        default_factory=list, max_length=3
    )
    event_type: str | None = Field(default=None, max_length=32)
    temporal_evidence: list[Annotated[str, Field(max_length=192)]] = Field(
        default_factory=list, max_length=6
    )
    shot_size: Literal[
        "extreme close-up",
        "close-up",
        "medium close-up",
        "medium",
        "medium wide",
        "wide",
        "extreme wide",
        "uncertain",
    ] = "uncertain"
    camera_angle: Literal["eye level", "low", "high", "overhead", "dutch", "uncertain"] = (
        "uncertain"
    )
    camera_motion: Literal[
        "static",
        "push in",
        "pull out",
        "dolly forward",
        "dolly backward",
        "pan left",
        "pan right",
        "track left",
        "track right",
        "tilt up",
        "tilt down",
        "uncertain",
    ] = "uncertain"
    subject_view: Literal["front", "back", "side", "three quarter", "uncertain"] = "uncertain"
    subject_motion: Literal[
        "approaching", "receding", "turning", "stationary", "moving across", "uncertain"
    ] = "uncertain"

    @field_validator("temporal_evidence")
    @classmethod
    def require_observation_clauses(cls, values: list[str]) -> list[str]:
        for value in values:
            words = value.split()
            latin_words = [word for word in words if re.search(r"[A-Za-z]", word)]
            if len(latin_words) < 2 and len(re.findall(r"[\u3400-\u9fff]", value)) < 2:
                raise ValueError("temporal_evidence needs a complete observation clause")
        return values


SOURCE_VISUAL_PROMPT = (
    "Inspect the actual clip chronologically, including all visible shot changes. Classify "
    "the setting as indoor, outdoor, mixed or uncertain. Return "
    "concise observations of the setting, actions, framing, subject direction and camera "
    "change. Distinguish characters by visible clothing, face covering, eye appearance "
    "and held objects, without inferring identity. Preserve who acts on whom, who holds "
    "an object, and visible changes of possession or physical state. "
    "Describe continuing actions as continuing; a last sampled frame does not establish "
    "that motion stopped. Distinguish walking, running, standing and turning from actual "
    "limb movement. Subject approach/recession is relative to the CAMERA, not a building "
    "or other destination. "
    "Judge shot size by the subject's visible body: wide shows head through feet, medium "
    "wide roughly knees up, medium roughly waist up, medium close-up chest up. "
    "Compare BACKGROUND geometry to distinguish camera movement from subject movement. "
    "Background features growing support inward motion; shrinking features and newly "
    "revealed surroundings support outward motion. Camera motion is not proven by the "
    "subject getting larger. Do not infer lens size or camera movement from focus alone. "
    "Use uncertain for facts the video does not establish. In opening_state and closing_state "
    "describe the actual first and final visible state, each as a complete observation clause. "
    "A continuing action can appear in both states: do not invent a change. Each state describes "
    "a visible action or state, never a JSON fragment, field name or isolated label. Preserve the "
    "visible sequence. Include actual shot "
    "changes and relevant object/actor interactions. Keep description under 512 characters, "
    "at most six short actions, six appearance descriptions and six tags. Include event "
    "type and safety risk."
)


def source_visual_schema() -> dict[str, Any]:
    schema = SourceClipSemantics.model_json_schema()
    # Explicit state fields let a continuous one-event shot finish naturally.
    # Forcing a minimum-length array made real decoding emit later field names
    # as string entries when it tried to move on after its first observation.
    schema["properties"].pop("temporal_evidence")
    for name in ("opening_state", "closing_state"):
        schema["properties"][name] = {"type": "string", "minLength": 1, "maxLength": 192}
    schema["required"] = list(schema["properties"])
    return schema


def source_visual_observations(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    canonical = dict(raw)
    if "opening_state" in canonical or "closing_state" in canonical:
        canonical["temporal_evidence"] = [
            canonical.pop("opening_state", None),
            canonical.pop("closing_state", None),
        ]
    return canonical


def source_visual_violations(error: Exception, *, boundaries: bool) -> list[dict[str, Any]]:
    if not isinstance(error, ValidationError):
        return [{"path": ["opening_state", "closing_state"], "validator": "ordered_observations"}]
    output: list[dict[str, Any]] = []
    for item in error.errors(include_input=False, include_context=False, include_url=False):
        path = list(item["loc"])
        paths: list[list[int | str]]
        if boundaries and path and path[0] == "temporal_evidence":
            paths = [["opening_state"], ["closing_state"]]
        else:
            paths = [path]
        output.extend({"path": p, "validator": item["type"], "message": item["msg"]} for p in paths)
    return output[:5]


def source_visual_contract_digest() -> str:
    value = (
        json.dumps(source_visual_schema(), sort_keys=True)
        + SOURCE_VISUAL_PROMPT
        + "rtmlib-0.0.14-rtmpose-s-body7-v1"
    )
    return hashlib.sha256(value.encode()).hexdigest()


class QwenVisualAnalysisClient(_ProviderClient):
    """Direct-video structured visual analysis; never infers semantics from ASR alone."""

    def __init__(self, settings: Settings) -> None:
        visual_key = settings.visual_analysis_api_key
        super().__init__(
            base_url=settings.visual_analysis_base_url or settings.llm_base_url,
            api_key=(
                visual_key.get_secret_value()
                if visual_key and visual_key.get_secret_value()
                else settings.llm_api_key.get_secret_value()
            ),
            settings=settings,
        )
        self.model = settings.visual_analysis_model or settings.llm_model
        self.last_analysis_usage: dict[str, int] = {}

    def analyze_video(
        self, path: Path, *, transcript: str = "", compact: bool = False
    ) -> SegmentSemantics:
        media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
        prompt = (
            "Analyze only visible/audible facts in this clip. Describe scene, actions, people "
            "without identity inference, composition, retrieval tags, event type and safety risk."
        )
        data = {
            "model": self.model,
            "prompt": prompt,
            "transcript": transcript,
            "response_schema": json.dumps(SegmentSemantics.model_json_schema(), ensure_ascii=False),
        }
        if compact:
            data.update(
                prompt=SOURCE_VISUAL_PROMPT,
                response_schema=json.dumps(source_visual_schema(), ensure_ascii=False),
                max_tokens="1536",
            )
        for attempt in range(2):
            raw: Any = None
            try:
                with path.open("rb") as stream:
                    payload = self.request(
                        "POST",
                        "/v1/analyze",
                        data=data,
                        files={"video": (path.name, stream, media_type)},
                    )
                raw = payload.get("analysis", payload)
                try:
                    value = (SourceClipSemantics if compact else SegmentSemantics).model_validate(
                        source_visual_observations(raw) if compact else raw
                    )
                    if isinstance(value, SourceClipSemantics) and len(value.temporal_evidence) < 2:
                        raise ValueError("missing ordered temporal evidence")
                except (TypeError, ValueError) as exc:
                    raise ProviderOutputError(
                        "visual analysis returned invalid structured semantics",
                        violations=source_visual_violations(
                            exc, boundaries=isinstance(raw, dict) and "opening_state" in raw
                        ),
                    ) from exc
                usage = payload.get("usage", {})
                self.last_analysis_usage = {
                    key: usage[key]
                    for key in ("prompt_tokens", "completion_tokens")
                    if isinstance(usage, dict) and isinstance(usage.get(key), int)
                }
                return value
            except ProviderOutputError as exc:
                if attempt:
                    raise
                data["prompt"] += exc.correction() + (
                    " Use uncertain for unsupported visual directions; "
                    "action verbs belong in actions. Opening and closing states must each "
                    "be a complete visible observation. Correct the reported invalid fields "
                    "using the actual video and preserve supported facts."
                )
                if compact and isinstance(raw, dict):
                    data["prompt"] += " Previous observations (data): " + json.dumps(
                        raw, ensure_ascii=False
                    )
        raise AssertionError("unreachable")


class QwenAsrClient(_ProviderClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.asr_base_url,
            api_key=settings.asr_api_key.get_secret_value(),
            settings=settings,
        )
        self.model = settings.asr_model

    def transcribe(self, path: Path) -> dict[str, Any]:
        media_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
        with path.open("rb") as stream:
            return self.request(
                "POST",
                "/v1/audio/transcriptions",
                data={"model": self.model},
                files={"file": (path.name, stream, media_type)},
            )


def parse_structured_content(content: str) -> dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        normalized = "\n".join(lines).strip()
    try:
        result = json.loads(_normalize_json_whitespace(normalized))
    except json.JSONDecodeError as exc:
        raise ValueError("model response is not JSON") from exc
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        return {"items": result}
    raise ValueError("model JSON must be an object or array")


def _normalize_json_whitespace(content: str) -> str:
    """Repair escaped formatting whitespace without changing any JSON string value."""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(content):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "\\" and content[index : index + 2] in (r"\n", r"\r", r"\t"):
            output.append(" ")
            index += 2
            continue
        output.append(char)
        index += 1
    return "".join(output)
