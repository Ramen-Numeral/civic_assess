from enum import StrEnum


class AgentRole(StrEnum):
    VALIDATOR = "validator"
    QUERY_RESOLVER = "query_resolver"
    PLANNER = "planner"
    RESEARCHER = "researcher"
    WRITER = "writer"
    CRITIC = "critic"
    REWRITER = "rewriter"
    USER_FACING = "user_facing"
