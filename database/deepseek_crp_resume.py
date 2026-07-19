from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.dataset_pipeline import ROOT
from database.deepseek_client import DeepSeekClient, DeepSeekError


ENTITY_ROOT = ROOT / "DataSetRaw" / "entity"
PROMPT_PATH = ROOT / "Prompt" / "work_psychInfo_generating_prompt.txt"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_partial_response_array(path: Path) -> list[dict[str, Any]]:
    """Read every complete top-level object from an array still being appended."""
    text = path.read_text(encoding="utf-8-sig")
    index = 0
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] != "[":
        return []
    index += 1
    decoder = json.JSONDecoder()
    responses: list[dict[str, Any]] = []
    while index < len(text):
        while index < len(text) and (text[index].isspace() or text[index] == ","):
            index += 1
        if index >= len(text) or text[index] == "]":
            break
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            responses.append(value)
        index = end
    return responses


def load_existing(set_number: int, output: Path) -> list[dict[str, Any]]:
    candidates = [
        output,
        ENTITY_ROOT / f"workcrpset{set_number}.v4pro.json",
        ENTITY_ROOT / f"workcrpset{set_number}.normalized.json",
        ENTITY_ROOT / f"workcrpset{set_number}.json",
    ]
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in candidates:
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            value = load_json(path)
        except json.JSONDecodeError:
            responses = load_partial_response_array(path)
        else:
            responses = value if isinstance(value, list) else [value]
        for response in responses:
            if not isinstance(response, dict) or not isinstance(response.get("items"), list):
                continue
            ids = [item_work_id(item) for item in response["items"] if isinstance(item, dict)]
            new_ids = [work_id for work_id in ids if work_id and work_id not in seen]
            if not new_ids:
                continue
            # Existing CRP responses use one item per response. For defensive handling
            # of multi-item responses, retain only items that were not already merged.
            copied = dict(response)
            copied["items"] = [
                item for item in response["items"]
                if isinstance(item, dict) and item_work_id(item) in new_ids
            ]
            merged.append(copied)
            seen.update(new_ids)
    return merged


def item_work_id(item: dict[str, Any]) -> str | None:
    basic = item.get("basic") if isinstance(item.get("basic"), dict) else {}
    return item.get("workId") or basic.get("workId") or basic.get("contentId")


def completed_ids(responses: list[dict[str, Any]]) -> set[str]:
    return {
        work_id
        for response in responses
        for item in response.get("items", [])
        if isinstance(item, dict) and (work_id := item_work_id(item))
    }


def parse_model_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        value = json.loads(text)
    except json.JSONDecodeError as original_error:
        # Long structured responses occasionally contain a trailing comma before
        # a closing object/array or unescaped quotation marks inside a string
        # property. Repair only these unambiguous, line-local JSON defects.
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        repaired_lines: list[str] = []
        string_property = re.compile(r'^(\s*"[^"\\]+"\s*:\s*)"(.*)"(\s*,?\s*)$')
        for line in repaired.splitlines():
            match = string_property.match(line)
            if match:
                inner = re.sub(r'(?<!\\)"', r'\\"', match.group(2))
                line = f'{match.group(1)}"{inner}"{match.group(3)}'
            repaired_lines.append(line)
        repaired = "\n".join(repaired_lines)
        try:
            value = json.loads(repaired)
        except json.JSONDecodeError:
            # A long response may omit exactly one item-closing brace while
            # still finishing the outer items array and response object.
            marker = "\n  ]\n}"
            if marker in repaired:
                brace_repaired = repaired.replace(marker, "\n    }" + marker, 1)
                try:
                    value = json.loads(brace_repaired)
                except json.JSONDecodeError:
                    raise original_error
            else:
                raise original_error
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        value = value[0]
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError("模型输出必须是包含 items 数组的 JSON 对象")
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def reset_work(set_number: int, work_id: str) -> dict[str, Any]:
    output = ENTITY_ROOT / f"workcrpset{set_number}.json"
    if not output.exists() or not output.stat().st_size:
        raise ValueError(f"CRP 文件不存在或为空：{output}")
    responses = load_partial_response_array(output)
    try:
        value = load_json(output)
    except json.JSONDecodeError:
        pass
    else:
        responses = value if isinstance(value, list) else [value]
    kept: list[dict[str, Any]] = []
    removed = 0
    for response in responses:
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            continue
        copied = dict(response)
        copied["items"] = []
        for item in response["items"]:
            if isinstance(item, dict) and item_work_id(item) == work_id:
                removed += 1
            else:
                copied["items"].append(item)
        if copied["items"]:
            kept.append(copied)
    if not removed:
        raise ValueError(f"{output} 中没有找到 {work_id}")
    atomic_write_json(output, kept)
    raw_dir = ENTITY_ROOT / "deepseek_crp_raw" / f"set{set_number}"
    deleted_cache: list[str] = []
    if raw_dir.exists():
        for path in raw_dir.glob(f"{work_id}.*"):
            if path.is_file():
                path.unlink()
                deleted_cache.append(str(path.resolve()))
    return {
        "set": set_number, "workId": work_id, "removedItems": removed,
        "remainingResponses": len(kept), "deletedCacheFiles": deleted_cache,
        "output": str(output.resolve()),
    }


