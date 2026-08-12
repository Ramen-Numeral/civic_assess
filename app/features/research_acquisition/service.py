import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.research import (
    AcquiredSearchResult,
    QueryAcquisitionFailure,
    QueryAcquisitionOutcome,
    QueryAcquisitionSuccess,
    ResearchAcquisitionSet,
)
from app.domain.research import ResearchQuerySet
from app.features.research_acquisition.client import (
    SearchClient,
    SearchClientError,
)
from app.features.research_acquisition.errors import ResearchAcquisitionError


class ResearchAcquisitionService:
    def __init__(
        self,
        client: SearchClient,
        *,
        results_per_query: int = 5,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= results_per_query <= 5:
            raise ValueError("results_per_query must be between 1 and 5")
        self._client = client
        self._results_per_query = results_per_query
        self._now = now or (lambda: datetime.now(UTC))

    async def acquire(
        self,
        query_set: ResearchQuerySet,
    ) -> ResearchAcquisitionSet:
        queries = (query_set.original_query, *query_set.diversified_queries)
        if len(queries) > 6:
            raise ValueError("acquisition accepts at most six research queries")
        outcomes = tuple(
            await asyncio.gather(*(
                self._acquire_query(query.query_id, query.text)
                for query in queries
            ))
        )
        if not any(
            isinstance(outcome, QueryAcquisitionSuccess)
            for outcome in outcomes
        ):
            raise ResearchAcquisitionError(
                "acquisition_unavailable",
                "I couldn't retrieve research sources right now.",
                retryable=any(outcome.retryable for outcome in outcomes),
            )
        return ResearchAcquisitionSet(
            acquisition_id=uuid4(),
            acquired_at=self._now(),
            outcomes=outcomes,
        )

    async def _acquire_query(
        self,
        query_id: UUID,
        query_text: str,
    ) -> QueryAcquisitionOutcome:
        try:
            response = await self._client.search(
                query_text,
                max_results=self._results_per_query,
            )
        except SearchClientError as exc:
            return QueryAcquisitionFailure(
                query_id=query_id,
                query_text=query_text,
                error_code=exc.code,
                retryable=exc.retryable,
            )
        return QueryAcquisitionSuccess(
            query_id=query_id,
            query_text=query_text,
            provider_request_id=response.request_id,
            credits_used=response.credits_used,
            results=tuple(
                AcquiredSearchResult(
                    result_id=uuid4(),
                    rank=rank,
                    title=result.title,
                    url=result.url,
                    snippet=result.content,
                    raw_content=result.raw_content,
                    provider_score=result.score,
                    provider_result_id=result.provider_result_id,
                )
                for rank, result in enumerate(
                    response.results[:self._results_per_query],
                    start=1,
                )
            ),
        )
