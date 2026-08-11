from enum import StrEnum


class AgentRole(StrEnum):
    VALIDATOR = "validator"
    QUERY_RESOLVER = "query_resolver"
    QUERY_DIVERSIFIER = "query_diversifier"
    EVIDENCE_COVERAGE = "evidence_coverage"
    ANSWER_WRITER = "answer_writer"
    ANSWER_AUDITOR = "answer_auditor"
    CONVERSATION_SUMMARIZER = "conversation_summarizer"
    REWRITER = "rewriter"
