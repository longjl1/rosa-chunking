import pytest

from rosa_chunking import (
    AgenticChunker,
    BudgetExceededError,
    Document,
    Element,
    OversizeError,
    ProviderError,
    ProviderTimeoutError,
    SemanticChunker,
)

from .helpers import FakeBoundaries, FakeEmbeddings, assert_coverage


def test_semantic_distance_boundary_and_trace():
    doc = Document("d", "First. Second. Third.")
    provider = FakeEmbeddings([[1, 0], [1, 0], [0, 1]])
    chunks = SemanticChunker(provider, buffer_size=0, threshold=0.5).chunk(doc)
    assert [c.text for c in chunks] == ["First. Second. ", "Third."]
    assert chunks[0].metadata["boundary_distance"] == 1.0
    assert all(
        c.config["model"] == "test-embeddings" and c.metadata["provider_calls"] == 1 for c in chunks
    )
    assert_coverage(doc, chunks)


def test_semantic_buffer_and_hard_size():
    doc = Document("d", "A. B. C. D. " + "长" * 30)
    provider = FakeEmbeddings()
    chunks = SemanticChunker(provider, chunk_size=8, buffer_size=1).chunk(doc)
    assert provider.calls[0][0][0] == "A. B. "
    assert all(len(c.text) <= 8 for c in chunks)
    assert_coverage(doc, chunks)


def test_semantic_leading_whitespace_never_embeds_blank_inputs():
    class Nonblank(FakeEmbeddings):
        def embed(self, texts, *, timeout):
            assert all(text.strip() for text in texts)
            return super().embed(texts, timeout=timeout)

    doc = Document("d", "\n" * 20 + "A. B.")
    provider = Nonblank()
    chunks = SemanticChunker(provider, chunk_size=5, buffer_size=0).chunk(doc)
    assert_coverage(doc, chunks)
    assert all(len(chunk.text) <= 5 for chunk in chunks)


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[1, 0]],
        [[1, 0], [1]],
        [[0, 0], [1, 0]],
        [[float("nan"), 0], [1, 0]],
        [[float("inf"), 0], [1, 0]],
        [[True, 0], [1, 0]],
    ],
)
def test_bad_embeddings_raise(vectors):
    with pytest.raises(ProviderError):
        SemanticChunker(FakeEmbeddings(vectors), retries=0).chunk(Document("d", "A. B."))


def test_embedding_dimension_changes_across_batches():
    class Changing(FakeEmbeddings):
        def embed(self, texts, *, timeout):
            return (
                [[1.0] * (len(self.calls) + 1) for _ in texts]
                if self.calls.append(1) is None
                else []
            )

    with pytest.raises(ProviderError):
        SemanticChunker(Changing(), batch_size=1).chunk(Document("d", "A. B."))


@pytest.mark.parametrize("text", ["", " ", "single", "单句。"])
def test_semantic_single_or_blank_avoids_calls(text):
    provider = FakeEmbeddings()
    doc = Document("d", text)
    assert_coverage(doc, SemanticChunker(provider).chunk(doc))
    assert not provider.calls


def test_agentic_boundaries_keep_original_and_subdivide():
    doc = Document("d", "First. Second. 第三句。末句！")
    provider = FakeBoundaries([2, 4])
    chunks = AgenticChunker(provider, chunk_size=20).chunk(doc)
    assert [c.text for c in chunks] == ["First. Second. ", "第三句。末句！"]
    assert all(c.metadata["decision_ends"] == [2, 4] for c in chunks)
    assert_coverage(doc, chunks)
    small = AgenticChunker(FakeBoundaries(), chunk_size=8).chunk(doc)
    assert all(len(c.text) <= 8 for c in small)
    assert_coverage(doc, small)


@pytest.mark.parametrize("ends", [[], [0, 2], [2, 1], [1], [3], [2, 2], [True, 2], [1.0, 2], ["2"]])
def test_bad_agentic_boundaries_raise(ends):
    with pytest.raises(ProviderError):
        AgenticChunker(FakeBoundaries(ends), retries=0).chunk(Document("d", "A. B."))


