# rosa-chunking 中文入门

这是面向开发者的 chunking 库，当前版本为 `0.1.0a1`。核心功能不依赖模型 SDK。
仓库准备好发布不等于已上传 PyPI；目前先从本地安装。

## 1. 安装与运行

在项目根目录，使用 Python 3.11 或更新版本：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python examples/six_strategies.py
```

如果本机只有系统 Python 3.9，需要另装 3.11+，不用修改系统 Python。
公开 alpha 发布后安装 `pip install --pre rosa-chunking`；正式版本发布后安装 `pip install rosa-chunking`。

## 2. 第一个 chunk

```python
from rosa_chunking import Document, RecursiveChunker

doc = Document(id="manual", text="第一节。这里是介绍。\n\n第二节。这里是详细内容。")
chunks = RecursiveChunker(chunk_size=20, overlap=3).chunk(doc)
for chunk in chunks:
    print(chunk.text)
    print(chunk.sources[0].start, chunk.sources[0].end)
    assert chunk.text == "".join(span.extract(doc) for span in chunk.sources)
```

`chunk_size` 默认按字符计量，Unicode 位置遵循 Python 字符串索引，不是字节位置。
不能把参数缩小到默认 overlap 以下：例如 `chunk_size=20` 时需显式设置 `overlap<20`。
token 模式需注入保留原文字符边界的 tokenizer；不会自动加载或下载 tokenizer。

## 3. 六种策略怎么选

| 策略 | 适用场景 | 需要模型吗 |
| --- | --- | --- |
| Fixed-size | 建立可预测的基线，固定大小滑动窗口 | 否 |
| Recursive | 优先尊重段落、换行和中英文句子边界 | 否 |
| Hierarchical | 小块检索、大块补充上下文 | 否 |
| Semantic | 根据相邻句子窗口的 embedding 距离切分 | 是，或自定义本地实现 |
| Modality-specific | 已解析的表格、图片引用、音视频片段 | 否 |
| Agentic | 让 LLM 决定连续内容在哪里断开 | 是，或自定义实现 |

Hierarchical 返回所有层级的平铺列表，可用 `level` 筛选；父子节点通过 ID 关联。
不要直接把所有层都当作互不重复的文本。没有任何一种策略预先保证提升检索效果。

## 4. 多模态输入与来源

`Document` 接收纯文本或有序 `Element` 列表。元素类型为 text、table、image、audio、video。
库不进行 PDF 解析、OCR、转录或场景识别。

- 表格用 `headers` 和 `rows` 表达，单元格为字符串；规范文本是每行一个 JSON 数组。
  位置引用 `Element.render_text()`。长表按行分组，后续块把表头放入 `additional_context`，
  `embedding_text` 可用于检索输入；表头计入大小预算。
- 图片用 URI 和可选 caption 表达，音视频用转录／描述、URI、可选片段起止时间表达。
  保留整个输入片段，时间位置不等于逐字对齐。
- 不可分割元素超预算默认抛出 `OversizeError`；显式 `allow_oversize=True` 后原样保留并标记。
- `Chunk.text` 始终来自源范围，额外表头单独标记；元数据、页码、URI 和时间定位保留在结果中。
- Agentic 把非文本元素当作原子单元；大表应先用 Modality 处理或在上游预分组。

## 5. 接入模型

安装 `pip install '.[providers]'`，再设置服务参数。不要把真实密钥写入源码或 Git。
模型名由你的服务决定，库不替你选择模型。可参考 [英文 README](../README.md#model-providers)。

适配器调用 `/embeddings` 和 `/chat/completions`，base URL 包含服务要求的 `/v1`。
LLM 返回形如 `{"ends":[2,4]}` 的连续分组结束编号，不能重写原文。输出需通过完整性校验。
不支持 JSON mode 的兼容端点可设置 `json_mode=False`，但仍需返回有效 JSON。

默认每个文档最多 100 次调用，失败重试 1 次，HTTP 网络操作超时为 30 秒。
调用预算包含重试，不等于费用预算；句子上下文窗口和系统提示词也会消耗 token。
自定义同步 provider 必须遵守传入的 timeout，库无法安全强制中断任意 Python 函数。
失败默认抛出异常；配置 `fallback="recursive"` 才会整篇回退，并记录原因和调用次数。

## 6. 验证和发布

运行 `pytest -q`、`ruff check .`、`mypy`，再执行构建、twine 检查和独立安装验证。
六种策略的离线示例不调用真实模型，基准结果也不代表真实模型的速度或检索质量。
正式发布步骤见 [发布指南](publishing.md)，账户绑定完成前不会自动上传。

