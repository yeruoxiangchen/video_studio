import pytest
from camcat.agent.continuity import backward_candidates, confirmed_pair_order, stable_block_order


def review(order, earlier, certainty="clear"):
    return {
        "order": order,
        "result": {
            "likely_earlier": earlier,
            "certainty": certainty,
            "observed_causal_link": "The visible entry into the room precedes actions inside it.",
        },
    }


def test_repair_moves_a_confirmed_entry_before_its_aftermath_and_keeps_other_blocks():
    original = ["intro", "fight", "entry-and-dialogue", "ending"]
    assert stable_block_order(original, [("entry-and-dialogue", "fight")]) == [
        "intro",
        "entry-and-dialogue",
        "fight",
        "ending",
    ]
    assert original == ["intro", "fight", "entry-and-dialogue", "ending"]
    assert stable_block_order(original, []) == original


def test_only_consistent_evidence_in_both_presentation_orders_confirms_a_constraint():
    forward = review(["entry", "fight"], "A")
    reverse = review(["fight", "entry"], "B")
    assert confirmed_pair_order([forward, reverse]) == ("entry", "fight")
    assert confirmed_pair_order([forward, review(["fight", "entry"], "A")]) is None
    assert confirmed_pair_order([forward, review(["fight", "entry"], "B", "weak")]) is None


def test_bad_constraints_cannot_drop_sources_or_force_cycles():
    with pytest.raises(ValueError, match="unknown"):
        stable_block_order(["one", "two"], [("unknown", "two")])
    with pytest.raises(ValueError, match="cycle"):
        stable_block_order(["one", "two"], [("one", "two"), ("two", "one")])


def test_review_candidates_include_only_clear_violations_of_the_actual_draft():
    def edge(before, after, certainty="clear"):
        return {
            "earlier_block": before,
            "later_block": after,
            "certainty": certainty,
            "visible_evidence": "The actor visibly enters before the encounter.",
        }

    reviews = [
        {
            "constraints": [
                edge("intro", "fight"),
                edge("entry", "fight"),
                edge("ending", "intro", "uncertain"),
            ]
        }
    ]
    assert backward_candidates(reviews, ["intro", "fight", "entry", "ending"]) == [
        ("entry", "fight")
    ]
