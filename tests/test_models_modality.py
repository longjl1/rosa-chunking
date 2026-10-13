import json

import pytest

from rosa_chunking import Chunk, Document, Element, ModalityChunker, OversizeError, SourceSpan

from .helpers import assert_coverage


def test_json_roundtrip_and_metadata_isolation():
    doc = Document(
        "d",
        elements=[Element("t", text="abc", page=3, metadata={"bbox": [1, 2, 3, 4]})],
        metadata={"nested": {"a": 1}},
    )
    assert Document.from_json(doc.to_json()) == doc
    chunk = ModalityChunker().chunk(doc)[0]
    assert Chunk.from_json(chunk.to_json()) == chunk
    assert SourceSpan.from_json(chunk.sources[0].to_json()) == chunk.sources[0]
    chunk.metadata["document_metadata"]["nested"]["a"] = 2
    assert doc.metadata["nested"]["a"] == 1
    assert chunk.sources[0].page == 3
    assert chunk.metadata["element_metadata"]["t"]["bbox"] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Document(""),
        lambda: Document("d", "abc", [Element("e")]),
        lambda: Document("d", elements=[Element("e"), Element("e")]),
        lambda: Element("e", "wrong"),
        lambda: Element("e", "table", headers=["a"], rows=[["x", "y"]]),
        lambda: Element("e", "audio", text="a", start_time=4, end_time=3),
        lambda: Element("e", "video", text="v", start_time=float("nan"), end_time=5),
        lambda: Element("e", "image"),
        lambda: Document("d", metadata={"bad": object()}),
    ],
)
def test_model_validation(factory):
    with pytest.raises((ValueError, TypeError)):
        factory()


def test_source_extraction_rejects_wrong_document_or_range():
    doc = Document("d", "abc")
    with pytest.raises(ValueError):
        SourceSpan("other", 0, 1).extract(doc)
    with pytest.raises(ValueError):
        SourceSpan("d", 0, 9).extract(doc)


def test_table_lossless_rows_and_supplementary_header():
    table = Element(
        "table",
        "table",
        headers=["name", "value"],
        rows=[["甲\n乙", "1"], ["a|b", "2"], ["🧑🏽‍💻", "3"], ["four", "4"]],
    )
    doc = Document("d", elements=[table])
    chunks = ModalityChunker(55).chunk(doc)
    assert len(chunks) > 1
    assert_coverage(doc, chunks)
    assert [json.loads(line) for line in "".join(c.text for c in chunks).splitlines()] == [
        table.headers,
        *table.rows,
    ]
    assert not chunks[0].additional_context
    for chunk in chunks[1:]:
        context = chunk.additional_context[0]
        assert context["kind"] == "table_header"
        assert SourceSpan.from_dict(context["source"]).extract(doc) == context["text"]
        assert len(chunk.embedding_text) <= 55
    assert [c.metadata["row_start"] for c in chunks] == sorted(
        c.metadata["row_start"] for c in chunks
    )


@pytest.mark.parametrize(
    "table",
    [
        Element("t", "table"),
        Element("t", "table", headers=["a"]),
        Element("t", "table", rows=[["a"], ["b"]]),
    ],
)
def test_empty_header_only_and_headerless_tables(table):
    doc = Document("d", elements=[table])
    chunks = ModalityChunker(20).chunk(doc)
    assert_coverage(doc, chunks)


def test_multimodal_locations_and_atomic_content():
    doc = Document(
        "mixed",
        elements=[
            Element("text", text="第一段。下一段！"),
            Element("image", "image", uri="asset://image", page=2),
            Element("audio", "audio", text="spoken words", start_time=2.5, end_time=7.0),
            Element(
                "video",
                "video",
                text="scene caption",
                uri="asset://video",
                start_time=10,
                end_time=12,
            ),
        ],
    )
    chunks = ModalityChunker(20).chunk(doc)
    assert_coverage(doc, chunks)
    assert [c.metadata["modality"] for c in chunks] == ["text", "image", "audio", "video"]
    assert chunks[1].sources[0].uri == "asset://image"
    assert (chunks[2].sources[0].start_time, chunks[2].sources[0].end_time) == (2.5, 7.0)


@pytest.mark.parametrize(
    "element",
    [
        Element("t", "table", headers=["a"], rows=[["x" * 50]]),
        Element("i", "image", text="x" * 50, uri="asset://image"),
        Element("a", "audio", text="x" * 50, start_time=0, end_time=10),
    ],
)
def test_atomic_oversize_policy(element):
    doc = Document("d", elements=[element])
    with pytest.raises(OversizeError):
        ModalityChunker(20).chunk(doc)
    chunks = ModalityChunker(20, allow_oversize=True).chunk(doc)
    assert all(c.metadata["oversize"] for c in chunks)
    assert_coverage(doc, chunks)
