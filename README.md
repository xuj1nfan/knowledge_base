# Terminal Knowledge Base

一个只面向终端的本地知识库，用于管理 PDF、Markdown 笔记和论文写作证据。它不依赖 Obsidian 或 Zotero，可以直接通过 CLI、脚本和 MCP agent 使用。

## 特性

- SQLite FTS5 全文检索，支持中文和英文
- PDF 按页解析，返回 `citekey`、页码和原文片段
- Markdown 笔记递归导入，保留稳定 citekey
- BibTeX 书目文件和研究草稿目录
- JSON-RPC over stdio MCP server，可接入 Codex 等终端 agent
- 可选 LanceDB + Sentence Transformers 向量索引
- 所有索引和解析结果均为本地可重建文件

## 环境要求

- Linux/macOS
- Python 3.11+
- `pdftotext`、`pdfinfo`、`pdftoppm`（推荐安装 `poppler`）

基础全文检索不需要额外 Python 依赖。推荐使用 Python 3.12 虚拟环境和 [uv](https://docs.astral.sh/uv/)。

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

可选依赖：

```bash
# 向量检索（CPU 环境）
uv pip install --python .venv/bin/python lancedb sentence-transformers

# 更复杂的 PDF 版面、表格和公式解析
uv pip install --python .venv/bin/python docling
```

非N卡：

```bash
uv pip install --python .venv/bin/python \
  torch==2.6.0+cpu \
  --index-url https://download.pytorch.org/whl/cpu
```

## 快速开始

```bash
./kb init
./kb add ~/Books/paper.pdf --title "Paper title" --author "Doe, Jane" --year 2024
./kb add ~/notes/method.md --title "Method notes"
./kb index --all
./kb search "retrieval augmented generation" --limit 5
```

常用命令：

```bash
./kb status
./kb doctor
./kb show <citekey> --page 2
./kb passage --citekey <citekey> --page 2
./kb cite <citekey> --page 2
./kb page-image <citekey> 2 --dpi 150
```

论文引用格式为：`[@citekey, p. 2]`。

## 导入现有目录

`kb add` 可以逐个添加文件。批量导入时可使用 shell：

```bash
find ~/Books/final -type f \( -iname '*.pdf' -o -iname '*.md' \) -print0 |
  while IFS= read -r -d '' file; do
    ./kb add "$file"
  done
./kb index --all --force
```

## MCP agent 接入

`serve-mcp` 使用 stdin/stdout 传输 JSON-RPC，不需要额外 MCP SDK：

```toml
[mcp_servers.terminal_kb]
command = "/absolute/path/to/knowledge-base/kb"
args = ["--root", "/absolute/path/to/knowledge-base", "serve-mcp"]
```

提供的工具包括：

- `search_library`：搜索 PDF 和 Markdown 证据
- `get_passage`：取得带页码的精确片段
- `get_document`：查看文档元数据和状态
- `get_page_image`：渲染 PDF 页面核对公式、表格和图形
- `find_evidence`：按论断寻找证据
- `index_status`：查看索引状态

## 向量检索

向量索引是可选功能，在 `.kb/config.toml` 中启用：

```toml
enable_vectors = true
embedding_model = "BAAI/bge-small-zh-v1.5"
```

然后重建：

```bash
./kb index --all --force
```

首次运行会从 Hugging Face 下载模型。



## 验证

```bash
./kb doctor
.venv/bin/python -m unittest discover -s tests -v
```
