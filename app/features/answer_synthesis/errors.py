from typing import Literal


AnswerSynthesisErrorCode = Literal["answer_writer_unavailable"]


class AnswerSynthesisError(ValueError):
    def __init__(
        self,
        code: AnswerSynthesisErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class InvalidAnswerProposalError(ValueError):
    """Answer output violated claim or grounding invariants."""
