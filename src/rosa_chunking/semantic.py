"""Embedding-distance boundaries with explicit hard limits and error handling."""

from __future__ import annotations

import math
from collections.abc import Sequence
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
from .local import RecursiveChunker
from .models import Chunk, Document
from .protocols import EmbeddingProvider


def validate_vectors(vectors: Sequence[Sequence[float]], count: int) -> list[list[float]]:
    if len(vectors) != count:
        raise ProviderError("Embedding count does not match the input count")
    result = []
    dimension = 0
    for vector in vectors:
        if not vector or (dimension and len(vector) != dimension):
            raise ProviderError("Embeddings must have a nonempty consistent dimension")
        dimension = len(vector)
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in vector
        ):
            raise ProviderError("Embedding values must be finite numbers")
        norm = math.hypot(*vector)
        if norm == 0 or not math.isfinite(norm):
            raise ProviderError("Embedding vectors must have a finite nonzero norm")
        result.append([v / norm for v in vector])
    return result


def fallback_chunks(
    document: Document, strategy: str, config: dict[str, Any], error: ProviderError, calls: int
) -> list[Chunk]:
    chunks = RecursiveChunker(
        config["chunk_size"], 0, allow_oversize=config["allow_oversize"]
    ).chunk(document)
    for chunk in chunks:
        chunk.metadata.update(
            {
                "requested_strategy": strategy,
                "fallback": "recursive",
                "fallback_reason": type(error).__name__,
                "provider_calls": calls,
                "requested_config": config,
            }
        )
    return chunks


class SemanticChunker:
    strategy = "semantic"

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        chunk_size: int = 1000,
        threshold: float = 0.5,
        buffer_size: int = 1,
        batch_size: int = 64,
        timeout: float = 30.0,
        retries: int = 1,
        max_calls: int = 100,
        fallback: Literal["raise", "recursive"] = "raise",
        allow_oversize: bool = False,
    ):
        positive_int(chunk_size, "chunk_size")
        positive_int(batch_size, "batch_size")
        if type(buffer_size) is not int or buffer_size < 0:
            raise ValueError("buffer_size must be a nonnegative integer")
        if not math.isfinite(threshold) or not 0 <= threshold <= 2:
            raise ValueError("Cosine distance threshold must be in [0, 2]")
        if fallback not in {"raise", "recursive"}:
            raise ValueError("fallback must be 'raise' or 'recursive'")
        CallBudget(max_calls, retries, timeout)
        self.provider, self.chunk_size = provider, chunk_size
        self.threshold, self.buffer_size, self.batch_size = threshold, buffer_size, batch_size
        self.timeout, self.retries, self.max_calls = timeout, retries, max_calls
        self.fallback, self.allow_oversize = fallback, allow_oversize

    @property
    def config(self) -> dict[str, Any]:
        return {
            "model": self.provider.model,
            "chunk_size": self.chunk_size,
            "threshold": self.threshold,
            "buffer_size": self.buffer_size,
            "batch_size": self.batch_size,
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
        result = []
        for record in records(document):
            if record.element and record.element.modality in {"image", "audio", "video"}:
                meta = oversize_metadata(len(record.text), self.chunk_size, self.allow_oversize)
                meta["modality"] = record.element.modality
                result.append(
                    factory.make([record.span(document, 0, len(record.text))], metadata=meta)
                )
                continue
            ranges = []
            for start, end in sentence_ranges(record.text):
                for lo, hi in recursive_ranges(
                    record.text[start:end], self.chunk_size, 0, LengthPolicy()
                ):
                    ranges.append((start + lo, start + hi))
            if not ranges:
                continue
            texts = [record.text[start:end] for start, end in ranges]
            windows = [
                "".join(texts[max(0, i - self.buffer_size) : i + self.buffer_size + 1])
                for i in range(len(texts))
            ]
            vectors: dict[int, list[float]] = {}
            if len(texts) > 1 and record.text.strip():
                dimension: int | None = None
                nonblank = [index for index, window in enumerate(windows) if window.strip()]
                for start in range(0, len(nonblank), self.batch_size):
                    indices = nonblank[start : start + self.batch_size]
                    batch = [windows[index] for index in indices]

                    def request(batch: list[str] = batch) -> list[list[float]]:
                        return validate_vectors(
                            self.provider.embed(batch, timeout=self.timeout), len(batch)
                        )

                    embedded = budget.run(request)
                    if dimension is not None and len(embedded[0]) != dimension:
                        raise ProviderError("Embedding dimension changed between batches")
                    dimension = len(embedded[0])
                    vectors.update(zip(indices, embedded, strict=True))
            distances = [
                max(
                    0.0,
                    min(
                        2.0,
                        1.0
                        - sum(
                            a * b for a, b in zip(vectors[index], vectors[index + 1], strict=True)
                        ),
                    ),
                )
                if index in vectors and index + 1 in vectors
                else 0.0
                for index in range(len(texts) - 1)
            ]
            group_start = 0
            for index in range(1, len(ranges) + 1):
                last = index == len(ranges)
                semantic_break = (
                    not last and bool(distances) and distances[index - 1] > self.threshold
                )
                too_large = not last and ranges[index][1] - ranges[group_start][0] > self.chunk_size
                if last or semantic_break or too_large:
                    start, end = ranges[group_start][0], ranges[index - 1][1]
                    result.append(
                        factory.make(
                            [record.span(document, start, end)],
                            metadata={
                                "unit_start": group_start,
                                "unit_end": index,
                                "boundary_reason": "end"
                                if last
                                else "semantic"
                                if semantic_break
                                else "size",
                                "boundary_distance": distances[index - 1]
                                if semantic_break
                                else None,
                            },
                        )
                    )
                    group_start = index
        for chunk in result:
            chunk.metadata["provider_calls"] = budget.calls
        return result
