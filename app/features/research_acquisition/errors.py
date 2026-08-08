from typing import Literal


ResearchAcquisitionErrorCode = Literal["acquisition_unavailable"]


class ResearchAcquisitionError(RuntimeError):
    def __init__(
        self,
        code: ResearchAcquisitionErrorCode,
        safe_message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
