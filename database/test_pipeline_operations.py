from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from database.dataset_pipeline import resolve_manifest
from database.deepseek_dataset_intake import extract_json
from database.enum_contract_check import check


class PipelineOperationsTest(unittest.TestCase):
    def test_extracts_json_from_markdown_fence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response.txt"
            path.write_text("```json\n{\"characters\": [], \"works\": []}\n```", encoding="utf-8")
            self.assertEqual({"characters": [], "works": []}, extract_json(path))

    def test_rejects_trailing_model_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response.txt"
            path.write_text("[]\n以上是结果", encoding="utf-8")
            with self.assertRaises(ValueError):
                extract_json(path)

    def test_resolves_direct_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps({"status": "failed"}), encoding="utf-8")
            self.assertEqual(path, resolve_manifest(path))

    def test_static_enum_contract_is_aligned(self) -> None:
        result = check(False)
        self.assertTrue(result["passed"], result["errors"])


if __name__ == "__main__":
    unittest.main()
