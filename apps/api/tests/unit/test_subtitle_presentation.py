from camcat.domain.project import Canvas, EditingProjectV2, TextSegment, TextTrack


def test_identical_translation_hides_only_the_covered_original_caption():
    from camcat.domain.localization import hidden_duplicate_caption_ids

    project = EditingProjectV2(
        project_id="subtitle-presentation",
        title="Subtitles",
        goal="Present translated subtitles",
        canvas=Canvas(width=1080, height=1920, fps_num=30, fps_den=1),
        timeline={
            "tracks": [
                TextTrack(
                    track_id="original",
                    name="Original",
                    segments=[
                        TextSegment(
                            segment_id="source",
                            start_us=0,
                            duration_us=1_020_000,
                            text="Welcome to the museum!",
                        )
                    ],
                ),
                TextTrack(
                    track_id="translated",
                    name="Translated",
                    segments=[
                        TextSegment(
                            segment_id="target",
                            start_us=0,
                            duration_us=4_000_000,
                            text="Welcome to the museum!",
                        )
                    ],
                ),
            ]
        },
        metadata={"localization": {"source_track_id": "original", "target_track_id": "translated"}},
    )
    assert hidden_duplicate_caption_ids(project) == {"source"}
    assert len(project.timeline.tracks[0].segments) == 1
    target = project.timeline.tracks[1].segments[0]
    project.timeline.tracks[1].segments[0] = target.model_copy(update={"text": "欢迎来到博物馆！"})
    assert hidden_duplicate_caption_ids(project) == set()
    project.timeline.tracks[1].segments[0] = target.model_copy(update={"duration_us": 500_000})
    assert hidden_duplicate_caption_ids(project) == set()
