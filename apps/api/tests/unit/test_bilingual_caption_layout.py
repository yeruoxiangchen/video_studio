"""Long bilingual cues must retain their text and fit separate readable regions."""

import re

from camcat.domain.project import EditingProjectV2, TextSegment, TextStyle, TextTrack


def caption_project(width=640, height=360):
    return EditingProjectV2(
        project_id="layout",
        title="Caption layout",
        goal="Readable bilingual captions",
        canvas={"width": width, "height": height, "fps_num": 30, "fps_den": 1},
        timeline={
            "tracks": [
                TextTrack(
                    track_id="captions",
                    name="Source",
                    segments=[
                        TextSegment(
                            segment_id="source",
                            start_us=0,
                            duration_us=12_000_000,
                            text="游客乘坐游船穿过湖面，欣赏两岸的城市风景。" * 4,
                            style=TextStyle(y=0.78),
                        )
                    ],
                ),
                TextTrack(
                    track_id="localization-subtitles",
                    name="English",
                    segments=[
                        TextSegment(
                            segment_id="target",
                            start_us=0,
                            duration_us=12_000_000,
                            text=(
                                "The visitors sail across the lake "
                                "and enjoy the city on both banks. "
                            )
                            * 6,
                            style=TextStyle(y=0.90),
                        )
                    ],
                ),
            ]
        },
        metadata={
            "localization": {
                "source_track_id": "captions",
                "target_track_id": "localization-subtitles",
            }
        },
    )


def test_long_captions_are_paged_without_changing_saved_text_or_cue_timing():
    from camcat.rendering.captions import caption_presentation

    project = caption_project()
    saved = project.model_dump(mode="json")
    shown = caption_presentation(project)
    assert len(shown["target"]) > 1
    for track in project.timeline.tracks:
        original = track.segments[0]
        pages = shown[original.segment_id]
        assert re.sub(r"\s", "", "".join(p.text for p in pages)) == re.sub(r"\s", "", original.text)
        assert pages[0].start_us == original.start_us
        assert pages[-1].start_us + pages[-1].duration_us == 12_000_000
        assert all(p.duration_us > 0 and len(p.text.splitlines()) <= 2 for p in pages)
        assert all(
            a.start_us + a.duration_us == b.start_us for a, b in zip(pages, pages[1:], strict=False)
        )
    assert project.model_dump(mode="json") == saved


def test_source_and_target_caption_regions_do_not_overlap_at_small_resolution():
    from camcat.rendering.captions import caption_line_height, caption_presentation

    project = caption_project()
    shown = caption_presentation(project)
    for source in shown["source"]:
        for target in shown["target"]:
            assert source.style.size <= 18 and target.style.size <= 18
            top = target.style.y * 360 - 2 * caption_line_height(target.style.size)
            assert source.style.y * 360 + 6 <= top
            assert source.style.y * 360 - 2 * caption_line_height(source.style.size) >= 18


def test_unrelated_text_styles_and_portrait_readability_are_preserved():
    from camcat.rendering.captions import caption_presentation

    project = caption_project(1080, 1920)
    shown = caption_presentation(project)
    assert shown["target"][0].style.size == 42
    project.metadata.clear()
    assert caption_presentation(project) == {}


def test_short_captions_keep_the_requested_lower_positions_when_they_fit():
    from camcat.rendering.captions import caption_presentation

    project = caption_project(1920, 1080)
    source, target = project.timeline.tracks
    source.segments[0] = source.segments[0].model_copy(
        update={
            "text": "欢迎来到博物馆。",
            "style": TextStyle(y=0.745),
        }
    )
    target.segments[0] = target.segments[0].model_copy(
        update={
            "text": "Welcome to the museum.",
            "style": TextStyle(y=0.815),
        }
    )
    shown = caption_presentation(project)
    assert shown["target"][0].style.y == 0.815
    assert shown["source"][0].style.y >= 0.74
