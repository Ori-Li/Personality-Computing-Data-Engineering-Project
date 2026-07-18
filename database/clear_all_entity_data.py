from __future__ import annotations

import argparse
import json

from database.config import MySQLConfig
from database.mysql_client import connect


PRESERVE = {"t_crp_dimension_definition"}
PREFIXES = ("t_character", "t_work", "t_crp")


def discover(cursor) -> list[str]:
    cursor.execute("""
        SELECT table_name AS name FROM information_schema.tables
        WHERE table_schema=DATABASE() AND table_type='BASE TABLE'
    """)
    all_tables={row["name"] for row in cursor.fetchall()}
    selected={name for name in all_tables if name.startswith(PREFIXES) and name not in PRESERVE}
    while True:
        cursor.execute("""
            SELECT DISTINCT table_name AS child_name,referenced_table_name AS parent_name
            FROM information_schema.key_column_usage
            WHERE table_schema=DATABASE() AND referenced_table_name IS NOT NULL
        """)
        additions={row["child_name"] for row in cursor.fetchall() if row["parent_name"] in selected}
        additions-=PRESERVE
        if additions <= selected:
            break
        selected |= additions
    return sorted(selected)


def count_rows(cursor, tables: list[str]) -> dict[str,int]:
    result={}
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) n FROM `{table}`")
        result[table]=cursor.fetchone()["n"]
    return result


def clear(config: MySQLConfig, execute: bool) -> dict[str,object]:
    db=connect(config)
    try:
        with db.cursor() as cursor:
            tables=discover(cursor)
            before=count_rows(cursor,tables)
            if not execute:
                return {"dryRun":True,"tables":tables,"rows":before,"totalRows":sum(before.values())}
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            try:
                for table in tables:
                    cursor.execute(f"DELETE FROM `{table}`")
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            after=count_rows(cursor,tables)
            cursor.execute("SELECT COUNT(*) n FROM t_crp_dimension_definition")
            dimensions=cursor.fetchone()["n"]
            return {"dryRun":False,"tables":tables,"rowsDeleted":before,"totalRowsDeleted":sum(before.values()),"remainingRows":sum(after.values()),"preservedDimensionDefinitions":dimensions}
    finally:
        db.close()


def main() -> None:
    parser=argparse.ArgumentParser(description="清空人物、作品及其关联业务数据，保留结构与基础字典")
    parser.add_argument("--execute",action="store_true",help="实际执行；不提供时仅预览")
    args=parser.parse_args()
    print(json.dumps(clear(MySQLConfig.from_env(),args.execute),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
