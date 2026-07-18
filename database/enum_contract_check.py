from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.import_prompt_dataset import RELATION_GENRES, SUBCATEGORY_RANGE, VALID_GENRES, VALID_RELATIONS
from database.mysql_client import connect


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ENUM = ROOT.parent / "Backend" / "src" / "main" / "java" / "com" / "rgmj" / "common" / "constants" / "CharacterConstants.java"
WORK_PROMPT = ROOT / "Prompt" / "work_generating_prompt.txt"
CHARACTER_PROMPT = ROOT / "Prompt" / "character_generating_prompt.txt"


def enum_block(source: str, name: str) -> str:
    start = source.index(f"enum {name}")
    end = source.index("private final", start)
    return source[start:end]


def backend_contract() -> tuple[set[int], dict[int, int], dict[int, set[int]]]:
    source = BACKEND_ENUM.read_text(encoding="utf-8-sig")
    relation_block = enum_block(source, "WorkCreatorRelationEnum")
    relation_pairs = re.findall(r"\b([A-Z][A-Z0-9_]*)\((\d+),", relation_block)
    relation_by_name = {name: int(key) for name, key in relation_pairs}
    subcategory_block = enum_block(source, "WorkSubcategoryEnum")
    subcategories = {int(key): int(genre) for key, genre in re.findall(
        r"\b[A-Z][A-Z0-9_]*\((\d+)\s*,\s*(\d+)\s*,", subcategory_block)}
    method_start = source.index("boolean valid = switch (relation)", source.index("enum WorkCreatorRelationEnum"))
    method_end = source.index("};", method_start)
    genre_map: dict[int, set[int]] = {}
    for names, expression in re.findall(r"case\s+([A-Z0-9_, ]+)\s*->\s*([^;]+);", source[method_start:method_end]):
        genres = set(VALID_GENRES) if expression.strip() == "true" else {
            int(value) for value in re.findall(r"genre\s*==\s*(\d+)", expression)
        }
        for name in (item.strip() for item in names.split(",")):
            genre_map[relation_by_name[name]] = genres
    return set(relation_by_name.values()), subcategories, genre_map


def prompt_contract() -> tuple[set[int], set[int], set[int]]:
    work = WORK_PROMPT.read_text(encoding="utf-8-sig")
    character = CHARACTER_PROMPT.read_text(encoding="utf-8-sig")
    relation_start = work.index("## 7.")
    relation_end = work.index("## 8.", relation_start)
    work_relations = {int(value) for value in re.findall(r"^- `([0-9]+)`", work[relation_start:relation_end], re.MULTILINE)}
    character_start = character.index("## 6.")
    character_end = character.index("## 7.", character_start)
    character_relations = {int(value) for value in re.findall(r"^- `([0-9]+)`", character[character_start:character_end], re.MULTILINE)}
    expected_subcategories = {value for values in SUBCATEGORY_RANGE.values() for value in values}
    work_subcategories = {int(value) for value in re.findall(r"`(\d{4,5})`", work)} & expected_subcategories
    return work_relations, character_relations, work_subcategories


def database_contract(config: MySQLConfig) -> dict[str, Any]:
    connection = connect(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT relation_type value FROM t_work_creator_relation")
            relations = {int(row["value"]) for row in cursor.fetchall()}
            cursor.execute("SELECT DISTINCT subcategory value FROM t_character_work WHERE subcategory IS NOT NULL")
            subcategories = {int(row["value"]) for row in cursor.fetchall()}
            cursor.execute("SELECT DISTINCT subcategory value FROM t_work_subcategory_relation")
            subcategories.update(int(row["value"]) for row in cursor.fetchall())
        return {"relations": sorted(relations), "subcategories": sorted(subcategories)}
    finally:
        connection.close()


def check(include_database: bool) -> dict[str, Any]:
    backend_relations, backend_subcategories, backend_relation_genres = backend_contract()
    work_relations, character_relations, prompt_subcategories = prompt_contract()
    python_subcategories = {value: genre for genre, values in SUBCATEGORY_RANGE.items() for value in values}
    errors: list[str] = []
    if backend_relations != VALID_RELATIONS:
        errors.append("Backend WorkCreatorRelationEnum 与 Python VALID_RELATIONS 不一致")
    if backend_relation_genres != {**RELATION_GENRES, 1: set(VALID_GENRES), 99: set(VALID_GENRES)}:
        errors.append("Backend 关系-体裁限制与 Python RELATION_GENRES 不一致")
    if backend_subcategories != python_subcategories:
        errors.append("Backend WorkSubcategoryEnum 与 Python SUBCATEGORY_RANGE 不一致")
    if work_relations != VALID_RELATIONS or character_relations != VALID_RELATIONS:
        errors.append("人物/作品 Prompt 的关系枚举与 Python 不一致")
    if prompt_subcategories != set(python_subcategories):
        errors.append("作品 Prompt 的子领域枚举与 Python 不一致")
    database = None
    if include_database:
        database = database_contract(MySQLConfig.from_env())
        unknown_relations = set(database["relations"]) - VALID_RELATIONS
        unknown_subcategories = set(database["subcategories"]) - set(python_subcategories)
        if unknown_relations:
            errors.append(f"数据库存在非法关系：{sorted(unknown_relations)}")
        if unknown_subcategories:
            errors.append(f"数据库存在非法子领域：{sorted(unknown_subcategories)}")
    return {
        "schema": "rgmj-enum-contract-check/v1",
        "passed": not errors,
        "errors": errors,
        "counts": {"relations": len(VALID_RELATIONS), "subcategories": len(python_subcategories)},
        "sources": {"backend": str(BACKEND_ENUM), "workPrompt": str(WORK_PROMPT),
                    "characterPrompt": str(CHARACTER_PROMPT), "databaseChecked": include_database},
        "database": database,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Prompt、Python、Backend 与数据库枚举契约检查")
    parser.add_argument("--database", action="store_true", help="额外检查数据库现有值，需要 MYSQL_PASSWORD")
    args = parser.parse_args()
    result = check(args.database)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
