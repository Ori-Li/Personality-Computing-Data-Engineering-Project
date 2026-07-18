from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.dataset_pipeline import ROOT, write_report
from database.crp_dataset_pipeline import validate_dataset as validate_crp_dataset
from database.import_prompt_dataset import validation_issues


OUTPUT_ROOT = ROOT / "DataSetRaw" / "deepseek_runs"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    decoder = json.JSONDecoder()
    errors: list[str] = []
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
            tail = text[index + end:].strip()
            if tail and not tail.startswith("```"):
                errors.append(f"JSON 后仍存在非空文本：{tail[:80]}")
            if errors:
                raise ValueError("；".join(errors))
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError(f"{path} 中未找到完整 JSON；可能被截断或包含语法错误")


def arrays_from_inputs(raw: Path | None, character_raw: Path | None,
                       work_raw: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if raw:
        value = extract_json(raw)
        if not isinstance(value, dict):
            raise ValueError("--raw 的顶层必须是包含 characters 和 works 的对象")
        characters, works = value.get("characters"), value.get("works")
    else:
        if not character_raw or not work_raw:
            raise ValueError("必须使用 --raw，或同时提供 --characters-raw 与 --works-raw")
        characters, works = extract_json(character_raw), extract_json(work_raw)
    if not isinstance(characters, list) or not isinstance(works, list):
        raise ValueError("characters 和 works 必须都是 JSON 数组")
    return characters, works


def intake(batch: str, kind: str, raw: Path | None, character_raw: Path | None, work_raw: Path | None,
           model: str, prompt_version: str) -> tuple[Path, dict[str, Any]]:
    run_dir = OUTPUT_ROOT / batch
    if run_dir.exists():
        raise FileExistsError(f"批次目录已经存在：{run_dir}；请使用新 batch 名，避免覆盖原始响应")
    run_dir.mkdir(parents=True)
    source_paths = [path for path in (raw, character_raw, work_raw) if path]
    stored_sources = []
    for index, path in enumerate(source_paths, 1):
        stored = run_dir / f"raw_{index:02d}{path.suffix or '.txt'}"
        shutil.copyfile(path, stored)
        stored_sources.append({"originalPath": str(path.resolve()), "storedPath": str(stored.resolve()),
                               "sha256": sha256(stored)})
    report: dict[str, Any] = {
        "schema": "rgmj-deepseek-intake/v1",
        "batch": batch,
        "kind": kind,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "promptVersion": prompt_version,
        "sources": stored_sources,
        "status": "processing",
    }
    report_path = run_dir / "intake_report.json"
    try:
        if kind == "crp":
            if raw is None:
                raise ValueError("CRP 接入必须使用 --raw")
            dataset = extract_json(raw)
            if not isinstance(dataset, dict):
                raise ValueError("CRP 顶层必须是对象")
            issues = validate_crp_dataset(dataset)
            output = run_dir / "crp.json"
            output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            counts = {"items": len(dataset.get("items", [])) if isinstance(dataset.get("items"), list) else 0}
            outputs = {"crp": str(output.resolve())}
        else:
            characters, works = arrays_from_inputs(raw, character_raw, work_raw)
            issues = validation_issues(characters, works)
            character_output = run_dir / "characters.json"
            work_output = run_dir / "works.json"
            character_output.write_text(json.dumps(characters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            work_output.write_text(json.dumps(works, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            counts = {"characters": len(characters), "works": len(works)}
            outputs = {"characters": str(character_output.resolve()), "works": str(work_output.resolve())}
        report.update({
            "status": "validated" if not issues else "quarantined",
            "readyForImport": not issues,
            "counts": counts,
            "outputs": outputs,
            "issues": issues,
        })
    except Exception as error:
        report.update({"status": "quarantined", "readyForImport": False,
                       "fatalError": f"{type(error).__name__}: {error}"})
    write_report(report, report_path)
    return report_path, report


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="DeepSeek 原始响应提取、契约校验与失败隔离（不写数据库）")
    parser.add_argument("--batch", required=True)
    parser.add_argument("--kind", choices=("entity", "crp"), default="entity")
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--characters-raw", type=Path)
    parser.add_argument("--works-raw", type=Path)
    parser.add_argument("--model", default="deepseek-unknown")
    parser.add_argument("--prompt-version", default="entity-prompt-v1")
    args = parser.parse_args()
    report_path, report = intake(args.batch, args.kind, args.raw, args.characters_raw, args.works_raw,
                                 args.model, args.prompt_version)
    print(json.dumps({"report": str(report_path.resolve()), **report}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("readyForImport") else 2)


if __name__ == "__main__":
    main()
