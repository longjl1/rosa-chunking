"""Independently verify exported chunks and build a bilingual, reproducible report.

The semantic rubric uses authored, source-bound sample reviews, alongside structural checks.
No new model-provider API is called. Existing exports are read without modification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NAMES = ("fixed", "recursive", "hierarchical", "semantic", "modality", "agentic")
LABELS = {
    "fixed": "Fixed-size",
    "recursive": "Recursive",
    "hierarchical": "Hierarchical",
    "semantic": "Semantic (offline stub)",
    "modality": "Modality (raw Markdown)",
    "agentic": "Agentic (offline stub)",
}
WEIGHTS = {"provenance": 30, "tables": 35, "references": 25, "budget": 10}
FINAL_WEIGHTS = {
    "semantic_quality": 50,
    "provenance": 15,
    "tables": 17.5,
    "references": 12.5,
    "budget": 5,
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def union(intervals):
    """Independent interval sweep; no exporter helper is imported."""
    ends = []
    for lo, hi in sorted(intervals):
        if not ends or lo > ends[-1][1]:
            ends.append([lo, hi])
        else:
            ends[-1][1] = max(hi, ends[-1][1])
    return ends


def contains(intervals, lo, hi):
    return any(a <= lo and hi <= b for a, b in union(intervals))


def verify_location(text, location):
    start, end = location["start"], location["end"]
    assert 0 <= start < end <= len(text)
    for offset, key in ((start, "start_position"), (end, "end_position_exclusive")):
        prefix = text[:offset]
        assert location[key] == {
            "line": prefix.count("\n") + 1,
            "column": offset - (prefix.rfind("\n") + 1) + 1,
        }
    assert location["covered_lines"] == [
        text[:start].count("\n") + 1,
        text[: end - 1].count("\n") + 1,
    ]


def evaluate(payload, text, regions):
    all_entries = payload["chunks"]
    leaves = [entry for entry in all_entries if not entry["chunk"]["child_ids"]]
    lookup = {entry["chunk"]["id"]: entry for entry in all_entries}
    assert len(lookup) == len(all_entries), "Duplicate chunk ID"
    for entry in all_entries:
        chunk = entry["chunk"]
        assert chunk["document_id"] == "aster-long-report"
        native = chunk["sources"]
        original = entry["original_sources"]
        assert [(s["start"], s["end"]) for s in native] == [
            (s["start"], s["end"]) for s in original
        ]
        assert all(s["document_id"] == chunk["document_id"] for s in native)
        assert all(s["element_id"] is None for s in native)
        assert chunk["text"] == "".join(text[s["start"] : s["end"]] for s in native)
        for location in original:
            verify_location(text, location)
        if chunk["parent_id"]:
            parent = lookup[chunk["parent_id"]]["chunk"]
            assert chunk["id"] in parent["child_ids"]
            assert chunk["level"] == parent["level"] + 1
            for span in native:
                assert contains(
                    [(s["start"], s["end"]) for s in parent["sources"]],
                    span["start"],
                    span["end"],
                )
        for child_id in chunk["child_ids"]:
            assert lookup[child_id]["chunk"]["parent_id"] == chunk["id"]

    intervals = [
        union([(s["start"], s["end"]) for s in entry["original_sources"]]) for entry in leaves
    ]
    assert union([pair for group in intervals for pair in group]) == [[0, len(text)]]
    cuts = {point for group in intervals for pair in group for point in pair}

    def counts(kind):
        selected = [r for r in regions if r["kind"] == kind]
        whole = sum(any(contains(g, r["start"], r["end"]) for g in intervals) for r in selected)
        cut = sum(any(r["start"] < p < r["end"] for p in cuts) for r in selected)
        return len(selected), whole, cut

    table_n, whole_tables, _ = counts("table")
    row_n, _, cut_rows = counts("table_row")
    ref_n, whole_refs, cut_refs = counts("reference_entry")
    marker_n, _, cut_markers = counts("inline_reference")
    assert (table_n, row_n, ref_n, marker_n) == (3, 16, 4, 4)
    lengths = [len(entry["chunk"]["text"]) for entry in leaves]
    compliant = 0
    for entry in leaves:
        chunk = entry["chunk"]
        config = chunk["config"]
        limit = (
            config["chunk_sizes"][chunk["level"]]
            if "chunk_sizes" in config
            else config["chunk_size"]
        )
        compliant += len(chunk["text"]) <= limit
    all_compliant = all(
        len(entry["chunk"]["text"])
        <= (
            entry["chunk"]["config"]["chunk_sizes"][entry["chunk"]["level"]]
            if "chunk_sizes" in entry["chunk"]["config"]
            else entry["chunk"]["config"]["chunk_size"]
        )
        for entry in all_entries
    )
    assert all_compliant, "This fixture report expects all published budgets to pass"
    score = {
        "provenance": 30.0,
        "tables": 20 * whole_tables / table_n + 15 * (1 - cut_rows / row_n),
        "references": (
            10 * whole_refs / ref_n + 10 * (1 - cut_refs / ref_n) + 5 * (1 - cut_markers / marker_n)
        ),
        "budget": 10 * compliant / len(leaves),
    }
    metrics = {
        "name": payload["name"],
        "total_chunks": len(all_entries),
        "leaf_chunks": len(leaves),
        "intact_tables": whole_tables,
        "table_rows_with_boundary_cuts": cut_rows,
        "intact_reference_entries": whole_refs,
        "reference_entries_with_boundary_cuts": cut_refs,
        "inline_markers_with_boundary_cuts": cut_markers,
        "leaf_budget_compliant": compliant,
        "all_levels_budget_compliant": all_compliant,
        "min_characters": min(lengths),
        "median_characters": statistics.median(lengths),
        "mean_characters": statistics.mean(lengths),
        "max_characters": max(lengths),
        "leaf_redundancy_ratio": sum(lengths) / len(text),
        "all_levels_redundancy_ratio": sum(len(e["chunk"]["text"]) for e in all_entries)
        / len(text),
        "score_components": score,
        "score": sum(score.values()),
        "row_focused_sensitivity_score": (sum(score.values()) - 20 * whole_tables / table_n)
        / 80
        * 100,
        "provider_calls": max(e["chunk"]["metadata"].get("provider_calls", 0) for e in all_entries),
        "config": payload["config"],
        "verified": True,
    }
    for key in (
        "intact_tables",
        "table_rows_with_boundary_cuts",
        "intact_reference_entries",
        "reference_entries_with_boundary_cuts",
        "inline_markers_with_boundary_cuts",
    ):
        assert metrics[key] == payload["statistics"][key], key
    return metrics


def markdown_table(headers, rows):
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows],
        ]
    )


def evaluate_semantic(review, payloads, directory, source_text):
    """Validate authored judgments and compute sample means; never invent fresh ratings."""
    assert review["source_sha256"] == digest(directory / "source.md")
    assert set(review["reviews"]) == set(NAMES)
    anchors = review["anchors"]
    assert len(anchors) == 12 and len({a["id"] for a in anchors}) == 12
    dimensions = review["dimension_weights"]
    assert dimensions == {"coherence": 0.4, "completeness": 0.4, "context": 0.2}
    results, samples = {}, {}
    for anchor in anchors:
        start = source_text.index(anchor["needle"])
        offset = start + 4 if anchor["id"] == "A07" else start + len(anchor["needle"]) // 2
        assert anchor["offset"] == offset
        assert anchor["line"] == source_text[:offset].count("\n") + 1
    for payload in payloads:
        name = payload["name"]
        assert review["exports_sha256"][name] == digest(directory / f"{name}.json"), (
            "Exports changed; semantic judgments require a new review"
        )
        leaves = [entry for entry in payload["chunks"] if not entry["chunk"]["child_ids"]]
        rows = review["reviews"][name]
        assert len(rows) == len(anchors)
        scores, seen, samples[name] = [], {}, []
        for anchor, row in zip(anchors, rows, strict=True):
            entry = next(
                entry
                for entry in leaves
                if any(s["start"] <= anchor["offset"] < s["end"] for s in entry["original_sources"])
            )
            chunk = entry["chunk"]
            assert row["anchor"] == anchor["id"]
            assert row["chunk_index"] == entry["index"] and row["chunk_id"] == chunk["id"]
            assert row["text_sha256"] == hashlib.sha256(chunk["text"].encode()).hexdigest()
            ratings = row["ratings"]
            assert set(ratings) == set(dimensions)
            assert all(type(value) is int and 1 <= value <= 5 for value in ratings.values())
            assert row["rationale_zh"] and row["rationale_en"]
            assert row["leading_excerpt"] == chunk["text"][:110]
            assert row["trailing_excerpt"] == chunk["text"][-110:]
            if chunk["id"] in seen:
                assert seen[chunk["id"]] == ratings, "Same chunk received inconsistent ratings"
            seen[chunk["id"]] = ratings
            score = sum(ratings[key] * weight for key, weight in dimensions.items()) / 5 * 100
            scores.append(score)
            samples[name].append(
                {
                    **row,
                    "score": score,
                    "text": chunk["text"],
                    "original_sources": entry["original_sources"],
                }
            )
        results[name] = {
            "semantic_score": statistics.mean(scores),
            "semantic_dimension_means": {
                key: statistics.mean(row["ratings"][key] for row in rows) for key in dimensions
            },
            "semantic_min_sample_score": min(scores),
            "semantic_sample_anchor_count": len(anchors),
            "semantic_unique_sample_chunks": len(seen),
        }
    return results, samples


def semantic_ledger(review, samples):
    parts = [
        "# Semantic review ledger / 语义评审明细\n",
        "12 purposive source anchors × 6 methods = 72 judgments, covering 68 distinct leaf chunks. This is one nonblinded assistant review, not an exhaustive evaluation or retrieval test. / 12 个定向来源锚点、72 次评分，涉及 68 个不同叶子块。单一助手非盲评，不是全量评审或检索测试。\n",
        "C = thematic coherence / 主题连贯；M = meaning completeness / 语义完整；X = standalone context / 独立上下文。Each is 1–5; score = 20 × (0.4C + 0.4M + 0.2X).\n",
    ]
    for name in NAMES:
        parts.append(f"## {LABELS[name]}\n")
        for row in samples[name]:
            ratings = row["ratings"]
            spans = ", ".join(f"[{s['start']},{s['end']})" for s in row["original_sources"])
            parts.extend(
                [
                    f"### {row['anchor']} — chunk {row['chunk_index']}\n",
                    f"Source / 原文：`{spans}`；ID：`{row['chunk_id']}`。\n",
                    f"C/M/X：**{ratings['coherence']}/{ratings['completeness']}/{ratings['context']}**；sample score / 样本分：**{row['score']:.2f}/100**。\n",
                    row["rationale_zh"] + "\n",
                    row["rationale_en"] + "\n",
                    "Full reviewed chunk / 完整受评块：\n",
                    "````markdown\n"
                    + row["text"]
                    + ("" if row["text"].endswith("\n") else "\n")
                    + "````\n",
                ]
            )
    return "\n".join(parts)


def semantic_report(data, directory, root):
    ranked = sorted(data["results"], key=lambda row: row["final_score"], reverse=True)
    score_table = markdown_table(
        [
            "Rank",
            "Method",
            "Semantic /100",
            "Semantic /50",
            "Source /15",
            "Tables /17.5",
            "Refs /12.5",
            "Budget /5",
            "Total /100",
        ],
        [
            [
                rank,
                LABELS[r["name"]],
                f"{r['semantic_score']:.2f}",
                f"{r['semantic_score'] / 2:.2f}",
                *[f"{r['score_components'][key] / 2:.2f}" for key in WEIGHTS],
                f"{r['final_score']:.2f}",
            ]
            for rank, r in enumerate(ranked, 1)
        ],
    )
    semantic_table = markdown_table(
        [
            "Method",
            "Coherence /5",
            "Completeness /5",
            "Context /5",
            "Mean /100",
            "Lowest sampled /100",
            "Anchors / distinct chunks",
        ],
        [
            [
                LABELS[r["name"]],
                *[
                    f"{r['semantic_dimension_means'][key]:.2f}"
                    for key in ("coherence", "completeness", "context")
                ],
                f"{r['semantic_score']:.2f}",
                f"{r['semantic_min_sample_score']:.2f}",
                f"{r['semantic_sample_anchor_count']} / {r['semantic_unique_sample_chunks']}",
            ]
            for r in ranked
        ],
    )
    anchors = markdown_table(
        ["Anchor", "Diagnostic target / 诊断点", "Character offset", "Line"],
        [[a["id"], a["label"], a["offset"], a["line"]] for a in data["semantic_review"]["anchors"]],
    )
    best = ranked[0]
    summary = ", ".join(f"{LABELS[r['name']]} {r['final_score']:.2f}" for r in ranked)
    return f"""# rosa-chunking — Final report / 六种切分方法最终报告

