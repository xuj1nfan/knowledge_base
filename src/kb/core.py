from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TEXT_SUFFIXES = {".md", ".markdown", ".txt"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9]+", "", value).lower()
    return value or "document"


def stable_citekey(author: str, year: str | int | None, title: str, existing: Iterable[str] = ()) -> str:
    family = (author or "Unknown").split(",")[0].split()[-1]
    year_text = str(year or "n.d.")
    title_word = next((w for w in re.split(r"\W+", title) if len(w) > 2), "paper")
    base = f"{slug(family)}{year_text}{slug(title_word)}"
    used = set(existing)
    if base not in used:
        return base
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = base + suffix
        if candidate not in used:
            return candidate
    return base + hashlib.sha1(title.encode()).hexdigest()[:6]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bib_escape(value: str) -> str:
    return value.replace("{", "\\{").replace("}", "\\}")


def parse_bibtex(text: str) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"@(?P<type>\w+)\s*\{\s*(?P<key>[^,]+),(?P<body>.*?)\n\}", text, re.S):
        fields: dict[str, str] = {"ENTRYTYPE": match.group("type"), "ID": match.group("key").strip()}
        for field in re.finditer(r"(\w+)\s*=\s*(?:\{([^{}]*)\}|\"([^\"]*)\"|([^,\n]+))", match.group("body")):
            fields[field.group(1).lower()] = next((x for x in field.groups()[1:] if x is not None), "").strip()
        entries[fields["ID"]] = fields
    return entries


def append_bibtex(path: Path, citekey: str, entry_type: str, fields: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [f"@{entry_type}{{{citekey},"]
    ordered = ["author", "title", "year", "journal", "booktitle", "doi", "url"]
    for key in ordered:
        if fields.get(key):
            lines.append(f"  {key} = {{{bib_escape(fields[key])}}},")
    lines.append("}\n")
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + "\n".join(lines), encoding="utf-8")


@dataclass
class Config:
    root: Path

    @property
    def library(self) -> Path:
        return self.root / "library"

    @property
    def papers(self) -> Path:
        return self.library / "papers"

    @property
    def notes(self) -> Path:
        return self.library / "notes"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def bib(self) -> Path:
        return self.root / "bibliography" / "references.bib"

    @property
    def state(self) -> Path:
        return self.root / ".kb"

    @property
    def db_path(self) -> Path:
        return self.state / "catalog.sqlite"

    @property
    def config_path(self) -> Path:
        return self.state / "config.toml"

    def ensure(self) -> None:
        for path in (self.papers, self.notes, self.projects, self.bib.parent, self.state / "parsed", self.state / "pages", self.state / "index"):
            path.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(
                "# Rebuildable local knowledge-base settings\n"
                "# Vector search is opt-in because it may download a model on first use.\n"
                "enable_vectors = false\n"
                "embedding_model = \"BAAI/bge-m3\"\n"
                "\n",
                encoding="utf-8",
            )


