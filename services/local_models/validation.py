"""Validate generated objects with the same standard JSON Schema sent by the caller."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator


class ModelOutputError(ValueError):
    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations[:5]
        self.metrics: dict[str, Any] = {}
        super().__init__("Generated output did not satisfy the requested JSON contract")

    def details(self) -> dict[str, Any]:
        # Include field names and validator types, never the generated media description.
        return {
            "code": "model_output_invalid",
            "retryable": False,
            "violations": self.violations,
            "metrics": self.metrics,
        }


def validate_model_output(value: Any, schema: dict[str, Any]) -> None:
    validator = Draft202012Validator(schema)
    errors = validator.iter_errors(value)
    violations = []
    for error in errors:
        violations.append({"path": list(error.absolute_path), "validator": error.validator})
        if len(violations) == 5:
            break
    if violations:
        raise ModelOutputError(violations)
