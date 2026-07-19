from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from database.dataset_pipeline import ROOT
from database.deepseek_client import DeepSeekClient, DeepSeekError
from database.deepseek_dataset_intake import extract_json


DEFAULT_PROMPT = ROOT / "Prompt" / "work_generating_prompt.txt"
DEFAULT_OUTPUT_ROOT = ROOT / "DataSetRaw" / "deepseek_generations"


def read_items(path: Path) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        item = raw_line.strip()
        if not item or item.startswith("#") or item in seen:
            continue
        seen.add(item)
        items.append(item)
    if not items:
        raise ValueError(f"作品列表为空：{path}")
    return items


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def safe_batch_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError("--batch 只能包含字母、数字、点、下划线和连字符")
    return value


def build_user_prompt(items: list[str], batch_index: int, total_batches: int) -> str:
    numbered = "\n".join(f"{index}. {name}" for index, name in enumerate(items, 1))
    return (
        f"请严格按照系统 Prompt 的契约，为下面 {len(items)} 个指定作品生成作品实体。\n"
        "只能生成名单中的作品，不得替换、扩展或遗漏；无法可靠确认的字段按契约使用 null。\n"
        f"这是第 {batch_index}/{total_batches} 批。只输出合法 JSON 数组。\n\n"
        f"指定作品列表：\n{numbered}"
    )


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(
    *,
    client: DeepSeekClient,
    prompt_path: Path,
    list_path: Path,
    output_dir: Path,
    batch_size: int,
    max_tokens: int,
    thinking: bool,
    delay_seconds: float,
    resume: bool,
) -> dict[str, Any]:
    system_prompt = prompt_path.read_text(encoding="utf-8-sig")
    items = read_items(list_path)
    item_batches = list(chunks(items, batch_size))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")
    (output_dir / "input_items.txt").write_text("\n".join(items) + "\n", encoding="utf-8")

    manifest: dict[str, Any] = {
        "schema": "rgmj-deepseek-generation/v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "model": client.config.model,
        "promptPath": str(prompt_path.resolve()),
        "listPath": str(list_path.resolve()),
        "batchSize": batch_size,
        "itemCount": len(items),
        "thinking": thinking,
        "batches": [],
    }
    combined: list[Any] = []
    for batch_index, batch_items in enumerate(item_batches, 1):
        stem = f"batch_{batch_index:04d}"
        content_path = output_dir / f"{stem}_content.json"
        if resume and content_path.exists():
            parsed = json.loads(content_path.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                combined.extend(parsed)
            manifest["batches"].append({"index": batch_index, "status": "skipped", "items": batch_items})
            continue

        user_prompt = build_user_prompt(batch_items, batch_index, len(item_batches))
        (output_dir / f"{stem}_prompt.txt").write_text(user_prompt + "\n", encoding="utf-8")
        entry: dict[str, Any] = {"index": batch_index, "status": "running", "items": batch_items}
        manifest["batches"].append(entry)
        write_json(output_dir / "manifest.json", manifest)
        try:
            response = client.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                thinking=thinking,
            )
            write_json(output_dir / f"{stem}_response.json", response)
            content = response["choices"][0]["message"]["content"]
            raw_path = output_dir / f"{stem}_content.txt"
            raw_path.write_text(content + "\n", encoding="utf-8")
            parsed = extract_json(raw_path)
            if not isinstance(parsed, list):
                raise ValueError("模型输出的顶层不是 JSON 数组")
            write_json(content_path, parsed)
            combined.extend(parsed)
            entry.update({"status": "completed", "outputCount": len(parsed)})
        except Exception as error:
            entry.update({"status": "failed", "error": f"{type(error).__name__}: {error}"})
            manifest["status"] = "failed"
            write_json(output_dir / "manifest.json", manifest)
            write_json(output_dir / "combined.json", combined)
            raise
        write_json(output_dir / "combined.json", combined)
        write_json(output_dir / "manifest.json", manifest)
        if delay_seconds and batch_index < len(item_batches):
            time.sleep(delay_seconds)

    manifest["status"] = "completed"
    manifest["completedAt"] = datetime.now(timezone.utc).isoformat()
    manifest["outputCount"] = len(combined)
    write_json(output_dir / "combined.json", combined)
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="向 DeepSeek V4 Pro 批量发送作品名单并自动保存结果")
    parser.add_argument("--list", type=Path, required=True, help="每行一个作品名的 UTF-8 文本文件")
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--batch", required=True, help="输出批次名称，例如 works_20260718_01")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--no-thinking", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    if args.batch_size < 1 or args.max_tokens < 1 or args.delay_seconds < 0:
        parser.error("batch-size/max-tokens 必须大于 0，delay-seconds 不能小于 0")

    try:
        output_dir = args.output_root / safe_batch_name(args.batch)
        manifest = generate(
            client=DeepSeekClient(),
            prompt_path=args.prompt,
            list_path=args.list,
            output_dir=output_dir,
            batch_size=args.batch_size,
            max_tokens=args.max_tokens,
            thinking=not args.no_thinking,
            delay_seconds=args.delay_seconds,
            resume=not args.no_resume,
        )
        print(json.dumps({"ok": True, "outputDirectory": str(output_dir.resolve()), **manifest}, ensure_ascii=False, indent=2))
    except (DeepSeekError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
