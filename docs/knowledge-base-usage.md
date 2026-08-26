# Terminal Knowledge Base 使用指南

本文说明如何在终端和 Codex 中使用本知识库进行文献检索、证据核验和论文写作。

## 当前能力

当前知识库包含 PDF 和 Markdown 文档，支持：

- SQLite FTS5 全文检索；
- PDF 页码、citekey 和原文片段定位；
- Markdown 笔记检索；
- BibTeX 引用键生成；
- MCP agent 调用；
- 可选的 lexical + semantic RRF 混合检索；
- DOI/arXiv 元数据自动导入。

当前默认使用稳定的全文检索，配置文件为 `.kb/config.toml`。向量索引数据可以保留，但 `enable_vectors` 是否开启取决于本机 LanceDB 的运行情况。

## CLI 基本用法

```bash
cd ~/knowledge-base

# 查看状态和依赖
./kb status
./kb doctor

# 添加文献或笔记
./kb add ~/Books/paper.pdf --title "Paper title" --author "Doe, Jane" --year 2024
./kb add ~/notes/method.md --title "Method notes"
./kb --json import ~/Books/incoming

# 建立或重建索引
./kb index --all
./kb index --all --force

# 搜索
./kb search "结构化论辩" --limit 5

# 查看文档和具体页码
./kb show <citekey> --page 2
./kb passage --citekey <citekey> --page 2
./kb page-image <citekey> 2 --dpi 150

# 生成论文引用
./kb cite <citekey> --page 2
```

论文中的引用格式建议使用：

```text
[@citekey, p. 2]
```

## 批量导入目录

```bash
find ~/Books/final -type f \( -iname '*.pdf' -o -iname '*.md' \) -print0 |
  while IFS= read -r -d '' file; do
    ./kb add "$file"
  done

./kb index --all --force
```

添加新文件后，必须重新运行 `index`，Codex 才能检索到新内容。

`kb import` 会递归扫描 PDF，读取嵌入 metadata 和首页文本，检测明确 DOI/arXiv ID，查询 Crossref/arXiv，生成 citekey、BibTeX 并索引。可用参数：

```bash
./kb import ~/Books/incoming --dry-run   # 只解析并预览
./kb import ~/Books/incoming --offline   # 不访问网络
./kb import ~/Books/incoming --no-index  # 只复制和登记
```

导入不做标题模糊匹配；无标识符时允许使用本地字段但会标记 `metadata_status=partial`。标识符冲突或远端查询失败的文件不会产生半成品。Crossref 联系地址可配置为 `crossref_mailto`。

## Codex 中的使用方式

MCP 配置加载后，可以直接用自然语言提问。推荐明确要求 Codex：

1. 先搜索知识库；
2. 对关键结果读取精确片段；
3. 给出 citekey 和页码；
4. 区分原文证据与模型推断；
5. 找不到证据时明确说明。

### 文献综述提示词

```text
请只使用 terminal-kb 知识库中的文献。

主题：逻辑论辩中的无穷性。

请：
1. 搜索相关文献；
2. 为每篇文献提取核心问题、方法和结论；
3. 对关键结论读取精确证据片段；
4. 输出文献对比表；
5. 每个结论附上 [@citekey, p. N]；
6. 区分“文献明确指出”和“综合推断”。
```

### 论文段落提示词

```text
基于 terminal-kb 中的证据，帮我写一段关于“结构化论辩中的组合爆炸”的论文正文。

要求：
- 先检索，再读取关键片段；
- 只使用能核实的内容；
- 使用学术中文；
- 引用使用 [@citekey, p. N]；
- 不虚构作者、年份、页码或 DOI；
- 最后列出本段使用的证据。
```

### 论断核验提示词

```text
请核验以下论断：

“有限知识库一定会产生有限的论证集合。”

请：
1. 寻找支持和反驳证据；
2. 列出原文片段、citekey 和页码；
3. 判断论断是成立、部分成立还是无法确认；
4. 找不到证据时不要编造。
```

### 证据矩阵提示词

```text
请为主题“语义压缩是否保持排序信息”建立证据矩阵。

字段：
- claim
- source
- citekey
- page
- supporting passage
- evidence type
- confidence
- open question
```

## 推荐论文工作流

```text
搜索主题
  ↓
读取具体片段
  ↓
建立证据矩阵
  ↓
比较文献观点
  ↓
生成论文提纲
  ↓
写作并添加引用
  ↓
逐条核验引用和页码
```

建议把论文草稿、提纲和证据矩阵保存到 `projects/`，不要直接修改 `library/` 中的原始文献和笔记。

## MCP 提供的工具

`./kb serve-mcp` 通过 stdin/stdout 提供 JSON-RPC 服务。Codex 可以调用：

- `search_library`：检索本地文献；
- `get_passage`：读取带页码的精确片段；
- `get_document`：查看文档状态和元数据；
- `get_page_image`：渲染 PDF 页面核对公式、表格和图形；
- `find_evidence`：根据论断寻找证据；
- `index_status`：查看索引统计。

手动检查 MCP：

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | ./kb serve-mcp
```

## 证据和引用规范

- 不要只根据搜索结果摘要写作，关键论断要读取 `get_passage`；
- 有页码时使用 `[@citekey, p. N]`；
- 公式、表格和图形使用 `get_page_image` 核对；
- 明确区分原文结论、跨文献综合和自己的推断；
- 知识库没有支持时，写“当前资料不足”，不要补造引用。

## 预期结果和限制

知识库适合查找本地证据、制作文献比较表、构建论文提纲和生成带引用草稿。它不能保证 OCR、公式解析或模型推断绝对正确，也不会自动替代人工核对原文。

当前默认检索是全文检索。若启用向量检索，需要在 `.kb/config.toml` 中设置 `enable_vectors = true`，搜索会把 lexical BM25 与 semantic 候选用 RRF 融合，并运行：

```bash
./kb index --all --force
```

首次启用时会下载嵌入模型，CPU 环境推荐 `BAAI/bge-small-zh-v1.5`。

PDF 页码是从 1 开始的物理页码。Docling 使用 `page_break_placeholder` 保留分页；分页数量校验失败时自动回退 `pdftotext -layout`，因此页码不应根据 Markdown 行号推断。