def recover_invalid(set_numbers: list[int]) -> dict[str, Any]:
    recovered: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for set_number in set_numbers:
        entity_path = ENTITY_ROOT / f"workentityset{set_number}.json"
        output = ENTITY_ROOT / f"workcrpset{set_number}.json"
        raw_dir = ENTITY_ROOT / "deepseek_crp_raw" / f"set{set_number}"
        if not entity_path.exists() or not raw_dir.exists():
            continue
        works = load_json(entity_path)
        work_by_id = {work.get("workId"): work for work in works if isinstance(work, dict)}
        if output.exists() and output.stat().st_size:
            value = load_json(output)
            responses = value if isinstance(value, list) else [value]
        else:
            responses = []
        done = completed_ids(responses)
        changed = False
        for invalid_path in sorted(raw_dir.glob("*.invalid.txt")):
            work_id = invalid_path.name.removesuffix(".invalid.txt")
            work = work_by_id.get(work_id)
            try:
                if work is None:
                    raise ValueError("作品不在对应 workentity set 中")
                parsed = parse_model_content(invalid_path.read_text(encoding="utf-8-sig"))
                for item in parsed.get("items", []):
                    enforce_existing_schema(item, work)
                returned_ids = {item_work_id(item) for item in parsed.get("items", []) if isinstance(item, dict)}
                if returned_ids != {work_id}:
                    raise ValueError(f"作品 ID 不一致：{returned_ids}")
                action = "already_present"
                if work_id not in done:
                    done.add(work_id)
                    remaining_ids = [w.get("workId") for w in works if w.get("workId") not in done]
                    update_state(
                        parsed, current_id=work_id, next_id=remaining_ids[0] if remaining_ids else None,
                        processed=len(done), total=len(works),
                    )
                    responses.append(parsed)
                    changed = True
                    action = "merged"
                invalid_path.unlink()
                recovered.append({"set": set_number, "workId": work_id, "action": action})
            except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                unresolved.append({
                    "set": set_number, "workId": work_id,
                    "error": f"{type(error).__name__}: {error}",
                })
        if changed:
            atomic_write_json(output, responses)
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "recovered": recovered,
        "unresolved": unresolved,
    }
    atomic_write_json(ENTITY_ROOT / "deepseek_crp_raw" / "invalid_recovery_report.json", report)
    return report


def update_state(response: dict[str, Any], *, current_id: str, next_id: str | None,
                 processed: int, total: int) -> None:
    response["processingState"] = {
        "processed": processed,
        "remaining": total - processed,
        "total": total,
        "currentWorkId": current_id,
        "nextWorkId": next_id,
        "isCompleted": processed == total,
    }


