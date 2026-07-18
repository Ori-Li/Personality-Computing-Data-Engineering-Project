from __future__ import annotations

import json

from database.config import MySQLConfig
from database.crp_dimension_catalog import chinese_name, group_chinese_name
from database.mysql_client import transaction


def main() -> None:
    config = MySQLConfig.from_env()
    with transaction(config) as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT id,dimension_code,group_code FROM t_crp_dimension_definition WHERE schema_version=%s", ("cpr-1.2",))
            definitions = list(cursor.fetchall())
            for definition in definitions:
                description = f"{group_chinese_name(definition['group_code'])}：由 CPR-1.2 Prompt 定义的归一化维度"
                cursor.execute(
                    "UPDATE t_crp_dimension_definition SET name_zh=%s,name_en=%s,description=%s WHERE id=%s",
                    (chinese_name(definition["dimension_code"]), definition["dimension_code"].rsplit(".", 1)[-1], description, definition["id"]),
                )
            cursor.execute("""
                UPDATE t_crp_dimension_value v
                SET evidence_count=(
                    SELECT COUNT(*) FROM t_crp_dimension_evidence de
                    WHERE de.profile_id=v.profile_id AND de.dimension_id=v.dimension_id
                )
            """)
            cursor.execute("SELECT COUNT(*) count FROM t_crp_dimension_definition WHERE schema_version=%s AND name_zh REGEXP '^[A-Za-z0-9_ ./-]+$'", ("cpr-1.2",))
            english = cursor.fetchone()["count"]
            cursor.execute("SELECT COUNT(*) count FROM t_crp_dimension_value v WHERE v.evidence_count<>(SELECT COUNT(*) FROM t_crp_dimension_evidence de WHERE de.profile_id=v.profile_id AND de.dimension_id=v.dimension_id)")
            mismatched = cursor.fetchone()["count"]
    print(json.dumps({"definitionsUpdated": len(definitions), "englishNameZh": english, "mismatchedEvidenceCount": mismatched}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