def test_agentic_bounded_windows_and_structured_sources():
    doc = Document(
        "d",
        elements=[
            Element("a", text="一。二。三。"),
            Element("b", text="Four. Five."),
            Element("i", "image", uri="asset://img"),
        ],
    )
    provider = FakeBoundaries()
    chunks = AgenticChunker(provider, chunk_size=7, window_size=10, max_units=2).chunk(doc)
    assert all(len(units) <= 2 and sum(map(len, units)) <= 10 for units, _ in provider.calls)
    assert any(s.uri == "asset://img" for c in chunks for s in c.sources)
    assert_coverage(doc, chunks)


def test_agentic_atomic_oversize_does_not_exceed_model_window():
    doc = Document("d", elements=[Element("big", "image", text="x" * 100, uri="asset://img")])
    provider = FakeBoundaries()
    with pytest.raises(OversizeError):
        AgenticChunker(provider, chunk_size=10, window_size=20).chunk(doc)
    chunks = AgenticChunker(provider, chunk_size=10, window_size=20, allow_oversize=True).chunk(doc)
    assert not provider.calls
    assert chunks[0].metadata["decision"] == "atomic_passthrough"
    assert_coverage(doc, chunks)


@pytest.mark.parametrize(
    "cls,provider", [(SemanticChunker, FakeEmbeddings()), (AgenticChunker, FakeBoundaries())]
)
def test_call_budget_resets_per_document(cls, provider):
    doc = Document("d", "A. B. C. D.")
    kwargs = {"batch_size": 1} if cls is SemanticChunker else {"max_units": 1}
    chunker = cls(provider, max_calls=1, **kwargs)
    for _ in range(2):
        with pytest.raises(BudgetExceededError):
            chunker.chunk(doc)


@pytest.mark.parametrize("cls", [SemanticChunker, AgenticChunker])
def test_timeout_retry_budget_and_explicit_fallback(cls):
    class Failing:
        model = "failing-model"
        calls = 0

        def run(self, texts, *, timeout):
            assert timeout == 0.1
            self.calls += 1
            raise TimeoutError("do not expose sensitive transport details")

        embed = run
        boundaries = run

    doc = Document("d", "A. B.")
    provider = Failing()
    with pytest.raises(ProviderTimeoutError):
        cls(provider, timeout=0.1, retries=1).chunk(doc)
    assert provider.calls == 2
    with pytest.raises(BudgetExceededError):
        cls(provider, timeout=0.1, retries=2, max_calls=1).chunk(doc)
    chunks = cls(provider, timeout=0.1, fallback="recursive").chunk(doc)
    assert chunks[0].strategy == "recursive"
    assert chunks[0].metadata["fallback_reason"] == "ProviderTimeoutError"
    assert chunks[0].metadata["requested_config"]["model"] == "failing-model"
    assert_coverage(doc, chunks)


def test_transient_invalid_response_retried_within_budget():
    class Recovering(FakeBoundaries):
        def boundaries(self, units, *, timeout):
            self.calls.append(units)
            return [] if len(self.calls) == 1 else [len(units)]

    provider = Recovering()
    chunks = AgenticChunker(provider, retries=1, max_calls=2).chunk(Document("d", "A. B."))
    assert len(provider.calls) == 2
    assert chunks[0].metadata["provider_calls"] == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"timeout": float("nan")},
        {"retries": -1},
        {"max_calls": 0},
        {"fallback": "silent"},
    ],
)
@pytest.mark.parametrize(
    "cls,provider", [(SemanticChunker, FakeEmbeddings()), (AgenticChunker, FakeBoundaries())]
)
def test_invalid_model_config(cls, provider, kwargs):
    with pytest.raises(ValueError):
        cls(provider, **kwargs)
