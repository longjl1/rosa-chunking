"""Run all six strategies offline. Stub model outputs demonstrate interfaces, not model quality."""

from collections.abc import Sequence

from rosa_chunking import (
    AgenticChunker,
    Document,
    Element,
    FixedSizeChunker,
    HierarchicalChunker,
    ModalityChunker,
    RecursiveChunker,
    SemanticChunker,
)


class DemoEmbeddings:
    model = "offline-demo-topic-vectors"

    def embed(self, texts: Sequence[str], *, timeout: float) -> list[list[float]]:
        return [[1.0, 0.1] if "fruit" in text else [0.1, 1.0] for text in texts]


class DemoBoundaries:
    model = "offline-demo-boundaries"

    def boundaries(self, units: Sequence[str], *, timeout: float) -> list[int]:
        return list(range(2, len(units), 2)) + [len(units)]


def main() -> None:
    document = Document(
        "demo", "Apples are fruit. Oranges are fruit.\n\nPython is a language. 中文句子也支持。"
    )
    strategies = [
        FixedSizeChunker(30, 5),
        RecursiveChunker(30, 5),
        HierarchicalChunker((60, 30, 15)),
        SemanticChunker(DemoEmbeddings(), chunk_size=50, buffer_size=0),
        ModalityChunker(40),
        AgenticChunker(DemoBoundaries(), chunk_size=40),
    ]
    for strategy in strategies:
        chunks = strategy.chunk(document)
        print(f"\n{strategy.strategy}: {len(chunks)} chunks")
        for chunk in chunks:
            assert chunk.text == "".join(source.extract(document) for source in chunk.sources)
            print(f"  level={chunk.level} {chunk.text!r}")
    structured = Document(
        "media",
        elements=[
            Element(
                "t", "table", headers=["Item", "Value"], rows=[["A", "1"], ["B", "2"], ["C", "3"]]
            ),
            Element("i", "image", uri="asset://figure.png", page=2),
            Element("a", "audio", text="Transcript", start_time=0, end_time=3),
        ],
    )
    print("\nStructured elements:")
    for chunk in ModalityChunker(40).chunk(structured):
        print(chunk.to_json())


if __name__ == "__main__":
    main()
