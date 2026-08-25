from __future__ import annotations

import json
import base64
import sys
from typing import Any

from .core import Config, get_document, get_passage, page_image, search, status


TOOLS = [
    {"name": "search_library", "description": "Search indexed PDF and Markdown evidence.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 8}, "citekey": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_passage", "description": "Retrieve an exact indexed passage with citekey and page metadata.", "inputSchema": {"type": "object", "properties": {"chunk_id": {"type": "integer"}, "citekey": {"type": "string"}, "page": {"type": "integer"}}}},
    {"name": "get_document", "description": "Retrieve document metadata and index status.", "inputSchema": {"type": "object", "properties": {"citekey": {"type": "string"}}, "required": ["citekey"]}},
    {"name": "get_page_image", "description": "Render a PDF page for visual verification of formulas, tables, and figures.", "inputSchema": {"type": "object", "properties": {"citekey": {"type": "string"}, "page": {"type": "integer"}, "dpi": {"type": "integer", "default": 150}}, "required": ["citekey", "page"]}},
    {"name": "find_evidence", "description": "Find passages relevant to a claim; verify passages before citing.", "inputSchema": {"type": "object", "properties": {"claim": {"type": "string"}, "limit": {"type": "integer", "default": 8}}, "required": ["claim"]}},
    {"name": "index_status", "description": "Show local catalog and index status.", "inputSchema": {"type": "object", "properties": {}}},
]


def result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]}


def image_result(path: str) -> dict[str, Any]:
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return {"content": [{"type": "image", "data": encoded, "mimeType": "image/png"}, {"type": "text", "text": path}]}


def handle(root: Config, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized" or method == "notifications/cancelled":
        return None
    if method == "initialize":
        value = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "terminal-kb", "version": "0.1.0"}}
    elif method == "ping":
        value = {}
    elif method == "tools/list":
        value = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "search_library":
            value = result(search(root, args["query"], int(args.get("limit", 8)), args.get("citekey")))
        elif name == "get_passage":
            value = result(get_passage(root, args.get("chunk_id"), args.get("citekey"), args.get("page")))
        elif name == "get_document":
            value = result(get_document(root, args["citekey"]))
        elif name == "get_page_image":
            value = image_result(page_image(root, args["citekey"], int(args["page"]), int(args.get("dpi", 150))))
        elif name == "find_evidence":
            value = result(search(root, args["claim"], int(args.get("limit", 8))))
        elif name == "index_status":
            value = result(status(root))
        else:
            raise ValueError(f"unknown tool: {name}")
    else:
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def serve(root: Config) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        try:
            response = handle(root, request)
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
