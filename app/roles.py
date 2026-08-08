from enum import StrEnum


class AgentRole(StrEnum):
    VALIDATOR = "validator"
    QUERY_RESOLVER = "query_resolver"
    QUERY_DIVERSIFIER = "query_diversifier"
    REWRITER = "rewriter"
