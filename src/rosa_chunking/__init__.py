"""Composable RAG chunking with explicit source provenance."""

from importlib.metadata import PackageNotFoundError, version

from .agentic import AgenticChunker
from .errors import (
    BudgetExceededError,
    ChunkingError,
    OversizeError,
    ProviderError,
    ProviderTimeoutError,
)
from .local import FixedSizeChunker, HierarchicalChunker, RecursiveChunker
from .modality import ModalityChunker
from .models import Chunk, Document, Element, SourceSpan
from .protocols import BoundaryProvider, EmbeddingProvider, Tokenizer
from .semantic import SemanticChunker

try:
    __version__ = version("rosa-chunking")
except PackageNotFoundError:
    __version__ = "0+uninstalled"

__all__ = [
    "AgenticChunker",
    "BoundaryProvider",
    "BudgetExceededError",
    "Chunk",
    "ChunkingError",
    "Document",
    "Element",
    "EmbeddingProvider",
    "FixedSizeChunker",
    "HierarchicalChunker",
    "ModalityChunker",
    "OversizeError",
    "ProviderError",
    "ProviderTimeoutError",
    "RecursiveChunker",
    "SemanticChunker",
    "SourceSpan",
    "Tokenizer",
    "__version__",
]