## 中文版：Semantic Quality 为最大权重

根据你的要求，**Semantic Quality 占总分 50%**，高于其它任何单项。其它项是来源追溯 15%、表格 17.5%、reference 12.5%、大小预算 5%。原有结构质量评分保留为可独立查看的子分，按 50% 计入综合分。

本次综合分：**{summary}**。最高的是 **{LABELS[best["name"]]}（{best["final_score"]:.2f}/100）**，结论仅对应当前样例、默认参数与定向语义抽样。

### 新评分公式与结果

`综合分（百分制） = 0.50 × 语义分（百分制） + 0.50 × 原结构质量分（百分制）`。

语义分由三项 1–5 分组成：主题连贯性 C 占语义分的 40%，语义完整性 M 占 40%，独立上下文 X 占 20%。每个受评块的 `Semantic Quality = 20 × (0.4C + 0.4M + 0.2X)`；对每种方法的 12 个锚点评分取算术平均。因此语义部分对综合分最多贡献 20/20/10 分。1 分表示严重缺陷，转换后语义分的理论范围为 20–100。

{score_table}

### 如何评审语义

- **主题连贯性 C**：5 为单一主题或紧密相关的解释／例子；4 为一个主题带少量相邻残留；3 为两个主题或明显杂项；2 为严重混杂；1 为没有足够内容识别主题。两个领域作为同一个对比例子不自动扣分。
- **语义完整性 M**：5 为论述、代码、表格或条目形成完整单元；4 为少量边缘残留、孤立下一节标题或局部限定缺失；3 为论述／重要限定分离或多个边缘片段；2 为关键单元严重截断；1 为只剩编号、标题或无法识别的片段。
- **独立上下文 X**：5 为读者仅看该块即可解释关键论述／数值；4 为轻微邻块依赖；3 为重要指代或解释需要邻块；2 为缺少关键标签／对象／背景；1 为缺少数值含义或条目身份，不能独立解释。

