from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from database.deepseek_client import DeepSeekClient, DeepSeekConfig, DeepSeekError


class DeepSeekClientTest(unittest.TestCase):
    def test_config_requires_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(DeepSeekError, "DEEPSEEK_API_KEY"):
                DeepSeekConfig.from_env()

    def test_defaults_to_v4_pro(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            config = DeepSeekConfig.from_env()
        self.assertEqual(config.model, "deepseek-v4-pro")
        self.assertEqual(config.base_url, "https://api.deepseek.com")

    def test_ask_extracts_content(self) -> None:
        client = DeepSeekClient(DeepSeekConfig(api_key="test-key"))
        with patch.object(
            client,
            "chat",
            return_value={"choices": [{"message": {"content": "ok"}}]},
        ) as chat:
            self.assertEqual(client.ask("hello"), "ok")
        self.assertEqual(chat.call_args.args[0], [{"role": "user", "content": "hello"}])


if __name__ == "__main__":
    unittest.main()
