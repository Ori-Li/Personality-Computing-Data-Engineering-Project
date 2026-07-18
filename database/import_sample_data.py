from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.mysql_client import transaction


ROOT = Path(__file__).resolve().parent
DEFAULT_SAMPLE = ROOT / "sample_data.json"
SAMPLE_ID_BASE = 890_000_000_000_000_000


def load_sample(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def table_exists(cursor: Any, database: str, table: str) -> bool:
    cursor.execute(
        """SELECT 1 FROM information_schema.TABLES
           WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s LIMIT 1""",
        (database, table),
    )
    return cursor.fetchone() is not None


def columns(cursor: Any, database: str, table: str) -> set[str]:
    cursor.execute(
        """SELECT COLUMN_NAME FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s""",
        (database, table),
    )
    return {row["COLUMN_NAME"] for row in cursor.fetchall()}


def require_schema(cursor: Any, database: str) -> None:
    required = {
        "t_character_info",
        "t_character_name",
        "t_real_character_attribute",
        "t_character_work",
    }
    missing = sorted(t for t in required if not table_exists(cursor, database, t))
    if missing:
        raise RuntimeError(f"数据库缺少必要表：{', '.join(missing)}")


def cleanup(cursor: Any, data: dict[str, Any]) -> None:
    character_name = data["character"]["name"]
    work_name = data["work"]["name"]
    cursor.execute(
        "SELECT character_info_id FROM t_character_name WHERE character_name=%s",
        (character_name,),
    )
    character_ids = [row["character_info_id"] for row in cursor.fetchall()]
    cursor.execute("SELECT id FROM t_character_work WHERE work_name=%s", (work_name,))
    work_ids = [row["id"] for row in cursor.fetchall()]

    if work_ids and table_exists(cursor, cursor.connection.db.decode(), "t_work_creator_relation"):
        placeholders = ",".join(["%s"] * len(work_ids))
        cursor.execute(
            f"DELETE FROM t_work_creator_relation WHERE work_id IN ({placeholders})",
            work_ids,
        )
    cursor.execute("DELETE FROM t_character_work WHERE work_name=%s", (work_name,))
    if character_ids:
        placeholders = ",".join(["%s"] * len(character_ids))
        cursor.execute(
            f"DELETE FROM t_real_character_attribute WHERE character_info_id IN ({placeholders})",
            character_ids,
        )
        cursor.execute(
            f"DELETE FROM t_character_name WHERE character_info_id IN ({placeholders})",
            character_ids,
        )
        cursor.execute(
            f"DELETE FROM t_character_info WHERE id IN ({placeholders})",
            character_ids,
        )


def insert_sample(cursor: Any, config: MySQLConfig, data: dict[str, Any]) -> None:
    require_schema(cursor, config.database)
    cleanup(cursor, data)

    character = data["character"]
    work = data["work"]
    character_id = SAMPLE_ID_BASE + 1
    name_id = SAMPLE_ID_BASE + 2
    attribute_id = SAMPLE_ID_BASE + 3
    work_id = SAMPLE_ID_BASE + 4
    relation_id = SAMPLE_ID_BASE + 5

    info_columns = columns(cursor, config.database, "t_character_info")
    info_values: dict[str, Any] = {
        "id": character_id,
        "character_type": character["character_type"],
        "gender": character["gender"],
        "introduction": character["introduction"],
        "creator": config.creator_id,
        "deleted": 0,
    }
    if "creative_entity_type" in info_columns:
        info_values["creative_entity_type"] = character["creative_entity_type"]
    _insert(cursor, "t_character_info", info_values)

    _insert(
        cursor,
        "t_character_name",
        {
            "id": name_id,
            "character_name": character["name"],
            "character_info_id": character_id,
            "top": 100,
            "language": character["language"],
            "creator": config.creator_id,
            "deleted": 0,
        },
    )
    _insert(
        cursor,
        "t_real_character_attribute",
        {
            "id": attribute_id,
            "character_info_id": character_id,
            "area": character["area"],
            "field": character["field"],
            "sub_type": character["sub_type"],
            "creator": config.creator_id,
            "deleted": 0,
        },
    )

    work_columns = columns(cursor, config.database, "t_character_work")
    work_values: dict[str, Any] = {
        "id": work_id,
        "work_name": work["name"],
        "genre": work["genre"],
        "year": work["year"],
        "auth_character_id": character_id,
        "introduction": work["introduction"],
        "creator": config.creator_id,
        "deleted": 0,
    }
    optional_work_fields = {
        "original_name": "original_name",
        "subcategory": "subcategory",
        "cultural_impact": "cultural_impact",
    }
    for column, source_key in optional_work_fields.items():
        if column in work_columns:
            work_values[column] = work[source_key]
    _insert(cursor, "t_character_work", work_values)

    if table_exists(cursor, config.database, "t_work_creator_relation"):
        _insert(
            cursor,
            "t_work_creator_relation",
            {
                "id": relation_id,
                "work_id": work_id,
                "character_id": character_id,
                "relation_type": work["relation_type"],
                "sort_order": 0,
                "creator": config.creator_id,
                "deleted": 0,
            },
        )


def _insert(cursor: Any, table: str, values: dict[str, Any]) -> None:
    names = list(values)
    placeholders = ", ".join(["%s"] * len(names))
    quoted_names = ", ".join(f"`{name}`" for name in names)
    cursor.execute(
        f"INSERT INTO `{table}` ({quoted_names}) VALUES ({placeholders})",
        [values[name] for name in names],
    )


def verify(cursor: Any, data: dict[str, Any]) -> dict[str, Any]:
    cursor.execute(
        """SELECT ci.id, cn.character_name, ci.character_type, ci.gender,
                  ra.area, ra.field, ra.sub_type
           FROM t_character_info ci
           JOIN t_character_name cn ON cn.character_info_id=ci.id AND cn.deleted=0
           LEFT JOIN t_real_character_attribute ra
                  ON ra.character_info_id=ci.id AND ra.deleted=0
           WHERE cn.character_name=%s AND ci.deleted=0""",
        (data["character"]["name"],),
    )
    character = cursor.fetchone()
    cursor.execute(
        "SELECT id, work_name, genre, year, auth_character_id FROM t_character_work WHERE work_name=%s AND deleted=0",
        (data["work"]["name"],),
    )
    return {"character": character, "work": cursor.fetchone()}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="写入或清理 RGMJ MySQL 样板数据")
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--cleanup", action="store_true", help="只清理样板数据")
    args = parser.parse_args()

    config = MySQLConfig.from_env()
    data = load_sample(args.sample)
    with transaction(config) as connection:
        with connection.cursor() as cursor:
            require_schema(cursor, config.database)
            if args.cleanup:
                cleanup(cursor, data)
                print("样板数据已清理。")
            else:
                insert_sample(cursor, config, data)
                print(json.dumps(verify(cursor, data), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
