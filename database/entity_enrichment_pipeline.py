from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.dataset_pipeline import RUN_ROOT, select_rows, write_report
from database.import_prompt_dataset import RELATION_GENRES, country_ids, require_tables, stable_id, table_columns, upsert
from database.mysql_client import connect, transaction


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_SCHEMA = "rgmj-entity-snapshot/v1"
PATCH_SCHEMA = "rgmj-entity-enrichment/v1"


def _rows(cursor: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor.execute(sql, params)
    return list(cursor.fetchall())


def export_snapshot(config: MySQLConfig, output: Path) -> dict[str, Any]:
    with connect(config) as db, db.cursor() as cursor:
        characters = _rows(cursor, """
            SELECT ci.id AS databaseId, ci.gender, ci.introduction,
                   a.sub_type AS realSubType, a.creative_entity_type AS creativeEntityType,
                   a.field, a.dynasty, a.begin_century AS beginCentury,
                   a.end_century AS endCentury, c.code AS countryCode
            FROM t_character_info ci
            JOIN t_real_character_attribute a ON a.character_info_id=ci.id AND a.deleted=0
            LEFT JOIN t_word_countries c ON c.id=a.area
            WHERE ci.deleted=0 ORDER BY ci.id
        """)
        names = _rows(cursor, """
            SELECT character_info_id AS databaseId, language, character_name AS name
            FROM t_character_name WHERE deleted=0 ORDER BY character_info_id, language, id
        """)
        works = _rows(cursor, """
            SELECT w.id AS databaseId, w.work_name AS workName, w.original_name AS originalName,
                   w.genre, w.subcategory, w.year, c.code AS countryCode,
                   w.introduction, w.auth_character_id AS primaryAuthorDatabaseId
            FROM t_character_work w LEFT JOIN t_word_countries c ON c.id=w.country_id
            WHERE w.deleted=0 ORDER BY w.id
        """)
        relations = _rows(cursor, """
            SELECT work_id AS workDatabaseId, character_id AS characterDatabaseId,
                   relation_type AS relationType, sort_order AS sortOrder
            FROM t_work_creator_relation ORDER BY work_id, sort_order, id
        """)
        tags = _rows(cursor, """
            SELECT work_id AS workDatabaseId, subcategory, is_primary AS isPrimary,
                   sort_order AS sortOrder FROM t_work_subcategory_relation
            ORDER BY work_id, sort_order, id
        """) if table_columns(cursor, config.database, "t_work_subcategory_relation") else []

    names_by_id: dict[int, list[dict[str, Any]]] = {}
    for name in names:
        names_by_id.setdefault(name.pop("databaseId"), []).append(name)
    relations_by_work: dict[int, list[dict[str, Any]]] = {}
    for relation in relations:
        relations_by_work.setdefault(relation.pop("workDatabaseId"), []).append(relation)
    tags_by_work: dict[int, list[dict[str, Any]]] = {}
    for tag in tags:
        tags_by_work.setdefault(tag.pop("workDatabaseId"), []).append(tag)
    for character in characters:
        character["names"] = names_by_id.get(character["databaseId"], [])
    for work in works:
        work["creators"] = relations_by_work.get(work["databaseId"], [])
        work["subcategories"] = tags_by_work.get(work["databaseId"], [])
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "database": config.database,
        "characters": characters,
        "works": works,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output.resolve()), "characters": len(characters), "works": len(works), "relations": len(relations)}


