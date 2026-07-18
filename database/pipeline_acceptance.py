from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.dataset_pipeline import status as dataset_status
from database.enum_contract_check import check as check_enums
from database.recommendation_prior_evaluation import evaluate as evaluate_recommendation
from database.test_entity_contract import EntityContractTest


def contract_tests() -> dict[str, Any]:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EntityContractTest)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    return {"passed": result.wasSuccessful(), "testsRun": result.testsRun,
            "failures": [str(test) for test, _ in result.failures],
            "errors": [str(test) for test, _ in result.errors]}


def deepseek_status(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"provided": False, "passed": None}
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    return {"provided": True, "passed": report.get("readyForImport") is True,
            "status": report.get("status"), "batch": report.get("batch"),
            "issueCount": len(report.get("issues", [])), "report": str(path.resolve())}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="数据管道一键只读验收")
    parser.add_argument("--deepseek-report", type=Path)
    parser.add_argument("--manifest", type=Path, help="已导入批次的 manifest 或 latest 指针")
    parser.add_argument("--database", action="store_true", help="检查数据库与推荐先验，不执行写入")
    args = parser.parse_args()
    result: dict[str, Any] = {
        "schema": "rgmj-pipeline-acceptance/v1",
        "entityContract": contract_tests(),
        "enumContract": check_enums(args.database),
        "deepseekIntake": deepseek_status(args.deepseek_report),
        "databaseChecked": args.database,
    }
    if args.database:
        config = MySQLConfig.from_env()
        result["recommendationPrior"] = evaluate_recommendation(config)
        if args.manifest:
            result["datasetStatus"] = dataset_status(config, args.manifest)
    required = [result["entityContract"]["passed"], result["enumContract"]["passed"]]
    if args.deepseek_report:
        required.append(result["deepseekIntake"]["passed"])
    if args.database:
        required.append(result["recommendationPrior"].get("ready", False))
        if args.manifest:
            required.append(result["datasetStatus"].get("passed", False))
    result["passed"] = all(required)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 2)


if __name__ == "__main__":
    main()
