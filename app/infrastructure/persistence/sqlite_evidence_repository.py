import asyncio
import json
import re
import struct
from datetime import datetime
from uuid import UUID

from app.features.evidence_ingestion.repository import EvidenceRepository
from app.features.evidence_ingestion.schemas import (
    EvidenceIngestionBatch,
    EvidenceIngestionSnapshot,
    EvidenceWriteResult,
)
from app.features.evidence_retrieval.schemas import (
    EvidenceCandidate,
    ScoredEvidenceCandidate,
)
from app.infrastructure.persistence.sqlite import SQLiteDatabase


class SQLiteEvidenceRepository(EvidenceRepository):
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    async def write_evidence(
        self,
        batch: EvidenceIngestionBatch,
    ) -> EvidenceWriteResult:
        return await asyncio.to_thread(self._write_evidence, batch)

    def _write_evidence(
        self,
        batch: EvidenceIngestionBatch,
    ) -> EvidenceWriteResult:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            conversation_id = str(batch.conversation_id)
            conversation = connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                return EvidenceWriteResult("missing")

            existing = connection.execute(
                "SELECT * FROM evidence_ingestions WHERE acquisition_id = ?",
                (str(batch.acquisition_id),),
            ).fetchone()
            if existing is not None:
                if (
                    existing["conversation_id"] != conversation_id
                    or existing["fingerprint"] != batch.fingerprint
                ):
                    return EvidenceWriteResult("conflict")
                return EvidenceWriteResult(
                    "existing",
                    _snapshot(existing),
                )

            chunks_by_document = {
                document.document_id: tuple(
                    chunk
                    for chunk in batch.chunks
                    if chunk.document_id == document.document_id
                )
                for document in batch.documents
            }
            document_ids: list[UUID] = []
            chunk_ids: list[UUID] = []
            document_map: dict[UUID, UUID] = {}
            new_documents = 0
            reused_documents = 0
            new_chunks = 0
            for document in batch.documents:
                row = connection.execute(
                    "SELECT document_id FROM evidence_documents "
                    "WHERE conversation_id = ? AND canonical_url = ? "
                    "AND content_hash = ?",
                    (
                        conversation_id,
                        str(document.canonical_url),
                        document.content_hash,
                    ),
                ).fetchone()
                if row is None:
                    actual_id = document.document_id
                    connection.execute(
                        "INSERT INTO evidence_documents "
                        "(document_id, conversation_id, canonical_url, title, "
                        "content, content_hash, acquired_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(actual_id),
                            conversation_id,
                            str(document.canonical_url),
                            document.title,
                            document.content,
                            document.content_hash,
                            document.acquired_at.isoformat(),
                        ),
                    )
                    for chunk in chunks_by_document[document.document_id]:
                        connection.execute(
                            "INSERT INTO evidence_chunks "
                            "(chunk_id, document_id, chunk_index, text, "
                            "heading_path, start_offset, end_offset, "
                            "chunker_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                str(chunk.chunk_id),
                                str(actual_id),
                                chunk.chunk_index,
                                chunk.text,
                                json.dumps(chunk.heading_path, ensure_ascii=False),
                                chunk.start_offset,
                                chunk.end_offset,
                                chunk.chunker_version,
                            ),
                        )
                        chunk_ids.append(chunk.chunk_id)
                        new_chunks += 1
                    new_documents += 1
                else:
                    actual_id = UUID(row["document_id"])
                    rows = connection.execute(
                        "SELECT chunk_id FROM evidence_chunks "
                        "WHERE document_id = ? ORDER BY chunk_index",
                        (str(actual_id),),
                    ).fetchall()
                    chunk_ids.extend(UUID(item["chunk_id"]) for item in rows)
                    reused_documents += 1
                document_map[document.document_id] = actual_id
                document_ids.append(actual_id)

            snapshot = EvidenceIngestionSnapshot(
                acquisition_id=batch.acquisition_id,
                conversation_id=batch.conversation_id,
                round_number=batch.round_number,
                ingested_at=batch.ingested_at,
                document_ids=tuple(document_ids),
                chunk_ids=tuple(chunk_ids),
                skipped_result_ids=batch.skipped_result_ids,
                new_document_count=new_documents,
                reused_document_count=reused_documents,
                new_chunk_count=new_chunks,
            )
            connection.execute(
                "INSERT INTO evidence_ingestions "
                "(acquisition_id, conversation_id, round_number, fingerprint, "
                "ingested_at, document_ids, chunk_ids, skipped_result_ids, "
                "new_document_count, reused_document_count, new_chunk_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(batch.acquisition_id),
                    conversation_id,
                    batch.round_number,
                    batch.fingerprint,
                    batch.ingested_at.isoformat(),
                    _uuid_json(snapshot.document_ids),
                    _uuid_json(snapshot.chunk_ids),
                    _uuid_json(snapshot.skipped_result_ids),
                    new_documents,
                    reused_documents,
                    new_chunks,
                ),
            )
            queries = [
                (batch.query_set.original_query, "original"),
                *(
                    (query, "diversified")
                    for query in batch.query_set.diversified_queries
                ),
            ]
            for position, (query, kind) in enumerate(queries):
                connection.execute(
                    "INSERT INTO evidence_queries "
                    "(acquisition_id, query_id, position, kind, query_text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        str(batch.acquisition_id),
                        str(query.query_id),
                        position,
                        kind,
                        query.text,
                    ),
                )
            for document in batch.documents:
                for discovery in document.discoveries:
                    connection.execute(
                        "INSERT INTO evidence_document_discoveries "
                        "(acquisition_id, document_id, query_id, result_id, "
                        "provider_rank, original_url, provider_result_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(batch.acquisition_id),
                            str(document_map[document.document_id]),
                            str(discovery.query_id),
                            str(discovery.result_id),
                            discovery.rank,
                            str(discovery.original_url),
                            discovery.provider_result_id,
                        ),
                    )
            return EvidenceWriteResult("created", snapshot)

    async def unembedded_chunks(
        self, conversation_id: UUID, version: str, limit: int
    ) -> tuple[tuple[UUID, str], ...]:
        return await asyncio.to_thread(
            self._unembedded_chunks, conversation_id, version, limit
        )

    def _unembedded_chunks(
        self, conversation_id: UUID, version: str, limit: int
    ) -> tuple[tuple[UUID, str], ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT c.chunk_id, c.text FROM evidence_chunks c "
                "JOIN evidence_documents d USING (document_id) "
                "WHERE d.conversation_id = ? AND NOT EXISTS (SELECT 1 "
                "FROM evidence_chunk_embeddings e WHERE e.chunk_id = c.chunk_id "
                "AND e.embedding_version = ?) "
                "ORDER BY d.document_id, c.chunk_index LIMIT ?",
                (str(conversation_id), version, limit),
            ).fetchall()
            return tuple((UUID(row["chunk_id"]), row["text"]) for row in rows)

    async def write_embeddings(
        self,
        version: str,
        dimension: int,
        rows: tuple[tuple[UUID, tuple[float, ...]], ...],
        created_at: datetime,
    ) -> int | None:
        return await asyncio.to_thread(
            self._write_embeddings,
            version, dimension, rows, created_at,
        )

    def _write_embeddings(
        self,
        version: str,
        dimension: int,
        rows: tuple[tuple[UUID, tuple[float, ...]], ...],
        created_at: datetime,
    ) -> int | None:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for chunk_id, _ in rows:
                chunk = connection.execute(
                    "SELECT 1 FROM evidence_chunks WHERE chunk_id = ?",
                    (str(chunk_id),),
                ).fetchone()
                existing = connection.execute(
                    "SELECT dimension FROM evidence_chunk_embeddings "
                    "WHERE chunk_id = ? AND embedding_version = ?",
                    (str(chunk_id), version),
                ).fetchone()
                if chunk is None or (
                    existing is not None and existing["dimension"] != dimension
                ):
                    return None
            before = connection.total_changes
            connection.executemany(
                "INSERT OR IGNORE INTO evidence_chunk_embeddings "
                "(chunk_id, embedding_version, dimension, vector, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        str(chunk_id), version, dimension,
                        struct.pack(f"<{dimension}f", *vector),
                        created_at.isoformat(),
                    )
                    for chunk_id, vector in rows
                ],
            )
            return connection.total_changes - before

    async def search_evidence_text(
        self, conversation_id: UUID, query: str, limit: int
    ) -> tuple[ScoredEvidenceCandidate, ...]:
        return await asyncio.to_thread(
            self._search_evidence_text, conversation_id, query, limit
        )

    def _search_evidence_text(
        self, conversation_id: UUID, query: str, limit: int
    ) -> tuple[ScoredEvidenceCandidate, ...]:
        terms = tuple(dict.fromkeys(re.findall(r"\w+", query.casefold())))
        if not terms:
            return ()
        expression = " OR ".join(f'"{term}"' for term in terms)
        with self._database.connect() as connection:
            rows = connection.execute(
                "WITH discoveries AS (SELECT x.document_id, "
                "MAX(i.ingested_at) last_discovered_at "
                "FROM evidence_document_discoveries x "
                "JOIN evidence_ingestions i USING (acquisition_id) "
                "WHERE i.conversation_id = ? GROUP BY x.document_id) "
                "SELECT c.*, d.title, d.canonical_url, "
                "discoveries.last_discovered_at, "
                "bm25(evidence_chunks_fts) score FROM evidence_chunks_fts "
                "JOIN evidence_chunks c ON c.rowid = evidence_chunks_fts.rowid "
                "JOIN evidence_documents d USING (document_id) "
                "JOIN discoveries USING (document_id) "
                "WHERE evidence_chunks_fts MATCH ? AND d.conversation_id = ? "
                "ORDER BY score, c.chunk_id LIMIT ?",
                (str(conversation_id), expression, str(conversation_id), limit),
            ).fetchall()
            return tuple(
                ScoredEvidenceCandidate(
                    evidence=_candidate(row),
                    rank=rank,
                    score=row["score"],
                )
                for rank, row in enumerate(rows, start=1)
            )

    async def load_evidence_vectors(
        self, conversation_id: UUID, version: str
    ) -> tuple[tuple[EvidenceCandidate, tuple[float, ...]], ...]:
        return await asyncio.to_thread(
            self._load_evidence_vectors, conversation_id, version
        )

    def _load_evidence_vectors(
        self, conversation_id: UUID, version: str
    ) -> tuple[tuple[EvidenceCandidate, tuple[float, ...]], ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "WITH discoveries AS (SELECT x.document_id, "
                "MAX(i.ingested_at) last_discovered_at "
                "FROM evidence_document_discoveries x "
                "JOIN evidence_ingestions i USING (acquisition_id) "
                "WHERE i.conversation_id = ? GROUP BY x.document_id) "
                "SELECT c.*, d.title, d.canonical_url, "
                "discoveries.last_discovered_at, e.dimension, e.vector "
                "FROM evidence_chunk_embeddings e "
                "JOIN evidence_chunks c USING (chunk_id) "
                "JOIN evidence_documents d USING (document_id) "
                "JOIN discoveries USING (document_id) "
                "WHERE d.conversation_id = ? AND e.embedding_version = ? "
                "ORDER BY c.chunk_id",
                (str(conversation_id), str(conversation_id), version),
            ).fetchall()
            return tuple(
                (
                    _candidate(row),
                    struct.unpack(f'<{row["dimension"]}f', row["vector"]),
                )
                for row in rows
            )


def _uuid_json(values: tuple[UUID, ...]) -> str:
    return json.dumps([str(value) for value in values])


def _candidate(row) -> EvidenceCandidate:
    return EvidenceCandidate(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        text=row["text"],
        title=row["title"],
        canonical_url=row["canonical_url"],
        heading_path=tuple(json.loads(row["heading_path"])),
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        last_discovered_at=row["last_discovered_at"],
    )


def _snapshot(row) -> EvidenceIngestionSnapshot:
    return EvidenceIngestionSnapshot(
        acquisition_id=UUID(row["acquisition_id"]),
        conversation_id=UUID(row["conversation_id"]),
        round_number=row["round_number"],
        ingested_at=row["ingested_at"],
        document_ids=tuple(UUID(value) for value in json.loads(row["document_ids"])),
        chunk_ids=tuple(UUID(value) for value in json.loads(row["chunk_ids"])),
        skipped_result_ids=tuple(
            UUID(value) for value in json.loads(row["skipped_result_ids"])
        ),
        new_document_count=row["new_document_count"],
        reused_document_count=row["reused_document_count"],
        new_chunk_count=row["new_chunk_count"],
    )