这是助手阅读后的判断，Python 负责验证评审对象并汇总分数。语义评审含主观性，尚未进行第二评审者一致性检验；不以字符完整性或关键词匹配替代语义判断。各维度仍可能相关，并非三个统计独立变量。

{semantic_table}

### 样本范围和关键实例

使用 12 个原文位置，对每种方法选择**导出顺序中最早包含该位置的叶子块**，完整阅读该块，按独立检索单元评审，不拼接父节点或邻块。共 72 次评分、68 个不同叶子块。同一块命中多个锚点时分数一致，但在每个锚点各计一次；这是对选定来源位置等权，不是对全部 chunk 等权。重叠带来的其它候选块未取“最好版本”。

这些锚点是在前次结构检查之后选出的定向诊断点，覆盖条件、代码、对比、表格、标题、媒体限定、非指令限定和引用。它们没有预注册、不是随机样本，包含已发现的标题碎片问题，因此不能把平均值解释为所有块或真实文档总体的平均语义质量。“最低样本分”也不是全量最差分。

{anchors}

具体例子：Modality 第 13 块把表头、数据和最后数值列为虚构计数的解释放在一起，C/M/X=5/5/5；Hierarchical 第 46 块保留整表但计数解释不在叶子内，5/5/3。Semantic 替身第 11 块只保留字典开头和两个字段，缺 `unit`、闭括号和完整示例限定，5/2/2。Agentic 替身第 44 块只剩 `## 9. `，1/1/1；第 66 块只剩引用标题，4/1/1。这些评语针对输出，不证明某类真实模型的能力高低。

完整的 72 次评语、分数、chunk ID、原文范围和受评全文见 [semantic_review.md]({directory / "semantic_review.md"})；保存的人工式助手判断见 [semantic_review.json]({root / "benchmarks/semantic_review.json"})。新增的汇总指标及评审文件哈希见 [evaluation_metrics.json]({directory / "evaluation_metrics.json"})。结构指标依然全量验证，语义指标仅覆盖上述样本。

Semantic / Agentic 的切分 provider 依然是离线替身；本次语义评审由当前 Codex 助手完成，没有新增外部评审模型 API 调用。模型身份和已知／未知参数见后面的双语参数表。原始切分文件及其配置未改变。这里的 Semantic Quality 是所有六种方法的输出评价，与名为 Semantic 的切分策略不同。

## English version: Semantic Quality has the largest weight

**Semantic Quality now carries 50% of the total**, greater than any other individual dimension. Provenance carries 15%, tables 17.5%, references 12.5%, and budget compliance 5%. The previous structural score remains separately inspectable and contributes the other 50%.

