"""Reproducible offline mechanics benchmark with an original MIT-licensed fixture."""

import argparse
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

from rosa_chunking import (
    AgenticChunker,
    Document,
    FixedSizeChunker,
    HierarchicalChunker,
    ModalityChunker,
    RecursiveChunker,
    SemanticChunker,
)


class StubEmbeddings:
    model = "benchmark-offline-stub"

    def embed(self, texts, *, timeout):
        return [[1.0, 0.1] if "chunk" in text.lower() else [0.1, 1.0] for text in texts]


class StubBoundaries:
    model = "benchmark-offline-stub"

    def boundaries(self, units, *, timeout):
        return list(range(3, len(units), 3)) + [len(units)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).parent / "fixtures" / "bilingual.txt",
        help="UTF-8 text or Markdown fixture; passed as raw text, not parsed elements",
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--copies", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeat < 1 or args.copies < 1:
        parser.error("repeat and copies must be positive")
    fixture = args.fixture
    raw = fixture.read_bytes()
    doc = Document("benchmark", raw.decode("utf-8") * args.copies)
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "fixture_name": fixture.name,
        "copies": args.copies,
        "input_characters": len(doc.text),
        "input_whitespace_words": len(doc.text.split()),
        "provider_mode": "offline_stubs",
        "results": [],
    }
    strategies = [
        FixedSizeChunker(),
        RecursiveChunker(),
        HierarchicalChunker(),
        SemanticChunker(StubEmbeddings()),
        ModalityChunker(),
        AgenticChunker(StubBoundaries()),
    ]
    for strategy in strategies:
        elapsed = []
        for _ in range(args.repeat):
            start = time.perf_counter()
            chunks = strategy.chunk(doc)
            elapsed.append(time.perf_counter() - start)
        leaves = [c for c in chunks if not c.child_ids]
        covered = {p for c in leaves for s in c.sources for p in range(s.start, s.end)}
        assert covered == set(range(len(doc.text)))
        sizes = [len(c.text) for c in chunks]
        report["results"].append(
            {
                "strategy": strategy.strategy,
                "config": chunks[0].config if chunks else {},
                "elapsed_seconds": elapsed,
                "median_seconds": statistics.median(elapsed),
                "chunks": len(chunks),
                "size_min": min(sizes, default=0),
                "size_max": max(sizes, default=0),
                "size_mean": statistics.mean(sizes) if sizes else 0,
                "coverage": "complete",
            }
        )
    output = json.dumps(report, indent=2, ensure_ascii=False)
    print(output)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
