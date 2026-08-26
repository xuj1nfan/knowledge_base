from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote


DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.IGNORECASE,
)
ARXIV_RE = re.compile(
    r"(?<![\w.-])((?:[a-z][a-z0-9-]*(?:\.[A-Z]{2})?/\d{7})|(?:\d{4}\.\d{4,5}))(?:v\d+)?\b",
    re.IGNORECASE,
)


class MetadataResolutionError(RuntimeError):
    """Raised when an explicit identifier cannot be resolved safely."""


@dataclass
class PaperMetadata:
    title: str
    author: str = "Unknown"
    year: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    journal: str | None = None
    url: str | None = None
    entry_type: str = "article"
    source: str = "pdf"
    status: str = "partial"
    identifiers: list[str] = field(default_factory=list)


def _get_with_retry(client: Any, url: str, **kwargs: Any) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.get(url, **kwargs)
            status = int(getattr(response, "status_code", 200))
            if status == 429 or status >= 500:
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            status = int(getattr(locals().get("response"), "status_code", 0) or 0)
            retryable = status == 429 or status >= 500 or status == 0
            if attempt >= 2 or not retryable:
                raise
            time.sleep(2**attempt)
    raise last_error or RuntimeError("metadata request failed")


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    match = DOI_RE.search(value)
    if not match:
        return None
    doi = match.group(1).rstrip(".,;:)]}>\"'")
    return doi.lower()


def normalize_arxiv(value: str | None) -> str | None:
    if not value:
        return None
    match = ARXIV_RE.search(value)
    if not match:
        return None
    return match.group(1).lower()


def detect_identifiers(text: str) -> tuple[list[str], list[str]]:
    dois: list[str] = []
    arxiv_ids: list[str] = []
    for match in DOI_RE.finditer(text):
        value = normalize_doi(match.group(0))
        if value and value not in dois:
            dois.append(value)
    for match in ARXIV_RE.finditer(text):
        value = normalize_arxiv(match.group(0))
        if value and value not in arxiv_ids:
            arxiv_ids.append(value)
    return dois, arxiv_ids


