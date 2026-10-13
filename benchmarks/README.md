# Benchmark methodology

`fixtures/bilingual.txt` is original English/Chinese sample text distributed under this repository's
MIT license. No third-party corpus is copied. It becomes a public fixture when this repository is published.

Run `python benchmarks/run.py --repeat 3 --copies 20`. The JSON output records Python/platform,
fixture SHA-256, input length, configuration, elapsed samples, median time, chunk count, sizes and
coverage. Use `--output benchmark-results.json` to save an ignored local report.

For full chunk exports of the long English Markdown document, use:

```bash
.venv/bin/python benchmarks/export_chunks.py \
  --fixture benchmarks/fixtures/test.md \
  --output-dir benchmarks/exports/long-markdown
```

`test.md` is an original approximately 2,000-word fictional technical report,
with headings, three Markdown tables, a code block, an untrusted-instruction test sentence,
inline reference markers and four clearly labeled fake references using `example.invalid`.
Its numbers and citations are synthetic, with no claims of observed research results.
Both fixtures are distributed under this repository's MIT license.

The export command's `--fixture` accepts a UTF-8 text/Markdown path. Reports include the fixture
name, SHA-256 and input whitespace-separated word count. Markdown is passed as raw text: these
tables exercise textual boundaries, while table-row preservation requires parsed `Element` inputs.

All six strategies run on the same source. Hierarchical counts include every level; coverage is
measured at leaf level. Semantic and Agentic use deterministic **offline stubs** with no network.
Their timings do not measure real embedding inference, LLM latency or fees. Modality's text path
is timed here; table/media behavior is covered by dedicated tests and examples.

This is an inspectable mechanics/latency baseline, not a retrieval-quality benchmark. Retrieval
Recall@k, relevance labels, generation quality and provider costs require a separate representative
corpus, questions, ground truth and a fixed retriever. No strategy wins by default.

## Export full chunks and source locations

The current long fixture is `fixtures/test.md`. Export all six strategies using its current contents:

```bash
.venv/bin/python benchmarks/export_chunks.py \
  --fixture benchmarks/fixtures/test.md \
  --output-dir benchmarks/exports/long-markdown
```

Open `comparison.md` first. Each strategy has a Markdown full-text export and JSON containing the
native chunk, original character offsets, one-based lines/columns, table/reference intersections,
configuration and parent/child links. `source.md` is an exact source-byte snapshot with a SHA-256
in `manifest.json`. Complete coverage and source reconstruction are asserted during export.
Hierarchy comparisons use leaf nodes; all levels remain in the detailed exports. Annotation detects
structure after chunking; it does not change algorithm decisions. Semantic/Agentic use offline
stubs and are clearly marked throughout.
The same files are bundled in `benchmarks/exports/long-markdown.zip` for convenient download.

An additional `modality_structured_350` export demonstrates parsed table-row grouping and repeated
headers with a smaller 350-character budget. Its simple fixture adapter is not a general Markdown
parser. Table source offsets refer to canonical JSON-array lines; a separate row-level origin map
links them to original Markdown. It is supplementary, not part of the same-input six-strategy comparison.
Generated exports are ignored by Git and can be regenerated with the command above.

## Score the current exports

Run `.venv/bin/python benchmarks/score_chunks.py` after exporting. This verifies the six JSON
results independently and writes the bilingual `final report.md` in the repository root plus
`evaluation_metrics.json` beside the exports. The report records source/input hashes, configurations,
structural scores and weight sensitivity. Semantic Quality has the largest final-score weight (50%);
the other weights are provenance 15%, tables 17.5%, references 12.5% and budget 5%. The authored,
nonblinded semantic review uses 12 shared source anchors per method, covering 72 judgments and
68 distinct leaf chunks; it is a purposive diagnostic sample, not an exhaustive semantic average.
`semantic_review.json` binds its bilingual ratings and rationales to source, export and chunk hashes.
Changed exports require a new review: the scorer will reject stale judgments. Full sampled texts
and rationales are written to `semantic_review.md` and `semantic_review_inputs.json` beside the exports.
Exact assistant-model/sampling/reasoning settings that
are unavailable to the author are explicitly recorded as unknown. Scores are fixture-specific;
real retrieval quality and real model-provider performance are not measured.
