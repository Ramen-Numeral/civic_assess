import asyncio
import json
import sqlite3
from datetime import datetime
from uuid import UUID

from app.domain.conversation import (
    Conversation,
    ConversationRole,
    ConversationStateSnapshot,
    StoredConversationTurn,
)
from app.features.conversation.repository import (
    AppendUserTurnResult,
    AppendUserTurnStatus,
    ConversationRepository,
    StateWriteResult,
    StateWriteStatus,
)
from app.infrastructure.persistence.sqlite import SQLiteDatabase


class SQLiteConversationRepository(ConversationRepository):
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    async def create_conversation(self, conversation: Conversation) -> None:
        await asyncio.to_thread(self._create_conversation, conversation)

    async def get_conversation(
        self,
        conversation_id: UUID,
    ) -> Conversation | None:
        return await asyncio.to_thread(self._get_conversation, conversation_id)

    async def get_turn(self, turn_id: UUID) -> StoredConversationTurn | None:
        return await asyncio.to_thread(self._get_turn, turn_id)

    async def get_conversation_state(
        self,
        conversation_id: UUID,
    ) -> ConversationStateSnapshot | None:
        return await asyncio.to_thread(
            self._get_conversation_state,
            conversation_id,
        )

    async def write_conversation_state(
        self,
        snapshot: ConversationStateSnapshot,
        *,
        expected_revision: int | None,
    ) -> StateWriteResult:
        return await asyncio.to_thread(
            self._write_conversation_state,
            snapshot,
            expected_revision,
        )

    async def append_user_turn(
        self,
        *,
        conversation_id: UUID,
        turn_id: UUID,
        client_message_id: UUID,
        content: str,
        created_at: datetime,
    ) -> AppendUserTurnResult:
        return await asyncio.to_thread(
            self._append_user_turn,
            conversation_id,
            turn_id,
            client_message_id,
            content,
            created_at,
        )

    async def append_assistant_turn(
        self,
        *,
        conversation_id: UUID,
        turn_id: UUID,
        content: str,
        created_at: datetime,
    ) -> StoredConversationTurn | None:
        return await asyncio.to_thread(
            self._append_assistant_turn,
            conversation_id,
            turn_id,
            content,
            created_at,
        )

    async def list_turns(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int | None = None,
        before_sequence: int | None = None,
        limit: int | None = None,
    ) -> tuple[StoredConversationTurn, ...]:
        return await asyncio.to_thread(
            self._list_turns,
            conversation_id,
            after_sequence,
            before_sequence,
            limit,
        )

    async def delete_conversation(self, conversation_id: UUID) -> bool:
        return await asyncio.to_thread(self._delete_conversation, conversation_id)

    async def discard_latest_user_turn(
        self,
        conversation_id: UUID,
        turn_id: UUID,
    ) -> bool:
        return await asyncio.to_thread(
            self._discard_latest_user_turn,
            conversation_id,
            turn_id,
        )

    def _create_conversation(self, conversation: Conversation) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO conversations "
                "(conversation_id, created_at, expires_at) VALUES (?, ?, ?)",
                (
                    str(conversation.conversation_id),
                    conversation.created_at.isoformat(),
                    conversation.expires_at.isoformat(),
                ),
            )

    def _get_conversation(self, conversation_id: UUID) -> Conversation | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT conversation_id, created_at, expires_at "
                "FROM conversations WHERE conversation_id = ?",
                (str(conversation_id),),
            ).fetchone()
        return _conversation(row) if row is not None else None

    def _get_turn(self, turn_id: UUID) -> StoredConversationTurn | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE turn_id = ?",
                (str(turn_id),),
            ).fetchone()
        return _turn(row) if row is not None else None

    def _get_conversation_state(
        self,
        conversation_id: UUID,
    ) -> ConversationStateSnapshot | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_state WHERE conversation_id = ?",
                (str(conversation_id),),
            ).fetchone()
        return _state(row) if row is not None else None

    def _write_conversation_state(
        self,
        snapshot: ConversationStateSnapshot,
        expected_revision: int | None,
    ) -> StateWriteResult:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            conversation_id = str(snapshot.conversation_id)
            conversation = connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                return StateWriteResult(StateWriteStatus.MISSING)

            current_row = connection.execute(
                "SELECT * FROM conversation_state WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if expected_revision is None:
                if current_row is not None:
                    return StateWriteResult(StateWriteStatus.CONFLICT)
            elif current_row is None or current_row["revision"] != expected_revision:
                return StateWriteResult(StateWriteStatus.CONFLICT)

            current_revision = (
                int(current_row["revision"]) if current_row is not None else 0
            )
            if snapshot.revision != current_revision + 1:
                return StateWriteResult(StateWriteStatus.CONFLICT)

            current_watermark = (
                int(current_row["summary_through_sequence"])
                if current_row is not None
                else 0
            )
            latest_turn = connection.execute(
                "SELECT MAX(sequence_number) AS sequence_number FROM turns "
                "WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            latest_sequence = latest_turn["sequence_number"]
            if (
                snapshot.summary_through_sequence <= current_watermark
                or latest_sequence is None
                or snapshot.summary_through_sequence > latest_sequence
            ):
                return StateWriteResult(StateWriteStatus.INVALID_WATERMARK)

            values = (
                str(snapshot.state_id),
                snapshot.summary_through_sequence,
                snapshot.revision,
                snapshot.summarizer_version,
                snapshot.current_goal,
                json.dumps(snapshot.confirmed_decisions, ensure_ascii=False),
                json.dumps(snapshot.rejected_proposals, ensure_ascii=False),
                json.dumps(snapshot.superseded_decisions, ensure_ascii=False),
                json.dumps(snapshot.active_constraints, ensure_ascii=False),
                json.dumps(snapshot.open_questions, ensure_ascii=False),
                json.dumps(snapshot.important_corrections, ensure_ascii=False),
                snapshot.summary,
                snapshot.updated_at.isoformat(),
            )
            if current_row is None:
                connection.execute(
                    "INSERT INTO conversation_state "
                    "(state_id, conversation_id, summary_through_sequence, "
                    "revision, summarizer_version, current_goal, "
                    "confirmed_decisions, rejected_proposals, "
                    "superseded_decisions, active_constraints, open_questions, "
                    "important_corrections, summary, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (values[0], conversation_id, *values[1:]),
                )
                status = StateWriteStatus.CREATED
            else:
                connection.execute(
                    "UPDATE conversation_state SET state_id = ?, "
                    "summary_through_sequence = ?, revision = ?, "
                    "summarizer_version = ?, current_goal = ?, "
                    "confirmed_decisions = ?, rejected_proposals = ?, "
                    "superseded_decisions = ?, active_constraints = ?, "
                    "open_questions = ?, important_corrections = ?, summary = ?, "
                    "updated_at = ? WHERE conversation_id = ?",
                    (*values, conversation_id),
                )
                status = StateWriteStatus.UPDATED

            persisted = connection.execute(
                "SELECT * FROM conversation_state WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            return StateWriteResult(status, _state(persisted))

    def _append_user_turn(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        client_message_id: UUID,
        content: str,
        created_at: datetime,
    ) -> AppendUserTurnResult:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM turns "
                "WHERE conversation_id = ? AND client_message_id = ?",
                (str(conversation_id), str(client_message_id)),
            ).fetchone()
            if existing is not None:
                status = (
                    AppendUserTurnStatus.EXISTING
                    if existing["content"] == content
                    else AppendUserTurnStatus.CONFLICT
                )
                return AppendUserTurnResult(status, _turn(existing))

            sequence = _allocate_sequence(connection, conversation_id)
            if sequence is None:
                return AppendUserTurnResult(AppendUserTurnStatus.MISSING)
            connection.execute(
                "INSERT INTO turns "
                "(turn_id, conversation_id, sequence_number, "
                "client_message_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(turn_id),
                    str(conversation_id),
                    sequence,
                    str(client_message_id),
                    ConversationRole.USER.value,
                    content,
                    created_at.isoformat(),
                ),
            )
            turn = StoredConversationTurn(
                turn_id=turn_id,
                conversation_id=conversation_id,
                sequence_number=sequence,
                client_message_id=client_message_id,
                role=ConversationRole.USER,
                content=content,
                created_at=created_at,
            )
            return AppendUserTurnResult(AppendUserTurnStatus.CREATED, turn)

    def _append_assistant_turn(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        content: str,
        created_at: datetime,
    ) -> StoredConversationTurn | None:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            sequence = _allocate_sequence(connection, conversation_id)
            if sequence is None:
                return None
            connection.execute(
                "INSERT INTO turns "
                "(turn_id, conversation_id, sequence_number, role, content, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(turn_id),
                    str(conversation_id),
                    sequence,
                    ConversationRole.ASSISTANT.value,
                    content,
                    created_at.isoformat(),
                ),
            )
            return StoredConversationTurn(
                turn_id=turn_id,
                conversation_id=conversation_id,
                sequence_number=sequence,
                role=ConversationRole.ASSISTANT,
                content=content,
                created_at=created_at,
            )

    def _list_turns(
        self,
        conversation_id: UUID,
        after_sequence: int | None,
        before_sequence: int | None,
        limit: int | None,
    ) -> tuple[StoredConversationTurn, ...]:
        conditions = ["conversation_id = ?"]
        parameters: list[object] = [str(conversation_id)]
        if after_sequence is not None:
            conditions.append("sequence_number > ?")
            parameters.append(after_sequence)
        if before_sequence is not None:
            conditions.append("sequence_number < ?")
            parameters.append(before_sequence)
        where = " AND ".join(conditions)
        if limit is None:
            statement = f"SELECT * FROM turns WHERE {where} ORDER BY sequence_number"
        else:
            statement = (
                "SELECT * FROM (SELECT * FROM turns WHERE "
                f"{where} ORDER BY sequence_number DESC LIMIT ?) "
                "ORDER BY sequence_number"
            )
            parameters.append(limit)
        with self._database.connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return tuple(_turn(row) for row in rows)

    def _delete_conversation(self, conversation_id: UUID) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (str(conversation_id),),
            )
            return cursor.rowcount > 0

    def _discard_latest_user_turn(
        self,
        conversation_id: UUID,
        turn_id: UUID,
    ) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM turns WHERE conversation_id = ? AND turn_id = ? "
                "AND role = 'user' AND sequence_number = (SELECT MAX(sequence_number) "
                "FROM turns WHERE conversation_id = ?)",
                (str(conversation_id), str(turn_id), str(conversation_id)),
            )
            return cursor.rowcount == 1


