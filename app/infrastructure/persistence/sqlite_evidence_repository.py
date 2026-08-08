import asyncio
import json
from uuid import UUID

from app.domain.evidence import EvidenceIngestionBatch, EvidenceIngestionSnapshot
from app.features.evidence_ingestion.repository import (
    EvidenceRepository,
    EvidenceWriteResult,
    EvidenceWriteStatus,
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
                return EvidenceWriteResult(EvidenceWriteStatus.MISSING)

            existing = connection.execute(
                "SELECT * FROM evidence_ingestions WHERE acquisition_id = ?",
                (str(batch.acquisition_id),),
            ).fetchone()
            if existing is not None:
                if (
                    existing["conversation_id"] != conversation_id
                    or existing["fingerprint"] != batch.fingerprint
                ):
                    return EvidenceWriteResult(EvidenceWriteStatus.CONFLICT)
                return EvidenceWriteResult(
                    EvidenceWriteStatus.EXISTING,
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
                        "raw_content, content_hash, acquired_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(actual_id),
                            conversation_id,
                            str(document.canonical_url),
                            document.title,
                            document.raw_content,
                            document.content_hash,
                            document.acquired_at.isoformat(),
                        ),
                    )
                    for chunk in chunks_by_document[document.document_id]:
                        connection.execute(
                            "INSERT INTO evidence_chunks "
                            "(chunk_id, document_id, chunk_index, text, "
                            "heading_path, start_offset, end_offset, "
                            "content_hash, chunker_version) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                str(chunk.chunk_id),
                                str(actual_id),
                                chunk.chunk_index,
                                chunk.text,
                                json.dumps(chunk.heading_path, ensure_ascii=False),
                                chunk.start_offset,
                                chunk.end_offset,
                                chunk.content_hash,
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
            for query in batch.queries:
                connection.execute(
                    "INSERT INTO evidence_queries "
                    "(acquisition_id, query_id, position, kind, query_text, "
                    "facet, research_goal) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(batch.acquisition_id),
                        str(query.query_id),
                        query.position,
                        query.kind.value,
                        query.text,
                        query.facet.value if query.facet is not None else None,
                        query.research_goal,
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
            return EvidenceWriteResult(EvidenceWriteStatus.CREATED, snapshot)


def _uuid_json(values: tuple[UUID, ...]) -> str:
    return json.dumps([str(value) for value in values])


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
