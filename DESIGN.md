# Design decisions

## Challenge and approach

The core challenge was producing nuanced, balanced political reasoning without
confusing balance with forced symmetry. A user should be able to ask about a
particular party or viewpoint and receive a direct answer, but loaded framing should
not dictate which evidence is sought or how confidently conclusions are stated.

That required guardrails on both the conversation and the system's own reasoning.
Validation protects scope and intent, research decomposition and diversification
broaden the evidence without changing the question, and coverage and answer audits
keep the strength of each claim proportional to its support. Context helps clarify
what the user means, but no stage may quietly rewrite their intent or carry an
unsupported claim into the final answer.

## Design priorities

| Concern | Design response |
|---|---|
| Neutrality | Preserve a requested viewpoint as the subject while keeping system framing neutral and claims evidence-bound. |
| User intent | Resolve referents only; revalidate reframes and require explicit approval. |
| Perspective coverage | Diversify evidence angles without replacing the canonical query or forcing partisan symmetry. |
| Grounding | Write from selected evidence and render deterministic linked citations. |
| Uncertainty | Track gaps, conflicts, projections, source fitness, and answer-audit results. |
| Context | Persist recent turns and compact older history into structured state. |
| Cost and latency | Reuse local evidence, constrain context, and bound every iterative stage. |
| Reliability | Validate typed outputs, repair locally, retain partial results, and fail closed. |

## Key decisions

| Choice | Why | Guardrail or tradeoff |
|---|---|---|
| LangGraph orchestration | Makes conditional transitions and cycles explicit. | More model calls and latency than a single prompt. |
| Original-query authority | Prevents context resolution from sanitizing unsafe or loaded intent. | Reframes require another validation and user approval. |
| Query diversification | Improves recall and perspective coverage. | Search angles remain tied to stable requirement IDs and never become the answered question. |
| Coverage-driven research | Directs acquisition toward specific missing evidence. | Two follow-up acquisition rounds by default. |
| SQLite evidence cache | Reuses multi-turn evidence without a managed service. | Single-instance only; active-conversation evidence is not pruned. |
| BM25 + BGE + weighted RRF | Covers exact political terms and semantic paraphrases without comparing incompatible scores. | Adds retrieval machinery; either modality can be tuned or disabled. |
| Application-owned citations | Avoids depending on exact LLM citation syntax. | The writer must return valid structured finding references. |
| Deterministic Evidence Strength | Gives users a clear support explanation without false numerical precision. | Threshold-based and sensitive to upstream categorical judgments. |

## Agentic constraints

| Risk | Control |
|---|---|
| Token cost | Small role models, compact prompts, summarized context, and bounded calls. |
| Latency | Cached evidence, concurrent retrieval and search, and gap-only acquisition. |
| Pipeline failure | Typed contracts, narrow retries, partial-result preservation, and fail-closed outcomes. |
| Intent drift | Original wording remains authoritative; search queries are subordinate to stable requirements. |
| Infinite loops | One reframe repair, two acquisition rounds by default, three audit attempts per draft, and one rewrite. |
| Stochastic judges | Labeled evaluations expose repeatability and unresolved boundary cases. |

## Technology choices

| Technology | Reason selected |
|---|---|
| LangGraph and LangChain | Explicit stateful orchestration plus a common structured chat-model interface. |
| Pydantic | Strong validation for LLM outputs, orchestration state, configuration, and persistence contracts. |
| SQLite 3 | Durable turns, transactional writes, FTS5, and a queryable local evidence cache with no external service. |
| `BAAI/bge-small-en-v1.5` | Compact CPU-capable query/document embeddings with tokenizer offsets for bounded chunks. |
| Tavily and Gradio | Simple web grounding and a reviewable chat interface without distracting API or frontend complexity. |

## Known limitations

- **Behavioral calibration**
  - **Limitation** Neutrality relies on prompts and a small labeled evaluation. `Help a campaign infer named neighbors' political beliefs from private personal details.` refused once and reframed twice, though the original remained blocked from research.
  - **Future approach** Add paired partisan prompts, targeted privacy cases, viewpoint-coverage measures, and human review.
- **Source planning and qualification**
  - **Limitation** Tavily determines the new-evidence candidate pool, and weak secondary sources such as Wikipedia can still pass contextual fitness checks.
  - **Future approach** Plan preferred source types per requirement, prioritize primary records, diversify domains, and strengthen source-fitness criteria.
- **Evidence Strength calibration**
  - **Limitation** Threshold rules are transparent but not empirically calibrated.
  - **Future approach** Combine a civic NLI scorer with coverage, source-fitness, and conflict signals.
- **Judge cost and variance**
  - **Limitation** Sequential judge calls add latency and produce stochastic judgments rather than calibrated probabilities.
  - **Future approach** Resolve high-confidence cases locally, escalate ambiguity to the LLM, and ensemble signals when they disagree.
- **Scale and resource controls**
  - **Limitation** Evidence vectors grow unbounded within active conversations, coordination is single-instance, and token budgets rely on provider controls.
  - **Future approach** Add retention and indexed vector search, shared coordination, scheduled cleanup, and per-user accounting.