Updated totals: **{summary}**. **{LABELS[best["name"]]} leads at {best["final_score"]:.2f}/100** within this fixture, default configuration and purposive semantic sample.

### Formula and results

`Total (0–100) = 0.50 × Semantic Quality (20–100) + 0.50 × previous structural score (0–100)`.

For each reviewed chunk, score thematic coherence C, meaning completeness M and standalone context X on 1–5 scales. `Semantic Quality = 20 × (0.4C + 0.4M + 0.2X)`. Average the 12 source-anchor judgments per method. Thus coherence, completeness and context can contribute 20/20/10 points to the final total. A rating of 1 denotes a severe defect, so the transformed semantic scale ranges from 20 to 100.

{score_table}

### Judgment criteria

- **Coherence C**: 5 means one theme or closely linked explanation/examples; 4 means minor neighboring remnants; 3 means two themes or conspicuous extraneous material; 2 means serious mixing; 1 means insufficient content to identify a theme. A deliberate two-domain comparison is not automatically penalized.
- **Completeness M**: 5 means a complete argument/code/table/entry unit; 4 means a minor edge remnant, detached following heading or local qualification omission; 3 means separated reasoning/important qualifications or multiple fragments; 2 means a severely truncated key unit; 1 means only a number/title/unidentifiable fragment remains.
- **Standalone context X**: 5 means the reader can interpret the central argument/numbers from the chunk; 4 means minor neighbor dependence; 3 means an important antecedent/explanation is outside; 2 means key labels/object/background are missing; 1 means numbers or entry identity cannot be interpreted independently.

These are assistant reading judgments; Python verifies the reviewed objects and aggregates them. There is one nonblinded reviewer, no inter-reviewer agreement check, and the dimensions may correlate. Character integrity and keyword rules are not substituted for semantic judgment.

{semantic_table}

### Sample scope and concrete evidence

For each of 12 source offsets, select the earliest exported leaf containing that offset, read its full text and judge it as an independent retrieval unit without parent/neighbor expansion. There are 72 judgments covering 68 distinct leaf chunks. A chunk selected by multiple anchors receives identical ratings and counts at each anchor. This weights selected source locations equally, rather than all chunks equally; overlapping alternatives are not optimized by choosing their best version.

The diagnostic anchors were chosen after the previous structural inspection. They are not preregistered or random, and include the previously observed heading fragment. Their averages do not estimate whole-output or real-corpus average quality. The minimum is the lowest reviewed sample, not a claim about the worst of all chunks. The shared anchor table appears above.

Modality chunk 13 retains the complete table and numeric-count explanation: C/M/X=5/5/5. Hierarchical leaf 46 retains the table but lacks that explanation: 5/5/3. Semantic stub chunk 11 contains only the dictionary opening and two fields, missing `unit`, its closing brace and complete framing: 5/2/2. Agentic stub chunk 44 is only `## 9. `: 1/1/1; chunk 66 contains only a reference title: 4/1/1. These judgments concern the outputs, not real model-family capabilities.

The [complete review ledger]({directory / "semantic_review.md"}) includes all 72 bilingual rationales, ratings, chunk IDs, source ranges and full reviewed chunks. [Saved authored judgments]({root / "benchmarks/semantic_review.json"}) are bound to the source/export/chunk hashes, and [verified metrics]({directory / "evaluation_metrics.json"}) record their identity. Structural checks remain exhaustive; semantic judgments cover this sample only.

Semantic and Agentic chunking providers remain offline stubs. The current Codex assistant authored the semantic judgments, with no new external judge-model API call. Its known/unknown model parameters appear in the bilingual metadata table below. Raw exports and chunk configurations are unchanged. Semantic Quality evaluates outputs of all six methods and is distinct from the Semantic chunking strategy.

These scores still do not measure retrieval relevance or answer accuracy. The 500-character hierarchical leaf budget and 1000-character budgets elsewhere remain a comparison limitation. Parent expansion, actual retrieval, real providers and independently labeled questions are untested. 这些分数仍不衡量检索相关性或答案准确率，层级叶子预算更小也仍是比较限制。

