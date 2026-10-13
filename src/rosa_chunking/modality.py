"""Chunk parsed elements while respecting table rows and atomic media references."""

from __future__ import annotations

import json
from typing import Any

from ._common import (
    ChunkFactory,
    LengthPolicy,
    Record,
    oversize_metadata,
    positive_int,
    records,
    recursive_ranges,
)
from .models import Chunk, Document


class ModalityChunker:
    strategy = "modality"

    def __init__(self, chunk_size: int = 1000, *, allow_oversize: bool = False):
        positive_int(chunk_size, "chunk_size")
        self.chunk_size, self.allow_oversize = chunk_size, allow_oversize

    def chunk(self, document: Document) -> list[Chunk]:
        factory = ChunkFactory(
            document,
            self.strategy,
            {
                "chunk_size": self.chunk_size,
                "unit": "characters",
                "allow_oversize": self.allow_oversize,
            },
        )
        result = []
        for record in records(document):
            element = record.element
            modality = element.modality if element else "text"
            if modality == "text":
                for start, end in recursive_ranges(record.text, self.chunk_size, 0, LengthPolicy()):
                    result.append(
                        factory.make(
                            [record.span(document, start, end)], metadata={"modality": "text"}
                        )
                    )
            elif modality == "table":
                result.extend(self._table(document, record, factory))
            else:
                meta = {
                    "modality": modality,
                    **oversize_metadata(len(record.text), self.chunk_size, self.allow_oversize),
                }
                result.append(
                    factory.make([record.span(document, 0, len(record.text))], metadata=meta)
                )
        return result

    def _table(self, document: Document, record: Record, factory: ChunkFactory) -> list[Chunk]:
        element = record.element
        assert element is not None
        header = json.dumps(element.headers, ensure_ascii=False) + "\n" if element.headers else ""
        lines = [json.dumps(row, ensure_ascii=False) + "\n" for row in element.rows]
        offset, row_start, group_chars = len(header), 0, 0
        result: list[Chunk] = []

        def emit(end_row: int) -> None:
            nonlocal offset, row_start, group_chars
            if not group_chars and lines:
                return
            first = row_start == 0
            start = 0 if first else offset
            end = offset + group_chars
            meta: dict[str, Any] = {"modality": "table", "row_start": row_start, "row_end": end_row}
            meta.update(
                oversize_metadata(len(header) + group_chars, self.chunk_size, self.allow_oversize)
            )
            context = []
            if not first and header:
                context = [
                    {
                        "kind": "table_header",
                        "text": header,
                        "source": record.span(document, 0, len(header)).to_dict(),
                    }
                ]
            result.append(
                factory.make(
                    [record.span(document, start, end)], metadata=meta, additional_context=context
                )
            )
            offset, row_start, group_chars = end, end_row, 0

        for index, line in enumerate(lines):
            if group_chars and len(header) + group_chars + len(line) > self.chunk_size:
                emit(index)
            oversize_metadata(len(header) + len(line), self.chunk_size, self.allow_oversize)
            group_chars += len(line)
        emit(len(lines))
        return result
