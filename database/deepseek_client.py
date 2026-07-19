from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"


class DeepSeekError(RuntimeError):
    """Raised when DeepSeek configuration or an API request fails."""


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 120.0

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise DeepSeekError(
                "缺少 DEEPSEEK_API_KEY 环境变量；请从 DeepSeek 开放平台创建 API Key 后设置。"
            )
        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
            timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "120")),
        )


class DeepSeekClient:
    """Small OpenAI-compatible DeepSeek client with no third-party dependency."""

    def __init__(self, config: DeepSeekConfig | None = None) -> None:
        self.config = config or DeepSeekConfig.from_env()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.config.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise DeepSeekError(f"DeepSeek API 返回 HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise DeepSeekError(f"无法连接 DeepSeek API: {error.reason}") from error
        except (json.JSONDecodeError, TimeoutError) as error:
            raise DeepSeekError(f"DeepSeek API 响应无效或请求超时: {error}") from error

    def list_models(self) -> list[str]:
        response = self._request("GET", "/models")
        return [item["id"] for item in response.get("data", []) if isinstance(item, dict) and "id" in item]

    def chat(
        self,
        messages: Iterable[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        json_output: bool = False,
        thinking: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_output:
            payload["response_format"] = {"type": "json_object"}
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        return self._request("POST", "/chat/completions", payload)

    def ask(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self.chat(messages, **kwargs)
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise DeepSeekError(f"DeepSeek API 响应缺少消息内容: {response}") from error


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="验证 DeepSeek V4 Pro API 并发送测试消息")
    parser.add_argument("--check", action="store_true", help="只检查密钥、网络和模型可用性")
    parser.add_argument("--prompt", default="请只回复：DeepSeek V4 Pro API 连接成功")
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--no-thinking", action="store_true")
    args = parser.parse_args()

    try:
        client = DeepSeekClient()
        models = client.list_models()
        if client.config.model not in models:
            raise DeepSeekError(
                f"模型 {client.config.model!r} 不在账户可用列表中：{', '.join(models) or '空列表'}"
            )
        if args.check:
            print(json.dumps({"ok": True, "model": client.config.model, "availableModels": models}, ensure_ascii=False))
            return
        answer = client.ask(args.prompt, max_tokens=args.max_tokens, thinking=not args.no_thinking)
        print(answer)
    except DeepSeekError as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
