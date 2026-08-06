from enum import StrEnum


class AgentRole(StrEnum):
    VALIDATOR = "validator"
    PLANNER = "planner"
    RESEARCHER = "researcher"
    WRITER = "writer"
    CRITIC = "critic"
    REWRITER = "rewriter"
    USER_FACING = "user_facing"


class ModelProvider(StrEnum):
    GROQ = "groq"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
