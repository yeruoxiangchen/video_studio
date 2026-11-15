from camcat.agent.sequence import sequence_payload


def packet(windows):
    return sequence_payload(
        "Order the uploaded story.",
        [{"source_id": "source", "filename": "scene.mp4", "analyzed_windows": windows}],
        count=1,
        use_all_sources=True,
    )["materials"][0]


def test_sequence_context_preserves_observed_speech_with_its_source_window():
    result = packet([{"start_us": 1_000_000, "duration_us": 2_000_000, "transcript": "Step away."}])
    assert result["speech_evidence"] == [
        {"start_us": 1_000_000, "duration_us": 2_000_000, "text": "Step away."}
    ]


def test_overlapping_repeated_transcripts_are_deduplicated_in_temporal_order():
    result = packet(
        [
            {"start_us": 3, "duration_us": 3, "transcript": "Second line."},
            {"start_us": 0, "duration_us": 4, "transcript": "First line."},
            {"start_us": 1, "duration_us": 4, "transcript": "First line."},
            {"start_us": 7, "duration_us": 1, "transcript": ""},
        ]
    )
    assert [e["text"] for e in result["speech_evidence"]] == ["First line.", "Second line."]
    assert [e["start_us"] for e in result["speech_evidence"]] == [0, 3]


def test_speech_context_is_bounded_and_silence_does_not_add_evidence():
    result = packet(
        [
            {"start_us": i, "duration_us": 1, "transcript": str(i) + " long speech" * 100}
            for i in range(20)
        ]
    )
    assert sum(len(e["text"]) for e in result["speech_evidence"]) <= 600
    assert "speech_evidence" not in packet([])
