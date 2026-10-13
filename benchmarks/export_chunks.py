"""Export auditable chunk text, source coordinates, and structure-boundary comparisons.

The table adapter is a deliberately small fixture adapter for simple pipe tables,
not a general Markdown parser or a new package feature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from bisect import bisect_right
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

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


class OfflineEmbeddings:
    model = "benchmark-offline-stub"

    def embed(self, texts, *, timeout):
        return [[1.0, 0.1] if "chunk" in text.lower() else [0.1, 1.0] for text in texts]


class OfflineBoundaries:
    model = "benchmark-offline-stub"

    def boundaries(self, units, *, timeout):
        return list(range(3, len(units), 3)) + [len(units)]


def scan_regions(text):
    """Locate exact rows/entries in the original string without altering whitespace."""
    lines = []
    offset = 0
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        lines.append({"number": number, "start": offset, "end": offset + len(line), "text": line})
        offset += len(line)
    regions, tables = [], []
    index = 0
    while index < len(lines):
        if not lines[index]["text"].lstrip().startswith("|"):
            index += 1
            continue
        end = index
        while end < len(lines) and lines[end]["text"].lstrip().startswith("|"):
            end += 1
        table_lines = lines[index:end]
        if len(table_lines) < 2 or not re.fullmatch(r"\s*\|[\s|:\-]+\|\s*", table_lines[1]["text"]):
            index = end
            continue
        table_id = f"T{len(tables) + 1}"
        table = {
            "id": table_id,
            "kind": "table",
            "start": table_lines[0]["start"],
            "end": table_lines[-1]["end"],
            "lines": table_lines,
        }
        tables.append(table)
        regions.append({key: value for key, value in table.items() if key != "lines"})
        for row, line in enumerate(table_lines):
            kind = "table_header" if row == 0 else "table_separator" if row == 1 else "table_row"
            regions.append(
                {
                    "id": f"{table_id}.{kind}.{row}",
                    "kind": kind,
                    "table_id": table_id,
                    "row_index": row - 2 if row >= 2 else None,
                    "start": line["start"],
                    "end": line["end"] - (len(line["text"]) - len(line["text"].rstrip("\r\n"))),
                }
            )
        index = end
    reference_heading = re.search(r"^## References[^\n]*", text, re.M)
    reference_start = reference_heading.start() if reference_heading else len(text)
    if reference_heading:
        regions.append(
            {
                "id": "references",
                "kind": "reference_section",
                "start": reference_start,
                "end": len(text),
            }
        )
    for match in re.finditer(r"^- \*\*\[(R\d+)\]\*\*[^\n\r]*", text, re.M):
        regions.append(
            {
                "id": match.group(1),
                "kind": "reference_entry",
                "start": match.start(),
                "end": match.end(),
            }
        )
    for match in re.finditer(r"\[(R\d+)\]", text[:reference_start]):
        regions.append(
            {
                "id": f"citation.{match.group(1)}.{match.start()}",
                "reference_id": match.group(1),
                "kind": "inline_reference",
                "start": match.start(),
                "end": match.end(),
            }
        )
    return regions, tables


def position(text, offset):
    starts = [0] + [match.end() for match in re.finditer("\n", text)]
    line = bisect_right(starts, offset)
    return {"line": line, "column": offset - starts[line - 1] + 1}


def locator(text, start, end):
    assert 0 <= start <= end <= len(text)
    return {
        "start": start,
        "end": end,
        "start_position": position(text, start),
        "end_position_exclusive": position(text, end),
        "covered_lines": [
            position(text, start)["line"],
            position(text, max(start, end - 1))["line"],
        ],
    }


def merged_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def covers(ranges, start, end):
    return any(lo <= start and end <= hi for lo, hi in merged_ranges(ranges))


def region_hits(regions, ranges):
    hits = []
    for region in regions:
        intersections = [
            (max(start, region["start"]), min(end, region["end"]))
            for start, end in ranges
            if start < region["end"] and region["start"] < end
        ]
        if intersections:
            hits.append(
                {
                    "region_id": region["id"],
                    "kind": region["kind"],
                    "fully_contained": covers(ranges, region["start"], region["end"]),
                    "intersections": [list(pair) for pair in merged_ranges(intersections)],
                }
            )
    return hits


def pipe_cells(line):
    if "\\|" in line or "`" in line:
        raise ValueError("This fixture adapter supports simple pipe cells only")
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def structured_document(text, tables, metadata):
    """Retain a row-level origin map when normalizing Markdown into canonical table text."""
    elements, origin_map = [], {}
    cursor = 0

    def add_text(start, end):
        if start == end:
            return
        element = Element(f"text-{len(elements) + 1}", text=text[start:end])
        elements.append(element)
        origin_map[element.id] = [
            {
                "canonical_start": 0,
                "canonical_end": end - start,
                "original_start": start,
                "original_end": end,
                "relation": "verbatim",
            }
        ]

    for table in tables:
        add_text(cursor, table["start"])
        lines = table["lines"]
        element = Element(
            table["id"],
            "table",
            headers=pipe_cells(lines[0]["text"]),
            rows=[pipe_cells(line["text"]) for line in lines[2:]],
            metadata={"original_markdown_range": [table["start"], table["end"]]},
        )
        elements.append(element)
        canonical_offset = 0
        mapping = []
        original_lines = [(lines[0]["start"], lines[1]["end"])] + [
            (line["start"], line["end"]) for line in lines[2:]
        ]
        for canonical, (start, end) in zip(
            element.render_text().splitlines(keepends=True), original_lines, strict=True
        ):
            mapping.append(
                {
                    "canonical_start": canonical_offset,
                    "canonical_end": canonical_offset + len(canonical),
                    "original_start": start,
                    "original_end": end,
                    "relation": "normalized_table_row_origin",
                }
            )
            canonical_offset += len(canonical)
        origin_map[element.id] = mapping
        cursor = table["end"]
    add_text(cursor, len(text))
    return Document("aster-parsed", elements=elements, metadata=metadata), origin_map


def original_locators(span, text, origin_map):
    if origin_map is None:
        return [
            {
                "coordinate_space": "original_markdown",
                "relation": "verbatim",
                **locator(text, span.start, span.end),
            }
        ]
    result = []
    for mapping in origin_map[span.element_id]:
        start, end = (
            max(span.start, mapping["canonical_start"]),
            min(span.end, mapping["canonical_end"]),
        )
        if start >= end:
            continue
        if mapping["relation"] == "verbatim":
            original_start = mapping["original_start"] + start - mapping["canonical_start"]
            original_end = original_start + end - start
        else:
            original_start, original_end = mapping["original_start"], mapping["original_end"]
        result.append(
            {
                "coordinate_space": "original_markdown",
                "relation": mapping["relation"],
                "element_id": span.element_id,
                "canonical_intersection": [start, end],
                **locator(text, original_start, original_end),
            }
        )
    return result


def export_payload(name, chunker, document, text, regions, origin_map=None):
    chunks = chunker.chunk(document)
    entries = []
    for index, chunk in enumerate(chunks, 1):
        assert Chunk.from_json(chunk.to_json()) == chunk
        assert chunk.text == "".join(span.extract(document) for span in chunk.sources)
        locations = [
            location
            for span in chunk.sources
            for location in original_locators(span, text, origin_map)
        ]
        ranges = [(source["start"], source["end"]) for source in locations]
        context_locations = []
        for context in chunk.additional_context:
            from rosa_chunking import SourceSpan

            context_locations.extend(
                original_locators(SourceSpan.from_dict(context["source"]), text, origin_map)
            )
        entries.append(
            {
                "index": index,
                "chunk": chunk.to_dict(),
                "original_sources": locations,
                "original_context_sources": context_locations,
                "region_hits": region_hits(regions, ranges),
            }
        )
    # Only leaf nodes are used for hierarchy coverage and boundary metrics.
    evaluated = [entry for entry in entries if not entry["chunk"]["child_ids"]]
    evaluated_ranges = [
        [(s["start"], s["end"]) for s in entry["original_sources"]] for entry in evaluated
    ]
    union = merged_ranges([pair for ranges in evaluated_ranges for pair in ranges])
    assert union == [(0, len(text))], f"Original origin coverage failed: {name}"
    canonical_texts = (
        {element.id: element.render_text() for element in document.elements}
        if document.elements
        else {None: document.text}
    )
    for element_id, original in canonical_texts.items():
        spans = [
            (span["start"], span["end"])
            for entry in evaluated
            for span in entry["chunk"]["sources"]
            if span["element_id"] == element_id
        ]
        assert merged_ranges(spans) == [(0, len(original))]
    cut_positions = {
        boundary
        for ranges in evaluated_ranges
        for start, end in merged_ranges(ranges)
        for boundary in [start, end]
    }

    def count(kind, intact):
        selected = [region for region in regions if region["kind"] == kind]
        if intact:
            return sum(
                any(covers(ranges, region["start"], region["end"]) for ranges in evaluated_ranges)
                for region in selected
            )
        return sum(
            any(region["start"] < cut < region["end"] for cut in cut_positions)
            for region in selected
        )

    stats = {
        "name": name,
        "chunks": len(chunks),
        "leaf_chunks": len(evaluated),
        "intact_tables": count("table", True),
        "table_rows_with_boundary_cuts": count("table_row", False),
        "intact_reference_entries": count("reference_entry", True),
        "reference_entries_with_boundary_cuts": count("reference_entry", False),
        "inline_markers_with_boundary_cuts": count("inline_reference", False),
        "original_origin_coverage": "complete",
        "source_extraction_verified": True,
        "verbatim_original_text": origin_map is None,
    }
    model_note = (
        "OFFLINE STUB: keyword-based toy vectors, not a real embedding model"
        if name == "semantic"
        else "OFFLINE STUB: every three source units form a group, not an LLM decision"
        if name == "agentic"
        else "No model provider used"
    )
    return {
        "name": name,
        "package_version": __version__,
        "input_kind": "raw_markdown_text" if origin_map is None else "parsed_elements",
        "model_note": model_note,
        "statistics": stats,
        "config": chunks[0].config if chunks else {},
        "chunks": entries,
    }


def fenced(text):
    fence = "`" * max(
        4, max((len(match.group()) + 1 for match in re.finditer(r"`+", text)), default=4)
    )
    return f"{fence}markdown\n{text}" + ("" if text.endswith("\n") else "\n") + f"{fence}\n"


def chunk_markdown(payload):
    parts = [
        f"# {payload['name']} — chunk export\n",
        f"**{payload['model_note']}**\n",
        f"Input: `{payload['input_kind']}`. Offsets are Unicode `[start, end)`, with 1-based lines/columns.\n",
        "Canonical sources remain in the JSON. Parsed table origin mappings point to whole original rows; canonical JSON characters do not equal Markdown character positions.\n",
        "Hierarchical exports include all levels. Leaf-only boundary measurements appear in comparison.md.\n",
        "Chunk bodies below are literal source text in fenced blocks; they are not reformatted tables.\n",
        f"Configuration: `{json.dumps(payload['config'], ensure_ascii=False)}`\n",
    ]
    for entry in payload["chunks"]:
        chunk = entry["chunk"]
        parts.extend(
            [
                f"\n## Chunk {entry['index']:03d} · level {chunk['level']}\n",
                f"- ID: `{chunk['id']}`\n- Characters: {len(chunk['text'])}\n",
                f"- Parent: `{chunk['parent_id']}`; children: {len(chunk['child_ids'])}\n",
            ]
        )
        for source in entry["original_sources"]:
            start, end = source["start_position"], source["end_position_exclusive"]
            parts.append(
                f"- Original: chars `[{source['start']}, {source['end']})`, `{start['line']}:{start['column']} → {end['line']}:{end['column']}`, {source['relation']}\n"
            )
        hits = [
            f"{hit['region_id']} ({'whole' if hit['fully_contained'] else 'partial'})"
            for hit in entry["region_hits"]
        ]
        parts.append(f"- Region hits: {', '.join(hits) if hits else 'none'}\n")
        parts.append(fenced(chunk["text"]))
        for context in chunk["additional_context"]:
            parts.append(
                f"\n### Supplementary context: {context['kind']}\n\n" + fenced(context["text"])
            )
        if chunk["metadata"].get("row_start") is not None:
            parts.append(
                f"\nTable data rows: `[{chunk['metadata']['row_start']}, {chunk['metadata']['row_end']})`.\n"
            )
    return "\n".join(parts)


def comparison_markdown(payloads, regions, source_info):
    parts = [
        "# 六种 chunking 策略对比\n",
        f"来源：`{source_info['fixture_name']}`；快照：[source.md](source.md)；SHA-256：`{source_info['sha256']}`。\n",
        "六种主结果使用同一份原始 Markdown。Fixed / Recursive：1000 字符，重叠 100；Semantic / Modality / Agentic：最大 1000，无重叠；Hierarchical：2000 / 1000 / 500，无重叠。\n",
        "**Semantic 使用关键词玩具向量，Agentic 每三个源单元归组；本次没有调用真实 embedding 或 LLM，不能据此比较真实模型质量。**\n",
        "主表中的层级统计只检查叶子层。表格行统计仅计数据行；“行内边界”表示至少一个起止边界落在行内部。存在重叠时，同一行可能既在某块中完整保留，也被另一块的边界切到。\n",
        "| 策略 | 全部 / 叶子 chunks | 整表在单块内 | 行内边界 / 数据行数 | 完整参考条目 / 条目数 | 条目内边界 | inline 引用内边界 |\n| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    row_total = sum(r["kind"] == "table_row" for r in regions)
    table_total = sum(r["kind"] == "table" for r in regions)
    reference_total = sum(r["kind"] == "reference_entry" for r in regions)
    for payload in payloads:
        stats = payload["statistics"]
        parts.append(
            f"| [{payload['name']}]({payload['name']}.md) | {stats['chunks']} / {stats['leaf_chunks']} | {stats['intact_tables']} / {table_total} | {stats['table_rows_with_boundary_cuts']} / {row_total} | {stats['intact_reference_entries']} / {reference_total} | {stats['reference_entries_with_boundary_cuts']} | {stats['inline_markers_with_boundary_cuts']} |"
        )
    parts.extend(
        [
            "\n## 怎样阅读\n",
            "- 每种方法同时提供 `.md`（逐块全文）与 `.json`（原生 Chunk、配置、原文行列、结构命中）。\n- `original_sources` 指向 source.md 的字符范围；`sources` 是库返回的原生范围。\n- 所有输出都验证了来源提取和完整覆盖。主结果的 chunk 文本均可从原文范围直接重建。\n- 表格和参考文献标注是导出后的观察，不参与六种策略的边界决定。当前没有 reference-aware 专用策略。\n- 原生方法不会自动解析 Markdown 表格、展开引用或访问参考链接。参考文献均为虚构内容。\n",
            "## 表格与参考文献位置\n\n| 区域 | 类型 | 原文字符范围 | 原文行范围 |\n| --- | --- | --- | --- |",
        ]
    )
    for region in regions:
        if region["kind"] in {"table", "reference_entry"}:
            parts.append(
                f"| {region['id']} | {region['kind']} | [{region['start']}, {region['end']}) | {region['location']['covered_lines']} |"
            )
    parts.extend(
        [
            "\n## 补充：结构化 Modality 表格示例\n",
            "[modality_structured_350.md](modality_structured_350.md) / [JSON](modality_structured_350.json) 使用仅适用于本 fixture 的简单 pipe-table 适配器，将表头、数据行解析为 Element，并设为 350 字符，展示跨块重复表头。\n",
            "它是额外的输入结构与预算示例，不属于同预算的六策略主比较。表格以规范 JSON 行表示；Markdown 对齐线并入表头的来源映射。`original_sources` 的表格部分是整行来源关联，不是规范文本逐字符等价映射；文本部分保持逐字符对应。\n",
            "`additional_context` 单独保存重复表头，`original_context_sources` 给出其原文位置；不将这些附加表头计为新的源内容。`structured_document.json` 保存规范输入和来源映射，source.md 保留原始排版。\n",
        ]
    )
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", type=Path, default=Path(__file__).parent / "fixtures" / "test.md"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).parent / "exports" / "long-markdown"
    )
    args = parser.parse_args()
    raw = args.fixture.read_bytes()
    text = raw.decode("utf-8")
    if not text:
        parser.error("Fixture must contain text")
    source_info = {
        "fixture_name": args.fixture.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "characters": len(text),
        "whitespace_words": len(text.split()),
        "snapshot": "source.md",
    }
    regions, tables = scan_regions(text)
    for region in regions:
        region["location"] = locator(text, region["start"], region["end"])
    document = Document("aster-long-report", text=text, metadata=source_info)
    strategies = [
        FixedSizeChunker(),
        RecursiveChunker(),
        HierarchicalChunker(),
        SemanticChunker(OfflineEmbeddings()),
        ModalityChunker(),
        AgenticChunker(OfflineBoundaries()),
    ]
    payloads = [
        export_payload(chunker.strategy, chunker, document, text, regions) for chunker in strategies
    ]
    parsed, mapping = structured_document(text, tables, source_info)
    supplementary = export_payload(
        "modality_structured_350", ModalityChunker(350), parsed, text, regions, mapping
    )
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "source.md").write_bytes(raw)
    for payload in [*payloads, supplementary]:
        payload["source"] = source_info
        (output / f"{payload['name']}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (output / f"{payload['name']}.md").write_text(chunk_markdown(payload), encoding="utf-8")
    (output / "comparison.md").write_text(
        comparison_markdown(payloads, regions, source_info), encoding="utf-8"
    )
    (output / "regions.json").write_text(
        json.dumps(regions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "structured_document.json").write_text(
        json.dumps(
            {"document": parsed.to_dict(), "origin_map": mapping}, indent=2, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "source": source_info,
                "results": [p["statistics"] for p in [*payloads, supplementary]],
                "provider_mode": "offline_stubs",
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    filenames = [
        "source.md",
        "comparison.md",
        "regions.json",
        "structured_document.json",
        "manifest.json",
        *[f"{p['name']}.{ext}" for p in [*payloads, supplementary] for ext in ("md", "json")],
    ]
    archive = output.with_name(output.name + ".zip")
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        for filename in filenames:
            bundle.write(output / filename, arcname=f"{output.name}/{filename}")
    print(
        json.dumps(
            {
                "output_dir": str(output.resolve()),
                "archive": str(archive.resolve()),
                "source": source_info,
                "results": [p["statistics"] for p in [*payloads, supplementary]],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
