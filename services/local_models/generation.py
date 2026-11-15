"""Bound generation ownership and use XGrammar's maintained Transformers integration."""

from __future__ import annotations

import math
import time
from _thread import LockType
from collections.abc import Callable
from types import TracebackType
from typing import Any


def reasoning_structure(schema: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "type": "structural_tag",
        "format": {
            "type": "sequence",
            "elements": [
                {
                    "type": "tag",
                    "begin": "",
                    "end": "</think>",
                    "content": {
                        "type": "any_text",
                        "excludes": ["<think>", "</think>"],
                        "max_tokens": 1536,
                    },
                },
                {"type": "regex", "pattern": r"\s*"},
                {
                    "type": "json_schema",
                    "json_schema": schema or {"type": "object", "additionalProperties": True},
                    "max_whitespace_cnt": 2,
                },
            ],
        },
    }


def final_json_content(content: str, *, reasoning: bool) -> str:
    if not reasoning:
        return content
    _, marker, answer = content.partition("</think>")
    if not marker:
        raise ValueError("Missing the structured reasoning boundary")
    return answer.strip()


class GenerationTiming:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.started = clock()
        self.prompt_delivered = False
        self.first: float | None = None
        self.last: float | None = None

    def put(self, value: Any) -> None:
        if not self.prompt_delivered:
            self.prompt_delivered = True
            return
        now = self.clock()
        if self.first is None:
            self.first = now
        self.last = now

    def end(self) -> None:
        pass

    def metrics(self) -> dict[str, float]:
        if self.first is None or self.last is None:
            return {}
        return {
            "first_token_seconds": round(self.first - self.started, 4),
            "decode_seconds": round(self.last - self.first, 4),
        }


def chat_generation_options(tokenizer: Any, temperature: float) -> dict[str, Any]:
    """Use the chat EOS recognized by XGrammar, not a base-LM default EOS."""
    stop_id = tokenizer.eos_token_id
    if stop_id is None:
        raise ValueError("The structured chat tokenizer must define an EOS token")
    options: dict[str, Any] = {
        "do_sample": temperature > 0,
        "eos_token_id": stop_id,
        "pad_token_id": tokenizer.pad_token_id if tokenizer.pad_token_id is not None else stop_id,
    }
    if temperature > 0:
        options.update(temperature=temperature, top_p=0.9)
    return options


class GenerationTimeout(TimeoutError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "generation_timeout",
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.metrics = metrics or {}
        super().__init__(message)

    def details(self) -> dict[str, Any]:
        return {"code": self.code, "retryable": False, "metrics": self.metrics}


class GenerationLease:
    def __init__(self, lock: LockType, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Generation time budget must be positive and finite")
        self.lock = lock
        self.queued_at = time.monotonic()
        self.deadline = self.queued_at + seconds

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise GenerationTimeout("Local generation exceeded its time budget")
        return remaining

    def __enter__(self) -> GenerationLease:
        if not self.lock.acquire(timeout=self.remaining()):
            raise GenerationTimeout(
                "Local generation queue exceeded its time budget",
                code="generation_queue_timeout",
                metrics={"queue_seconds": time.monotonic() - self.queued_at},
            )
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.lock.release()
        if kind is None:
            self.remaining()


class StructuredDecoder:
    def __init__(self, tokenizer: Any, vocab_size: int) -> None:
        import xgrammar as xgr

        information = xgr.TokenizerInfo.from_huggingface(tokenizer, vocab_size=vocab_size)
        self.compiler = xgr.GrammarCompiler(
            information, max_threads=2, cache_limit_bytes=64 * 1024 * 1024
        )

    def compile(self, schema: dict[str, Any] | None, *, reasoning: bool = False) -> Any:
        if reasoning:
            return self.compiler.compile_structural_tag(reasoning_structure(schema))
        return self.compiler.compile_json_schema(
            # Explicitly keep the free-form object used for requirement understanding.
            # Supplied schemas use XGrammar's strict property handling: permissive
            # additional properties can otherwise admit a known key with an invalid value.
            schema if schema is not None else {"type": "object", "additionalProperties": True},
            strict_mode=True,
            max_whitespace_cnt=2,
        )

    def logits_processor(self, schema: dict[str, Any] | None, *, reasoning: bool = False) -> Any:
        from xgrammar.contrib.hf import LogitsProcessor

        return LogitsProcessor(self.compile(schema, reasoning=reasoning))
