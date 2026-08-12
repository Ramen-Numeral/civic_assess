import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.chat_interaction import ChatInteractionRequest, ChatInteractionService
from app.features.conversation import ConversationContextService, ConversationService
from app.features.query_reframe.schemas import QueryReframeProposal
from app.infrastructure.persistence import SQLiteConversationRepository, SQLiteDatabase
from app.orchestration.state import ChatRoute


pytestmark = [pytest.mark.integration, pytest.mark.regression]


def service(path: Path, orchestrator):
    database = SQLiteDatabase(path)
    database.initialize()
    repository = SQLiteConversationRepository(database)
    conversations = ConversationService(repository, ttl_minutes=30)
    contexts = ConversationContextService(repository, conversations, turn_retention=8)
    return ChatInteractionService(conversations, contexts, orchestrator), conversations


def request(conversation_id, *, message_id=None, message="Question"):
    return ChatInteractionRequest(
        conversation_id=conversation_id,
        client_message_id=message_id or uuid4(), message=message,
    )


def test_success_persists_exact_user_visible_answer(tmp_path: Path) -> None:
    class Orchestrator:
        async def invoke(self, request, *, conversation_context):
            return {"answer_result": SimpleNamespace(text="Verified answer. [1]")}

    async def scenario():
        interactions, conversations = service(tmp_path / "chat.sqlite3", Orchestrator())
        conversation = await interactions.create_conversation()
        result = await interactions.interact(request(conversation.conversation_id))
        turns = await conversations.list_turns(conversation.conversation_id)
        assert result.response_text == "Verified answer. [1]"
        assert [turn.content for turn in turns] == ["Question", "Verified answer. [1]"]

    asyncio.run(scenario())


def test_failure_releases_lock_and_rolls_back_turn(tmp_path: Path) -> None:
    class Orchestrator:
        def __init__(self):
            self.calls, self.contexts = 0, []
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def invoke(self, request, *, conversation_context):
            self.calls += 1
            self.contexts.append(conversation_context)
            if self.calls == 1:
                self.entered.set()
                await self.release.wait()
                raise RuntimeError("first failed")
            return {}

    async def scenario():
        orchestrator = Orchestrator()
        interactions, _ = service(tmp_path / "locked.sqlite3", orchestrator)
        conversation = await interactions.create_conversation()
        first = asyncio.create_task(interactions.interact(request(
            conversation.conversation_id, message="First question",
        )))
        await orchestrator.entered.wait()
        second = asyncio.create_task(interactions.interact(request(
            conversation.conversation_id, message="Second question",
        )))
        await asyncio.sleep(0)
        assert orchestrator.calls == 1
        orchestrator.release.set()
        with pytest.raises(RuntimeError, match="first failed"):
            await first
        await second
        assert orchestrator.calls == 2
        assert orchestrator.contexts[1].recent_turns == ()

    asyncio.run(scenario())


def test_different_conversations_run_concurrently(tmp_path: Path) -> None:
    class Orchestrator:
        def __init__(self):
            self.active = self.maximum_active = 0
            self.both_entered, self.release = asyncio.Event(), asyncio.Event()

        async def invoke(self, request, *, conversation_context):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            if self.active == 2:
                self.both_entered.set()
            await self.release.wait()
            self.active -= 1
            return {}

    async def scenario():
        orchestrator = Orchestrator()
        interactions, _ = service(tmp_path / "parallel.sqlite3", orchestrator)
        first, second = await interactions.create_conversation(), await interactions.create_conversation()
        tasks = [
            asyncio.create_task(interactions.interact(request(first.conversation_id))),
            asyncio.create_task(interactions.interact(request(second.conversation_id))),
        ]
        await asyncio.wait_for(orchestrator.both_entered.wait(), timeout=1)
        orchestrator.release.set()
        await asyncio.gather(*tasks)
        assert orchestrator.maximum_active == 2

    asyncio.run(scenario())


def test_yes_persists_canonical_reframe(tmp_path: Path) -> None:
    async def scenario():
        query = "What evidence bears on the policy?"
        flow = [{"query_reframe_proposal": QueryReframeProposal(proposed_query=query), "chat_route": ChatRoute.AWAIT_APPROVAL}, {"answer_result": SimpleNamespace(text="Answer")}]
        interactions, conversations = service(tmp_path / "reframe.sqlite3", SimpleNamespace(invoke=AsyncMock(side_effect=flow)))
        conversation = await interactions.create_conversation()
        await interactions.interact(request(conversation.conversation_id, message="Biased question"))
        await interactions.interact(request(conversation.conversation_id, message="Yes"))
        assert (await conversations.list_turns(conversation.conversation_id))[-2].content == query
    asyncio.run(scenario())
