import asyncio
import sqlite3
from datetime import datetime
from uuid import UUID

from app.domain.conversation import (
    Conversation,
    ConversationRole,
    StoredConversationTurn,
)
from app.features.conversation_memory.repository import (
    AppendUserTurnResult,
    AppendUserTurnStatus,
    ConversationRepository,
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
    ) -> tuple[StoredConversationTurn, ...]:
        return await asyncio.to_thread(self._list_turns, conversation_id)

    async def delete_conversation(self, conversation_id: UUID) -> bool:
        return await asyncio.to_thread(self._delete_conversation, conversation_id)

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
    ) -> tuple[StoredConversationTurn, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM turns WHERE conversation_id = ? "
                "ORDER BY sequence_number",
                (str(conversation_id),),
            ).fetchall()
        return tuple(_turn(row) for row in rows)

    def _delete_conversation(self, conversation_id: UUID) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (str(conversation_id),),
            )
            return cursor.rowcount > 0


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