"""


def report(data, directory, root):
    rows = data["results"]
    ranked = sorted(rows, key=lambda row: row["score"], reverse=True)
    score_table = markdown_table(
        ["Rank", "Method", "Source /30", "Table /35", "Reference /25", "Budget /10", "Total /100"],
        [
            [
                rank,
                LABELS[r["name"]],
                *[f"{r['score_components'][key]:.2f}" for key in WEIGHTS],
                f"{r['score']:.2f}",
            ]
            for rank, r in enumerate(ranked, 1)
        ],
    )
    metric_table = markdown_table(
        [
            "Method",
            "All / leaf chunks",
            "Whole tables /3",
            "Cut data rows /16",
            "Whole refs /4",
            "Cut refs /4",
            "Cut inline markers /4",
        ],
        [
            [
                LABELS[r["name"]],
                f"{r['total_chunks']} / {r['leaf_chunks']}",
                r["intact_tables"],
                r["table_rows_with_boundary_cuts"],
                r["intact_reference_entries"],
                r["reference_entries_with_boundary_cuts"],
                r["inline_markers_with_boundary_cuts"],
            ]
            for r in rows
        ],
    )
    size_table = markdown_table(
        ["Method", "Min", "Median", "Mean", "Max", "Budget pass", "Leaf text / source"],
        [
            [
                LABELS[r["name"]],
                r["min_characters"],
                r["median_characters"],
                f"{r['mean_characters']:.1f}",
                r["max_characters"],
                f"{r['leaf_budget_compliant']}/{r['leaf_chunks']}",
                f"{r['leaf_redundancy_ratio']:.3f}×",
            ]
            for r in rows
        ],
    )
    sensitivity = markdown_table(
        ["Method", "Main score", "Row-focused score (remove whole-table term)"],
        [
            [LABELS[r["name"]], f"{r['score']:.2f}", f"{r['row_focused_sensitivity_score']:.2f}"]
            for r in ranked
        ],
    )
    config_text = json.dumps({r["name"]: r["config"] for r in rows}, indent=2, ensure_ascii=False)
    links = markdown_table(
        ["Method", "Markdown", "JSON"],
        [
            [
                LABELS[name],
                f"[Full text]({directory / (name + '.md')})",
                f"[Source metadata]({directory / (name + '.json')})",
            ]
            for name in NAMES
        ],
    )
    evaluator_table = markdown_table(
        ["Parameter / 参数", "Recorded value / 记录值"],
        [
            ["Report author / 报告生成者", "Codex — GPT-6-based assistant / 基于 GPT-6 的助手"],
            ["Exact backend model / 确切后端型号", "Not exposed to this session / 本会话未暴露"],
            ["Model snapshot / 模型快照", "Not exposed / 未暴露"],
            [
                "reasoning_effort / 推理强度",
                "Not exposed; no low/medium/high setting inferred / 未暴露，不推测档位",
            ],
            ["temperature, top_p, seed, max_output_tokens", "Not exposed / 未暴露"],
            [
                "Review mode / 评审方式",
                "One nonblinded assistant, 72 anchored semantic judgments + deterministic structural verification / 单一助手非盲语义评审 72 次，结构指标确定性校验",
            ],
            [
                "Independent model judges / 独立模型评委",
                "0; no separate judge-model API request / 未调用独立评审模型",
            ],
            [
                "External benchmark model calls / 外部切分模型调用",
                "0; Semantic and Agentic use offline stubs / 两种模型策略均用离线替身",
            ],
            ["Generated at / 生成时间", data["generated_at"]],
            ["Package / 包版本", data["package_version"]],
            ["Scoring runtime / 评分运行环境", data["python"] + " / " + data["platform"]],
            [
                "Final weights / 综合权重",
                "Semantic 50%, provenance 15%, tables 17.5%, references 12.5%, budget 5% / 语义为最大单项权重",
            ],
            [
                "Structural subscore weights / 结构子分权重",
                "Author-selected 30/35/25/10; not an industry standard / 本报告自定权重，并非行业标准",
            ],
        ],
    )
    return f"""# rosa-chunking — Final report / 六种切分方法最终报告

## 中文版

### 结论与评分范围

本次约 2,000 英文词 Markdown 样例的**结构处理得分**为：Modality **100.00**、Recursive **97.19**、Semantic 离线替身 **93.33**、Hierarchical **86.67**、Fixed-size **70.31**、Agentic 离线替身 **58.12**。这些分数仅评价当前代码、参数和这份文档的输出。

Modality 的原始文本路径完整保留了 3 张表格和 4 条参考条目；Recursive 也把每张表完整保留在至少一个块中，但重叠块仍在 3 个数据行内部出现边界。所有主结果均覆盖完整原文、可逐字符重建，且字符预算全部通过。**100 分表示本报告所选结构检查全部通过，不表示检索或语义质量完美。**

Semantic 的向量由关键词规则产生，Agentic 按每三个源单元归组，均未调用真实模型。两者的排名只能描述替身输出，不能评价实际 embedding 或 LLM 能力。报告没有检索问题集、检索器、相关性标注或答案评估，因此不提供 Recall@k、nDCG 或答案正确率。原结构子分不包含语义连贯性；新增的语义抽样评审见报告前半部分。

### 可复算的质量评分

权重由本报告依据你关注的表格、reference 与来源定位制定；规则适用于六种主结果，计算时不按方法名称加减分。记 T 为完整落在至少一个叶子块中的表格数，D 为至少有一个叶子块边界落在内部的数据行数，R 为完整参考条目数，E 为有内部边界的参考条目数，I 为被内部边界切到的 inline 引用标记数。

- **来源追溯 30 分**：叶子块完整覆盖 10 分；所有块文本等于原文 source spans 拼接 10 分；所有导出位置与原生位置一致，字符范围及一基行列定位校验通过 10 分。本次三项全部通过；脚本发现无效来源时会停止评分，避免给损坏导出排名。稳定 ID 跨运行检验不属于此评分。
- **表格 35 分**：`20 × T/3 + 15 × (1 − D/16)`。既奖励整表共存，也惩罚行内切分。此项偏向需要单块表格上下文的任务。
- **参考文献 25 分**：`10 × R/4 + 10 × (1 − E/4) + 5 × (1 − I/4)`。只评价条目和标记的文本边界，不验证虚构链接，不评价引用是否支持论断，也不奖励文内标记与 bibliography 的关联检索。
- **大小预算 10 分**：`10 × 合规叶子块数 / 叶子块总数`。非层级策略最大 1000 字符；层级叶子最大 500。另行检查全部层级的预算与父子包含关系，均通过。

存在 overlap 时，“某条目在一块中完整”与“另一块切入该条目”可以同时成立。分数以未舍入数值计算后显示两位小数；分项舍入相加可能相差 0.01。层级仅以叶子块参与结构子评分，父节点保留在导出中。

{score_table}

### 实际切分结果

{metric_table}

下面的长度为 Unicode 字符数，包含原始 Markdown 标记与空白。重复比例为叶子块文本总长度除以原文长度，不是 token 数、存储大小或模型费用；1.000× 表示无源文本重复。

{size_table}

### 各方法评语与可定位实例

