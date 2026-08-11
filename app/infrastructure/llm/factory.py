from collections.abc import Mapping
from types import MappingProxyType

from config.settings import Settings
from config.llm import ModelProvider
from app.infrastructure.llm.adapters import AnthropicAdapter, GroqAdapter, OpenAIAdapter
from app.infrastructure.llm.client import LLM, RoutedModel
from app.observability.llm_usage import LLMUsageReporter
from app.roles import AgentRole


ADAPTERS = MappingProxyType({
    ModelProvider.GROQ: GroqAdapter,
    ModelProvider.OPENAI: OpenAIAdapter,
    ModelProvider.ANTHROPIC: AnthropicAdapter,
})


def build_llms(
    settings: Settings, usage_reporter: LLMUsageReporter | None = None
) -> Mapping[AgentRole, LLM]:
    clients: dict[AgentRole, LLM] = {}
    for role in AgentRole:
        route = settings.routes[role]
        models = [
            RoutedModel(
                candidate=item,
                client=ADAPTERS[item.provider].build(
                    item,
                    settings.provider_credentials[item.provider],
                ),
            )
            for item in route
        ]
        clients[role] = LLM(role, models, usage_reporter)
    return MappingProxyType(clients)
