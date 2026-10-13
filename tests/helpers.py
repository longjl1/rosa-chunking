from collections.abc import Sequence

from rosa_chunking import Chunk, Document


class FakeEmbeddings:
    model = "test-embeddings"

    def __init__(self, vectors=None):
        self.vectors = vectors
        self.calls = []

    def embed(self, texts: Sequence[str], *, timeout: float):
        self.calls.append((list(texts), timeout))
        return self.vectors if self.vectors is not None else [[1.0, 0.0] for _ in texts]


class FakeBoundaries:
    model = "test-boundaries"

    def __init__(self, ends=None):
        self.ends = ends
        self.calls = []

    def boundaries(self, units: Sequence[str], *, timeout: float):
        self.calls.append((list(units), timeout))
        return self.ends if self.ends is not None else [len(units)]


def assert_coverage(document: Document, chunks: list[Chunk], *, overlap=False):
    sources = (
        {element.id: element.render_text() for element in document.elements}
        if document.elements
        else {None: document.text}
    )
    offsets = {key: [] for key in sources}
    for chunk in chunks:
        assert chunk.text == "".join(span.extract(document) for span in chunk.sources)
        for span in chunk.sources:
            offsets[span.element_id].extend(range(span.start, span.end))
    for key, text in sources.items():
        assert sorted(set(offsets[key])) == list(range(len(text)))
        if not overlap:
            assert offsets[key] == list(range(len(text)))
