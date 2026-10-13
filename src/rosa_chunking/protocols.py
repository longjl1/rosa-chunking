"""Minimal synchronous interfaces for user-supplied backends."""

from collections.abc import Sequence
from typing import Protocol


class Tokenizer(Protocol):
    @property
    def name(self) -> str: ...

    def spans(self, text: str) -> Sequence[tuple[int, int]]:
        """Return positive, contiguous character spans covering the entire input."""
        ...


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    def embed(self, texts: Sequence[str], *, timeout: float) -> Sequence[Sequence[float]]: ...


class BoundaryProvider(Protocol):
    @property
    def model(self) -> str: ...

    def boundaries(self, units: Sequence[str], *, timeout: float) -> Sequence[int]:
        """Return exclusive group ends; strictly increasing and ending at len(units)."""
        ...