1. **Modality — 100.00/100**：主对比输入是 `Document.text`，执行 recursive 文本路径且 overlap=0，并未解析表格。第 4、13、18 块分别包含 T1、T2、T3；第 21 块包含 R1–R4。当前表格较短，三个整表恰好都能装入预算；不能从此推断所有 Markdown 表格都会被识别或保护。
2. **Recursive — 97.19/100**：第 5、15、22 块分别包含整表，第 25 块保留全部参考条目；第 6、16、23 块又部分命中表格。其源文本重复与行内边界说明 overlap 能提供邻接上下文，也会制造部分表格副本。当前扣分不代表原表数据丢失。
3. **Semantic 离线替身 — 93.33/100**：第 6、24 块保留 T1、T3；T2 分布于第 18、19 块，数据行未被切开，但未重复表头。第 27 块保留 R1–R4。关键词玩具向量不能证明这些边界符合主题变化。
4. **Hierarchical — 86.67/100**：输出 74 块，其中叶子 43 块。T2 在叶子第 46 块完整；T1 分到第 15、16 块，T3 分到第 64、65 块，行均未切断。R1/R2 在第 73 块，R3/R4 在第 74 块。父子 ID 和来源包含关系校验通过，可用于向父节点展开上下文；本次未模拟检索这种展开。500 字符叶子预算比其它方法更小，整表项会相应扣分。
5. **Fixed-size — 70.31/100**：三个表格都跨块，5/16 数据行出现内部边界。第 16 块包含完整 R1、部分 R2；第 17 块包含部分 R1、完整 R2–R4，所以四条参考条目均有完整版本，但 R1/R2 同时存在被截断的版本。作为机械切分基线清晰可复现，本次结构边界表现较弱。
6. **Agentic 离线替身 — 58.12/100**：生成 68 块，三个表均跨块，2/16 数据行有内部边界；第 63–68 块把 bibliography 拆散，没有一条参考条目完整落在一个块中。第 44 块仅为 6 字符的 `## 9. `，原文范围 `[9779,9785)`、第 82 行，标题剩余文字在另一块。标题完整性未纳入评分，但这个实例说明了单元归组造成的碎片。按三单元归组的规则没有 table/reference 感知能力；此结果不证明真实 Agentic 方法弱于其它策略。

块编号与各导出文件一致，Hierarchical 编号采用包含父节点的完整列表索引。实例说明属于本次审阅，不是额外的主观打分。

### 权重敏感性与补充表格示例

若关注“行不断开”，而允许表格跨块，将结构子分中整表共存的 20 分移除后，把剩余 80 分归一化为 100，结果如下。Hierarchical 与 Semantic 会和 Modality 并列：结构子分排名依赖所选任务和默认预算，不能作为通用策略排行榜。此表没有重新计算语义占 50% 的综合分。

{sensitivity}

额外的 [结构化 Modality 示例]({directory / "modality_structured_350.md"}) 使用已解析 `Element` 与 350 字符预算：66 块、0 个数据行内边界、5 处重复表头上下文。它展示按行分组，并以 `additional_context` / `original_context_sources` 单独标记重复表头。它与主比较的输入、预算不同，因此不加入六方法排名。

此适配器仅处理本 fixture 的简单 pipe table；规范表格内容用 JSON-array 行表示，其来源关联到原始 Markdown 整行。规范字符索引与原始字符索引不能互换。主结果来源是原文 Unicode `[start,end)`，行列从 1 开始；字符位置不是 UTF-8 字节或可见字素的位置。

### 本报告支持的决定与尚未验证的质量

在当前文档与默认参数下，需要完整 table/reference 和可重建文本时，Modality 原始文本路径表现最好；Recursive 适合同时保留邻接重叠上下文，但使用方应处理部分表格副本。需要强制表格行和重复表头语义时，应采用已解析元素路径。这里的选择来自当前本地结果，无真实检索效果承诺。

后续若要比较检索质量，应使用相同叶子预算与明确的 overlap 政策、真实 embedding/LLM provider、包含跨表格／引用问题的标注问答集以及固定检索器，记录 Recall@k、nDCG、答案证据定位、调用耗时和 token 用量。当前没有延迟测量或付费 provider 用量，不给出性价比得分。只有一份合成文档、3 张表、16 个数据行与4条引用，分数没有统计置信区间。

## English version

### Verdict and evaluation scope

For this approximately 2,000-English-word Markdown fixture, the **structural handling scores** are Modality **100.00**, Recursive **97.19**, Semantic offline stub **93.33**, Hierarchical **86.67**, Fixed-size **70.31**, and Agentic offline stub **58.12**. These scores describe the current implementation, configuration and document outputs.

Modality's raw-text path retains all three tables and four bibliography entries intact. Recursive also retains each entire table in at least one chunk, but overlap introduces boundaries inside three data rows. All main outputs cover the complete original source, reconstruct exactly from their spans and meet their character budgets. **100 means all selected structural checks passed; it does not establish perfect retrieval or semantic quality.**

Semantic uses keyword-derived toy vectors; Agentic groups every three source units. Neither calls a real chunking model, so their scores evaluate stub outputs only. No retrieval question set, retriever, relevance judgments or answer evaluation was used. Recall@k, nDCG and answer accuracy remain unmeasured. The structural subscore excludes semantic coherence; the new semantic sample assessment appears earlier in this report.

### Reproducible scoring rubric

The report author selected these weights around tables, references and provenance; they are not an industry standard. The same rules apply to all six main results, with no method-specific bonuses. T counts tables wholly contained in at least one leaf chunk; D counts data rows with any interior leaf-chunk boundary; R counts complete bibliography entries; E counts entries with an interior boundary; I counts inline markers with an interior boundary.

- **Provenance /30**: complete leaf coverage /10; exact reconstruction of every chunk from source spans /10; agreement of native and exported offsets plus validated character/one-based line/column coordinates /10. All three pass here; invalid provenance stops the script instead of ranking corrupted exports. Stable IDs across reruns are outside this score.
- **Tables /35**: `20 × T/3 + 15 × (1 − D/16)`. This rewards entire-table context and unbroken rows, and favors tasks that need a table in one chunk.
- **References /25**: `10 × R/4 + 10 × (1 − E/4) + 5 × (1 − I/4)`. This measures text boundaries only. Fictional links are not verified; citation support and retrieval association between an inline marker and its bibliography entry are not scored.
- **Budget /10**: `10 × compliant leaf chunks / all leaf chunks`. Nonhierarchical budgets are 1000 characters; hierarchical leaves use 500. All levels' budgets and parent/child containment are also checked and pass.

