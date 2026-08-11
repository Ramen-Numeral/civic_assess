import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid5

from app.domain.evidence import EvidenceChunk


CHUNK_NAMESPACE = UUID("585c20b7-9f06-44a0-a40a-e19cd6aee830")
BLOCK = re.compile(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", re.S)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SENTENCE = re.compile(r".+?(?:[.!?](?=\s)|\Z)", re.S)
UNIT = re.compile(r"\S+")


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    heading_path: tuple[str, ...]


class MarkdownChunker:
    def __init__(
        self,
        *,
        target_units: int = 350,
        max_units: int = 450,
        overlap_units: int = 64,
        min_units: int = 100,
        unit_spans: Callable[[str], Sequence[tuple[int, int]]] | None = None,
        unit_version: str = "whitespace",
    ) -> None:
        if not 0 <= overlap_units < target_units <= max_units:
            raise ValueError("chunk limits require overlap < target <= maximum")
        if not 0 <= min_units <= target_units:
            raise ValueError("minimum must be between zero and target")
        self._target = target_units
        self._maximum = max_units
        self._overlap = overlap_units
        self._minimum = min_units
        self._substantive_minimum = min(min_units, 8)
        self._unit_spans = unit_spans or self._whitespace_spans
        self.version = f"markdown-v3:{unit_version}"

    def chunk(self, document_id: UUID, content: str) -> tuple[EvidenceChunk, ...]:
        spans = self._atomic_spans(content)
        groups: list[list[_Span]] = []
        current: list[_Span] = []
        for span in spans:
            starts_new_section = (
                current and span.heading_path != current[-1].heading_path
            )
            combined = self._units(content, current + [span])
            if current and starts_new_section:
                if not groups or current[-1].end > groups[-1][-1].end:
                    groups.append(current)
                current = []
            elif current and combined > self._maximum:
                if not groups or current[-1].end > groups[-1][-1].end:
                    groups.append(current)
                available = self._maximum - self._count(
                    content[span.start:span.end]
                )
                current = self._overlap_spans(
                    content,
                    current,
                    limit=available,
                )
            current.append(span)
            if self._units(content, current) >= self._target:
                groups.append(current)
                current = self._overlap_spans(content, current)
        if current and (
            not groups or current[-1].end > groups[-1][-1].end
        ):
            groups.append(current)
        groups = self._merge_small_tail(content, groups)
        groups = self._merge_undersized(content, groups)
        groups = [
            group for group in groups
            if self._units(content, group) >= self._substantive_minimum
        ]
        return tuple(
            self._make_chunk(document_id, content, index, group)
            for index, group in enumerate(groups)
        )

    def _merge_undersized(
        self, content: str, groups: list[list[_Span]]
    ) -> list[list[_Span]]:
        merged: list[list[_Span]] = []
        pending: list[_Span] = []
        for group in groups:
            candidate = pending + group
            pending = []
            if self._units(content, candidate) >= self._minimum:
                merged.append(candidate)
                continue
            if merged and self._units(
                content, merged[-1] + candidate
            ) <= self._maximum:
                merged[-1] = merged[-1] + candidate
                continue
            pending = candidate
        if pending:
            merged.append(pending)
        return merged

    def _atomic_spans(self, content: str) -> list[_Span]:
        headings: list[str] = []
        spans: list[_Span] = []
        for match in BLOCK.finditer(content):
            heading = HEADING.match(match.group())
            if heading:
                level = len(heading.group(1))
                headings = headings[:level - 1] + [heading.group(2).strip()]
                continue
            span = _Span(match.start(), match.end(), tuple(headings))
            spans.extend(self._split_oversized(content, span))
        return spans

    def _split_oversized(self, content: str, span: _Span) -> list[_Span]:
        if self._count(content[span.start:span.end]) <= self._maximum:
            return [span]
        sentences = [
            _Span(span.start + item.start(), span.start + item.end(), span.heading_path)
            for item in SENTENCE.finditer(content[span.start:span.end])
            if item.group().strip()
        ]
        result: list[_Span] = []
        for sentence in sentences:
            if self._count(content[sentence.start:sentence.end]) <= self._maximum:
                result.append(sentence)
                continue
            units = self._unit_spans(content[sentence.start:sentence.end])
            for offset in range(0, len(units), self._maximum):
                window = units[offset:offset + self._maximum]
                result.append(_Span(
                    sentence.start + window[0][0],
                    sentence.start + window[-1][1],
                    span.heading_path,
                ))
        return result

    def _overlap_spans(
        self,
        content: str,
        spans: list[_Span],
        *,
        limit: int | None = None,
    ) -> list[_Span]:
        overlap = min(self._overlap, limit) if limit is not None else self._overlap
        if overlap <= 0:
            return []
        last = spans[-1]
        units = self._unit_spans(content[last.start:last.end])
        if len(units) <= overlap:
            return [last]
        start = last.start + units[-overlap][0]
        return [_Span(start, last.end, last.heading_path)]

    def _merge_small_tail(
        self,
        content: str,
        groups: list[list[_Span]],
    ) -> list[list[_Span]]:
        if len(groups) < 2 or self._units(content, groups[-1]) >= self._minimum:
            return groups
        if groups[-2][-1].heading_path != groups[-1][-1].heading_path:
            return groups
        merged = [*groups[-2], *groups[-1]]
        if self._units(content, merged) <= self._maximum:
            return [*groups[:-2], merged]
        return groups

    def _make_chunk(
        self,
        document_id: UUID,
        content: str,
        index: int,
        spans: list[_Span],
    ) -> EvidenceChunk:
        start, end = spans[0].start, spans[-1].end
        text = content[start:end]
        digest = hashlib.sha256(text.encode()).hexdigest()
        chunk_id = uuid5(
            CHUNK_NAMESPACE,
            f"{document_id}:{self.version}:{index}:{digest}",
        )
        return EvidenceChunk(
            chunk_id=chunk_id,
            document_id=document_id,
            chunk_index=index,
            text=text,
            heading_path=spans[-1].heading_path,
            start_offset=start,
            end_offset=end,
            chunker_version=self.version,
        )

    def _units(self, content: str, spans: list[_Span]) -> int:
        if not spans:
            return 0
        return self._count(content[spans[0].start:spans[-1].end])

    def _count(self, text: str) -> int:
        return len(self._unit_spans(text))

    @staticmethod
    def _whitespace_spans(text: str) -> tuple[tuple[int, int], ...]:
        return tuple(match.span() for match in UNIT.finditer(text))
