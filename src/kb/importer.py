from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .core import Config, add_document, index_document, stable_citekey
from .metadata import PaperMetadata, resolve_metadata


def _pdf_files(source: Path) -> list[Path]:
    source = source.expanduser().resolve()
    if source.is_file():
        if source.suffix.lower() != ".pdf":
            raise ValueError("kb import accepts PDF files or directories")
        return [source]
    if source.is_dir():
        return sorted(path for path in source.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
    raise ValueError(f"source does not exist: {source}")


def _preview_citekey(config: Config, metadata: PaperMetadata) -> str:
    if not config.db_path.exists():
        return stable_citekey(metadata.author, metadata.year, metadata.title, ())
    db = sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True)
    try:
        existing = [row[0] for row in db.execute("SELECT citekey FROM documents")]
    except sqlite3.Error:
        existing = []
    finally:
        db.close()
    return stable_citekey(metadata.author, metadata.year, metadata.title, existing)


def _crossref_mailto(config: Config) -> str | None:
    try:
        text = config.config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    import re

    match = re.search(r"^\s*crossref_mailto\s*=\s*[\"']([^\"']*)[\"']", text, re.M)
    return match.group(1).strip() or None if match else None


def import_documents(
    config: Config,
    source: Path,
    *,
    dry_run: bool = False,
    offline: bool = False,
    no_index: bool = False,
) -> list[dict[str, Any]]:
    files = _pdf_files(source)
    if not files:
        return []

    client = None
    if not offline:
        try:
            import httpx  # type: ignore

            client = httpx.Client(
                timeout=15.0,
                headers={"User-Agent": "terminal-knowledge-base/0.1 (metadata importer)"},
            )
        except ImportError:
            # Local-only files without identifiers can still be imported; an
            # identifier-bearing file will report the missing dependency below.
            client = None

    results: list[dict[str, Any]] = []
    try:
        for path in files:
            result: dict[str, Any] = {"source": str(path), "status": "failed"}
            try:
                metadata = resolve_metadata(
                    path,
                    offline=offline,
                    mailto=_crossref_mailto(config),
                    client=client,
                    arxiv_delay=3.0 if client is not None else 0.0,
                )
                citekey = _preview_citekey(config, metadata)
                result.update(
                    {
                        "citekey": citekey,
                        "title": metadata.title,
                        "author": metadata.author,
                        "year": metadata.year,
                        "doi": metadata.doi,
                        "arxiv_id": metadata.arxiv_id,
                        "metadata_source": metadata.source,
                        "metadata_status": metadata.status,
                    }
                )
                if dry_run:
                    result["status"] = "preview"
                    results.append(result)
                    continue
                added = add_document(
                    config,
                    path,
                    title=metadata.title,
                    author=metadata.author,
                    year=metadata.year,
                    doi=metadata.doi,
                    arxiv_id=metadata.arxiv_id,
                    journal=metadata.journal,
                    url=metadata.url,
                    metadata_source=metadata.source,
                    metadata_status=metadata.status,
                    citekey=citekey,
                )
                result.update({"citekey": added["citekey"], "catalog": added["status"], "path": added.get("path")})
                if added["status"] == "duplicate":
                    result["status"] = "duplicate"
                elif no_index:
                    result.update({"status": "added", "index": "skipped"})
                else:
                    indexed = index_document(config, added["citekey"])
                    result.update({"status": "added", "index": indexed["status"], "method": indexed.get("method")})
            except Exception as exc:
                result["error"] = str(exc)
            results.append(result)
    finally:
        if client is not None:
            client.close()
    return results
