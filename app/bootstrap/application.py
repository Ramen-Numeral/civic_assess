from collections.abc import Mapping
from dataclasses import dataclass

from config.settings import Settings, load_application_config
from app.features.conversation_memory import ConversationService
from app.infrastructure.llm.client import LLM
from app.infrastructure.llm.factory import build_llms
from app.infrastructure.persistence import (
    SQLiteConversationRepository,
    SQLiteDatabase,
)
from app.observability.logging import configure_logging
from app.roles import AgentRole


@dataclass(frozen=True)
class Application:
    settings: Settings
    llms: Mapping[AgentRole, LLM]
    database: SQLiteDatabase
    conversations: ConversationService


def build_application(settings: Settings | None = None) -> Application:
    resolved = settings or load_application_config()
    configure_logging(debug=resolved.debug)
    database = SQLiteDatabase(resolved.sqlite_database_path)
    database.initialize()
    conversations = ConversationService(
        SQLiteConversationRepository(database),
        ttl_minutes=resolved.conversation_ttl_minutes,
    )
    return Application(
        settings=resolved,
        llms=build_llms(resolved),
        database=database,
        conversations=conversations,
    )
