"""Run from outside the checkout to verify an installed distribution, with no network."""

import json
import sys
from collections.abc import Sequence
from importlib.metadata import metadata

from rosa_chunking import (
    AgenticChunker,
    Chunk,
    Document,
    Element,
    FixedSizeChunker,
    HierarchicalChunker,
    ModalityChunker,
    RecursiveChunker,
    SemanticChunker,
    __version__,
)


class Embeddings:
    model = "install-smoke-stub"

    def embed(self, texts: Sequence[str], *, timeout: float) -> list[list[float]]:
        return [[1.0, 0.5] for _ in texts]


class Boundaries:
    model = "install-smoke-stub"

    def boundaries(self, units: Sequence[str], *, timeout: float) -> list[int]:
        return [len(units)]


def main() -> None:
    doc = Document("installed", "Hello. 中文句子！\n\n" * 8)
    strategies = [
        FixedSizeChunker(20, 2),
        RecursiveChunker(20, 2),
        HierarchicalChunker((40, 20, 10)),
        SemanticChunker(Embeddings(), chunk_size=20),
        ModalityChunker(20),
        AgenticChunker(Boundaries(), chunk_size=20),
    ]
    for strategy in strategies:
        chunks = strategy.chunk(doc)
        assert chunks
        leaves = [c for c in chunks if not c.child_ids]
        assert {p for c in leaves for s in c.sources for p in range(s.start, s.end)} == set(
            range(len(doc.text))
        )
        for chunk in chunks:
            assert chunk.text == "".join(s.extract(doc) for s in chunk.sources)
            assert Chunk.from_json(chunk.to_json()) == chunk
    mixed = Document(
        "mixed",
        elements=[
            Element("table", "table", headers=["x"], rows=[["a"], ["b"]]),
            Element("image", "image", uri="asset://image", page=2),
            Element("audio", "audio", text="spoken", start_time=1.0, end_time=2.0),
        ],
    )
    assert len(ModalityChunker(20).chunk(mixed)) == 3
    assert "httpx" not in sys.modules, "Core import must not load optional HTTP dependencies"
    package_metadata = metadata("rosa-chunking")
    assert package_metadata["Requires-Python"] == ">=3.11"
    assert package_metadata["License-Expression"] == "MIT"
    requirements = package_metadata.get_all("Requires-Dist", [])
    assert all("extra ==" in requirement for requirement in requirements)
    print(
        json.dumps(
            {
                "version": __version__,
                "python": sys.version.split()[0],
                "strategies": 6,
                "mandatory_dependencies": 0,
                "status": "passed",
            }
        )
    )


if __name__ == "__main__":
    main()
