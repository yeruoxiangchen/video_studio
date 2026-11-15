import pytest


def test_context_overflow_has_safe_token_counts_and_accepts_the_budget_boundary():
    from services.local_models.budget import ContextBudgetExceeded, check_input_budget

    check_input_budget(12288)
    with pytest.raises(ContextBudgetExceeded) as error:
        check_input_budget(13000)
    assert error.value.details() == {
        "code": "context_budget_exceeded",
        "prompt_tokens": 13000,
        "max_input_tokens": 12288,
    }
