from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.import_prompt_dataset import (
    DEFAULT_CHARACTERS,
    DEFAULT_WORKS,
    VALID_GENRES,
    VALID_RELATIONS,
    RELATION_GENRES,
    SUBCATEGORY_RANGE,
    import_dataset,
    load_array,
    stable_id,
    upsert,
    validate,
    validation_issues,
)
from database.mysql_client import connect, transaction


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "DataSetRaw" / "import_runs"
REPORT_ROOT = ROOT / "DataSetRaw" / "reports"
LEGACY_RUN_ROOT = ROOT / "DataSet" / "import_runs"
TABLE_ORDER = [
    "t_character_info",
    "t_character_name",
    "t_real_character_attribute",
    "t_character_work",
    "t_work_creator_relation",
    "t_work_subcategory_relation",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_dataset(character_path: Path, work_path: Path) -> dict[str, Any]:
    characters = load_array(character_path)
    works = load_array(work_path)
    errors = validation_issues(characters, works)
    warnings: list[str] = []

    valid_work_objects = [work for work in works if isinstance(work, dict)]
    valid_character_objects = [character for character in characters if isinstance(character, dict)]

    for work in valid_work_objects:
        work_id = str(work.get("workId", "<missing>"))
        introduction = work.get("introduction")
        if isinstance(introduction, str) and len(introduction.strip()) < 80:
            warnings.append(f"{work_id}: introduction 低于 Prompt 建议的 80 字")

    for character in valid_character_objects:
        cid = str(character.get("id", ""))
        introduction = character.get("introduction")
        if isinstance(introduction, str) and len(introduction.strip()) < 100:
            warnings.append(f"{cid}: introduction 低于 Prompt 建议的 100 字")

    unknown_birth = sum(c.get("beginCentury") is None for c in valid_character_objects)
    historical_china = [c for c in valid_character_objects if c.get("realSubType") == 2 and c.get("field") == 10]
    duplicate_work_names = {
        name for name in (w.get("workName") for w in valid_work_objects) if name
        if sum(other.get("workName") == name for other in valid_work_objects) > 1
    }
    return {
        "schema": "rgmj-prompt-dataset-analysis/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "characters": str(character_path.resolve()),
            "works": str(work_path.resolve()),
            "characterSha256": sha256_file(character_path),
            "workSha256": sha256_file(work_path),
        },
        "counts": {
            "characters": len(characters), "works": len(works),
            "creatorRelations": sum(len(w.get("creators", [])) for w in valid_work_objects if isinstance(w.get("creators"), list)),
            "ownWorkReferences": sum(len(c.get("ownWork", [])) for c in valid_character_objects if isinstance(c.get("ownWork"), list)),
            "unknownBeginYear": unknown_birth,
            "missingWorkYear": sum(w.get("year") in (0, None, "unknown") for w in valid_work_objects),
            "unknownCountry": sum(w.get("countryCode") in (None, "") for w in valid_work_objects),
            "subcategoryTags": sum(len(w.get("subcategories", [] if w.get("subcategory") is None else [w["subcategory"]])) for w in valid_work_objects if isinstance(w.get("subcategories", [] if w.get("subcategory") is None else [w["subcategory"]]), list)),
        },
        "crossReferences": {
            "characterIds": len({str(x.get('id')) for x in valid_character_objects}),
            "workIds": len({str(x.get('workId')) for x in valid_work_objects}),
            "broken": 0,
        },
        "duplicateWorkNames": sorted(duplicate_work_names),
        "errors": errors,
        "warnings": warnings,
        "isStructurallyValid": not errors,
        "qualitySignals": {
            "unknownBeginYear": unknown_birth,
            "missingWorkYear": sum(w.get("year") in (0, None, "unknown") for w in valid_work_objects),
            "unknownWorkCountry": sum(w.get("countryCode") in (None, "") for w in valid_work_objects),
            "missingHistoricalDynasty": sum(c.get("dynasty") is None for c in historical_china),
        },
        "factualCompleteness": {
            "beginYearCoverage": round((len(valid_character_objects)-unknown_birth)/len(valid_character_objects),4) if valid_character_objects else 1.0,
            "historicalDynastyCoverage": round(sum(c.get("dynasty") is not None for c in historical_china)/len(historical_china),4) if historical_china else 1.0,
        },
        "isPromptComplete": not errors and not warnings,
    }


