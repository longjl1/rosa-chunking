"""Deterministic character/token, recursive, and parent-child chunkers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._common import (
    DEFAULT_SEPARATORS,
    ChunkFactory,
    LengthPolicy,
    Record,
    Unit,
    fixed_ranges,
    oversize_metadata,
    positive_int,
    records,
    recursive_ranges,
)
from .models import Chunk, Document
from .protocols import Tokenizer


class FixedSizeChunker:
    strategy = "fixed"

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 100,
        *,
        unit: Unit = "characters",
        tokenizer: Tokenizer | None = None,
        allow_oversize: bool = False,
    ):
        positive_int(chunk_size, "chunk_size")
        if type(overlap) is not int or not 0 <= overlap < chunk_size:
            raise ValueError("overlap must be an integer in [0, chunk_size)")
        self.chunk_size, self.overlap = chunk_size, overlap
        self.policy = LengthPolicy(unit, tokenizer)
        self.allow_oversize = allow_oversize

    @property
    def config(self) -> dict[str, Any]:
        return {
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "allow_oversize": self.allow_oversize,
            **self.policy.config(),
        }

    def _ranges(self, text: str) -> list[tuple[int, int]]:
        return fixed_ranges(text, self.chunk_size, self.overlap, self.policy)

    def chunk(self, document: Document) -> list[Chunk]:
        factory = ChunkFactory(document, self.strategy, self.config)
        chunks = []
        for record in records(document):
            if record.element and record.element.modality in {"image", "audio", "video"}:
                meta = oversize_metadata(
                    self.policy.count(record.text), self.chunk_size, self.allow_oversize
                )
                meta["modality"] = record.element.modality
                chunks.append(
                    factory.make([record.span(document, 0, len(record.text))], metadata=meta)
                )
            else:
                for start, end in self._ranges(record.text):
                    chunks.append(factory.make([record.span(document, start, end)]))
        return chunks


class RecursiveChunker(FixedSizeChunker):
    strategy = "recursive"

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 100,
        *,
        unit: Unit = "characters",
        tokenizer: Tokenizer | None = None,
        separators: Sequence[str] = DEFAULT_SEPARATORS,
        allow_oversize: bool = False,
    ):
        super().__init__(
            chunk_size, overlap, unit=unit, tokenizer=tokenizer, allow_oversize=allow_oversize
        )
        if isinstance(separators, str) or not all(isinstance(s, str) for s in separators):
            raise ValueError("separators must be a sequence of strings")
        self.separators = tuple(separators)

    @property
    def config(self) -> dict[str, Any]:
        return {**super().config, "separators": list(self.separators)}

    def _ranges(self, text: str) -> list[tuple[int, int]]:
        return recursive_ranges(text, self.chunk_size, self.overlap, self.policy, self.separators)


class HierarchicalChunker:
    strategy = "hierarchical"

    def __init__(
        self,
        chunk_sizes: Sequence[int] = (2000, 1000, 500),
        *,
        unit: Unit = "characters",
        tokenizer: Tokenizer | None = None,
        allow_oversize: bool = False,
    ):
        if not chunk_sizes:
            raise ValueError("At least one hierarchy level is required")
        for size in chunk_sizes:
            positive_int(size, "chunk size")
        if any(left <= right for left, right in zip(chunk_sizes, chunk_sizes[1:], strict=False)):
            raise ValueError("Hierarchy sizes must be strictly decreasing")
        self.chunk_sizes = tuple(chunk_sizes)
        self.policy = LengthPolicy(unit, tokenizer)
        self.allow_oversize = allow_oversize

    def chunk(self, document: Document) -> list[Chunk]:
        config = {
            "chunk_sizes": list(self.chunk_sizes),
            "allow_oversize": self.allow_oversize,
            **self.policy.config(),
        }
        factory = ChunkFactory(document, self.strategy, config)
        result: list[Chunk] = []

        def expand(record: Record, start: int, end: int, level: int, parent: Chunk | None) -> None:
            size = self.chunk_sizes[level]
            atomic = record.element and record.element.modality in {"image", "audio", "video"}
            if atomic:
                ranges = [(0, end - start)]
            else:
                ranges = recursive_ranges(record.text[start:end], size, 0, self.policy)
            for lo, hi in ranges:
                meta = oversize_metadata(
                    self.policy.count(record.text[start + lo : start + hi]),
                    size,
                    self.allow_oversize,
                )
                chunk = factory.make(
                    [record.span(document, start + lo, start + hi)],
                    metadata=meta,
                    parent_id=parent.id if parent else None,
                    level=level,
                )
                result.append(chunk)
                if parent:
                    parent.child_ids.append(chunk.id)
                if level + 1 < len(self.chunk_sizes):
                    expand(record, start + lo, start + hi, level + 1, chunk)

        for record in records(document):
            expand(record, 0, len(record.text), 0, None)
        return result
