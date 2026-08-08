from dataclasses import dataclass
from typing import Protocol

from app.domain.acquisition import AcquisitionFailureCode


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str
    raw_content: str | None = None
    score: float | None = None
    provider_result_id: str | None = None


@dataclass(frozen=True)
class SearchResponse:
    request_id: str
    results: tuple[SearchHit, ...]
    credits_used: int | None = None


class SearchClientError(RuntimeError):
    def __init__(
        self,
        code: AcquisitionFailureCode,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.retryable = retryable


class SearchClient(Protocol):
    async def search(
        self,
        query: str,
        *,
        max_results: int,
    ) -> SearchResponse: ...
