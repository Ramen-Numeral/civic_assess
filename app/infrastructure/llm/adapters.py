from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from config.settings import ModelCandidate


def _args(candidate: ModelCandidate, credential: SecretStr) -> dict:
    return {
        "model": candidate.model,
        "api_key": credential.get_secret_value(),
        "temperature": candidate.temperature,
        "timeout": candidate.timeout_seconds,
        "max_retries": candidate.max_retries,
    }


class GroqAdapter:
    @staticmethod
    def build(candidate: ModelCandidate, credential: SecretStr) -> BaseChatModel:
        return ChatGroq(**_args(candidate, credential))


class OpenAIAdapter:
    @staticmethod
    def build(candidate: ModelCandidate, credential: SecretStr) -> BaseChatModel:
        return ChatOpenAI(**_args(candidate, credential))


class AnthropicAdapter:
    @staticmethod
    def build(candidate: ModelCandidate, credential: SecretStr) -> BaseChatModel:
        return ChatAnthropic(**_args(candidate, credential))
