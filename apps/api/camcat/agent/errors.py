"""Actionable input failures shared by synchronous and streamed editing."""


class AgentInputError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        self.error_code = code
        self.public_message = message
        self.details: dict[str, str] = {}
        super().__init__(message)


class EditPlanRejected(AgentInputError):
    def __init__(self, feedback: str) -> None:
        super().__init__(
            f"domain-command model failed validation after repair: {feedback}",
            code="edit_plan_invalid",
        )
        self.public_message = "编辑方案未通过约束检查，请调整要求后重试。本次编辑未保存。"
        self.details = {"validation_error": feedback}