With overlap, an entry may be complete in one chunk and cut in another. Totals use unrounded values and display two decimals, so displayed components may differ by 0.01 when added. Only leaves enter hierarchical scoring; detailed exports retain parents.

{score_table}

### Observed results and output sizes

{metric_table}

Lengths include original Markdown syntax and whitespace. The ratio is total leaf text length divided by source length; it is not token usage, storage size or model cost. A ratio of 1.000× means no source-text duplication.

{size_table}

### Assessment of each method

1. **Modality — 100.00/100**: the main input is `Document.text`; the implementation uses its recursive text path with overlap=0, without parsing tables. Chunks 4, 13 and 18 contain T1, T2 and T3 respectively; chunk 21 contains R1–R4. These relatively short tables fit the budget. This does not establish automatic Markdown-table recognition or protection for arbitrary tables.
2. **Recursive — 97.19/100**: chunks 5, 15 and 22 contain whole tables, and chunk 25 contains all bibliography entries. Chunks 6, 16 and 23 also intersect tables partially. Overlap supplies neighboring context while producing partial table copies; the deduction does not imply source-data loss.
3. **Semantic offline stub — 93.33/100**: chunks 6 and 24 preserve T1 and T3. T2 spans chunks 18 and 19 without cutting data rows, but headers are not repeated. Chunk 27 retains R1–R4. Toy vectors do not establish topical correctness of these boundaries.
4. **Hierarchical — 86.67/100**: 74 total chunks include 43 leaves. Leaf chunk 46 contains T2; T1 spans chunks 15/16 and T3 spans 64/65 without breaking rows. R1/R2 appear in chunk 73 and R3/R4 in 74. IDs and parent containment pass, supporting potential parent-context expansion; retrieval with expansion was not tested. The 500-character leaf budget is smaller than other methods and affects whole-table scoring.
5. **Fixed-size — 70.31/100**: all three tables span chunks, and 5/16 rows have interior boundaries. Chunk 16 contains full R1 and partial R2; chunk 17 contains partial R1 and full R2–R4. Every entry has a complete version, but R1/R2 also have truncated copies. It is a clear mechanical baseline with weaker structural boundaries on this fixture.
6. **Agentic offline stub — 58.12/100**: 68 chunks split all tables, cut 2/16 data rows, and fragment every bibliography entry across chunks 63–68. Chunk 44 is only the six-character `## 9. ` at source `[9779,9785)`, line 82; the rest of the heading is in another chunk. Heading integrity is outside the score, but this example illustrates fragmentation from unit grouping. Grouping every three source units is not table/reference-aware. This does not show that real LLM-driven Agentic chunking is inferior.

Chunk numbers match the export files; hierarchical numbers use the complete list, including parents. These observations explain the mechanical score rather than adding subjective points.

### Weight sensitivity and parsed-table supplement

For row-focused tasks that allow a table to span chunks, remove the 20-point whole-table term and normalize the remaining 80 points to 100:

{sensitivity}

Hierarchical and Semantic then tie with Modality on this structural variant. Structural ranking depends on task weights and default budgets. This table does not recompute the new semantic-weighted final score.

The supplementary [parsed Modality example]({directory / "modality_structured_350.md"}) uses parsed elements and a 350-character budget: 66 chunks, no interior data-row cuts, and five repeated-header contexts. Repeated headers and their origins are stored separately in `additional_context` and `original_context_sources`. Different input and budget exclude this example from the six-way ranking.

Its adapter supports only the fixture's simple pipe tables. Canonical JSON-array lines map to whole original Markdown rows; canonical and original character indices are not interchangeable. Main results use original Unicode `[start,end)` offsets and one-based lines/columns, not UTF-8 byte or grapheme offsets.

### Supported decision and remaining evaluation

For this document and default settings, Modality's raw-text path best preserves complete tables/references with reconstructable text. Recursive also provides overlapping neighboring context, with partial table copies to handle. Explicit table-row and repeated-header behavior requires parsed elements. These choices derive from local output evidence and make no retrieval-quality promise.

A retrieval comparison should use equal leaf budgets, an explicit overlap policy, real embedding/LLM providers, labeled questions involving tables and citations, and a fixed retriever. Measure Recall@k, nDCG, answer-evidence location, latency and token usage. This report has no latency measurements or paid-provider usage and assigns no cost-efficiency score. One synthetic document, three tables, sixteen rows and four references provide no statistical confidence interval.

## Model, reasoning and execution parameters / 模型、推理与执行参数

The report-writing assistant and the chunking providers are separate. Unavailable assistant settings are recorded as unavailable rather than inferred from prose quality or the app's supported-model list. 报告生成助手与切分 provider 是不同对象；没有暴露的会话参数按未知记录，不由输出风格或可选模型列表推测。

{evaluator_table}

Offline Semantic returns `[1.0, 0.1]` when a window contains the substring `chunk` (case-insensitive), otherwise `[0.1, 1.0]`. Offline Agentic returns group ends at 3, 6, 9, … and the final unit. These are deterministic rules with no sampling temperature or reasoning setting. Semantic reports {next(r["provider_calls"] for r in rows if r["name"] == "semantic")} internal stub calls; Agentic reports {next(r["provider_calls"] for r in rows if r["name"] == "agentic")} internal stub calls. They are not external API calls. 两个替身均为确定性规则，无采样或推理强度设置；内部调用次数不等于外部 API 请求。

Full exported configurations / 完整导出参数：

```json
{config_text}
```

All methods use characters, no tokenizer and `allow_oversize=false`. Fixed and Recursive overlap=100; all others overlap=0. Hierarchical budgets are 2000/1000/500. Model strategies set timeout=30s, retries=1, max_calls=100, fallback=raise. Semantic uses threshold=0.5, buffer_size=1 and batch_size=64. Agentic uses window_size=8000 and max_units=64. 所有参数以导出 JSON 为准；timeout 是配置预算，不是测得耗时。

