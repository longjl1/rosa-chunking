"""Check original-coordinate auditing independently of canonical table serialization."""

from pathlib import Path

from benchmarks.export_chunks import (
    export_payload,
    locator,
    region_hits,
    scan_regions,
    structured_document,
)
from rosa_chunking import ModalityChunker


def test_fixture_structure_locators():
    path = Path(__file__).resolve().parents[1] / "benchmarks/fixtures/test.md"
    text = path.read_bytes().decode("utf-8")
    regions, tables = scan_regions(text)
    assert len(tables) == 3
    assert sum(region["kind"] == "table_row" for region in regions) == 16
    assert sum(region["kind"] == "reference_entry" for region in regions) == 4
    assert sum(region["kind"] == "inline_reference" for region in regions) == 4
    for table in tables:
        origin = locator(text, table["start"], table["end"])
        assert origin["start_position"]["column"] == 1
        assert text[table["start"] : table["end"]].startswith("| ")
    assert locator("甲\n🧑🏽‍💻 end", 2, 6)["start_position"] == {"line": 2, "column": 1}
    assert locator("甲\n🧑🏽‍💻 end", 2, 6)["end_position_exclusive"] == {"line": 2, "column": 5}


def test_region_containment_uses_union_of_adjacent_sources():
    regions = [{"id": "R1", "kind": "reference_entry", "start": 2, "end": 8}]
    assert region_hits(regions, [(0, 5), (5, 10)])[0]["fully_contained"] is True
    assert region_hits(regions, [(0, 4), (6, 10)])[0]["fully_contained"] is False
    assert region_hits(regions, [(8, 10)]) == []


def test_parsed_header_context_maps_to_original_header_and_separator():
    text = "Intro.\n\n| Name | Value |\n| --- | --- |\n| Alpha | 1 |\n| Beta | 2 |\n\nEnd.\n"
    regions, tables = scan_regions(text)
    document, mapping = structured_document(text, tables, {})
    payload = export_payload(
        "structured-test", ModalityChunker(35), document, text, regions, mapping
    )
    table_chunks = [
        entry
        for entry in payload["chunks"]
        if entry["chunk"]["metadata"].get("modality") == "table"
    ]
    assert len(table_chunks) == 2
    assert table_chunks[1]["chunk"]["text"] == '["Beta", "2"]\n'
    assert table_chunks[1]["chunk"]["additional_context"][0]["text"] == '["Name", "Value"]\n'
    context = table_chunks[1]["original_context_sources"][0]
    assert text[context["start"] : context["end"]] == "| Name | Value |\n| --- | --- |\n"
    row = table_chunks[1]["original_sources"][0]
    assert text[row["start"] : row["end"]] == "| Beta | 2 |\n"
    assert payload["statistics"]["table_rows_with_boundary_cuts"] == 0
    assert payload["statistics"]["original_origin_coverage"] == "complete"
    assert payload["statistics"]["verbatim_original_text"] is False
