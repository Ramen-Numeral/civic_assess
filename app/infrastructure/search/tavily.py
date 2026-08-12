from typing import Annotated

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
)

from app.domain.research import AcquisitionFailureCode
from app.features.research_acquisition.client import (
    SearchClientError,
    SearchHit,
    SearchResponse,
)


ProviderText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class _TavilyResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: ProviderText
    url: HttpUrl
    content: ProviderText
    raw_content: str | None = None
    score: float | None = Field(default=None, ge=0)
    id: ProviderText | None = None

    @field_validator("raw_content")
    @classmethod
    def normalize_raw_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class _TavilyUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credits: int = Field(ge=0)


class _TavilyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: ProviderText
    results: tuple[_TavilyResult, ...] = Field(max_length=20)
    usage: _TavilyUsage | None = None


class TavilySearchClient:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 20,
        base_url: str = "https://api.tavily.com",
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._url = f"{base_url.rstrip('/')}/search"

    async def search(
        self,
        query: str,
        *,
        max_results: int,
    ) -> SearchResponse:
        if not 1 <= max_results <= 5:
            raise ValueError("max_results must be between 1 and 5")
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "topic": "general",
            "include_answer": False,
            "include_raw_content": "markdown",
            "include_images": False,
            "include_favicon": False,
            "auto_parameters": False,
            "include_usage": True,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise SearchClientError(
                AcquisitionFailureCode.TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise SearchClientError(
                AcquisitionFailureCode.PROVIDER_UNAVAILABLE,
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            raise _http_error(response.status_code)
        try:
            parsed = _TavilyResponse.model_validate(response.json())
        except ValueError as exc:
            raise SearchClientError(
                AcquisitionFailureCode.INVALID_RESPONSE,
                retryable=False,
            ) from exc
        return SearchResponse(
            request_id=parsed.request_id,
            credits_used=(parsed.usage.credits if parsed.usage is not None else None),
            results=tuple(
                SearchHit(
                    title=result.title,
                    url=str(result.url),
                    content=result.content,
                    raw_content=result.raw_content,
                    score=result.score,
                    provider_result_id=result.id,
                )
                for result in parsed.results
            ),
        )


def _http_error(status_code: int) -> SearchClientError:
    if status_code in {401, 403}:
        code = AcquisitionFailureCode.UNAUTHORIZED
        retryable = False
    elif status_code == 429:
        code = AcquisitionFailureCode.RATE_LIMITED
        retryable = True
    elif status_code in {432, 433}:
        code = AcquisitionFailureCode.QUOTA_EXCEEDED
        retryable = False
    elif 400 <= status_code < 500:
        code = AcquisitionFailureCode.REQUEST_REJECTED
        retryable = False
    else:
        code = AcquisitionFailureCode.PROVIDER_UNAVAILABLE
        retryable = True
    return SearchClientError(code, retryable=retryable)