def open_db(config: Config) -> sqlite3.Connection:
    config.ensure()
    db = sqlite3.connect(config.db_path)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            citekey TEXT UNIQUE NOT NULL,
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            title TEXT,
            author TEXT,
            year TEXT,
            doi TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            pages INTEGER,
            added_at TEXT NOT NULL,
            indexed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_no INTEGER NOT NULL,
            page INTEGER,
            heading TEXT,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            start_line INTEGER,
            end_line INTEGER
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text, heading, content='chunks', content_rowid='id', tokenize='unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
            INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
        END;
        """
    )
    db.commit()
    return db


def _pdf_pages(path: Path) -> int | None:
    if shutil.which("pdfinfo"):
        try:
            output = subprocess.check_output(["pdfinfo", str(path)], text=True, stderr=subprocess.DEVNULL)
            match = re.search(r"^Pages:\s+(\d+)", output, re.M)
            return int(match.group(1)) if match else None
        except (OSError, subprocess.CalledProcessError, ValueError):
            return None
    return None


def extract_pdf(path: Path, output_dir: Path) -> tuple[str, int | None, str]:
    """Return text, page count and extraction method."""
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import docling  # type: ignore  # noqa: F401
        from docling.document_converter import DocumentConverter  # type: ignore

        result = DocumentConverter().convert(str(path))
        text = result.document.export_to_markdown()
        method = "docling"
    except Exception:
        if not shutil.which("pdftotext"):
            raise RuntimeError("PDF extraction requires Docling or the pdftotext command")
        text = subprocess.check_output(["pdftotext", "-layout", str(path), "-"], text=True, stderr=subprocess.STDOUT)
        method = "pdftotext"
    return text, _pdf_pages(path), method


def extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def split_chunks(text: str, kind: str = "text", max_chars: int = 2200) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    heading = ""
    start = 1
    global_line = 1

    def flush(end_line: int, page: int) -> None:
        nonlocal current, start
        content = "\n".join(current).strip()
        if content:
            chunks.append({"text": content, "heading": heading, "kind": kind, "page": page, "start_line": start, "end_line": end_line})
        current = []

    # Split explicitly on PDF form-feed page boundaries. splitlines() would
    # discard \f and make every extracted passage appear to be on page 1.
    pages = text.replace("\r\n", "\n").split("\f")
    for page, page_text in enumerate(pages, 1):
        lines = page_text.splitlines()
        for line in lines:
            number = global_line
            global_line += 1
            page_match = re.search(r"(?:^|\s)(?:Page|页)\s+(\d+)\s*$", line, re.I)
            actual_page = int(page_match.group(1)) if page_match else page
            if re.match(r"^#{1,6}\s+", line):
                flush(number - 1, actual_page)
                heading = re.sub(r"^#+\s+", "", line).strip()
                start = number
                current.append(line)
            elif current and sum(len(x) + 1 for x in current) + len(line) > max_chars:
                flush(number - 1, actual_page)
                start = number
                current.append(line)
            else:
                if not current:
                    start = number
                current.append(line)
        flush(global_line - 1, page)
    return chunks


def add_document(config: Config, source: Path, *, title: str | None = None, author: str | None = None, year: str | None = None, doi: str | None = None, citekey: str | None = None) -> dict[str, Any]:
    config.ensure()
    source = source.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".pdf", *TEXT_SUFFIXES}:
        raise ValueError("source must be a PDF, Markdown, or text file")
    digest = sha256(source)
    db = open_db(config)
    duplicate = db.execute("SELECT citekey, path FROM documents WHERE sha256=?", (digest,)).fetchone()
    if duplicate:
        return {"status": "duplicate", "citekey": duplicate["citekey"], "path": duplicate["path"]}
    title = title or source.stem
    author = author or "Unknown"
    keys = [row[0] for row in db.execute("SELECT citekey FROM documents")]
    citekey = citekey or stable_citekey(author, year, title, keys)
    suffix = source.suffix.lower()
    destination = config.papers / f"{citekey}{suffix}" if suffix == ".pdf" else config.notes / f"{citekey}{suffix}"
    if destination.exists() and sha256(destination) != digest:
        raise ValueError(f"citekey already maps to a different file: {citekey}")
    if source != destination:
        shutil.copy2(source, destination)
    kind = "pdf" if suffix == ".pdf" else "markdown"
    db.execute(
        "INSERT INTO documents(citekey,path,kind,sha256,title,author,year,doi,status,added_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (citekey, str(destination), kind, digest, title, author, year, doi, "new", now_iso()),
    )
    db.commit()
    append_bibtex(config.bib, citekey, "article" if kind == "pdf" else "misc", {"title": title, "author": author, "year": year or "", "doi": doi or ""})
    db.close()
    return {"status": "added", "citekey": citekey, "path": str(destination)}


def index_document(config: Config, citekey: str) -> dict[str, Any]:
    db = open_db(config)
    document = db.execute("SELECT * FROM documents WHERE citekey=?", (citekey,)).fetchone()
    if not document:
        raise ValueError(f"unknown citekey: {citekey}")
    path = Path(document["path"])
    if not path.exists():
        db.execute("UPDATE documents SET status='missing' WHERE id=?", (document["id"],))
        db.commit()
        return {"citekey": citekey, "status": "missing"}
    try:
        current_digest = sha256(path)
        if document["kind"] == "pdf":
            text, pages, method = extract_pdf(path, config.state / "parsed")
        else:
            text, pages, method = extract_text(path), None, "plain-text"
        parsed = config.state / "parsed" / f"{citekey}.md"
        parsed.write_text(text, encoding="utf-8")
        chunks = split_chunks(text)
        db.execute("DELETE FROM chunks WHERE document_id=?", (document["id"],))
        for number, chunk in enumerate(chunks):
            db.execute(
                "INSERT INTO chunks(document_id,chunk_no,page,heading,kind,text,start_line,end_line) VALUES(?,?,?,?,?,?,?,?)",
                (document["id"], number, chunk["page"], chunk["heading"], chunk["kind"], chunk["text"], chunk["start_line"], chunk["end_line"]),
            )
        db.commit()
        vector_status = "disabled"
        try:
            from .vectors import enabled as vectors_enabled, sync_document
            if vectors_enabled(config):
                vector_rows = [dict(row) for row in db.execute("SELECT id, page, heading, text FROM chunks WHERE document_id=? ORDER BY chunk_no", (document["id"],))]
                sync_document(config, citekey, vector_rows)
                vector_status = "indexed"
        except ImportError:
            vector_status = "unavailable"
        except Exception as exc:
            vector_status = f"error: {exc}"
        db.execute("UPDATE documents SET status='indexed', sha256=?, pages=?, indexed_at=? WHERE id=?", (current_digest, pages, now_iso(), document["id"]))
        db.commit()
        return {"citekey": citekey, "status": "indexed", "chunks": len(chunks), "pages": pages, "method": method, "vectors": vector_status}
    except Exception as exc:
        db.execute("UPDATE documents SET status=? WHERE id=?", (f"error: {exc}", document["id"]))
        db.commit()
        raise
    finally:
        db.close()


def index_all(config: Config, force: bool = False) -> list[dict[str, Any]]:
    db = open_db(config)
    documents = db.execute("SELECT citekey, path, sha256, status FROM documents").fetchall()
    db.close()
    results: list[dict[str, Any]] = []
    for document in documents:
        path = Path(document["path"])
        if not force and document["status"] == "indexed" and path.exists() and sha256(path) == document["sha256"]:
            results.append({"citekey": document["citekey"], "status": "unchanged"})
        else:
            results.append(index_document(config, document["citekey"]))
    return results


def search(config: Config, query: str, limit: int = 10, citekey: str | None = None) -> list[dict[str, Any]]:
    db = open_db(config)
    terms = re.sub(r"[^\w\-]+", " ", query, flags=re.UNICODE).strip()
    if not terms:
        return []
    where = ""
    params: list[Any] = [terms]
    if citekey:
        where = " AND d.citekey=?"
        params.append(citekey)
    params.append(limit)
    rows = db.execute(
        f"""
        SELECT c.id, d.citekey, d.title, d.author, d.year, c.page, c.heading, c.kind, c.text,
               bm25(chunks_fts) AS score
        FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid
        JOIN documents d ON d.id=c.document_id
        WHERE chunks_fts MATCH ? {where}
        ORDER BY score LIMIT ?
        """,
        params,
    ).fetchall()
    # unicode61 is intentionally conservative for CJK text. Use a bounded
    # substring fallback when the query contains CJK characters or FTS finds
    # nothing, while retaining BM25 ranking for Latin-language queries.
    if not rows or re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", query):
        like = f"%{query}%"
        like_params: list[Any] = [like]
        like_where = ""
        if citekey:
            like_where = " AND d.citekey=?"
            like_params.append(citekey)
        like_params.append(limit)
        fallback = db.execute(
            f"""
            SELECT c.id, d.citekey, d.title, d.author, d.year, c.page, c.heading, c.kind, c.text,
                   0.0 AS score
            FROM chunks c JOIN documents d ON d.id=c.document_id
            WHERE c.text LIKE ? {like_where}
            ORDER BY d.citekey, c.chunk_no LIMIT ?
            """,
            like_params,
        ).fetchall()
        if fallback:
            rows = fallback
    try:
        from .vectors import query as vector_query
        semantic_rows = vector_query(config, query, limit)
        known = {row["id"] for row in rows}
        rows = list(rows) + [row for row in semantic_rows if row["id"] not in known]
        rows = rows[:limit]
    except (ImportError, OSError, RuntimeError):
        pass
    db.close()
    return [dict(row) for row in rows]


def get_document(config: Config, citekey: str) -> dict[str, Any] | None:
    db = open_db(config)
    row = db.execute("SELECT * FROM documents WHERE citekey=?", (citekey,)).fetchone()
    if not row:
        db.close()
        return None
    result = dict(row)
    result["note"] = str(config.notes / f"{citekey}.md") if (config.notes / f"{citekey}.md").exists() else None
    result["chunks"] = db.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (row["id"],)).fetchone()[0]
    db.close()
    return result


def get_passage(config: Config, chunk_id: int | None = None, citekey: str | None = None, page: int | None = None) -> dict[str, Any] | None:
    db = open_db(config)
    if chunk_id is not None:
        row = db.execute("SELECT c.*, d.citekey, d.title, d.path FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?", (chunk_id,)).fetchone()
    else:
        row = db.execute("SELECT c.*, d.citekey, d.title, d.path FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.citekey=? AND (? IS NULL OR c.page=?) ORDER BY c.chunk_no LIMIT 1", (citekey, page, page)).fetchone()
    db.close()
    return dict(row) if row else None


def page_image(config: Config, citekey: str, page: int, dpi: int = 150) -> str:
    db = open_db(config)
    row = db.execute("SELECT path FROM documents WHERE citekey=? AND kind='pdf'", (citekey,)).fetchone()
    db.close()
    if not row:
        raise ValueError(f"unknown PDF citekey: {citekey}")
    source = Path(row[0])
    output = config.state / "pages" / f"{citekey}-p{page}.png"
    if not output.exists():
        if not shutil.which("pdftoppm"):
            raise RuntimeError("get_page_image requires pdftoppm")
        prefix = output.with_suffix("")
        subprocess.check_call(["pdftoppm", "-f", str(page), "-l", str(page), "-png", "-r", str(dpi), str(source), str(prefix)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        candidates = sorted(prefix.parent.glob(prefix.name + "-*.png"))
        if candidates:
            candidates[0].replace(output)
        if not output.exists():
            raise RuntimeError(f"page renderer did not create {output}")
    return str(output)


def status(config: Config) -> dict[str, Any]:
    db = open_db(config)
    rows = db.execute("SELECT status, COUNT(*) AS count FROM documents GROUP BY status").fetchall()
    result = {row["status"]: row["count"] for row in rows}
    result["documents"] = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    result["chunks"] = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    db.close()
    return result
