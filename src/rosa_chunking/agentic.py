"""Bounded LLM boundary decisions; all returned content is extracted from source."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ._common import (
    CallBudget,
    ChunkFactory,
    LengthPolicy,
    oversize_metadata,
    positive_int,
    records,
    recursive_ranges,
    sentence_ranges,
)
from .errors import ProviderError
from .models import Chunk, Document, SourceSpan
from .protocols import BoundaryProvider
from .semantic import fallback_chunks


def validate_boundaries(ends: Sequence[int], count: int) -> list[int]:
    if not ends or any(type(end) is not int for end in ends):
        raise ProviderError("Boundaries must be a nonempty list of integer exclusive ends")
    previous = 0
    for end in ends:
        if not previous < end <= count:
            raise ProviderError("Boundaries must be strictly increasing and within the window")
        previous = end
    if previous != count:
        raise ProviderError("Boundaries must cover every unit in the window")
    return list(ends)


@dataclass
class _AgentUnit:
    text: str
    span: SourceSpan
    oversize: bool = False


class AgenticChunker:
    strategy = "agentic"

    def __init__(
        self,
        provider: BoundaryProvider,
        *,
        chunk_size: int = 1000,
        window_size: int = 8000,
        max_units: int = 64,
        timeout: float = 30.0,
        retries: int = 1,
        max_calls: int = 100,
        fallback: Literal["raise", "recursive"] = "raise",
        allow_oversize: bool = False,
    ):
        for name, value in [
            ("chunk_size", chunk_size),
            ("window_size", window_size),
            ("max_units", max_units),
        ]:
            positive_int(value, name)
        if window_size < chunk_size:
            raise ValueError("window_size must be at least chunk_size")
        if fallback not in {"raise", "recursive"}:
            raise ValueError("fallback must be 'raise' or 'recursive'")
        CallBudget(max_calls, retries, timeout)
        self.provider, self.chunk_size, self.window_size, self.max_units = (
            provider,
            chunk_size,
            window_size,
            max_units,
        )
        self.timeout, self.retries, self.max_calls = timeout, retries, max_calls
        self.fallback, self.allow_oversize = fallback, allow_oversize

    @property
    def config(self) -> dict[str, Any]:
        return {
            "model": self.provider.model,
            "chunk_size": self.chunk_size,
            "window_size": self.window_size,
            "max_units": self.max_units,
            "timeout": self.timeout,
            "retries": self.retries,
            "max_calls": self.max_calls,
            "fallback": self.fallback,
            "allow_oversize": self.allow_oversize,
            "unit": "characters",
        }

    def chunk(self, document: Document) -> list[Chunk]:
        budget = CallBudget(self.max_calls, self.retries, self.timeout)
        try:
            return self._chunk(document, budget)
        except ProviderError as exc:
            if self.fallback == "raise":
                raise
            return fallback_chunks(document, self.strategy, self.config, exc, budget.calls)

    def _chunk(self, document: Document, budget: CallBudget) -> list[Chunk]:
        factory = ChunkFactory(document, self.strategy, self.config)
        units = []
        for record in records(document):
            if record.element and record.element.modality != "text":
                meta = oversize_metadata(len(record.text), self.chunk_size, self.allow_oversize)
                units.append(
                    _AgentUnit(record.text, record.span(document, 0, len(record.text)), bool(meta))
                )
            else:
                for start, end in sentence_ranges(record.text):
                    for lo, hi in recursive_ranges(
                        record.text[start:end], self.chunk_size, 0, LengthPolicy()
                    ):
                        units.append(
                            _AgentUnit(
                                record.text[start + lo : start + hi],
                                record.span(document, start + lo, start + hi),
                            )
                        )
        result = []
        window: list[_AgentUnit] = []
        window_chars = 0
        offset = 0

        def emit_window() -> None:
            nonlocal window, window_chars, offset
            if not window:
                return
            texts = [unit.text for unit in window]

            def request() -> list[int]:
                return validate_boundaries(
                    self.provider.boundaries(texts, timeout=self.timeout), len(texts)
                )

            ends = budget.run(request)
            begin = 0

            def emit_group(
                group: list[_AgentUnit],
                group_start: int,
                unit_end: int,
                decision_start: int,
                decision_end: int,
            ) -> None:
                if group:
                    result.append(
                        factory.make(
                            [u.span for u in group],
                            metadata={
                                "window_start": offset,
                                "window_end": offset + len(window),
                                "decision_ends": ends,
                                "unit_start": offset + group_start,
                                "unit_end": offset + unit_end,
                                "oversize": any(u.oversize for u in group),
                                "size_subdivision": group_start != decision_start
                                or unit_end != decision_end,
                            },
                        )
                    )

            for end in ends:
                # LLM decisions may exceed the output budget: subdivide at source-unit boundaries.
                group: list[_AgentUnit] = []
                length = 0
                group_start = begin

                for index in range(begin, end):
                    unit = window[index]
                    if group and (length + len(unit.text) > self.chunk_size or unit.oversize):
                        emit_group(group, group_start, index, begin, end)
                        group, length, group_start = [], 0, index
                    group.append(unit)
                    length += len(unit.text)
                emit_group(group, group_start, end, begin, end)
                begin = end
            offset += len(window)
            window, window_chars = [], 0

        for unit in units:
            if unit.oversize:
                # Never exceed the model's input budget to accommodate an allowed atomic unit.
                emit_window()
                result.append(
                    factory.make(
                        [unit.span],
                        metadata={
                            "oversize": True,
                            "decision": "atomic_passthrough",
                            "unit_start": offset,
                            "unit_end": offset + 1,
                        },
                    )
                )
                offset += 1
                continue
            if window and (
                window_chars + len(unit.text) > self.window_size or len(window) >= self.max_units
            ):
                emit_window()
            window.append(unit)
            window_chars += len(unit.text)
        emit_window()
        for chunk in result:
            chunk.metadata["provider_calls"] = budget.calls
        return result
