import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import patch

from src.kb.core import Config, add_document, extract_pdf, get_passage, index_document, open_db, search, split_chunks, stable_citekey
from src.kb.metadata import PaperMetadata, detect_identifiers, normalize_arxiv, normalize_doi, resolve_metadata
from src.kb.importer import import_documents
from src.kb.mcp_server import handle


class KnowledgeBaseTests(unittest.TestCase):
    def test_citekey_is_stable_and_unique(self):
        self.assertEqual(stable_citekey("Smith, Jane", 2024, "Retrieval Systems"), "smith2024retrieval")
        self.assertEqual(stable_citekey("Smith, Jane", 2024, "Retrieval Systems", ["smith2024retrieval"]), "smith2024retrievala")

    def test_markdown_add_index_search_and_passage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "kb"
            source = Path(directory) / "paper.md"
            source.write_text("# Retrieval\n\nHybrid retrieval combines lexical and semantic search.\n\n## Formula\n\nE = mc^2", encoding="utf-8")
            config = Config(root)
            added = add_document(config, source, title="Hybrid Retrieval", author="Smith, Jane", year="2024")
            self.assertEqual(added["citekey"], "smith2024hybrid")
            indexed = index_document(config, added["citekey"])
            self.assertEqual(indexed["status"], "indexed")
            results = search(config, "semantic search")
            self.assertTrue(results)
            passage = get_passage(config, citekey=added["citekey"])
            self.assertIn("Hybrid retrieval", passage["text"])

    def test_cjk_search_and_mcp_tool_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "kb"
            source = Path(directory) / "paper.md"
            source.write_text("# 研究方法\n\n本研究讨论知识库与论文写作。", encoding="utf-8")
            config = Config(root)
            added = add_document(config, source, title="研究笔记", author="张三", year="2026")
            index_document(config, added["citekey"])
            self.assertTrue(search(config, "研究"))
            response = handle(config, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
            names = {tool["name"] for tool in response["result"]["tools"]}
            self.assertIn("get_page_image", names)

    def test_page_break_form_feed_preserves_physical_pages(self):
        chunks = split_chunks("page one\fpage two\fpage three")
        self.assertEqual([chunk["page"] for chunk in chunks], [1, 2, 3])

    def test_docling_export_receives_page_break_placeholder(self):
        calls = {}

        class FakeDocument:
            pages = {1: object(), 2: object()}

            def export_to_markdown(self, **kwargs):
                calls.update(kwargs)
                return "one\ftwo"

        class FakeConverter:
            def convert(self, _path):
                return types.SimpleNamespace(document=FakeDocument())

        docling_module = types.ModuleType("docling")
        converter_module = types.ModuleType("docling.document_converter")
        converter_module.DocumentConverter = FakeConverter
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "paper.pdf"
            source.write_bytes(b"fake")
            with patch.dict(sys.modules, {"docling": docling_module, "docling.document_converter": converter_module}):
                text, pages, method = extract_pdf(source, Path(directory) / "parsed")
        self.assertEqual(calls, {"page_break_placeholder": "\f"})
        self.assertEqual((text, pages, method), ("one\ftwo", None, "docling"))

    def test_docling_page_count_mismatch_falls_back_to_pdftotext(self):
        class FakeDocument:
            pages = {1: object(), 2: object()}

            def export_to_markdown(self, page_break_placeholder=None):
                return "only one page marker"

        class FakeConverter:
            def convert(self, _path):
                return types.SimpleNamespace(document=FakeDocument())

        docling_module = types.ModuleType("docling")
        converter_module = types.ModuleType("docling.document_converter")
        converter_module.DocumentConverter = FakeConverter
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "paper.pdf"
            source.write_bytes(b"fake")
            with patch.dict(sys.modules, {"docling": docling_module, "docling.document_converter": converter_module}), patch(
                "src.kb.core.shutil.which", return_value="/usr/bin/tool"
            ), patch("src.kb.core.subprocess.check_output", return_value="fallback"):
                text, pages, method = extract_pdf(source, Path(directory) / "parsed")
        self.assertEqual((text, pages, method), ("fallback", None, "pdftotext"))

    def test_rrf_merges_overlapping_lexical_and_semantic_hits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "kb"
            source = Path(directory) / "paper.md"
            source.write_text("# Alpha\n\nalpha lexical result", encoding="utf-8")
            config = Config(root)
            added = add_document(config, source, title="Alpha", author="Doe, Jane", year="2024")
            index_document(config, added["citekey"])
            db = open_db(config)
            chunk_id = db.execute("SELECT id FROM chunks LIMIT 1").fetchone()[0]
            db.close()
            semantic = [{"id": chunk_id, "citekey": added["citekey"], "page": 1, "heading": "Alpha", "kind": "text", "text": "alpha lexical result", "score": 0.12}]
            with patch("src.kb.vectors.query", return_value=semantic):
                results = search(config, "alpha", limit=1)
            self.assertEqual(results[0]["retrieval_sources"], ["lexical", "semantic"])
            self.assertEqual(results[0]["lexical_rank"], 1)
            self.assertEqual(results[0]["semantic_rank"], 1)
            self.assertAlmostEqual(results[0]["score"], 2 / 61)

    def test_identifier_normalization_is_conservative(self):
        dois, arxiv_ids = detect_identifiers("doi:10.1234/ABC.1 and https://arxiv.org/abs/2401.01234v2")
        self.assertEqual(dois, ["10.1234/abc.1"])
        self.assertEqual(arxiv_ids, ["2401.01234"])
        self.assertEqual(normalize_doi("https://doi.org/10.5555/Test."), "10.5555/test")
        self.assertEqual(normalize_arxiv("arXiv:2401.01234v3"), "2401.01234")

    def test_crossref_metadata_overrides_local_fields(self):
        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "message": {
                        "title": ["Remote title"],
                        "author": [{"family": "Doe", "given": "Jane"}],
                        "published": {"date-parts": [[2024]]},
                        "DOI": "10.1234/ABC",
                    }
                }

        class Client:
            def get(self, *_args, **_kwargs):
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "paper.pdf"
            source.write_bytes(b"fake")
            local = PaperMetadata(title="Local title", identifiers=["10.1234/abc"])
            with patch("src.kb.metadata.local_metadata", return_value=(local, "doi:10.1234/abc")):
                metadata = resolve_metadata(source, client=Client(), arxiv_delay=0)
        self.assertEqual((metadata.title, metadata.author, metadata.year, metadata.doi), ("Remote title", "Doe, Jane", "2024", "10.1234/abc"))
        self.assertEqual(metadata.source, "crossref")

    def test_arxiv_metadata_is_stored_in_bibtex(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "kb"
            source = Path(directory) / "paper.pdf"
            source.write_bytes(b"fake pdf")
            result = add_document(
                Config(root), source, title="A Preprint", author="Doe, Jane", year="2024",
                arxiv_id="2401.01234", metadata_source="arxiv", metadata_status="complete",
            )
            bib = (root / "bibliography" / "references.bib").read_text(encoding="utf-8")
            self.assertEqual(result["status"], "added")
            self.assertIn("eprint = {2401.01234}", bib)
            self.assertIn("archivePrefix = {arXiv}", bib)

    def test_import_directory_offline_is_idempotent_and_can_skip_index(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "kb"
            source_dir = work / "incoming"
            source_dir.mkdir()
            (source_dir / "paper.pdf").write_bytes(b"not a real PDF")
            first = import_documents(Config(root), source_dir, offline=True, no_index=True)
            second = import_documents(Config(root), source_dir, offline=True, no_index=True)
            self.assertEqual(first[0]["status"], "added")
            self.assertEqual(first[0]["index"], "skipped")
            self.assertEqual(second[0]["status"], "duplicate")


if __name__ == "__main__":
    unittest.main()
