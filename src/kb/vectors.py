from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_MODEL_CACHE: dict[str, Any] = {}


def enabled(config: Any) -> bool:
    try:
        text = config.config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"^\s*enable_vectors\s*=\s*true\s*$", text, re.M | re.I))


def model_name(config: Any) -> str:
    try:
        text = config.config_path.read_text(encoding="utf-8")
        match = re.search(r"^\s*embedding_model\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
        return match.group(1) if match else "BAAI/bge-m3"
    except OSError:
        return "BAAI/bge-m3"


def _get_model(name: str, sentence_transformer: Any) -> Any:
    model = _MODEL_CACHE.get(name)
    if model is not None:
        return model
    try:
        model = sentence_transformer(name, local_files_only=True)
    except (OSError, ValueError):
        # First-time indexing may need to fetch the model; subsequent CLI
        # searches should use the local cache without network retries.
        model = sentence_transformer(name)
    _MODEL_CACHE[name] = model
    return model


def sync_document(config: Any, citekey: str, chunks: list[dict[str, Any]]) -> bool:
    """Synchronize one document into an optional local LanceDB table."""
    if not enabled(config):
        return False
    import lancedb  # type: ignore
    from sentence_transformers import SentenceTransformer  # type: ignore

    name = model_name(config)
    model = _get_model(name, SentenceTransformer)
    texts = [chunk["text"] for chunk in chunks]
    vectors = model.encode(texts, normalize_embeddings=True).tolist()
    rows = [
        {
            "chunk_id": int(chunk["id"]),
            "citekey": citekey,
            "page": chunk["page"],
            "heading": chunk["heading"] or "",
            "text": chunk["text"],
            "vector": vector,
        }
        for chunk, vector in zip(chunks, vectors)
    ]
    db = lancedb.connect(str(config.state / "index" / "lancedb"))
    if "chunks" in db.table_names():
        table = db.open_table("chunks")
        safe_key = citekey.replace("'", "''")
        table.delete(f"citekey = '{safe_key}'")
        table.add(rows)
    else:
        db.create_table("chunks", data=rows)
    return True


def query(config: Any, text: str, limit: int, *, citekey: str | None = None) -> list[dict[str, Any]]:
    if not enabled(config):
        return []
    import lancedb  # type: ignore
    from sentence_transformers import SentenceTransformer  # type: ignore

    db = lancedb.connect(str(config.state / "index" / "lancedb"))
    if "chunks" not in db.table_names():
        return []
    model = _get_model(model_name(config), SentenceTransformer)
    vector = model.encode([text], normalize_embeddings=True)[0].tolist()
    search = db.open_table("chunks").search(vector)
    if citekey:
        # LanceDB applies the predicate before limiting, ensuring a document
        # filter does not accidentally discard all relevant semantic hits.
        safe_key = citekey.replace("'", "''")
        search = search.where(f"citekey = '{safe_key}'")
    rows = search.limit(limit).to_list()
    return [
        {
            "id": row.get("chunk_id"),
            "citekey": row.get("citekey"),
            "page": row.get("page"),
            "heading": row.get("heading", ""),
            "kind": "text",
            "text": row.get("text", ""),
            "score": row.get("_distance", row.get("_score", 0.0)),
            "semantic": True,
        }
        for row in rows
    ]
