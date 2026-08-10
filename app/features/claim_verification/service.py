import asyncio
import math
from collections.abc import Sequence
from threading import Lock
from typing import Any, Protocol
from uuid import UUID

from app.features.answer_synthesis.schemas import GroundedAnswerDraft
from app.features.claim_verification.schemas import (
    CitationVerification,
    ClaimVerification,
    ClaimVerificationRequest,
    ClaimVerificationResult,
    ConflictCandidate,
    EntailmentVerdict,
    TextPairVerification,
)
from app.features.evidence_retrieval.schemas import EvidenceCandidate


Scores = tuple[float, float, float]  # entailment, contradiction, neutral
ScoringPair = tuple[str, str, str]  # premise, hypothesis, repeated prefix


class _CitationScorer(Protocol):
    version: str

    def score(
        self, pairs: Sequence[ScoringPair]
    ) -> tuple[tuple[Scores, ...], ...]: ...


class ClaimVerificationService:
    def __init__(
        self,
        model_id: str,
        revision: str,
        *,
        batch_size: int = 16,
        device: str = "cpu",
        entailment_threshold: float = 0.8,
        contradiction_threshold: float = 0.8,
        scorer: _CitationScorer | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not 0 <= entailment_threshold <= 1:
            raise ValueError("entailment_threshold must be between zero and one")
        if not 0 <= contradiction_threshold <= 1:
            raise ValueError("contradiction_threshold must be between zero and one")
        self._scorer = scorer or _CrossEncoderScorer(
            model_id, revision, batch_size=batch_size, device=device
        )
        self._entailment_threshold = entailment_threshold
        self._contradiction_threshold = contradiction_threshold

    async def verify(
        self, request: ClaimVerificationRequest
    ) -> ClaimVerificationResult:
        if not request.draft.claims:
            return ClaimVerificationResult(
                model_version=self._scorer.version, claims=()
            )
        evidence = {item.chunk_id: item for item in request.evidence}
        pairs = [
            self._evidence_pair(evidence[chunk_id], claim.text)
            for claim in request.draft.claims
            for chunk_id in claim.supporting_chunk_ids
        ]
        scored = await self._score(pairs)
        chunk_ids = (
            chunk_id
            for claim in request.draft.claims
            for chunk_id in claim.supporting_chunk_ids
        )
        citations = iter(
            self._citation(chunk_id, windows)
            for chunk_id, windows in zip(chunk_ids, scored, strict=True)
        )
        claims = []
        for claim in request.draft.claims:
            resolved = tuple(next(citations) for _ in claim.supporting_chunk_ids)
            verdict: EntailmentVerdict
            if all(item.verdict == "entailed" for item in resolved):
                verdict = "entailed"
            elif any(item.verdict == "contradicted" for item in resolved):
                verdict = "contradicted"
            else:
                verdict = "insufficient_evidence"
            claims.append(ClaimVerification(
                claim_id=claim.claim_id,
                verdict=verdict,
                citations=resolved,
            ))
        return ClaimVerificationResult(
            model_version=self._scorer.version, claims=tuple(claims)
        )

    async def verify_fidelity(
        self, pairs: Sequence[tuple[str, str]]
    ) -> tuple[tuple[TextPairVerification, TextPairVerification], ...]:
        scored = await self._score(tuple(
            item for source, rewrite in pairs
            for item in ((source, rewrite, ""), (rewrite, source, ""))
        ))
        results = tuple(self._text_pair(item) for item in scored)
        return tuple(
            (results[index], results[index + 1])
            for index in range(0, len(results), 2)
        )

    async def screen_conflicts(
        self, draft: GroundedAnswerDraft
    ) -> tuple[ConflictCandidate, ...]:
        claim_pairs = [
            (left, right)
            for index, left in enumerate(draft.claims)
            for right in draft.claims[index + 1:]
        ]
        scored = await self.verify_fidelity(tuple(
            (left.text, right.text) for left, right in claim_pairs
        ))
        return tuple(
            ConflictCandidate(
                left_claim_id=left.claim_id,
                right_claim_id=right.claim_id,
                left_to_right=forward,
                right_to_left=reverse,
            )
            for (left, right), (forward, reverse) in zip(
                claim_pairs, scored, strict=True
            )
            if "contradicted" in (forward.verdict, reverse.verdict)
        )

    async def _score(
        self, pairs: Sequence[ScoringPair]
    ) -> tuple[tuple[Scores, ...], ...]:
        if not pairs:
            return ()
        scored = await asyncio.to_thread(self._scorer.score, pairs)
        if len(scored) != len(pairs) or any(not windows for windows in scored):
            raise RuntimeError("NLI scorer returned incomplete text-pair results")
        return scored

    @staticmethod
    def _evidence_pair(evidence: EvidenceCandidate, claim: str) -> ScoringPair:
        heading = " > ".join(evidence.heading_path)
        prefix = (
            f"Document title: {evidence.title}\n"
            f"Section: {heading or '(none)'}\n\nPassage:\n"
        )
        return evidence.text, claim, prefix

    def _citation(
        self, chunk_id: UUID, windows: tuple[Scores, ...]
    ) -> CitationVerification:
        result = self._text_pair(windows)
        return CitationVerification(chunk_id=chunk_id, **result.model_dump())

    def _text_pair(self, windows: tuple[Scores, ...]) -> TextPairVerification:
        if any(
            len(scores) != 3
            or any(not math.isfinite(value) or value < 0 or value > 1 for value in scores)
            or abs(sum(scores) - 1) > 1e-5
            for scores in windows
        ):
            raise RuntimeError("NLI scorer returned invalid probabilities")
        contradiction = max(windows, key=lambda item: item[1])
        entailment = max(windows, key=lambda item: item[0])
        if contradiction[1] >= self._contradiction_threshold:
            decisive, verdict = contradiction, "contradicted"
        elif entailment[0] >= self._entailment_threshold:
            decisive, verdict = entailment, "entailed"
        else:
            decisive, verdict = max(windows, key=lambda item: item[2]), "insufficient_evidence"
        return TextPairVerification(
            verdict=verdict,
            entailment_score=decisive[0],
            contradiction_score=decisive[1],
            neutral_score=decisive[2],
        )


class _CrossEncoderScorer:
    _OVERLAP = 48
    _METADATA_LIMIT = 48

    def __init__(
        self, model_id: str, revision: str, *, batch_size: int, device: str
    ) -> None:
        self.version = f"{model_id}@{revision}"
        self._model_id, self._revision = model_id, revision
        self._batch_size, self._device = batch_size, device
        self._model: Any = None
        self._labels: dict[str, int] = {}
        self._lock = Lock()

    def score(
        self, pairs: Sequence[ScoringPair]
    ) -> tuple[tuple[Scores, ...], ...]:
        model = self._load()
        grouped = [self._windows(*pair) for pair in pairs]
        flat = [pair for windows in grouped for pair in windows]
        from torch.nn import Identity

        logits = model.predict(
            flat,
            batch_size=self._batch_size,
            show_progress_bar=False,
            activation_fn=Identity(),
            apply_softmax=False,
            convert_to_numpy=True,
        )
        rows = [self._probabilities(row) for row in logits]
        output, cursor = [], 0
        for windows in grouped:
            output.append(tuple(rows[cursor:cursor + len(windows)]))
            cursor += len(windows)
        return tuple(output)

    def _windows(
        self, premise: str, hypothesis: str, prefix: str
    ) -> list[tuple[str, str]]:
        tokenizer, model = self._model.tokenizer, self._model
        hypothesis_tokens = tokenizer(
            hypothesis, add_special_tokens=False
        )["input_ids"]
        prefix_tokens = tokenizer(
            prefix, add_special_tokens=False
        )["input_ids"][:self._METADATA_LIMIT]
        prefix = tokenizer.decode(prefix_tokens, skip_special_tokens=True)
        budget = (
            model.max_seq_length
            - len(hypothesis_tokens)
            - len(prefix_tokens)
            - tokenizer.num_special_tokens_to_add(pair=True)
            - 4
        )
        if budget < 1:
            raise ValueError("NLI hypothesis exceeds the model token limit")
        passage = tokenizer(premise, add_special_tokens=False)["input_ids"]
        step = max(1, budget - min(self._OVERLAP, budget // 2))
        return [
            (
                prefix + tokenizer.decode(
                    passage[start:start + budget],
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                ),
                hypothesis,
            )
            for start in range(0, len(passage), step)
            if passage[start:start + budget]
        ]

    def _probabilities(self, values: Any) -> Scores:
        logits = [float(value) for value in values]
        if len(logits) != 3 or any(not math.isfinite(value) for value in logits):
            raise RuntimeError("NLI model returned invalid logits")
        peak = max(logits)
        exponentials = [math.exp(value - peak) for value in logits]
        total = sum(exponentials)
        probabilities = [value / total for value in exponentials]
        return tuple(probabilities[self._labels[label]] for label in (
            "entailment", "contradiction", "neutral"
        ))  # type: ignore[return-value]

    def _load(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder

                    model = CrossEncoder(
                        self._model_id,
                        revision=self._revision,
                        device=self._device,
                    )
                    labels = {
                        str(label).casefold(): int(index)
                        for index, label in model.model.config.id2label.items()
                    }
                    if set(labels) != {"contradiction", "entailment", "neutral"}:
                        raise ValueError("NLI model must expose the expected labels")
                    self._model, self._labels = model, labels
        return self._model
