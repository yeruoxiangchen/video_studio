"""Request-local provider receipts. Payloads, credentials and media URLs stay out of traces."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar("camcat_provider_calls", default=None)


def safe_usage(value: dict[str, Any]) -> dict[str, int]:
    return {
        key: value[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(value.get(key), int) and not isinstance(value[key], bool)
    }


def safe_timings(value: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "queue_seconds",
        "preprocess_seconds",
        "grammar_seconds",
        "generation_seconds",
        "first_token_seconds",
        "decode_seconds",
        "total_seconds",
        "prompt_tokens",
        "completion_tokens",
    )
    result = {
        key: value[key]
        for key in fields
        if isinstance(value.get(key), (int, float)) and not isinstance(value[key], bool)
    }
    if value.get("precision") in {"nf4", "bfloat16"}:
        result["precision"] = value["precision"]
    return result


@contextmanager
def capture_provider_calls() -> Iterator[list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


@contextmanager
def provider_attempt(
    model: str, path: str, attempt: int, arguments: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    schema = (
        arguments.get("json", {}).get("response_format", {}).get("json_schema", {}).get("schema")
    )
    if schema is None and arguments.get("data", {}).get("response_schema"):
        schema = json.loads(arguments["data"]["response_schema"])
    receipt: dict[str, Any] = {
        "request_id": uuid4().hex,
        "model": model,
        "endpoint": path,
        "attempt": attempt,
        "schema_sha256": (
            hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
            if schema is not None
            else None
        ),
    }
    started = time.perf_counter()
    try:
        yield receipt
    except Exception as error:
        receipt.update(
            error_type=type(error).__name__, retryable=getattr(error, "retryable", False)
        )
        raise
    finally:
        receipt["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        active = _calls.get()
        if active is not None:
            active.append(receipt)
        logger = logging.getLogger("camcat.providers")
        if receipt.get("error_type") or receipt.get("status_code", 0) >= 400:
            logger.warning("provider_call %s", json.dumps(receipt))
        else:
            logger.info("provider_call %s", json.dumps(receipt))
