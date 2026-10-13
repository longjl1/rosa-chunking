# rosa-chunking

Six composable chunking strategies for RAG developers, with explicit source provenance.
Python 3.11+, MIT, zero mandatory runtime dependencies. Version **0.1.0a1** is an alpha API.

## Installation

Install the first alpha from [PyPI](https://pypi.org/project/rosa-chunking/):

```bash
python -m pip install 'rosa-chunking==0.1.0a1'
# Optional HTTP adapters:
python -m pip install 'rosa-chunking[providers]==0.1.0a1'
```

For development, install a local checkout:

```bash
python -m pip install .
# Optional HTTP adapters:
python -m pip install '.[providers]'
```

Once an alpha is published, use `python -m pip install --pre rosa-chunking`.
Once a final release is published, use `python -m pip install rosa-chunking`.

## Quick start

```python
from rosa_chunking import Document, RecursiveChunker

document = Document(id="demo", text="第一段。Second sentence.\n\nAnother paragraph.")
chunks = RecursiveChunker(chunk_size=1000, overlap=100).chunk(document)
for chunk in chunks:
    print(chunk.text, chunk.sources)
    assert chunk.text == "".join(source.extract(document) for source in chunk.sources)
```

## Choose a strategy

| Class | Behavior | Useful when |
| --- | --- | --- |
| `FixedSizeChunker` | Character or token windows, configurable overlap | You want a predictable baseline |
| `RecursiveChunker` | Paragraph → line → sentence → character boundaries | You want structure-sensitive local splitting |
| `SemanticChunker` | Embedding cosine-distance boundaries with hard output limits | You want to test topic-aware boundaries |
| `HierarchicalChunker` | Non-overlapping levels with parent/child IDs | Your retriever needs small hits and larger context |
| `ModalityChunker` | Parsed text, table rows, image references, timed audio/video elements | Your parser supplies typed elements |
| `AgenticChunker` | LLM-selected contiguous boundaries, validated before source extraction | You want to test model-driven grouping |

No strategy is claimed to improve retrieval on every corpus. Measure retrieval quality separately.
File parsing, OCR, speech transcription, vector storage, and retrieval are application responsibilities.

## API and source contract

All chunkers implement `chunk(document) -> list[Chunk]` synchronously. There are no automatic network
requests in the local chunkers, and no model is downloaded at import time.

- `Document(id, text=...)` or `Document(id, elements=[...])`: ordered source data. Nonempty text and
  elements cannot be supplied together. IDs are unique within a document.
- `Element`: `text`, `table`, `image`, `audio`, or `video`, with optional URI, page, time bounds and metadata.
- `SourceSpan`: Unicode character offsets `[start, end)`, plus document ID and optional element ID,
  URI, page and time bounds. These are Python string indices, not bytes or grapheme-cluster indices.
- `Chunk`: `text`, `sources`, `strategy`, `config`, `metadata`, `additional_context`, `level`,
  `parent_id` and `child_ids`. Hierarchies return a flat list containing all levels; index the leaves
  or the desired level explicitly.
- `chunk.text` is always the concatenation of the original source spans, with no implicit separators.
  Separate parsed elements are never silently concatenated into a new document-wide offset space.
- Every public dataclass supports `to_dict()`, `from_dict()`, `to_json()`, `from_json()`.
  Metadata must be JSON-serializable. Input metadata is copied into results.
- Stable chunk IDs hash document content/metadata, source ranges, strategy configuration and decisions.
  They are deterministic for deterministic backends; model service responses can vary.

### Tokens

Fixed, Recursive, and Hierarchical support `unit="tokens"` with an injected `Tokenizer`.
Implement `name` and `spans(text)` returning contiguous, positive character ranges covering the
entire string, including whitespace. The library never decodes token IDs to recreate source text.
Each span is one budget unit. Multiple subword tokens sharing the same character must be coalesced
by the adapter; exact provider-token accounting can therefore differ. Token boundaries must remain
consistent when a returned span-aligned substring is tokenized. There is no implicit tokenizer.

### Parsed tables and media

```python
from rosa_chunking import Document, Element, ModalityChunker

document = Document(
    "report",
    elements=[
        Element(
            "table-1", "table", headers=["Item", "Value"], rows=[["A", "1"], ["B", "2"]], page=2
        ),
        Element("figure-1", "image", text="Architecture diagram", uri="asset://figure.png", page=3),
        Element("speech-1", "audio", text="Transcript segment", start_time=2.5, end_time=6.0),
    ],
)
chunks = ModalityChunker(chunk_size=1000).chunk(document)
```

Tables render losslessly as one JSON array per line: header first, then rows. Source offsets refer
to `element.render_text()`. Embedded newlines and separators inside cells remain escaped and
round-trip correctly. Later row groups carry repeated headers in `additional_context`, with a
separate source locator; `embedding_text` combines supplementary headers with original chunk text.
Headers count toward the size budget. `row_start`/`row_end` are zero-based, end-exclusive row indices.

Images and audio/video segments remain atomic. Caption/transcript text may be empty for a URI
reference; a zero-length span still preserves that reference. Time bounds refer to the entire
supplied segment, not inferred word alignment. Tables with an oversized row and oversized media
raise `OversizeError` by default. Use `allow_oversize=True` to keep an atomic element and mark it.
Adjacent media segments are not merged, inspected, transcribed, or sliced into raw media files.

Local text chunkers and Semantic process each element separately; media remain atomic.
Use Modality for table-row preservation. Agentic treats non-text elements as atomic units;
large tables should be pre-grouped or processed through Modality first.

## Model providers

Your own backend can implement either protocol without installing the optional HTTP dependency:

```python
class MyEmbeddings:
    model = "my-embedding-model"

    def embed(self, texts, *, timeout):
        # Return one finite, nonzero, equal-dimension vector per input.
        ...


class MyBoundaries:
    model = "my-boundary-model"

    def boundaries(self, units, *, timeout):
        # Exclusive, strictly increasing ends; the last end must be len(units).
        return [len(units)]
```

For an OpenAI-compatible service, install `[providers]`, configure the API root and explicit model:

```python
import os
from rosa_chunking import SemanticChunker, AgenticChunker
from rosa_chunking.providers import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleBoundaryProvider,
)

settings = {"base_url": os.environ["ROSA_BASE_URL"], "api_key": os.environ["ROSA_API_KEY"]}
embedding = OpenAICompatibleEmbeddingProvider(model=os.environ["ROSA_EMBEDDING_MODEL"], **settings)
boundaries = OpenAICompatibleBoundaryProvider(model=os.environ["ROSA_CHAT_MODEL"], **settings)
semantic_chunks = SemanticChunker(embedding, threshold=0.5).chunk(document)
agentic_chunks = AgenticChunker(boundaries, window_size=8000, max_units=64).chunk(document)
```

The API root includes `/v1` if your service requires it. Adapters use `/embeddings` (float encoding)
and `/chat/completions` (numbered source units; JSON `{"ends": [...]}`). Use `json_mode=False` for
compatible endpoints without `response_format`; valid JSON output is still required. Support for
these endpoints and model options depends on the selected service, not merely its branding.
Reference schemas: [embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
and [chat completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

### Parameters, cost and failure behavior

| Strategy | Main defaults |
| --- | --- |
| Fixed / Recursive | `chunk_size=1000`, `overlap=100`, `unit="characters"` |
| Hierarchical | `chunk_sizes=(2000, 1000, 500)`, no overlap |
| Semantic | `chunk_size=1000`, `threshold=0.5`, `buffer_size=1`, `batch_size=64` |
| Modality | `chunk_size=1000`, no overlap |
| Agentic | `chunk_size=1000`, `window_size=8000`, `max_units=64`, no overlap |

Semantic uses cosine **distance**; a boundary is selected when distance is greater than the
threshold. `buffer_size` is the number of neighboring sentences on each side of each embedding
input. Overlong sentences are subdivided locally first. Embedding inputs can therefore contain
up to `(2 * buffer_size + 1) * chunk_size` characters; configure this for your service limits.
Batch size limits inputs per request, not provider tokens. Blank/single-unit sources need no embeddings.

Both model chunkers default to `timeout=30`, `retries=1`, `max_calls=100`, `fallback="raise"`.
The budget resets for each document, counts retries, and is shared across all windows/batches.
It limits calls, not money or provider token consumption. Semantic embeds overlapping sentence
windows; Agentic sends source units with a system prompt. Those requests may incur service charges.
Agentic window limits measure source characters and unit count, not prompt overhead or tokens.
`provider_calls` is included in result metadata. Configure provider-side token/spending limits too.

HTTP timeouts are applied to each request's network operations; they are not a hard total wall-clock
deadline. Custom providers must honor `timeout`; the library cannot interrupt arbitrary synchronous
Python code safely. Calls are retried immediately up to the configured limit, with no hidden retries
in the HTTP adapter. Errors do not silently change strategy. `fallback="recursive"` explicitly
enables a document-wide fallback and records the requested configuration, exception type and calls.

Agentic validates ordered, complete boundaries before source extraction. LLM decisions exceeding
the output budget are subdivided at source-unit boundaries. Allowed oversized atomic units pass
through without being sent to the model and carry `decision="atomic_passthrough"`.

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
ruff check .
mypy
python examples/six_strategies.py
python benchmarks/run.py --repeat 3
python -m build
python -m twine check dist/*
python scripts/verify_distributions.py
```

Tests normally use local stubs or `httpx.MockTransport`; no model credentials are needed.
Real integration tests require all `ROSA_*` settings plus `ROSA_RUN_INTEGRATION=1`:
`pytest -m integration`. This makes real requests and can incur charges.

See [中文入门](https://github.com/longjl1/rosa-chunking/blob/main/docs/quickstart.zh-CN.md),
[publishing](https://github.com/longjl1/rosa-chunking/blob/main/docs/publishing.md),
[benchmark methodology](https://github.com/longjl1/rosa-chunking/blob/main/benchmarks/README.md),
and [changelog](https://github.com/longjl1/rosa-chunking/blob/main/CHANGELOG.md).