def enforce_existing_schema(item: dict[str, Any], source_work: dict[str, Any]) -> None:
    forbidden = {"dimension_evidence", "evidenceIndexes", "dimensionCode", "relationType", "evidenceWeight"}
    for key in forbidden:
        item.pop(key, None)
    basic = item.get("basic")
    if not isinstance(basic, dict):
        raise ValueError("模型输出缺少 basic 对象")
    work_id = source_work.get("workId")
    basic.setdefault("contentId", work_id)
    basic.setdefault("parentContentId", None)
    basic.setdefault("workId", work_id)
    basic.setdefault("sourceType", "CULTURAL_WORK")
    basic.setdefault("dataOrigin", "PROVIDED")
    basic.setdefault("isSynthetic", False)
    semantic_container = item.get("semantic")
    nested_top_level_keys = (
        "experience_vector", "psychology_vector", "media_vector", "vector_facts",
        "experience_vector_summary", "personality_affinity", "supporting_evidence", "quality_control",
    )
    if isinstance(semantic_container, dict):
        for nested_key in nested_top_level_keys:
            if nested_key not in item and nested_key in semantic_container:
                item[nested_key] = semantic_container.pop(nested_key)
    if "social" in item.get("psychology_vector", {}) and "social_relationship" not in item["psychology_vector"]:
        item["psychology_vector"]["social_relationship"] = item["psychology_vector"].pop("social")
        facts_to_normalize = item.get("vector_facts")
        if isinstance(facts_to_normalize, list):
            for fact in facts_to_normalize:
                if not isinstance(fact, dict):
                    continue
                path = fact.get("dimensionPath")
                if isinstance(path, str) and path.startswith("psychology_vector.social."):
                    fact["dimensionPath"] = path.replace(
                        "psychology_vector.social.",
                        "psychology_vector.social_relationship.",
                        1,
                    )
    media = item.get("media_vector")
    facts_to_normalize = item.get("vector_facts")
    if isinstance(facts_to_normalize, list):
        for fact in facts_to_normalize:
            if not isinstance(fact, dict) or "dimensionPath" in fact:
                continue
            # Repair observed model spelling/format variants without guessing a path.
            for alias in ("imdensionPath", "dimension_path", "dimensionpath"):
                value = fact.get(alias)
                if isinstance(value, str) and value:
                    fact["dimensionPath"] = fact.pop(alias)
                    break
    if isinstance(media, dict) and len(media) == 1 and isinstance(facts_to_normalize, list):
        media_key = next(iter(media))
        short_media_key = media_key.removesuffix("_vector")
        short_prefix = f"media_vector.{short_media_key}."
        canonical_prefix = f"media_vector.{media_key}."
        for fact in facts_to_normalize:
            if not isinstance(fact, dict):
                continue
            path = fact.get("dimensionPath")
            if isinstance(path, str) and path.startswith(short_prefix):
                fact["dimensionPath"] = path.replace(short_prefix, canonical_prefix, 1)
    required = (
        "semantic", "experience_vector", "psychology_vector", "media_vector", "vector_facts",
        "experience_vector_summary", "personality_affinity", "supporting_evidence", "quality_control",
    )
    missing = [key for key in required if key not in item]
    if missing:
        raise ValueError(f"模型输出与现有 workcrp schema 不一致，缺少字段：{', '.join(missing)}")
    semantic = item.get("semantic")
    semantic_keys = ("summary", "themes", "keywords", "core_experience", "experience_introduction")
    if isinstance(semantic, dict) and not semantic.get("experience_introduction"):
        summary_text = semantic.get("summary")
        experience_summary = item.get("experience_vector_summary")
        experience_text = (
            experience_summary.get("experience_description")
            if isinstance(experience_summary, dict) else None
        )
        parts = [
            value.strip() for value in (summary_text, experience_text)
            if isinstance(value, str) and value.strip()
        ]
        if parts:
            semantic["experience_introduction"] = "\n".join(dict.fromkeys(parts))
    if not isinstance(semantic, dict) or any(key not in semantic for key in semantic_keys):
        raise ValueError("semantic 缺少现有 schema 字段：" + ", ".join(semantic_keys))
    affinity = item.get("personality_affinity")
    affinity_keys = (
        "Ni", "Ne", "Ti", "Te", "Fi", "Fe", "Si", "Se",
        "openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism",
        "Assertive", "Turbulent",
    )
    if not isinstance(affinity, dict) or any(key not in affinity for key in affinity_keys):
        raise ValueError("personality_affinity 必须包含完整 15 个字段")
    summary = item.get("experience_vector_summary")
    summary_keys = (
        "experience_description", "structure_description",
        "cognitive_expression", "behavioral_tendency_description",
    )
    if not isinstance(summary, dict) or any(
        not isinstance(summary.get(key), str) or not summary[key].strip() for key in summary_keys
    ):
        raise ValueError(
            "experience_vector_summary 必须包含四个非空字段：" + ", ".join(summary_keys)
        )
    expected_fact_paths: set[str] = set()
    for root_key in ("experience_vector", "psychology_vector", "media_vector"):
        def collect_numeric(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    collect_numeric(child_value, f"{path}.{child_key}")
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                expected_fact_paths.add(path)
        collect_numeric(item[root_key], root_key)
    facts = item.get("vector_facts")
    if isinstance(facts, list):
        # The established dataset maps numerical vector leaves only. Discard
        # model-added explanations for descriptive media strings.
        facts[:] = [
            fact for fact in facts
            if isinstance(fact, dict) and fact.get("dimensionPath") in expected_fact_paths
        ]
    actual_fact_paths = {
        fact.get("dimensionPath") for fact in facts
        if isinstance(fact, dict) and isinstance(fact.get("dimensionPath"), str)
    } if isinstance(facts, list) else set()
    if actual_fact_paths != expected_fact_paths or len(facts) != len(expected_fact_paths):
        raise ValueError(
            f"vector_facts 覆盖不完整：需要 {len(expected_fact_paths)} 条，实际 {len(facts) if isinstance(facts, list) else 0} 条，"
            f"缺少 {len(expected_fact_paths - actual_fact_paths)} 个维度"
        )
    quality = item.get("quality_control")
    if not isinstance(quality, dict) or not quality:
        evidence = item.get("supporting_evidence")
        vector_facts = item.get("vector_facts")
        evidence_sufficient = isinstance(evidence, list) and len(evidence) >= 8
        vector_coverage = isinstance(vector_facts, list) and bool(vector_facts)
        reasons: list[str] = []
        if not evidence_sufficient:
            reasons.append("supporting_evidence 少于 8 条，证据不足")
        if not vector_coverage:
            reasons.append("vector_facts 为空，维度解释覆盖不足")
        trainable = evidence_sufficient and vector_coverage
        item["quality_control"] = {
            "trainingStatus": "TRAINABLE" if trainable else "REJECTED",
            "evidenceSufficiency": "SUFFICIENT" if evidence_sufficient else "INSUFFICIENT",
            "cleanlinessPassed": True,
            "schemaPassed": True,
            "vectorCoveragePassed": vector_coverage,
            "rejectionReasons": reasons,
        }


def generate_one_work(client: DeepSeekClient, set_number: int, work: dict[str, Any],
                      system_prompt: str, thinking: bool, max_tokens: int) -> dict[str, Any]:
    work_id = work.get("workId")
    if not work_id:
        raise ValueError("作品缺少 workId")
    user_prompt = (
        "继续生成尚未完成的作品 CRP。严格按照系统规范，只处理下面提供的这一部作品，"
        "不得重做其他作品。只输出一个合法 JSON 对象，顶层包含 processingState 和 items；"
        "items 必须恰好包含这一部作品。\n\n输入作品 JSON：\n"
        + json.dumps(work, ensure_ascii=False, indent=2)
    )
    raw_dir = ENTITY_ROOT / "deepseek_crp_raw" / f"set{set_number}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    response_path = raw_dir / f"{work_id}.response.json"
    content_path = raw_dir / f"{work_id}.content.txt"
    parsed = None
    if content_path.exists() and content_path.stat().st_size:
        try:
            parsed = parse_model_content(content_path.read_text(encoding="utf-8-sig"))
            cached_items = parsed.get("items", [])
            if not cached_items:
                raise ValueError("缓存 items 为空")
            for cached_item in cached_items:
                if not isinstance(cached_item, dict):
                    raise ValueError("缓存 item 不是对象")
                enforce_existing_schema(cached_item, work)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            parsed = None
    if parsed is None:
        response = client.chat(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            thinking=thinking, max_tokens=max_tokens,
        )
        content = response["choices"][0]["message"]["content"]
        atomic_write_json(response_path, response)
        content_path.write_text(content + "\n", encoding="utf-8")
        try:
            parsed = parse_model_content(content)
        except json.JSONDecodeError as error:
            invalid_path = raw_dir / f"{work_id}.invalid.txt"
            invalid_path.write_text(content + "\n", encoding="utf-8")
            raise ValueError(
                f"模型输出 JSON 语法错误（第 {error.lineno} 行，第 {error.colno} 列）；"
                f"失败内容已保存到 {invalid_path.resolve()}"
            ) from error
    for item in parsed["items"]:
        enforce_existing_schema(item, work)
    returned_ids = {item_work_id(item) for item in parsed["items"] if isinstance(item, dict)}
    if returned_ids != {work_id}:
        raise ValueError(f"模型返回作品与输入不一致：期望 {work_id}，实际 {sorted(x for x in returned_ids if x)}")
    return parsed


def resume_set(
    client: DeepSeekClient,
    set_number: int,
    max_items: int,
    thinking: bool,
    max_tokens: int,
    output: Path | None = None,
    fail_fast: bool = False,
) -> dict[str, Any]:
    entity_path = ENTITY_ROOT / f"workentityset{set_number}.json"
    canonical_output = ENTITY_ROOT / f"workcrpset{set_number}.json"
    output = output or canonical_output
    if not entity_path.exists() or entity_path.stat().st_size == 0:
        return {"set": set_number, "status": "waiting", "reason": "work entity 文件为空", "output": str(output)}
    works = load_json(entity_path)
    if not isinstance(works, list):
        raise ValueError(f"{entity_path} 顶层必须是数组")
    responses = load_existing(set_number, output)
    if output.resolve() == canonical_output.resolve() and output.exists() and output.stat().st_size:
        backup = ENTITY_ROOT / f"workcrpset{set_number}.pre-resume.json"
        if not backup.exists():
            shutil.copy2(output, backup)
    done = completed_ids(responses)
    remaining = [work for work in works if work.get("workId") not in done]
    if not remaining:
        atomic_write_json(output, responses)
        return {"set": set_number, "status": "completed", "processed": len(done), "total": len(works), "output": str(output.resolve())}

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8-sig")
    selected = remaining[:max_items]
    succeeded = 0
    failures: list[dict[str, Any]] = []
    consecutive_failures = 0
    for work in selected:
        work_id = work.get("workId")
        raw_dir = ENTITY_ROOT / "deepseek_crp_raw" / f"set{set_number}"
        raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            parsed = generate_one_work(client, set_number, work, system_prompt, thinking, max_tokens)
        except (DeepSeekError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
            failure = {
                "workId": work_id, "workName": work.get("workName"),
                "error": f"{type(error).__name__}: {error}",
                "failedAt": datetime.now(timezone.utc).isoformat(),
            }
            failures.append(failure)
            atomic_write_json(raw_dir / "failures_latest.json", failures)
            print(json.dumps({"event": "item_failed", **failure}, ensure_ascii=False), file=sys.stderr)
            consecutive_failures += 1
            if fail_fast:
                raise
            if consecutive_failures >= 3:
                print("连续失败 3 部，本 set 提前结束，避免持续消耗额度。", file=sys.stderr)
                break
            continue
        consecutive_failures = 0
        done.add(work_id)
        remaining_ids = [item.get("workId") for item in works if item.get("workId") not in done]
        update_state(
            parsed, current_id=work_id, next_id=remaining_ids[0] if remaining_ids else None,
            processed=len(done), total=len(works),
        )
        responses.append(parsed)
        atomic_write_json(output, responses)
        succeeded += 1

    processed = len(completed_ids(responses))
    return {
        "set": set_number,
        "status": "completed" if processed == len(works) else "in_progress",
        "attemptedThisRun": succeeded + len(failures),
        "generatedThisRun": succeeded,
        "failedThisRun": len(failures),
        "failures": failures,
        "processed": processed,
        "remaining": len(works) - processed,
        "total": len(works),
        "output": str(output.resolve()),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="从已有进度续跑 7 组作品 CRP；默认每次每组生成 1 部")
    parser.add_argument("--set", type=int, choices=range(1, 8), help="只处理指定 set；不填则依次检查 set1-set7")
    parser.add_argument("--max-items", type=int, default=1, help="本次每个 set 最多新增多少部")
    parser.add_argument(
        "--max-tokens", type=int, default=32768,
        help="单部 CRP 的最大输出 token；默认留足 vector_facts 和 JSON 闭合空间",
    )
    parser.add_argument("--no-thinking", action="store_true")
    parser.add_argument("--fail-fast", action="store_true", help="任一作品失败时立即停止；默认记录失败并继续")
    parser.add_argument("--reset-work-id", help="从指定 set 删除一条错误 CRP 及其缓存，不调用 API")
    parser.add_argument("--recover-invalid", action="store_true", help="清洗并合并可恢复的 invalid 响应，不调用 API")
    args = parser.parse_args()
    if args.max_items < 1 or args.max_tokens < 1:
        parser.error("--max-items 和 --max-tokens 必须大于 0")

    try:
        if args.reset_work_id:
            if not args.set:
                parser.error("--reset-work-id 必须同时指定 --set")
            print(json.dumps({"ok": True, **reset_work(args.set, args.reset_work_id)}, ensure_ascii=False, indent=2))
            return
        if args.recover_invalid:
            set_numbers = [args.set] if args.set else list(range(1, 8))
            print(json.dumps({"ok": True, **recover_invalid(set_numbers)}, ensure_ascii=False, indent=2))
            return
        client = DeepSeekClient()
        sets = [args.set] if args.set else list(range(1, 8))
        results = [
            resume_set(client, number, args.max_items, not args.no_thinking, args.max_tokens,
                       fail_fast=args.fail_fast)
            for number in sets
        ]
        print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    except (DeepSeekError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
