"""Disabled unless explicit environment opt-in AND service/model credentials are present."""

import os

import pytest

from rosa_chunking import AgenticChunker, Document, SemanticChunker
from rosa_chunking.providers import (
    OpenAICompatibleBoundaryProvider,
    OpenAICompatibleEmbeddingProvider,
)

from .helpers import assert_coverage


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("ROSA_RUN_INTEGRATION") != "1", reason="Real provider tests require explicit opt-in"
)
def test_real_compatible_service():
    required = ["ROSA_API_KEY", "ROSA_BASE_URL", "ROSA_EMBEDDING_MODEL", "ROSA_CHAT_MODEL"]
    if not all(os.getenv(name) for name in required):
        pytest.fail("Set all documented ROSA_* provider settings before opting in")
    kwargs = {"api_key": os.environ["ROSA_API_KEY"], "base_url": os.environ["ROSA_BASE_URL"]}
    doc = Document(
        "integration", "Apples are fruit. Oranges are fruit. Python is a programming language."
    )
    embedding = OpenAICompatibleEmbeddingProvider(
        model=os.environ["ROSA_EMBEDDING_MODEL"], **kwargs
    )
    llm = OpenAICompatibleBoundaryProvider(model=os.environ["ROSA_CHAT_MODEL"], **kwargs)
    assert_coverage(doc, SemanticChunker(embedding, retries=0, max_calls=1).chunk(doc))
    assert_coverage(doc, AgenticChunker(llm, retries=0, max_calls=1).chunk(doc))