def write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(output)


def tracked_ids(characters: list[dict[str, Any]], works: list[dict[str, Any]]) -> dict[str, list[int]]:
    result = {table: [] for table in TABLE_ORDER}
    for character in characters:
        logical_id = str(character["id"])
        result["t_character_info"].append(stable_id("character", logical_id))
        result["t_real_character_attribute"].append(stable_id("real-attribute", logical_id))
        for order, _ in enumerate(character["names"]):
            result["t_character_name"].append(stable_id("character-name", f"{logical_id}:{order}"))
    for work in works:
        work_logical_id = str(work["workId"])
        result["t_character_work"].append(stable_id("work", work_logical_id))
        for creator in work["creators"]:
            character_logical_id = str(creator["characterId"])
            relation_type = creator["relationType"]
            result["t_work_creator_relation"].append(stable_id(
                "work-creator", f"{work_logical_id}:{character_logical_id}:{relation_type}"
            ))
        for subcategory in work.get("subcategories", [] if work.get("subcategory") is None else [work["subcategory"]]):
            result["t_work_subcategory_relation"].append(stable_id("work-subcategory", f"{work_logical_id}:{subcategory}"))
    current_work_ids={str(work["workId"]) for work in works}
    for character in characters:
        character_logical_id=str(character["id"])
        for own in character.get("ownWork",[]):
            work_logical_id=str(own["workId"])
            if work_logical_id not in current_work_ids:
                result["t_work_creator_relation"].append(stable_id("work-creator",f"{work_logical_id}:{character_logical_id}:{own['relationType']}"))
    return result


