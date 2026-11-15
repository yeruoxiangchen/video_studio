import pytest
from camcat.services.storyboard_review import (
    apply_pairwise_review,
    ranked_assessments,
    video_assessments,
)


def rows() -> list[dict]:
    return [
        {"material_id": "a", "fit": 95, "contradiction": False, "reason": "first view"},
        {"material_id": "b", "fit": 90, "contradiction": False, "reason": "second view"},
        {"material_id": "c", "fit": 99, "contradiction": True, "reason": "wrong setting"},
    ]


def test_only_order_independent_comparisons_can_reverse_the_ranking() -> None:
    original = rows()
    verdict = {"preferred": "b", "confidence": "weak", "opening_comparison": "knees visible"}
    output = apply_pairwise_review(original, ["a", "b"], [verdict, verdict])
    assert output[1]["fit"] > output[0]["fit"]
    assert output[2] == original[2]
    assert original == rows()
    for second in (
        {**verdict, "preferred": "a"},
        {**verdict, "confidence": "invalid"},
        {**verdict, "preferred": "unknown"},
    ):
        assert apply_pairwise_review(original, ["a", "b"], [verdict, second]) == original


def test_consistent_uncertain_preferences_remain_soft_rankings_with_explicit_review_text() -> None:
    verdict = {"preferred": "b", "confidence": "uncertain", "opening_comparison": "wider opening"}
    output = apply_pairwise_review(rows(), ["a", "b"], [verdict, verdict])
    assert output[1]["fit"] > output[0]["fit"]
    assert "uncertain" in output[1]["reason"]


def test_visual_ranking_preserves_every_retrieved_candidate_for_global_assignment() -> None:
    reviews = [
        {"preferred": "c", "ranking": ["c", "a", "b"], "opening_comparison": "wide opening"},
        {"preferred": "c", "ranking": ["c", "b", "a"], "opening_comparison": "same opening"},
    ]
    output = ranked_assessments(["a", "b", "c"], reviews)
    by_id = {r["material_id"]: r for r in output}
    assert set(by_id) == {"a", "b", "c"}
    assert by_id["c"]["fit"] > by_id["a"]["fit"] == by_id["b"]["fit"]
    assert not any(r["contradiction"] for r in output)
    with pytest.raises(ValueError, match="exact candidate"):
        ranked_assessments(["a", "b", "c"], [{**reviews[0], "ranking": ["c", "a", "a"]}])


def test_direct_video_probabilities_keep_input_identity_and_all_candidates() -> None:
    output = video_assessments(["second", "first", "third"], [0.2, 0.95, 0.05])
    assert [(r["material_id"], r["fit"]) for r in output] == [
        ("second", 20),
        ("first", 95),
        ("third", 5),
    ]
    assert not any(r["contradiction"] for r in output)
    for bad in ([0.2], [0.1, float("nan"), 0.2], [0.1, 1.5, 0.2]):
        with pytest.raises(ValueError, match="video reranker"):
            video_assessments(["second", "first", "third"], bad)