def _pdf_text(path: Path) -> tuple[dict[str, str], str]:
    metadata: dict[str, str] = {}
    pages: list[str] = []
    try:
        import fitz  # type: ignore

        document = fitz.open(path)
        try:
            metadata = {key: str(value or "") for key, value in (document.metadata or {}).items()}
            for page in list(document)[:2]:
                pages.append(page.get_text("text"))
        finally:
            document.close()
    except (ImportError, OSError, RuntimeError):
        if not _has_command("pdftotext"):
            return metadata, ""
        try:
            text = subprocess.check_output(
                ["pdftotext", "-f", "1", "-l", "2", "-layout", str(path), "-"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            pages.append(text)
        except (OSError, subprocess.CalledProcessError):
            pass
    return metadata, "\n".join(pages)


def _has_command(name: str) -> bool:
    from shutil import which

    return which(name) is not None


def _metadata_year(metadata: dict[str, str]) -> str | None:
    for key in ("creationDate", "modDate", "format"):
        match = re.search(r"\b((?:19|20)\d{2})\b", metadata.get(key, ""))
        if match:
            return match.group(1)
    return None


def local_metadata(path: Path) -> tuple[PaperMetadata, str]:
    pdf_metadata, first_pages = _pdf_text(path)
    identifier_text = "\n".join(
        [
            pdf_metadata.get("keywords", ""),
            pdf_metadata.get("subject", ""),
            pdf_metadata.get("title", ""),
            first_pages,
        ]
    )
    dois, arxiv_ids = detect_identifiers(identifier_text)
    title = pdf_metadata.get("title", "").strip() or path.stem
    author = pdf_metadata.get("author", "").strip() or "Unknown"
    return (
        PaperMetadata(
            title=title,
            author=author,
            year=_metadata_year(pdf_metadata),
            doi=dois[0] if len(dois) == 1 else None,
            arxiv_id=arxiv_ids[0] if len(arxiv_ids) == 1 else None,
            source="pdf",
            status="partial",
            identifiers=dois + arxiv_ids,
        ),
        first_pages,
    )


def _crossref_author(record: dict[str, Any]) -> str:
    authors = []
    for author in record.get("author") or []:
        family = str(author.get("family") or "").strip()
        given = str(author.get("given") or "").strip()
        if family and given:
            authors.append(f"{family}, {given}")
        elif family or given:
            authors.append(family or given)
    return " and ".join(authors) or "Unknown"


def _record_year(record: dict[str, Any]) -> str | None:
    for key in ("published", "published-print", "published-online", "issued"):
        parts = (record.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            return str(parts[0][0])
    return None


def crossref_metadata(client: Any, doi: str, mailto: str | None = None) -> dict[str, Any]:
    params = {"mailto": mailto} if mailto else None
    response = _get_with_retry(
        client,
        f"https://api.crossref.org/works/{quote(doi, safe='/')}",
        params=params,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["message"]


def arxiv_metadata(client: Any, arxiv_id: str) -> dict[str, Any]:
    response = _get_with_retry(
        client,
        "https://export.arxiv.org/api/query",
        params={"id_list": arxiv_id, "max_results": 1},
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", namespace)
    if entry is None:
        raise MetadataResolutionError(f"arXiv ID not found: {arxiv_id}")
    text = lambda tag: (entry.findtext(f"atom:{tag}", default="", namespaces=namespace) or "").strip()
    authors = [
        (node.findtext("atom:name", default="", namespaces=namespace) or "").strip()
        for node in entry.findall("atom:author", namespace)
    ]
    identifier = normalize_arxiv(text("id")) or arxiv_id
    doi = None
    for link in entry.findall("atom:link", namespace):
        doi = normalize_doi(link.attrib.get("href")) or doi
    return {
        "title": re.sub(r"\s+", " ", text("title")),
        "author": " and ".join(authors) or "Unknown",
        "year": text("published")[:4] or None,
        "arxiv_id": identifier,
        "doi": doi,
        "journal": text("journal_ref") or None,
        "url": text("id") or None,
    }


def resolve_metadata(
    path: Path,
    *,
    offline: bool = False,
    mailto: str | None = None,
    client: Any = None,
    arxiv_delay: float = 3.0,
) -> PaperMetadata:
    local, first_pages = local_metadata(path)
    all_text = "\n".join([local.title, local.author, first_pages])
    dois, arxiv_ids = detect_identifiers(all_text)
    if len(dois) > 1 or len(arxiv_ids) > 1:
        raise MetadataResolutionError(
            f"ambiguous identifiers (DOI={dois}, arXiv={arxiv_ids})"
        )
    doi = dois[0] if dois else local.doi
    arxiv_id = arxiv_ids[0] if arxiv_ids else local.arxiv_id
    if offline or not (doi or arxiv_id):
        local.doi = doi
        local.arxiv_id = arxiv_id
        local.identifiers = [value for value in (doi, arxiv_id) if value]
        local.status = "complete" if local.author != "Unknown" and local.year else "partial"
        return local

    if client is None:
        import httpx  # type: ignore

        client = httpx.Client(
            timeout=15.0,
            headers={"User-Agent": "terminal-knowledge-base/0.1 (metadata importer)"},
        )
        close_client = True
    else:
        close_client = False
    try:
        remote: dict[str, Any] = {}
        sources: list[str] = []
        if doi:
            remote = crossref_metadata(client, doi, mailto)
            sources.append("crossref")
        elif arxiv_id:
            if arxiv_delay > 0:
                time.sleep(arxiv_delay)
            remote = arxiv_metadata(client, arxiv_id)
            sources.append("arxiv")
            if remote.get("doi"):
                remote_crossref = crossref_metadata(client, normalize_doi(remote["doi"]) or remote["doi"], mailto)
                remote = {**remote, **remote_crossref}
                sources.append("crossref")
        local.title = str(remote.get("title") or local.title).strip()
        if isinstance(remote.get("title"), list):
            local.title = str(remote["title"][0]).strip()
        local.author = str(remote.get("author") or local.author).strip()
        if remote.get("author") and isinstance(remote["author"], list):
            local.author = _crossref_author(remote)
        local.year = str(remote.get("year") or _record_year(remote) or local.year or "") or None
        local.doi = normalize_doi(str(remote.get("DOI") or remote.get("doi") or doi))
        local.arxiv_id = normalize_arxiv(str(remote.get("arxiv_id") or arxiv_id))
        container = remote.get("container-title")
        if isinstance(container, list):
            container = container[0] if container else None
        local.journal = str(container or remote.get("journal") or "") or None
        local.url = str(remote.get("URL") or remote.get("url") or "") or None
        local.source = "+".join(sources) or "pdf"
        local.identifiers = [value for value in (local.doi, local.arxiv_id) if value]
        local.status = "complete" if local.author != "Unknown" and local.year else "partial"
        return local
    finally:
        if close_client:
            client.close()