def _allocate_sequence(
    connection: sqlite3.Connection,
    conversation_id: UUID,
) -> int | None:
    row = connection.execute(
        "UPDATE conversations SET next_sequence = next_sequence + 1 "
        "WHERE conversation_id = ? RETURNING next_sequence - 1",
        (str(conversation_id),),
    ).fetchone()
    return int(row[0]) if row is not None else None


def _conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        conversation_id=row["conversation_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def _turn(row: sqlite3.Row) -> StoredConversationTurn:
    return StoredConversationTurn(
        turn_id=row["turn_id"],
        conversation_id=row["conversation_id"],
        sequence_number=row["sequence_number"],
        client_message_id=row["client_message_id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
    )


def _state(row: sqlite3.Row) -> ConversationStateSnapshot:
    return ConversationStateSnapshot(
        state_id=row["state_id"],
        conversation_id=row["conversation_id"],
        summary_through_sequence=row["summary_through_sequence"],
        revision=row["revision"],
        summarizer_version=row["summarizer_version"],
        current_goal=row["current_goal"],
        confirmed_decisions=tuple(json.loads(row["confirmed_decisions"])),
        rejected_proposals=tuple(json.loads(row["rejected_proposals"])),
        superseded_decisions=tuple(json.loads(row["superseded_decisions"])),
        active_constraints=tuple(json.loads(row["active_constraints"])),
        open_questions=tuple(json.loads(row["open_questions"])),
        important_corrections=tuple(json.loads(row["important_corrections"])),
        summary=row["summary"],
        updated_at=row["updated_at"],
    )
