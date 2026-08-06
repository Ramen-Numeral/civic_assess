from collections.abc import Mapping
from types import MappingProxyType

from config.settings import Settings
from app.infrastructure.llm.adapters import AnthropicAdapter, GroqAdapter, OpenAIAdapter
from app.infrastructure.llm.client import LLM
from app.roles import AgentRole, ModelProvider


ADAPTERS = MappingProxyType({
    ModelProvider.GROQ: GroqAdapter,
    ModelProvider.OPENAI: OpenAIAdapter,
    ModelProvider.ANTHROPIC: AnthropicAdapter,
})


def build_llms(settings: Settings) -> Mapping[AgentRole, LLM]:
    clients: dict[AgentRole, LLM] = {}
    for role in AgentRole:
        route = settings.routes[role]
        models = [
            ADAPTERS[item.provider].build(
                item,
                settings.provider_credentials[item.provider],
            )
            for item in route
        ]
        clients[role] = LLM(models)
    return MappingProxyType(clients)
