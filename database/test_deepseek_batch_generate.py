from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from database.deepseek_batch_generate import generate, read_items
from database.deepseek_client import DeepSeekConfig


class FakeClient:
    config = DeepSeekConfig(api_key="test", model="deepseek-v4-pro")

    def chat(self, messages, **kwargs):
        names = [line.split(". ", 1)[1] for line in messages[1]["content"].splitlines() if ". " in line]
        content = json.dumps([{"workName": name} for name in names], ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}


class DeepSeekBatchGenerateTest(unittest.TestCase):
    def test_read_items_removes_empty_comments_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "items.txt"
            path.write_text("# comment\n作品甲\n\n作品甲\n作品乙\n", encoding="utf-8")
            self.assertEqual(read_items(path), ["作品甲", "作品乙"])

    def test_generate_saves_batches_and_combined_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt = root / "prompt.txt"
            items = root / "items.txt"
            output = root / "output"
            prompt.write_text("只输出 JSON", encoding="utf-8")
            items.write_text("作品甲\n作品乙\n作品丙\n", encoding="utf-8")
            manifest = generate(
                client=FakeClient(), prompt_path=prompt, list_path=items, output_dir=output,
                batch_size=2, max_tokens=100, thinking=False, delay_seconds=0, resume=True,
            )
            combined = json.loads((output / "combined.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual([item["workName"] for item in combined], ["作品甲", "作品乙", "作品丙"])
            self.assertTrue((output / "batch_0001_response.json").exists())


if __name__ == "__main__":
    unittest.main()
