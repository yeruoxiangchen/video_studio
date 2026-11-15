from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from camcat.editing.policies import (
    choose_aspect_ratio,
    expiry_for_upload,
    explicit_external_ratio,
    explicit_source_range,
    prepare_source_candidates,
    requested_aspect_ratio,
    resolution_for_ratio,
)


def test_uploads_expire_exactly_four_hours_after_creation() -> None:
    created = datetime(2026, 7, 19, 8, 30, tzinfo=UTC)

    assert expiry_for_upload(created) == created + timedelta(hours=4)


def test_aspect_ratio_uses_explicit_intent_then_source_shape() -> None:
    assert choose_aspect_ratio("做成小红书竖屏 3:4", 1920, 1080) == "3:4"
    assert choose_aspect_ratio("发 TikTok", 1920, 1080) == "9:16"
    assert choose_aspect_ratio("保留原画幅", 1080, 1080) == "1:1"
    assert choose_aspect_ratio("旅行 vlog", 1920, 1080) == "16:9"
    assert choose_aspect_ratio("旅行 vlog", 1440, 1080) == "4:3"
    assert resolution_for_ratio("3:4") == (1080, 1440)


@pytest.mark.parametrize(
    "instruction",
    [
        "Reorder the clips according to the short story overview.",
        "Create a short narrative while retaining the picture and original audio.",
        "A man wearing shorts walks toward the building.",
        "Join the film reels into a coherent story.",
    ],
)
def test_narrative_or_clothing_words_do_not_request_a_portrait_output(instruction):
    assert requested_aspect_ratio(instruction) is None
    assert choose_aspect_ratio(instruction, 1920, 1080) == "16:9"


@pytest.mark.parametrize(
    "instruction",
    [
        "Export for YouTube Shorts",
        "Prepare for Instagram Reels",
        "Publish for Reels",
        "Make a vertical video",
        "做成竖屏短片",
    ],
)
def test_explicit_portrait_output_and_platform_requests_still_apply(instruction):
    assert requested_aspect_ratio(instruction) == "9:16"


def test_duplicate_source_shots_are_removed_but_order_is_stable() -> None:
    candidates = [
        {"segment_id": "source:a:1", "shot_signature": "same", "quality_score": 0.92},
        {"segment_id": "source:a:2", "shot_signature": "same", "quality_score": 0.42},
        {"segment_id": "source:b:1", "shot_signature": "unique", "quality_score": 0.78},
    ]

    prepared = prepare_source_candidates(candidates)

    assert [item["segment_id"] for item in prepared] == ["source:a:1", "source:b:1"]


def test_external_material_limit_is_explicit_and_bounded() -> None:
    assert explicit_external_ratio("素材库最多 40%") == 0.4
    assert explicit_external_ratio("普通剪辑") == 0.25
    assert explicit_external_ratio("stock 99%") == 0.75


@pytest.mark.parametrize(
    "instruction,expected",
    [
        ("裁剪为原片第2秒至第4秒", (2_000_000, 4_000_000)),
        ("截取原视频的1.25到3.75秒", (1_250_000, 3_750_000)),
        ("Trim original video from 0.5 to 3 seconds", (500_000, 3_000_000)),
        ("裁剪原片2s–3.5s", (2_000_000, 3_500_000)),
        ("从时间线第2秒到4秒裁剪", None),
        ("在原片第2秒至第4秒添加字幕", None),
        ("把字幕改为“裁剪原片第2秒至第4秒”", None),
        ("不要裁剪原片第2秒至第4秒，只改字幕", None),
        ("保留原片第2到4秒和第6到8秒", None),
        ("裁剪原片2到4秒，裁剪原片6到8秒", None),
    ],
)
def test_only_unambiguous_source_crop_requests_become_absolute_constraints(
    instruction: str,
    expected: tuple[int, int] | None,
) -> None:
    assert explicit_source_range(instruction) == expected


def test_reversed_source_crop_range_has_an_actionable_error() -> None:
    with pytest.raises(ValueError, match="终点必须晚于起点"):
        explicit_source_range("裁剪原片第4秒到第2秒")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("先改成16:9，算了，最终改成9:16。", "9:16"),
        ("不要改16:9而是改成9:16。", "9:16"),
        ("把字幕改成‘9:16’，其他设置保持不变。", None),
        ("把字幕改成'9:16'，其他设置保持不变。", None),
        ("Don't change to 16:9; instead switch to 9:16.", "9:16"),
        ("先改成16:9，算了，保持原画幅。", None),
    ],
)
def test_ratio_requests_follow_corrections_and_exclude_quoted_caption_content(text, expected):
    assert requested_aspect_ratio(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("将主视频裁剪为原片第2秒至第4秒，不对，改为原片第5秒至第7秒。", (5_000_000, 7_000_000)),
        ("裁剪原片第2到4秒，算了，改成第5到7秒。", (5_000_000, 7_000_000)),
        ("把字幕改成‘裁剪原片第2到4秒’。", None),
        ("把字幕改成'裁剪原片第2到4秒'。", None),
    ],
)
def test_source_crop_corrections_replace_the_old_absolute_range(text, expected):
    assert explicit_source_range(text) == expected
