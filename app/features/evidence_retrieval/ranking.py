from dataclasses import dataclass
from uuid import UUID

from app.features.evidence_retrieval.schemas import (
    EvidenceCandidate,
    QueryEvidenceRetrieval,
    RankedEvidenceCandidate,
)


@dataclass
class _Ranked:
    evidence: EvidenceCandidate
    score: float
    best_rank: int


def finalize_candidates(
    results: tuple[QueryEvidenceRetrieval, ...],
    *,
    rrf_k: int,
    lexical_weight: float,
    semantic_weight: float,
    limit: int,
    document_limit: int,
    query_requirements: dict[UUID, tuple[UUID, ...]],
) -> tuple[RankedEvidenceCandidate, ...]:
    per_query: list[list[_Ranked]] = []
    global_items: dict[UUID, _Ranked] = {}
    supports: dict[UUID, list[UUID]] = {}
    for result in results:
        items: dict[UUID, _Ranked] = {}
        for candidates, weight in (
            (result.lexical, lexical_weight),
            (result.semantic, semantic_weight),
        ):
            if weight == 0:
                continue
            for candidate in candidates:
                chunk_id = candidate.evidence.chunk_id
                item = items.get(chunk_id)
                contribution = weight / (rrf_k + candidate.rank)
                if item is None:
                    items[chunk_id] = _Ranked(
                        candidate.evidence, contribution, candidate.rank
                    )
                else:
                    item.score += contribution
                    item.best_rank = min(item.best_rank, candidate.rank)
        ranking = sorted(items.values(), key=_query_key)
        per_query.append(ranking)
        for item in ranking:
            chunk_id = item.evidence.chunk_id
            supports.setdefault(chunk_id, []).append(result.query_id)
            current = global_items.get(chunk_id)
            if current is None:
                global_items[chunk_id] = _Ranked(
                    item.evidence, item.score, item.best_rank
                )
            else:
                if item.score > current.score:
                    current.score = item.score
                current.best_rank = min(current.best_rank, item.best_rank)

    selected: list[_Ranked] = []
    selected_ids: set[UUID] = set()
    document_counts: dict[UUID, int] = {}
    requirement_queries: dict[UUID, list[int]] = {}
    for index, result in enumerate(results):
        for requirement_id in query_requirements.get(result.query_id, ()):
            requirement_queries.setdefault(requirement_id, []).append(index)
    positions: dict[tuple[UUID, int], int] = {}
    query_cursors = {requirement_id: 0 for requirement_id in requirement_queries}
    while len(selected) < limit:
        progressed = False
        for requirement_id, indexes in requirement_queries.items():
            chosen = False
            for _ in indexes:
                cursor = query_cursors[requirement_id]
                index = indexes[cursor % len(indexes)]
                query_cursors[requirement_id] = cursor + 1
                ranking = per_query[index]
                key = (requirement_id, index)
                position = positions.get(key, 0)
                while position < len(ranking):
                    item = ranking[position]
                    position += 1
                    chunk_id = item.evidence.chunk_id
                    if chunk_id in selected_ids:
                        continue
                    document_id = item.evidence.document_id
                    if document_counts.get(document_id, 0) >= document_limit:
                        continue
                    selected.append(global_items[chunk_id])
                    selected_ids.add(chunk_id)
                    document_counts[document_id] = (
                        document_counts.get(document_id, 0) + 1
                    )
                    progressed = True
                    chosen = True
                    break
                positions[key] = position
                if chosen:
                    break
            if len(selected) == limit:
                break
        if not progressed:
            break

    for item in sorted(global_items.values(), key=_query_key):
        if len(selected) == limit:
            break
        if item.evidence.chunk_id not in selected_ids:
            selected.append(item)
            selected_ids.add(item.evidence.chunk_id)
    return tuple(
        RankedEvidenceCandidate(
            evidence=item.evidence,
            rank=rank,
            rrf_score=item.score,
            supporting_query_ids=tuple(supports[item.evidence.chunk_id]),
        )
        for rank, item in enumerate(selected, start=1)
    )


def _query_key(item: _Ranked) -> tuple[float, float, int, str]:
    return (
        -item.score,
        -item.evidence.last_discovered_at.timestamp(),
        item.best_rank,
        str(item.evidence.chunk_id),
    )
