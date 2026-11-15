"""Keep failed-node receipts on Python exception notes without changing exception types."""

import json
from typing import Any

_PREFIX = "camcat_node_trace="


def attach_failure_trace(error: Exception, trace: list[dict[str, Any]]) -> None:
    error.add_note(_PREFIX + json.dumps(trace, ensure_ascii=False))


def failure_node_trace(error: Exception) -> list[dict[str, Any]]:
    for note in reversed(getattr(error, "__notes__", [])):
        if note.startswith(_PREFIX):
            value = json.loads(note[len(_PREFIX) :])
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return value
    return []