def select_rows(cursor: Any, table: str, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join(["%s"] * len(ids))
    cursor.execute(f"SELECT * FROM `{table}` WHERE id IN ({placeholders})", ids)
    return list(cursor.fetchall())


def table_exists(cursor: Any, database: str, table: str) -> bool:
    cursor.execute("SELECT COUNT(*) count FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (database, table))
    return cursor.fetchone()["count"] == 1


def create_run_id(dataset_name: str, report: dict[str, Any]) -> str:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    combined = report["inputs"]["characterSha256"] + report["inputs"]["workSha256"]
    short_hash = hashlib.sha256(combined.encode("ascii")).hexdigest()[:8]
    return f"{dataset_name}_{timestamp}_{short_hash}"


def run_import(
    config: MySQLConfig,
    dataset_name: str,
    character_path: Path,
    work_path: Path,
    retry_of: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    report = analyze_dataset(character_path, work_path)
    if not report["isStructurallyValid"]:
        raise ValueError("JSON 结构或枚举映射失败，请先查看分析报告")
    characters = load_array(character_path)
    works = load_array(work_path)
    ids = tracked_ids(characters, works)
    run_id = create_run_id(dataset_name, report)
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "analysis.json"
    manifest_path = run_dir / "manifest.json"
    write_report(report, report_path)

    manifest: dict[str, Any] = {
        "schema": "rgmj-dataset-import-run/v1",
        "runId": run_id,
        "datasetName": dataset_name,
        "status": "running",
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "inputs": report["inputs"],
        "trackedIds": ids,
        "beforeRows": {},
        "result": None,
    }
    if retry_of:
        manifest["retryOfRunId"] = retry_of
    write_report(manifest, manifest_path)
    try:
        with transaction(config) as connection:
            with connection.cursor() as cursor:
                active_tables=[table for table in TABLE_ORDER if table_exists(cursor,config.database,table)]
                manifest["tableOrder"]=active_tables
                manifest["optionalTablesSkipped"]=[table for table in TABLE_ORDER if table not in active_tables]
                manifest["trackedIds"]={table:ids[table] for table in active_tables}
                for table in active_tables:
                    manifest["beforeRows"][table] = select_rows(cursor, table, ids.get(table, []))
                result = import_dataset(cursor, config, characters, works)
        manifest["status"] = "succeeded"
        manifest["finishedAt"] = datetime.now(timezone.utc).isoformat()
        manifest["result"] = result
        write_report(manifest, manifest_path)
        (RUN_ROOT / f"{dataset_name}_latest.json").write_text(
            json.dumps({"runId": run_id, "manifest": str(manifest_path.resolve())}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_report({"runId": run_id, "manifest": str(manifest_path.resolve())},
                     RUN_ROOT / f"{dataset_name}_latest_attempt.json")
        return manifest_path, result
    except Exception as error:
        manifest["status"] = "failed"
        manifest["finishedAt"] = datetime.now(timezone.utc).isoformat()
        manifest["error"] = repr(error)
        write_report(manifest, manifest_path)
        write_report({"runId": run_id, "manifest": str(manifest_path.resolve())},
                     RUN_ROOT / f"{dataset_name}_latest_attempt.json")
        raise


def resolve_manifest(value: Path) -> Path:
    if not value.exists():
        candidates = [RUN_ROOT / value.name, LEGACY_RUN_ROOT / value.name]
        existing = next((candidate for candidate in candidates if candidate.exists()), None)
        if existing is None:
            raise FileNotFoundError(f"找不到 manifest 或指针文件：{value}")
        value = existing
    data = json.loads(value.read_text(encoding="utf-8-sig"))
    if "manifest" in data:
        target = Path(data["manifest"])
        if target.exists():
            return target
        run_id = data.get("runId") or target.parent.name
        for root in (RUN_ROOT, LEGACY_RUN_ROOT):
            relocated = root / str(run_id) / "manifest.json"
            if relocated.exists():
                return relocated
        raise FileNotFoundError(f"指针存在，但目标 manifest 已丢失：{target}")
    return value


def retry(config: MySQLConfig, manifest_path: Path) -> tuple[Path, dict[str, Any]]:
    source_path = resolve_manifest(manifest_path)
    source = json.loads(source_path.read_text(encoding="utf-8-sig"))
    if source.get("status") != "failed":
        raise RuntimeError(f"只有 failed 运行可重试，当前为 {source.get('status')}")
    inputs = source.get("inputs") or {}
    character_path = Path(str(inputs.get("characters", "")))
    work_path = Path(str(inputs.get("works", "")))
    if not character_path.is_file() or not work_path.is_file():
        raise FileNotFoundError("失败批次的输入 JSON 已移动或丢失，不能安全重试")
    return run_import(config, str(source["datasetName"]), character_path, work_path,
                      retry_of=str(source["runId"]))


def delete_ids(cursor: Any, table: str, ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join(["%s"] * len(ids))
    return cursor.execute(f"DELETE FROM `{table}` WHERE id IN ({placeholders})", ids)


def restore_rows(cursor: Any, table: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        values = dict(row)
        upsert(cursor, table, values, [name for name in values if name != "id"])


def rollback(config: MySQLConfig, manifest_path: Path) -> dict[str, int]:
    manifest_path = resolve_manifest(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("status") != "succeeded":
        raise RuntimeError(f"只有 succeeded 运行可回滚，当前为 {manifest.get('status')}")
    deleted: dict[str, int] = {}
    with transaction(config) as connection:
        with connection.cursor() as cursor:
            table_order=manifest.get("tableOrder",list(manifest["trackedIds"]))
            for table in reversed(table_order):
                deleted[table] = delete_ids(cursor, table, manifest["trackedIds"].get(table, []))
            for table in table_order:
                restore_rows(cursor, table, manifest["beforeRows"].get(table, []))
    manifest["status"] = "rolled_back"
    manifest["rolledBackAt"] = datetime.now(timezone.utc).isoformat()
    manifest["rollbackDeleted"] = deleted
    write_report(manifest, manifest_path)
    return deleted


def status(config: MySQLConfig, manifest_path: Path) -> dict[str, Any]:
    manifest_path = resolve_manifest(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    counts: dict[str, int] = {}
    missing_rows: dict[str, int] = {}
    connection = connect(config)
    try:
        with connection.cursor() as cursor:
            table_order=manifest.get("tableOrder",list(manifest["trackedIds"]))
            for table in table_order:
                counts[table] = len(select_rows(cursor, table, manifest["trackedIds"].get(table, [])))
                missing_rows[table] = max(0, len(manifest["trackedIds"].get(table, [])) - counts[table])
            work_ids = manifest["trackedIds"]["t_character_work"]
            character_ids = manifest["trackedIds"]["t_character_info"]
            checks = {"invalidNameLanguage": 0, "duplicateStandardNames": 0,
                      "invalidRealSubtype": 0, "chinaWrongCountry": 0,
                      "invalidGender": 0, "invalidCreativeEntityType": 0,
                      "invalidField": 0, "invalidDynasty": 0,
                      "chinaMissingDynasty": 0,
                      "virtualCreators": 0, "duplicateRelations": 0,
                      "invalidRelationGenre": 0, "invalidSubcategoryGenre": 0,
                      "invalidTagGenre": 0, "invalidPrimaryTag": 0,
                      "missingOrDuplicatePrimaryTag": 0}
            if character_ids:
                cp=",".join(["%s"]*len(character_ids))
                cursor.execute(f"SELECT COUNT(*) count FROM t_character_name WHERE character_info_id IN ({cp}) AND language NOT IN (0,1,2,3)",character_ids); checks["invalidNameLanguage"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM (SELECT character_info_id,language FROM t_character_name WHERE character_info_id IN ({cp}) AND deleted=0 AND language IN (0,1,2) GROUP BY character_info_id,language HAVING COUNT(*)>1) q",character_ids); checks["duplicateStandardNames"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM t_real_character_attribute WHERE character_info_id IN ({cp}) AND deleted=0 AND sub_type NOT IN (1,2)",character_ids); checks["invalidRealSubtype"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM t_real_character_attribute r LEFT JOIN t_word_countries c ON c.id=r.area WHERE r.character_info_id IN ({cp}) AND r.deleted=0 AND r.sub_type=2 AND (c.code IS NULL OR c.code<>'CHN')",character_ids); checks["chinaWrongCountry"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM t_character_info WHERE id IN ({cp}) AND gender NOT IN (0,1,2,3)",character_ids); checks["invalidGender"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM t_real_character_attribute WHERE character_info_id IN ({cp}) AND deleted=0 AND creative_entity_type NOT IN (1,2,3,4,5,6,7,8,9,10,99)",character_ids); checks["invalidCreativeEntityType"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM t_real_character_attribute WHERE character_info_id IN ({cp}) AND deleted=0 AND ((sub_type=2 AND field NOT BETWEEN 0 AND 10) OR (sub_type=1 AND field NOT BETWEEN 0 AND 8))",character_ids); checks["invalidField"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM t_real_character_attribute WHERE character_info_id IN ({cp}) AND deleted=0 AND (dynasty=8 OR dynasty NOT BETWEEN 1 AND 21 OR (sub_type=1 AND dynasty IS NOT NULL))",character_ids); checks["invalidDynasty"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM t_real_character_attribute WHERE character_info_id IN ({cp}) AND deleted=0 AND sub_type=2 AND dynasty IS NULL",character_ids); checks["chinaMissingDynasty"]=cursor.fetchone()["count"]
            if work_ids:
                placeholders = ",".join(["%s"] * len(work_ids))
                cursor.execute(
                    f"""SELECT COUNT(*) AS count FROM t_work_creator_relation r
                        LEFT JOIN t_character_info c ON c.id=r.character_id
                        LEFT JOIN t_character_work w ON w.id=r.work_id
                        WHERE r.work_id IN ({placeholders}) AND (c.id IS NULL OR w.id IS NULL)""",
                    work_ids,
                )
                broken = cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM t_work_creator_relation r JOIN t_character_info c ON c.id=r.character_id WHERE r.work_id IN ({placeholders}) AND (c.character_type<>1 OR c.deleted<>0)",work_ids); checks["virtualCreators"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT COUNT(*) count FROM (SELECT work_id,character_id,relation_type FROM t_work_creator_relation WHERE work_id IN ({placeholders}) GROUP BY work_id,character_id,relation_type HAVING COUNT(*)>1) q",work_ids); checks["duplicateRelations"]=cursor.fetchone()["count"]
                cursor.execute(f"SELECT w.genre,w.subcategory,r.relation_type FROM t_character_work w LEFT JOIN t_work_creator_relation r ON r.work_id=w.id WHERE w.id IN ({placeholders})",work_ids)
                for row in cursor.fetchall():
                    rel=row["relation_type"]; genre=row["genre"]; sub=int(row["subcategory"]) if row["subcategory"] not in (None,"") else None
                    if rel not in (None,1,99) and genre not in RELATION_GENRES.get(rel,set()): checks["invalidRelationGenre"]+=1
                    if sub is not None and sub not in SUBCATEGORY_RANGE.get(genre,[]): checks["invalidSubcategoryGenre"]+=1
                if "t_work_subcategory_relation" in table_order:
                    cursor.execute(f"SELECT w.genre,w.subcategory,r.subcategory tag,r.is_primary FROM t_character_work w JOIN t_work_subcategory_relation r ON r.work_id=w.id WHERE w.id IN ({placeholders})",work_ids)
                    for row in cursor.fetchall():
                        tag=int(row["tag"]); primary=int(row["subcategory"]) if row["subcategory"] not in (None,"") else None
                        if tag not in SUBCATEGORY_RANGE.get(row["genre"],[]): checks["invalidTagGenre"]+=1
                        if row["is_primary"] and tag != primary: checks["invalidPrimaryTag"]+=1
                    cursor.execute(f"SELECT COUNT(*) count FROM (SELECT w.id FROM t_character_work w LEFT JOIN t_work_subcategory_relation r ON r.work_id=w.id AND r.is_primary=1 WHERE w.id IN ({placeholders}) AND w.subcategory IS NOT NULL GROUP BY w.id HAVING COUNT(r.id)<>1) q",work_ids)
                    checks["missingOrDuplicatePrimaryTag"]=cursor.fetchone()["count"]
            else:
                broken = 0
    finally:
        connection.close()
    return {"runId": manifest["runId"], "manifestStatus": manifest["status"], "rows": counts,
            "missingRows": missing_rows, "brokenRelations": broken, "strongChecks": checks,
            "passed": broken == 0 and all(v == 0 for v in checks.values()) and all(v == 0 for v in missing_rows.values())}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="RGMJ JSON→MySQL 可回滚数据管道")
    parser.add_argument("command", choices=["analyze", "import", "status", "rollback", "retry"])
    parser.add_argument("--characters", type=Path, default=DEFAULT_CHARACTERS)
    parser.add_argument("--works", type=Path, default=DEFAULT_WORKS)
    parser.add_argument("--dataset-name", default="evaluation_300")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.command == "analyze":
        report = analyze_dataset(args.characters, args.works)
        output = args.report or REPORT_ROOT / f"{args.dataset_name}.analysis.json"
        write_report(report, output)
        print(json.dumps({"report": str(output.resolve()), "valid": report["isStructurallyValid"], "promptComplete": report["isPromptComplete"]}, ensure_ascii=False, indent=2))
        return

    config = MySQLConfig.from_env()
    if args.command == "import":
        manifest, result = run_import(config, args.dataset_name, args.characters, args.works)
        print(json.dumps({"manifest": str(manifest.resolve()), **result}, ensure_ascii=False, indent=2))
    elif args.command == "retry":
        if not args.manifest:
            parser.error("retry 必须显式提供 --manifest，避免误重试错误批次")
        manifest, result = retry(config, args.manifest)
        print(json.dumps({"manifest": str(manifest.resolve()), "retry": True, **result}, ensure_ascii=False, indent=2))
    else:
        manifest = args.manifest or RUN_ROOT / f"{args.dataset_name}_latest.json"
        result = status(config, manifest) if args.command == "status" else rollback(config, manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
