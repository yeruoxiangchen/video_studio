"""Small lexical boundaries for explicit edit guards; ambiguous intent stays with the planner."""

from __future__ import annotations

import re
from collections.abc import Iterator

_QUOTED = re.compile(
    r'"[^\"]*"|“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’'
    r"|(?<![A-Za-z0-9])'[^']*'(?![A-Za-z0-9])"
)
_CORRECTION = re.compile(r"而是|不对|算了|最终|\binstead\b|\bactually\b", re.IGNORECASE)
_BOUNDARY = re.compile(
    r"([，,。;；\n!?！？]+|而是|不对|算了|最终|\binstead\b|\bactually\b)", re.IGNORECASE
)
_NEGATED = re.compile(r"不要|无需|不必|别|\bdo\s+not\b|\bdon't\b|\bnot\b", re.IGNORECASE)


def mask_quoted_text(text: str) -> str:
    return _QUOTED.sub(lambda match: " " * len(match[0]), text)


def instruction_clauses(text: str) -> Iterator[tuple[str, bool]]:
    corrected = False
    for part in _BOUNDARY.split(mask_quoted_text(text)):
        if not part.strip():
            continue
        if _BOUNDARY.fullmatch(part):
            corrected = corrected or bool(_CORRECTION.fullmatch(part))
            continue
        yield part.strip(), corrected
        corrected = False


def negated_prefix(text: str) -> bool:
    return bool(_NEGATED.search(text))
