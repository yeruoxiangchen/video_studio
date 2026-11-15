from copy import deepcopy

import pytest

from scripts.small_eval_checks import check_edit, check_translation, passed


def baseline() -> dict:
    return {
        "canvas": {"width": 720, "height": 1280, "fps_num": 30, "fps_den": 1},
        "timeline": {
            "tracks": [
                {
                    "type": "video",
                    "track_id": "main",
                    "segments": [
                        {
                            "segment_id": "clip",
                            "source_id": "real-source",
                            "timeline_start_us": 0,
                            "timeline_duration_us": 4_000_000,
                            "source_start_us": 1_056_000,
                            "source_duration_us": 4_000_000,
                            "speed": 1.0,
                            "audio_volume": 0,
                            "transform": {},
                        },
                    ],
                },
                {
                    "type": "text",
                    "track_id": "captions",
                    "segments": [
                        {
                            "segment_id": "c1",
                            "text": "原字幕",
                            "start_us": 0,
                            "duration_us": 4_000_000,
                            "style": {"y": 0.88},
                        },
                    ],
                },
            ],
            "transitions": [],
        },
    }


def test_subtitle_edit_preserves_picture_canvas_and_caption_timing() -> None:
    before = baseline()
    after = deepcopy(before)
    after["timeline"]["tracks"][1]["segments"][0]["text"] = "欢迎使用 CamCat。"
    assert passed(check_edit("subtitle", before, after))
    after["canvas"].update(width=1280, height=720)
    assert not passed(check_edit("subtitle", before, after))


@pytest.mark.parametrize("defect", [None, "wrong_midpoint", "wrong_source", "gap", "extra_clip"])
def test_split_checks_midpoint_and_source_continuity_not_just_two_segments(
    defect: str | None,
) -> None:
    before = baseline()
    after = deepcopy(before)
    first = after["timeline"]["tracks"][0]["segments"][0]
    first.update(timeline_duration_us=2_000_000, source_duration_us=2_000_000)
    second = {
        **first,
        "segment_id": "right",
        "timeline_start_us": 2_000_000,
        "source_start_us": 3_056_000,
    }
    clips = after["timeline"]["tracks"][0]["segments"]
    clips.append(second)
    if defect == "wrong_midpoint":
        first.update(timeline_duration_us=944_000, source_duration_us=944_000)
        second.update(
            timeline_start_us=944_000,
            timeline_duration_us=3_056_000,
            source_start_us=2_000_000,
            source_duration_us=3_056_000,
        )
    elif defect == "wrong_source":
        second["source_id"] = "another-source"
    elif defect == "gap":
        second["source_start_us"] += 100_000
    elif defect == "extra_clip":
        clips.append({**second, "segment_id": "third"})
    assert passed(check_edit("split", before, after)) == (defect is None)


def test_crop_checks_absolute_source_range() -> None:
    before = baseline()
    before["timeline"]["tracks"] = before["timeline"]["tracks"][:1]
    after = deepcopy(before)
    clip = after["timeline"]["tracks"][0]["segments"][0]
    clip.update(
        source_start_us=2_000_000, source_duration_us=2_000_000, timeline_duration_us=2_000_000
    )
    assert passed(check_edit("crop", before, after))
    clip["source_start_us"] = 0
    assert not passed(check_edit("crop", before, after))


def test_translation_key_information_checks_detect_lost_number() -> None:
    cues = [
        {"cue_id": "c1", "text": "Welcome to CamCat."},
        {"cue_id": "c2", "text": "Share Earth's story in three steps."},
    ]
    assert passed(check_translation(cues))
    cues[1]["text"] = "Share Earth's story in two steps."
    assert not passed(check_translation(cues))
