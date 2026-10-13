import random
import re

import pytest

from rosa_chunking import Document, Element, FixedSizeChunker, HierarchicalChunker, RecursiveChunker

from .helpers import assert_coverage

TEXTS = [
    "",
    " ",
    "hello",
    "重复重复重复重复",
    "第一句。第二句！Third sentence.\n\nNext paragraph. ",
    "🧑🏽‍💻é𐀀\r\n" * 8,
    "x" * 101,
    "a. a. a. a. ",
    "\n\n  \t\n",
]


@pytest.mark.parametrize("cls", [FixedSizeChunker, RecursiveChunker])
@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize("overlap", [0, 2, 6])
def test_local_invariants(cls, text, overlap):
    document = Document("source", text)
    chunker = cls(7, overlap)
    chunks = chunker.chunk(document)
    assert_coverage(document, chunks, overlap=bool(overlap))
    assert all(len(chunk.text) <= 7 for chunk in chunks)
    assert len({chunk.id for chunk in chunks}) == len(chunks)
    assert [c.to_dict() for c in chunks] == [c.to_dict() for c in chunker.chunk(document)]


def test_fixed_exact_overlap():
    chunks = FixedSizeChunker(5, 2).chunk(Document("d", "0123456789"))
    assert [c.text for c in chunks] == ["01234", "34567", "6789"]


def test_recursive_prefers_paragraph_boundary():
    chunks = RecursiveChunker(10, 0).chunk(Document("d", "abc\n\ndefghijklmno"))
    assert chunks[0].text == "abc\n\n"


class WordTokenizer:
    name = "test-words"

    def spans(self, text):
        return [(m.start(), m.end()) for m in re.finditer(r"\S+\s*|\s+", text)]


@pytest.mark.parametrize("cls", [FixedSizeChunker, RecursiveChunker])
def test_token_original_offsets(cls):
    document = Document("tokens", "one  two 三 🧑🏽‍💻 five six\n")
    chunks = cls(3, 1, unit="tokens", tokenizer=WordTokenizer()).chunk(document)
    assert_coverage(document, chunks, overlap=True)
    assert all(len(WordTokenizer().spans(c.text)) <= 3 for c in chunks)
    assert chunks[0].text == "one  two 三 "


@pytest.mark.parametrize("spans", [[(1, 3)], [(0, 2)], [(0, 0), (0, 3)], [(0, 4)], [(0.0, 3)]])
def test_invalid_token_spans(spans):
    class BrokenTokenizer:
        name = "broken"

        def spans(self, text):
            return spans

    with pytest.raises(ValueError):
        FixedSizeChunker(2, 0, unit="tokens", tokenizer=BrokenTokenizer()).chunk(
            Document("d", "abc")
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chunk_size": 0},
        {"overlap": -1},
        {"chunk_size": 3, "overlap": 3},
        {"unit": "tokens"},
        {"unit": "bytes"},
        {"chunk_size": True},
    ],
)
def test_invalid_local_config(kwargs):
    with pytest.raises(ValueError):
        FixedSizeChunker(**kwargs)


def test_randomized_coverage_and_progress():
    rng = random.Random(21)
    for _ in range(100):
        text = "".join(rng.choice("ab 。!?\n🧑🏽‍💻é") for _ in range(rng.randrange(1, 200)))
        size = rng.randrange(2, 25)
        overlap = rng.randrange(size)
        doc = Document("random", text)
        chunks = RecursiveChunker(size, overlap).chunk(doc)
        assert_coverage(doc, chunks, overlap=bool(overlap))
        assert all(0 < len(c.text) <= size for c in chunks)


def test_hierarchy_containment_and_level_coverage():
    doc = Document("d", "第一句。 More context.\n\n" * 12)
    chunks = HierarchicalChunker((40, 15, 5)).chunk(doc)
    by_id = {c.id: c for c in chunks}
    for level in range(3):
        assert_coverage(doc, [c for c in chunks if c.level == level])
    for chunk in chunks:
        assert len(chunk.text) <= [40, 15, 5][chunk.level]
        if chunk.parent_id:
            parent = by_id[chunk.parent_id]
            assert parent.level + 1 == chunk.level
            assert chunk.id in parent.child_ids
            assert (
                parent.sources[0].start
                <= chunk.sources[0].start
                <= chunk.sources[0].end
                <= parent.sources[0].end
            )
        else:
            assert chunk.level == 0


def test_hierarchy_structured_reference():
    doc = Document("d", elements=[Element("img", "image", uri="asset://figure.png")])
    chunks = HierarchicalChunker((30, 20, 10)).chunk(doc)
    assert len(chunks) == 3
    assert all(c.sources[0].uri == "asset://figure.png" for c in chunks)


@pytest.mark.parametrize("sizes", [[], [10, 10], [10, 20], [0]])
def test_invalid_hierarchy(sizes):
    with pytest.raises(ValueError):
        HierarchicalChunker(sizes)
