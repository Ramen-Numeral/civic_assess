# Architecture

Civic Assess is a three-stage reasoning pipeline with cross-cutting conversation
context. LangGraph owns top-level transitions; local code owns persistence, bounds,
and rendering.

## System overview

```mermaid
flowchart TD
    U[User request] --> V[1 · Validate and guardrail]
    V --> R[2 · Research and assess evidence]
    R --> A[3 · Write and audit answer]
    A --> O[Answer, Evidence Strength, and sources]

    C[(Conversation context)] -. recent turns and summary .-> V
    U -. persist user turn .-> C
    O -. persist assistant turn .-> C
    C -. compact older context on demand .-> C
```

## 1 · Validation and guardrails

```mermaid
flowchart TD
    U[User request] --> P[Structural preflight]
    P --> C[Resolve contextual references]
    C --> V[Validate original and resolved query]
    V -->|allow| Q[Canonical query]
    V -->|redirect or refuse| X[End without research]
    V -->|reframe| F[Propose neutral, safe, or clarified query]
    F --> V2[Validate proposal]
    V2 -->|reframe · 1 repair maximum| F
    V2 -->|reject| X
    V2 -->|allow| H[Request user approval]
    H -->|yes| Q
    H -->|no| X
```

## 2 · Research and evidence assessment

```mermaid
flowchart TD
    Q[Canonical query] --> P[Plan requirements and evidence angles]
    P --> L[Search conversation-scoped SQLite evidence]
    L --> C[Assess cumulative evidence coverage]
    C -->|sufficient| S[Select findings and evidence]
    C -->|material gaps| G[Plan gap-directed queries]
    G --> T[Tavily search]
    T --> I[Normalize, deduplicate, chunk, embed, and persist]
    I -->|reassess · 2 follow-up rounds maximum by default| L
```

## 3 · Answer synthesis and audit

```mermaid
flowchart TD
    E[Findings, evidence, and unresolved gaps] --> W[Draft evidence-bound answer]
    W --> A["Audit support and completeness<br/>3 attempts per draft"]
    A -->|passes 5/5| S[Calculate Evidence Strength]
    A -->|repairable · 1 rewrite maximum| RW[Rewrite from audit feedback]
    A -->|audit unavailable| X[Fail closed]
    RW --> A2["Audit revised draft<br/>3 attempts"]
    A2 -->|passes at least 4/5| S
    A2 -->|quality at least 3/5| SW[Select safest draft and validation warning]
    SW --> S
    A2 -->|unsupported or incomplete| X
    S --> O[Render answer, strength, optional warning, and sources]
```

## Package boundaries

- `app/application`: conversation lifecycle and interface use cases
- `app/orchestration`: graph transitions and bounded coordinators
- `app/features`: reasoning, conversation, research, and answer capabilities
- `app/domain`: infrastructure-independent contracts
- `app/infrastructure`: model providers, Tavily, SQLite, and repositories
- `app/prompts`: versioned role contracts
- `app/observability`: progress, usage, and structured logs
- `evals` and `tests`: live behavior measurement and regression coverage

## Models

TOML assigns ordered OpenAI, Groq, or Anthropic candidates to each agent role.
Evidence retrieval uses revision-pinned `BAAI/bge-small-en-v1.5`, loaded lazily and
once per process for token-aware chunking and normalized embeddings.

## Persistence

SQLite stores turns, compacted conversation state, documents, chunks, and
embeddings. WAL, write reservations, constraints, and ingestion fingerprints
provide predictable, idempotent writes for a single application instance.

## Retrieval

Conversation-scoped evidence is searched before Tavily. FTS5/BM25 lexical ranks
and embedding cosine ranks are combined with weighted reciprocal-rank fusion;
coverage-aware selection limits duplicate documents across planned requirements.

## Async and locking

- A per-conversation `asyncio.Lock` serializes the full interaction.
- SQLite `BEGIN IMMEDIATE` reserves writes; WAL permits concurrent reads.

SQLite and embedding work runs in worker threads. Independent retrieval, provider,
and search calls use async concurrency.