def load_patch(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema") != PATCH_SCHEMA:
        raise ValueError(f"补丁 schema 必须为 {PATCH_SCHEMA}")
    for key in ("newCharacters", "newWorks", "relations"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f"{key} 必须是数组")
    return data


def _character_matches(cursor: Any, ref: dict[str, Any]) -> list[int]:
    if "newId" in ref:
        return [stable_id("character", str(ref["newId"]))]
    required = ("name", "countryCode", "field")
    if any(ref.get(key) is None for key in required):
        raise ValueError(f"人物 match 必须包含 {required}")
    cursor.execute("""
        SELECT DISTINCT ci.id FROM t_character_info ci
        JOIN t_character_name n ON n.character_info_id=ci.id AND n.deleted=0
        JOIN t_real_character_attribute a ON a.character_info_id=ci.id AND a.deleted=0
        JOIN t_word_countries c ON c.id=a.area
        WHERE ci.deleted=0 AND TRIM(n.character_name)=TRIM(%s)
          AND c.code=%s AND a.field=%s
    """, (ref["name"], ref["countryCode"], ref["field"]))
    return [row["id"] for row in cursor.fetchall()]


def _work_matches(cursor: Any, ref: dict[str, Any]) -> list[int]:
    if "newId" in ref:
        return [stable_id("work", str(ref["newId"]))]
    required = ("workName", "countryCode", "genre")
    if any(ref.get(key) is None for key in required):
        raise ValueError(f"作品 match 必须包含 {required}")
    cursor.execute("""
        SELECT DISTINCT w.id FROM t_character_work w
        JOIN t_word_countries c ON c.id=w.country_id
        WHERE w.deleted=0 AND (TRIM(w.work_name)=TRIM(%s) OR TRIM(w.original_name)=TRIM(%s))
          AND c.code=%s AND w.genre=%s AND (w.year <=> %s)
    """, (ref["workName"], ref["workName"], ref["countryCode"], ref["genre"], ref.get("year")))
    return [row["id"] for row in cursor.fetchall()]


def _exactly_one(kind: str, ref: dict[str, Any], matches: list[int]) -> int:
    if len(matches) != 1:
        raise ValueError(f"{kind}引用必须唯一命中，实际 {len(matches)} 条：{ref}")
    return matches[0]


def validate_patch(config: MySQLConfig, patch: dict[str, Any]) -> dict[str, int]:
    character_ids = {str(item.get("id")) for item in patch.get("newCharacters", [])}
    work_ids = {str(item.get("workId")) for item in patch.get("newWorks", [])}
    if None in character_ids or len(character_ids) != len(patch.get("newCharacters", [])):
        raise ValueError("newCharacters id 缺失或重复")
    if None in work_ids or len(work_ids) != len(patch.get("newWorks", [])):
        raise ValueError("newWorks workId 缺失或重复")
    with connect(config) as db, db.cursor() as cursor:
        require_tables(cursor, config.database)
        for relation in patch.get("relations", []):
            cref, wref = relation.get("characterRef", {}), relation.get("workRef", {})
            if cref.get("newId") not in (None, *character_ids):
                raise ValueError(f"未知 new character id：{cref}")
            if wref.get("newId") not in (None, *work_ids):
                raise ValueError(f"未知 new work id：{wref}")
            _exactly_one("人物", cref, _character_matches(cursor, cref)) if "newId" not in cref else None
            work_id = _exactly_one("作品", wref, _work_matches(cursor, wref))
            if not isinstance(relation.get("relationType"), int) or not 1 <= relation["relationType"] <= 99:
                raise ValueError(f"relationType 非法：{relation}")
            if "newId" in wref:
                genre = next(item["genre"] for item in patch.get("newWorks", []) if item["workId"] == wref["newId"])
            else:
                cursor.execute("SELECT genre FROM t_character_work WHERE id=%s", (work_id,))
                genre = cursor.fetchone()["genre"]
            allowed = RELATION_GENRES.get(relation["relationType"])
            if allowed is not None and genre not in allowed:
                raise ValueError(f"关系 {relation['relationType']} 与作品 genre {genre} 不兼容：{relation}")
    return {"newCharacters": len(character_ids), "newWorks": len(work_ids), "relations": len(patch.get("relations", []))}


def apply_patch(config: MySQLConfig, patch_path: Path, dataset_name: str) -> tuple[Path, dict[str, int]]:
    patch = load_patch(patch_path)
    validate_patch(config, patch)
    run_id = f"{dataset_name}_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = run_dir / "manifest.json"
    tracked = {name: [] for name in ("t_character_info", "t_character_name", "t_real_character_attribute", "t_character_work", "t_work_creator_relation", "t_work_subcategory_relation")}
    before: dict[str, list[dict[str, Any]]] = {}
    with transaction(config) as db:
        with db.cursor() as cursor:
            countries = country_ids(cursor)
            for item in patch.get("newCharacters", []):
                logical_id = str(item["id"])
                tracked["t_character_info"].append(stable_id("character", logical_id))
                tracked["t_real_character_attribute"].append(stable_id("real-attribute", logical_id))
                tracked["t_character_name"].extend(stable_id("character-name", f"{logical_id}:{order}") for order, _ in enumerate(item["names"]))
            for item in patch.get("newWorks", []):
                logical_id = str(item["workId"])
                tracked["t_character_work"].append(stable_id("work", logical_id))
                tracked["t_work_subcategory_relation"].extend(stable_id("work-subcategory", f"{logical_id}:{tag}") for tag in item.get("subcategories", []))
            for relation in patch.get("relations", []):
                cid = _exactly_one("人物", relation["characterRef"], _character_matches(cursor, relation["characterRef"]))
                wid = _exactly_one("作品", relation["workRef"], _work_matches(cursor, relation["workRef"]))
                tracked["t_work_creator_relation"].append(stable_id("work-creator-db", f"{wid}:{cid}:{relation['relationType']}"))
            for table, ids in tracked.items():
                before[table] = select_rows(cursor, table, ids)
            for item in patch.get("newCharacters", []):
                logical_id = str(item["id"]); cid = stable_id("character", logical_id)
                upsert(cursor, "t_character_info", {"id": cid, "character_type": 1, "gender": item["gender"], "introduction": item.get("introduction"), "creator": config.creator_id, "deleted": 0}, ["gender", "introduction", "deleted"])
                for order, name in enumerate(item["names"]):
                    nid = stable_id("character-name", f"{logical_id}:{order}")
                    upsert(cursor, "t_character_name", {"id": nid, "character_name": name["name"], "character_info_id": cid, "top": 0, "language": name["language"], "creator": config.creator_id, "deleted": 0}, ["character_name", "language", "deleted"])
                upsert(cursor, "t_real_character_attribute", {"id": stable_id("real-attribute", logical_id), "character_info_id": cid, "begin_century": item.get("beginCentury"), "end_century": item.get("endCentury"), "area": countries[item["countryCode"]], "field": item["field"], "sub_type": item["realSubType"], "dynasty": item.get("dynasty"), "creative_entity_type": item.get("creativeEntityType", 1), "creator": config.creator_id, "deleted": 0}, ["begin_century", "end_century", "area", "field", "sub_type", "dynasty", "creative_entity_type", "deleted"])
            for item in patch.get("newWorks", []):
                logical_id = str(item["workId"]); wid = stable_id("work", logical_id)
                values = {"id": wid, "work_name": item["workName"], "original_name": item.get("originalName"), "genre": item["genre"], "subcategory": item.get("subcategory"), "year": item.get("year"), "country_id": countries[item["countryCode"]], "auth_character_id": None, "introduction": item.get("introduction"), "creator": config.creator_id, "deleted": 0}
                upsert(cursor, "t_character_work", values, [key for key in values if key != "id"])
                for order, tag in enumerate(item.get("subcategories", [])):
                    tid = stable_id("work-subcategory", f"{logical_id}:{tag}")
                    upsert(cursor, "t_work_subcategory_relation", {"id": tid, "work_id": wid, "subcategory": tag, "is_primary": tag == item.get("subcategory"), "sort_order": order, "creator": config.creator_id}, ["is_primary", "sort_order"])
            for relation in patch.get("relations", []):
                cid = _exactly_one("人物", relation["characterRef"], _character_matches(cursor, relation["characterRef"]))
                wid = _exactly_one("作品", relation["workRef"], _work_matches(cursor, relation["workRef"]))
                rel = relation["relationType"]
                rid = stable_id("work-creator-db", f"{wid}:{cid}:{rel}")
                upsert(cursor, "t_work_creator_relation", {"id": rid, "work_id": wid, "character_id": cid, "relation_type": rel, "sort_order": relation.get("sortOrder", 0), "creator": config.creator_id}, ["sort_order"])
                if relation.get("primaryAuthor"):
                    cursor.execute("UPDATE t_character_work SET auth_character_id=%s WHERE id=%s", (cid, wid))
    result = {"newCharacters": len(patch.get("newCharacters", [])), "newWorks": len(patch.get("newWorks", [])), "relations": len(patch.get("relations", []))}
    manifest = {"schema": "rgmj-dataset-import-run/v1", "runId": run_id, "datasetName": dataset_name, "status": "succeeded", "finishedAt": datetime.now(timezone.utc).isoformat(), "tableOrder": list(tracked), "trackedIds": tracked, "beforeRows": before, "result": result, "sourcePatch": str(patch_path.resolve())}
    write_report(manifest, manifest_path)
    (RUN_ROOT / f"{dataset_name}_latest.json").write_text(json.dumps({"manifest": str(manifest_path.resolve())}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path, result


def main() -> None:
    parser = argparse.ArgumentParser(description="数据库实体知识扩充管道")
    parser.add_argument("command", choices=("export", "validate", "apply"))
    parser.add_argument("--output", type=Path, default=ROOT / "DataSet" / "entity_snapshot.json")
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--dataset-name", default="entity_enrichment")
    args = parser.parse_args(); config = MySQLConfig.from_env()
    if args.command == "export": result = export_snapshot(config, args.output)
    else:
        if not args.patch: parser.error("validate/apply 必须提供 --patch")
        result = validate_patch(config, load_patch(args.patch)) if args.command == "validate" else {"manifest": str(apply_patch(config, args.patch, args.dataset_name)[0].resolve())}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
