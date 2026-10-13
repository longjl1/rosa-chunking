"""Errors applications can handle without inspecting provider exceptions."""


class ChunkingError(Exception):
    """Base class for operational chunking failures."""


class OversizeError(ChunkingError):
    """An indivisible source unit exceeds the configured budget."""


class ProviderError(ChunkingError):
    """A provider failed or returned an invalid response."""


class ProviderTimeoutError(ProviderError):
    """The provider's configured request timeout was exceeded."""


class BudgetExceededError(ProviderError):
    """The per-document call budget was exhausted, including retries."""
