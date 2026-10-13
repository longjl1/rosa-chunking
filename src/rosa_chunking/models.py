"""JSON-compatible public types. Offsets always index the original Python string."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Self

Modality = Literal["text", "table", "image", "audio", "video"]


class JSONMixin:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload, no-any-return]

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(**value)

    @classmethod
    def from_json(cls, value: str) -> Self:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return cls.from_dict(data)


@dataclass
class Element(JSONMixin):
    """A parsed element; tables use JSON-array lines as a lossless canonical text."""

    id: str
    modality: Modality = "text"
    text: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    uri: str | None = None
    page: int | None = None
    start_time: float | None = None
    end_time: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Element.id must be a nonempty string")
        if self.modality not in {"text", "table", "image", "audio", "video"}:
            raise ValueError("Unsupported modality")
        if not isinstance(self.text, str):
            raise ValueError("Element.text must be a string")
        if self.modality != "table" and (self.headers or self.rows):
            raise ValueError("Only table elements may have headers or rows")
        if self.modality == "table":
            if self.text:
                raise ValueError("Tables use headers/rows, not a second text representation")
            if any(not isinstance(cell, str) for row in [self.headers, *self.rows] for cell in row):
                raise ValueError("Table cells must be strings")
            if self.headers and any(len(row) != len(self.headers) for row in self.rows):
                raise ValueError("Table row width must match the header")
        if self.page is not None and (type(self.page) is not int or self.page < 1):
            raise ValueError("page must be a positive integer")
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("Both start_time and end_time are required together")
        if self.start_time is not None and self.end_time is not None:
            if not (math.isfinite(self.start_time) and math.isfinite(self.end_time)):
                raise ValueError("Time bounds must be finite")
            if self.start_time < 0 or self.end_time < self.start_time:
                raise ValueError("Invalid time bounds")
        if self.modality in {"image", "audio", "video"} and not (self.uri or self.text):
            raise ValueError("Media elements require a URI or textual representation")
        self.to_json()

    def render_text(self) -> str:
        if self.modality != "table":
            return self.text
        lines = ([self.headers] if self.headers else []) + self.rows
        return "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in lines)


@dataclass
class Document(JSONMixin):
    id: str
    text: str = ""
    elements: list[Element] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not isinstance(self.text, str):
            raise ValueError("Document requires a nonempty id and string text")
        if self.text and self.elements:
            raise ValueError("Supply text or elements, not both")
        if not all(isinstance(element, Element) for element in self.elements):
            raise ValueError("Document.elements must contain Element instances")
        if len({element.id for element in self.elements}) != len(self.elements):
            raise ValueError("Element IDs must be unique within a document")
        self.to_json()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        data = dict(value)
        data["elements"] = [Element.from_dict(element) for element in data.get("elements", [])]
        return cls(**data)

    def source_text(self, element_id: str | None = None) -> str:
        if element_id is None:
            if self.elements:
                raise ValueError("Structured source spans require element_id")
            return self.text
        for element in self.elements:
            if element.id == element_id:
                return element.render_text()
        raise ValueError(f"Unknown element: {element_id}")


@dataclass
class SourceSpan(JSONMixin):
    document_id: str
    start: int
    end: int
    element_id: str | None = None
    page: int | None = None
    uri: str | None = None
    start_time: float | None = None
    end_time: float | None = None

    def __post_init__(self) -> None:
        if not self.document_id or type(self.start) is not int or type(self.end) is not int:
            raise ValueError("SourceSpan requires a document ID and integer offsets")
        if not 0 <= self.start <= self.end:
            raise ValueError("Invalid source range")

    def extract(self, document: Document) -> str:
        if document.id != self.document_id:
            raise ValueError("SourceSpan belongs to a different document")
        text = document.source_text(self.element_id)
        if self.end > len(text):
            raise ValueError("SourceSpan is outside the source")
        return text[self.start : self.end]


@dataclass
class Chunk(JSONMixin):
    id: str
    document_id: str
    text: str
    strategy: str
    sources: list[SourceSpan]
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    additional_context: list[dict[str, Any]] = field(default_factory=list)
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)
    level: int = 0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        data = dict(value)
        data["sources"] = [SourceSpan.from_dict(span) for span in data.get("sources", [])]
        return cls(**data)

    @property
    def embedding_text(self) -> str:
        """Include explicitly marked supplementary context for retrieval, if desired."""
        return "".join(context["text"] for context in self.additional_context) + self.text
