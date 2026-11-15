"""The benchmark adapter must preserve labels, candidate scope and observed outputs."""

import pytest

from scripts.public_benchmark_protocol import select_msrvtt_pilot, validate_rankings


def test_oracle_has_writable_root_mountpoints_and_readonly_inputs(tmp_path):
    from scripts.public_benchmark_protocol import oracle_command

    workspace = tmp_path / "oracle"
    media = tmp_path / "materials"
    script = tmp_path / "solve.sh"
    tools = tmp_path / "ffmpeg-tools"
    command = oracle_command(workspace, media, script, tools)
    assert command[3:6] != ["--ro-bind", "/", "/"]
    assert "/" not in [command[i + 1] for i, item in enumerate(command) if item == "--ro-bind"]
    assert command[command.index("--bind") + 1 : command.index("--bind") + 3] == [
        str(workspace),
        "/workspace",
    ]
    assert [str(media), "/workspace/materials"] in [
        command[i + 1 : i + 3] for i, item in enumerate(command) if item == "--ro-bind"
    ]
    assert command[-2:] == ["/bin/bash", "/oracle/solve.sh"]
    for name in ("ffmpeg", "ffprobe"):
        assert [str(tools / name), "/tools/" + name] in [
            command[i + 1 : i + 3] for i, item in enumerate(command) if item == "--ro-bind"
        ]


@pytest.mark.asyncio
async def test_timed_out_case_cancels_its_jobs_and_keeps_failed_cancellation_for_cleanup():
    from scripts.run_public_editing import PublicEditing

    runner = object.__new__(PublicEditing)
    runner.jobs = {"own-a", "own-b"}
    requested = []

    async def request(method, path):
        requested.append((method, path))
        if "own-b" in path:
            raise RuntimeError("temporary API outage")
        return {"status": "cancelled"}

    runner.request = request
    cancelled = await runner.cancel_pending_jobs()
    assert cancelled == ["own-a"]
    assert runner.jobs == {"own-b"}
    assert set(requested) == {
        ("POST", "/api/v1/jobs/own-a/cancel"),
        ("POST", "/api/v1/jobs/own-b/cancel"),
    }


def test_large_task_brief_keeps_all_storyboard_slots_and_delivery_rules():
    from scripts.public_benchmark_protocol import transport_agentic_brief

    brief = (
        "# Assembly\n## What to submit\nsolution.mp4\n## Shot Language Reference\n"
        + "generic definitions\n" * 700
        + "## Storyboard\n### Slot 1\nA person walks.\n## Done when\nBoth outputs exist."
    )
    result, notes = transport_agentic_brief(brief)
    assert "### Slot 1\nA person walks." in result
    assert "## What to submit\nsolution.mp4" in result
    assert "## Done when\nBoth outputs exist." in result
    assert "generic definitions" not in result
    assert notes
    with pytest.raises(ValueError, match="8000"):
        transport_agentic_brief("task instructions " * 800)


def test_submission_manifest_comes_from_actual_saved_timeline_without_reordering():
    from scripts.public_benchmark_protocol import manifest_from_saved_state

    first = {
        "type": "video",
        "source_id": "uuid-a",
        "timeline_start_us": 0,
        "timeline_duration_us": 2_000_000,
        "source_start_us": 500_000,
        "source_duration_us": 2_000_000,
    }
    second = {**first, "source_id": "uuid-b", "timeline_start_us": 2_000_000, "source_start_us": 0}
    state = {"timeline": {"tracks": [{"type": "video", "segments": [second, first]}]}}
    expected = {
        "segments": [
            {"source": "7", "output": [0, 2], "source_range": [0.5, 2.5]},
            {"source": "3", "output": [2, 4], "source_range": [0, 2]},
        ]
    }
    assert manifest_from_saved_state(state, {"uuid-a": "7.mp4", "uuid-b": "3.mp4"}) == expected
    with pytest.raises(ValueError, match="source"):
        manifest_from_saved_state(state, {"uuid-a": "7.mp4"})


def rows(count=130):
    return [
        {"video_id": f"video{i}", "video": f"video{i}.mp4", "caption": f"query {i}"}
        for i in range(count)
    ]


def test_fixed_subset_is_order_independent_and_keeps_every_positive():
    first = select_msrvtt_pilot(rows(), queries=20, candidates=100, seed=20261113)
    reversed_input = select_msrvtt_pilot(rows()[::-1], queries=20, candidates=100, seed=20261113)
    assert first == reversed_input
    assert len(first["queries"]) == 20
    assert len(first["candidates"]) == 100
    candidate_ids = {item["video_id"] for item in first["candidates"]}
    assert all(set(q["labels"]) <= candidate_ids for q in first["queries"])
    assert all("caption" not in item and "text" not in item for item in first["candidates"])
    other_seed = select_msrvtt_pilot(rows(), queries=20, candidates=100, seed=7)
    assert other_seed["candidates"] != first["candidates"]


@pytest.mark.parametrize("queries,candidates", [(21, 20), (0, 20), (20, 0), (20, 131)])
def test_invalid_subset_sizes_fail_instead_of_silently_shrinking(queries, candidates):
    with pytest.raises(ValueError):
        select_msrvtt_pilot(rows(), queries=queries, candidates=candidates, seed=1)


def test_duplicate_ids_and_unsafe_video_names_fail():
    sample = rows()
    sample[-1] = sample[0]
    with pytest.raises(ValueError, match="duplicate"):
        select_msrvtt_pilot(sample, queries=2, candidates=10, seed=1)
    sample = rows()
    sample[0]["video"] = "../../escape.mp4"
    with pytest.raises(ValueError, match="filename"):
        select_msrvtt_pilot(sample, queries=2, candidates=10, seed=1)


def test_score_input_requires_full_rankings_and_exact_query_coverage():
    subset = select_msrvtt_pilot(rows(), queries=2, candidates=10, seed=1)
    ids = [c["video_id"] for c in subset["candidates"]]
    valid = {q["query_id"]: ids[:] for q in subset["queries"]}
    validate_rankings(subset, valid)
    with pytest.raises(ValueError, match="query"):
        validate_rankings(subset, dict(list(valid.items())[:1]))
    broken = {**valid, subset["queries"][0]["query_id"]: ids[:-1] + ids[:1]}
    with pytest.raises(ValueError, match="candidate"):
        validate_rankings(subset, broken)
    broken = {**valid, subset["queries"][0]["query_id"]: ids[:5]}
    with pytest.raises(ValueError, match="candidate"):
        validate_rankings(subset, broken)


def test_verifier_recovery_uses_the_parent_run_manifest_and_rejects_path_escape(tmp_path):
    from scripts.verify_public_editing_run import verification_paths

    parent, manifest = verification_paths(tmp_path, "editing_repair", "run-123")
    assert parent == tmp_path / "runs/editing_repair/run-123"
    assert manifest == parent / "inputs.json"
    for identifier in ("../escape", "/etc", "a/b"):
        with pytest.raises(ValueError):
            verification_paths(tmp_path, "editing_repair", identifier)
