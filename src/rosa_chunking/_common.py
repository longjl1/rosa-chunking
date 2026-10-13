from __future__ import annotations

import copy
import hashlib
import json
import re
from bisect import bisect_right
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from .errors import BudgetExceededError, OversizeError, ProviderError, ProviderTimeoutError
from .models import Chunk, Document, Element, SourceSpan
from .protocols import Tokenizer

T = TypeVar("T")
Unit = Literal["characters", "tokens"]


def positive_int(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


class LengthPolicy:
    def __init__(self, unit: Unit = "characters", tokenizer: Tokenizer | None = None):
        if unit not in {"characters", "tokens"}:
            raise ValueError("unit must be 'characters' or 'tokens'")
        if unit == "tokens" and tokenizer is None:
            raise ValueError("Token mode requires a tokenizer with original character spans")
        self.unit = unit
        self.tokenizer = tokenizer

    def boundaries(self, text: str) -> list[int]:
        if not text:
            return [0]
        if self.unit == "characters":
            return list(range(len(text) + 1))
        assert self.tokenizer is not None
        bounds = [0]
        for start, end in self.tokenizer.spans(text):
            if type(start) is not int or type(end) is not int:
                raise ValueError("Tokenizer offsets must be integers")
            if start != bounds[-1] or not start < end <= len(text):
                raise ValueError("Tokenizer spans must be contiguous, positive, and in bounds")
            bounds.append(end)
        if bounds[-1] != len(text):
            raise ValueError("Tokenizer spans must cover the entire original text")
        return bounds

    def count(self, text: str) -> int:
        return len(text) if self.unit == "characters" else len(self.boundaries(text)) - 1

    def config(self) -> dict[str, Any]:
        return {"unit": self.unit, "tokenizer": self.tokenizer.name if self.tokenizer else None}


@dataclass
class Record:
    text: str
    element: Element | None = None

    def span(self, document: Document, start: int, end: int) -> SourceSpan:
        element = self.element
        return SourceSpan(
            document.id,
            start,
            end,
            element_id=element.id if element else None,
            page=element.page if element else None,
            uri=element.uri if element else None,
            start_time=element.start_time if element else None,
            end_time=element.end_time if element else None,
        )


def records(document: Document) -> Iterator[Record]:
    if document.elements:
        for element in document.elements:
            yield Record(element.render_text(), element)
    elif document.text:
        yield Record(document.text)


class ChunkFactory:
    def __init__(self, document: Document, strategy: str, config: dict[str, Any]):
        self.document = document
        self.strategy = strategy
        self.config = config
        self.fingerprint = hashlib.sha256(
            json.dumps(
                document.to_dict(), sort_keys=True, ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
        self.source_texts: dict[str | None, str] = (
            {element.id: element.render_text() for element in document.elements}
            if document.elements
            else {None: document.text}
        )
        self.element_metadata = {element.id: element.metadata for element in document.elements}

    def make(
        self,
        spans: list[SourceSpan],
        *,
        metadata: dict[str, Any] | None = None,
        additional_context: list[dict[str, Any]] | None = None,
        parent_id: str | None = None,
        level: int = 0,
    ) -> Chunk:
        text = "".join(self.source_texts[span.element_id][span.start : span.end] for span in spans)
        identity = {
            "document": self.fingerprint,
            "strategy": self.strategy,
            "config": self.config,
            "sources": [span.to_dict() for span in spans],
            "parent": parent_id,
            "level": level,
            "decisions": metadata or {},
            "context": additional_context or [],
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest()
        meta: dict[str, Any] = {"document_metadata": copy.deepcopy(self.document.metadata)}
        element_meta = {
            element_id: copy.deepcopy(self.element_metadata[element_id])
            for element_id in dict.fromkeys(span.element_id for span in spans)
            if element_id is not None
        }
        if element_meta:
            meta["element_metadata"] = element_meta
        meta.update(copy.deepcopy(metadata or {}))
        return Chunk(
            id=digest,
            document_id=self.document.id,
            text=text,
            strategy=self.strategy,
            sources=spans,
            config=copy.deepcopy(self.config),
            metadata=meta,
            additional_context=copy.deepcopy(additional_context or []),
            parent_id=parent_id,
            level=level,
        )


def fixed_ranges(text: str, size: int, overlap: int, policy: LengthPolicy) -> list[tuple[int, int]]:
    if policy.unit == "characters":
        result = []
        for start in range(0, len(text), size - overlap):
            end = min(start + size, len(text))
            result.append((start, end))
            if end == len(text):
                break
        return result
    bounds = policy.boundaries(text)
    count = len(bounds) - 1
    result = []
    start = 0
    while start < count:
        end = min(start + size, count)
        result.append((bounds[start], bounds[end]))
        if end == count:
            break
        start = end - overlap
    return result


DEFAULT_SEPARATORS = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", ";", "；", " ", "")


def recursive_ranges(
    text: str,
    size: int,
    overlap: int,
    policy: LengthPolicy,
    separators: Sequence[str] = DEFAULT_SEPARATORS,
) -> list[tuple[int, int]]:
    # Every candidate boundary is in the original string; nothing is stripped or rejoined.
    positions: list[list[int]] = []
    for separator in separators:
        if separator:
            positions.append([match.end() for match in re.finditer(re.escape(separator), text)])
    result: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        bounds = (
            list(range(min(len(text) - start, size + 1) + 1))
            if policy.unit == "characters"
            else policy.boundaries(text[start:])
        )
        if len(bounds) - 1 <= size:
            result.append((start, len(text)))
            break
        hard_end = start + bounds[size]
        allowed = set(start + bound for bound in bounds[: size + 1])
        # Leave room for overlap so the next chunk always advances.
        floor = start + bounds[overlap]
        end = hard_end
        for candidates in positions:
            candidate_index = bisect_right(candidates, hard_end) - 1
            while candidate_index >= 0 and candidates[candidate_index] > floor:
                if candidates[candidate_index] in allowed:
                    end = candidates[candidate_index]
                    break
                candidate_index -= 1
            if end != hard_end or (
                candidate_index >= 0 and candidates[candidate_index] == hard_end
            ):
                break
        result.append((start, end))
        if overlap:
            consumed = policy.boundaries(text[start:end])
            start += consumed[max(1, len(consumed) - 1 - overlap)]
        else:
            start = end
    return result


def sentence_ranges(text: str) -> list[tuple[int, int]]:
    """Conservative Chinese/English sentence boundaries retaining all whitespace."""
    ends = [
        match.end() for match in re.finditer(r"[。！？!?]+[\"'”’）)]*\s*|\.(?=\s|$)\s*|\n+", text)
    ]
    if not ends or ends[-1] != len(text):
        ends.append(len(text))
    result = []
    start = 0
    for end in ends:
        if end > start:
            result.append((start, end))
            start = end
    return result


class CallBudget:
    """Per-operation budget. Backends must honor the supplied I/O timeout."""

    def __init__(self, max_calls: int, retries: int, timeout: float):
        positive_int(max_calls, "max_calls")
        if type(retries) is not int or retries < 0:
            raise ValueError("retries must be a nonnegative integer")
        import math

        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.max_calls, self.retries, self.timeout = max_calls, retries, timeout
        self.calls = 0

    def run(self, operation: Callable[[], T]) -> T:
        for attempt in range(self.retries + 1):
            if self.calls >= self.max_calls:
                raise BudgetExceededError("Per-document call budget exhausted (including retries)")
            self.calls += 1
            try:
                return operation()
            except BudgetExceededError:
                raise
            except (TimeoutError, ProviderTimeoutError) as exc:
                error: ProviderError = ProviderTimeoutError("Provider request timed out")
                if attempt == self.retries:
                    raise error from exc
            except Exception as exc:
                if attempt == self.retries:
                    raise ProviderError("Provider request failed or returned invalid data") from exc
        raise AssertionError("Unreachable")


def oversize_metadata(length: int, size: int, allow: bool) -> dict[str, Any]:
    if length > size:
        if not allow:
            raise OversizeError(f"Atomic element size {length} exceeds budget {size}")
        return {"oversize": True, "size": length}
    return {}
