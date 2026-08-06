class LLMError(RuntimeError):
    """Base error for model invocation failures."""


class LLMTimeoutError(LLMError):
    pass


class LLMStructuredOutputError(LLMError):
    pass
