from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.core import Config, add_document, get_document, get_passage, index_all, index_document, page_image, search, status
from kb.mcp_server import serve


def emit(value: object, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif isinstance(value, list):
        for item in value:
            print(json.dumps(item, ensure_ascii=False))
    elif isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
    else:
        print(value)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kb", description="Local-first terminal PDF/Markdown knowledge base")
    p.add_argument("--root", type=Path, default=Path(os.environ.get("KB_ROOT", Path.cwd())))
    p.add_argument("--json", action="store_true", dest="as_json")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    add = sub.add_parser("add")
    add.add_argument("source", type=Path)
    add.add_argument("--title")
    add.add_argument("--author")
    add.add_argument("--year")
    add.add_argument("--doi")
    add.add_argument("--citekey")
    idx = sub.add_parser("index")
    idx.add_argument("citekey", nargs="?")
    idx.add_argument("--all", action="store_true")
    idx.add_argument("--force", action="store_true", help="rebuild unchanged documents too")
    srch = sub.add_parser("search")
    srch.add_argument("query")
    srch.add_argument("--limit", type=int, default=10)
    srch.add_argument("--citekey")
    show = sub.add_parser("show")
    show.add_argument("citekey")
    show.add_argument("--page", type=int)
    note = sub.add_parser("note")
    note.add_argument("citekey")
    note.add_argument("--editor", action="store_true", help="open the note in $EDITOR")
    cite = sub.add_parser("cite")
    cite.add_argument("citekey")
    cite.add_argument("--page", type=int)
    passage = sub.add_parser("passage")
    passage.add_argument("--chunk-id", type=int)
    passage.add_argument("--citekey")
    passage.add_argument("--page", type=int)
    image = sub.add_parser("page-image")
    image.add_argument("citekey")
    image.add_argument("page", type=int)
    image.add_argument("--dpi", type=int, default=150)
    sub.add_parser("status")
    sub.add_parser("doctor")
    mcp = sub.add_parser("serve-mcp")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = Config(args.root.expanduser().resolve())
    if args.command == "init":
        config.ensure()
        emit({"status": "initialized", "root": str(config.root)}, args.as_json)
        return 0
    if args.command == "serve-mcp":
        serve(config)
        return 0
    try:
        if args.command == "add":
            value = add_document(config, args.source, title=args.title, author=args.author, year=args.year, doi=args.doi, citekey=args.citekey)
        elif args.command == "index":
            value = index_all(config, force=args.force) if args.all else index_document(config, args.citekey) if args.citekey else (_ for _ in ()).throw(ValueError("index needs a citekey or --all"))
        elif args.command == "search":
            value = search(config, args.query, args.limit, args.citekey)
        elif args.command == "show":
            value = get_document(config, args.citekey)
            if args.page:
                value = {"document": value, "passage": get_passage(config, citekey=args.citekey, page=args.page)}
        elif args.command == "note":
            config.ensure()
            note_path = config.notes / f"{args.citekey}.md"
            if not note_path.exists():
                document = get_document(config, args.citekey)
                if not document:
                    raise ValueError(f"unknown citekey: {args.citekey}")
                note_path.write_text(
                    f"---\ncitekey: {args.citekey}\ntags: []\nstatus: unread\n---\n\n"
                    f"# {document.get('title') or args.citekey}\n\n"
                    "## Summary\n\n"
                    "## Evidence\n\n"
                    "## Questions\n\n",
                    encoding="utf-8",
                )
            if args.editor:
                editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
                subprocess.call([editor, str(note_path)])
            value = {"path": str(note_path)}
        elif args.command == "cite":
            document = get_document(config, args.citekey)
            if not document:
                raise ValueError(f"unknown citekey: {args.citekey}")
            value = f"[@{args.citekey}" + (f", p. {args.page}" if args.page else "") + "]"
        elif args.command == "passage":
            value = get_passage(config, args.chunk_id, args.citekey, args.page)
        elif args.command == "page-image":
            value = {"path": page_image(config, args.citekey, args.page, args.dpi)}
        elif args.command == "status":
            value = status(config)
        elif args.command == "doctor":
            value = {
                "status": status(config),
                "sqlite_fts5": True,
                "commands": {name: bool(shutil.which(name)) for name in ("pdftotext", "pdfinfo", "pdftoppm")},
                "optional": {name: _optional_available(name) for name in ("docling", "lancedb", "sentence_transformers", "mcp")},
            }
        else:
            raise ValueError(f"unknown command: {args.command}")
        emit(value, args.as_json)
        return 0
    except Exception as exc:
        print(f"kb: error: {exc}", file=sys.stderr)
        return 1


def _optional_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
