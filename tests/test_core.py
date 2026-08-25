import tempfile
import unittest
from pathlib import Path

from src.kb.core import Config, add_document, get_passage, index_document, search, stable_citekey
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


if __name__ == "__main__":
    unittest.main()
