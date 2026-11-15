"""Independent goal checks for six authored business tasks, not a public benchmark."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

State = dict[str, Any]
Check = dict[str, Any]


def tracks(state: State, kind: str) -> list[State]:
    return [t for t in state["timeline"]["tracks"] if t["type"] == kind]


def clips(state: State) -> list[State]:
    return sorted(
        (s for t in tracks(state, "video") for s in t["segments"]),
        key=lambda s: s["timeline_start_us"],
    )


def check(name: str, expected: Any, observed: Any, *, ok: bool | None = None) -> Check:
    return {
        "name": name,
        "passed": expected == observed if ok is None else ok,
        "expected": expected,
        "observed": observed,
    }


def passed(checks: list[Check]) -> bool:
    return bool(checks) and all(item["passed"] for item in checks)


def _near(expected: list[int], observed: list[int], tolerance: int) -> bool:
    return len(expected) == len(observed) and all(
        abs(a - b) <= tolerance for a, b in zip(expected, observed, strict=True)
    )


def check_edit(kind: str, before: State, after: State) -> list[Check]:
    original = clips(before)
    actual = clips(after)
    tolerance = round(1_000_000 * before["canvas"]["fps_den"] / before["canvas"]["fps_num"])
    checks = [check("canvas_preserved", before["canvas"], after["canvas"])]
    expected_sources = {clip["source_id"] for clip in original}
    checks.append(
        check(
            "video_sources_preserved",
            sorted(expected_sources),
            sorted({clip["source_id"] for clip in actual}),
        )
    )
    checks.append(check("audio_tracks_preserved", tracks(before, "audio"), tracks(after, "audio")))
    if kind == "subtitle":
        checks.append(check("video_unchanged", tracks(before, "video"), tracks(after, "video")))
        expected_text = deepcopy(tracks(before, "text"))
        expected_text[0]["segments"][0]["text"] = "欢迎使用 CamCat。"
        checks.append(check("subtitle_text_timing_style", expected_text, tracks(after, "text")))
    else:
        checks.append(check("text_tracks_preserved", tracks(before, "text"), tracks(after, "text")))
        if kind == "crop":
            expected = [[0, 2_000_000, 2_000_000, 2_000_000]]
        elif kind == "split":
            first = original[0]
            half_source = first["source_duration_us"] // 2
            half_timeline = first["timeline_duration_us"] // 2
            expected = [
                [0, half_timeline, first["source_start_us"], half_source],
                [
                    half_timeline,
                    first["timeline_duration_us"] - half_timeline,
                    first["source_start_us"] + half_source,
                    first["source_duration_us"] - half_source,
                ],
            ]
        else:
            raise ValueError("unknown goal check: " + kind)
        observed = [
            [
                s[k]
                for k in (
                    "timeline_start_us",
                    "timeline_duration_us",
                    "source_start_us",
                    "source_duration_us",
                )
            ]
            for s in actual
        ]
        checks.append(check("clip_count", len(expected), len(actual)))
        checks.append(
            check(
                "timeline_and_source_ranges",
                expected,
                observed,
                ok=len(expected) == len(observed)
                and all(_near(a, b, tolerance) for a, b in zip(expected, observed, strict=True)),
            )
        )
        checks.append(
            check(
                "speed_audio_transform_preserved",
                True,
                all(
                    all(
                        s.get(k) == original[0].get(k)
                        for k in ("speed", "audio_volume", "transform")
                    )
                    for s in actual
                ),
            )
        )
        if kind == "split":
            continuous = len(actual) == 2 and (
                actual[0]["source_start_us"] + actual[0]["source_duration_us"]
                == actual[1]["source_start_us"]
            )
            checks.append(check("source_contiguous", True, continuous))
    checks.append(
        check(
            "transitions_preserved",
            before["timeline"]["transitions"],
            after["timeline"]["transitions"],
        )
    )
    return checks


def check_translation(cues: list[State]) -> list[Check]:
    text = {cue["cue_id"]: cue["text"].casefold() for cue in cues}
    first, second = text.get("c1", ""), text.get("c2", "")
    return [
        check("cue_ids", ["c1", "c2"], sorted(text)),
        check("brand_preserved", True, "camcat" in first),
        check("welcome_preserved", True, "welcome" in first),
        check("number_three_preserved", True, bool(re.search(r"\b(?:three|3)\b", second))),
        check("earth_preserved", True, "earth" in second),
        check("story_preserved", True, bool(re.search(r"\bstor(?:y|ies)\b", second))),
    ]
