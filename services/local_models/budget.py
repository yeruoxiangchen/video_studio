from __future__ import annotations

MAX_INPUT_TOKENS = 12288


class ContextBudgetExceeded(ValueError):
    def __init__(self, prompt_tokens: int, max_input_tokens: int = MAX_INPUT_TOKENS) -> None:
        self.prompt_tokens = prompt_tokens
        self.max_input_tokens = max_input_tokens
        super().__init__(
            f"Input uses {prompt_tokens} tokens; local input budget is {max_input_tokens}"
        )

    def details(self) -> dict[str, str | int]:
        return {
            "code": "context_budget_exceeded",
            "prompt_tokens": self.prompt_tokens,
            "max_input_tokens": self.max_input_tokens,
        }


def check_input_budget(prompt_tokens: int) -> None:
    if prompt_tokens > MAX_INPUT_TOKENS:
        raise ContextBudgetExceeded(prompt_tokens)