## Evidence and reproduction / 证据与复现

Source: [source.md]({directory / "source.md"}), {data["source"]["characters"]:,} Unicode characters; {data["source"]["whitespace_words"]:,} whitespace-separated tokens, including Markdown syntax, table cells and references. The fixture was written as an approximately 2,000-English-word report. 表格、测量和引用均为合成内容，不能作为真实研究证据。

Source SHA-256 / 原文哈希：`{data["source"]["sha256"]}`。

{links}

- [Structure regions / 结构区域]({directory / "regions.json"})
- [Export manifest / 导出清单]({directory / "manifest.json"})
- [Verified metrics, input hashes and scoring metadata / 校验指标、输入哈希和评分元数据]({directory / "evaluation_metrics.json"})
- [Scoring implementation / 评分代码]({root / "benchmarks/score_chunks.py"})
- [Export implementation / 导出代码]({root / "benchmarks/export_chunks.py"})

From the repository root / 在仓库根目录运行：

```bash
.venv/bin/python benchmarks/score_chunks.py
```

The scorer independently sweeps intervals, reconstructs every chunk, checks every source coordinate and all hierarchy links, recalculates structural counts and asserts agreement with the exports. It does not import the exporter's interval/count helpers or call any model API. It saves hashes for all six JSON inputs, the region annotations and source snapshot; no raw exports are overwritten. 评分脚本独立复算，不复用导出器的覆盖或计数函数，不修改原导出。
"""


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exports", type=Path, default=root / "benchmarks/exports/long-markdown")
    parser.add_argument("--report", type=Path, default=root / "final report.md")
    parser.add_argument(
        "--semantic-review", type=Path, default=root / "benchmarks/semantic_review.json"
    )
    args = parser.parse_args()
    directory = args.exports.resolve()
    source_path = directory / "source.md"
    text = source_path.read_bytes().decode("utf-8")
    manifest = json.loads((directory / "manifest.json").read_text())
    assert digest(source_path) == manifest["source"]["sha256"]
    regions = json.loads((directory / "regions.json").read_text())
    for region in regions:
        verify_location(text, region["location"])
        assert region["start"] == region["location"]["start"]
        assert region["end"] == region["location"]["end"]
        value = text[region["start"] : region["end"]]
        if region["kind"] == "table_row":
            assert value.startswith("|") and value.endswith("|")
        elif region["kind"] == "reference_entry":
            assert value.startswith(f"- **[{region['id']}]**")
        elif region["kind"] == "inline_reference":
            assert value == f"[{region['reference_id']}]"
    payloads = [json.loads((directory / f"{name}.json").read_text()) for name in NAMES]
    assert all(p["source"]["sha256"] == digest(source_path) for p in payloads)
    authored_review = json.loads(args.semantic_review.read_text())
    semantic_results, samples = evaluate_semantic(authored_review, payloads, directory, text)
    results = [evaluate(p, text, regions) for p in payloads]
    for result in results:
        result.update(semantic_results[result["name"]])
        result["final_score"] = result["semantic_score"] / 2 + result["score"] / 2
    (directory / "semantic_review.md").write_text(
        semantic_ledger(authored_review, samples), encoding="utf-8"
    )
    (directory / "semantic_review_inputs.json").write_text(
        json.dumps(
            {"anchors": authored_review["anchors"], "results": samples},
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    data = {
        "generated_at": datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds"),
        "source": manifest["source"],
        "package_version": payloads[0]["package_version"],
        "python": platform.python_version(),
        "platform": platform.platform(),
        "evaluator": {
            "identity": "Codex, GPT-6-based assistant",
            "exact_model": None,
            "model_snapshot": None,
            "reasoning_effort": None,
            "temperature": None,
            "top_p": None,
            "seed": None,
            "max_output_tokens": None,
            "unknown_reason": "Not exposed by current session instructions or tool metadata",
            "independent_judge_api_calls": 0,
        },
        "weights": FINAL_WEIGHTS,
        "structural_weights": WEIGHTS,
        "semantic_review": {
            "reviewer": authored_review["reviewer"],
            "review_date": authored_review["review_date"],
            "mode": authored_review["review_mode"],
            "anchors": authored_review["anchors"],
            "selection_rule": authored_review["selection_rule"],
            "dimension_weights": authored_review["dimension_weights"],
            "judgments": sum(len(items) for items in samples.values()),
            "distinct_sample_chunks": sum(
                len({row["chunk_id"] for row in items}) for items in samples.values()
            ),
        },
        "results": results,
        "evidence_sha256": {
            name: digest(directory / name)
            for name in [
                "source.md",
                "regions.json",
                "manifest.json",
                *[f"{n}.json" for n in NAMES],
            ]
        },
    }
    data["evidence_sha256"]["benchmarks/semantic_review.json"] = digest(args.semantic_review)
    data["evidence_sha256"]["benchmarks/score_chunks.py"] = digest(Path(__file__))
    assert sum(FINAL_WEIGHTS.values()) == 100
    assert FINAL_WEIGHTS["semantic_quality"] > max(
        value for key, value in FINAL_WEIGHTS.items() if key != "semantic_quality"
    )
    (directory / "evaluation_metrics.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    structural_appendix = "\n".join(
        "#" + line if line.startswith("#") else line
        for line in report(data, directory, root).splitlines()[1:]
    )
    args.report.write_text(
        semantic_report(data, directory, root)
        + "\n## Structural subscore appendix / 结构子分附录\n\n"
        + "The following scores retain the original structural rubric and are not the final totals. 以下评分保留原结构口径，均为结构子分，综合分见前文。\n"
        + structural_appendix,
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(args.report.resolve()),
                "scores": {r["name"]: round(r["final_score"], 2) for r in data["results"]},
                "verified": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
