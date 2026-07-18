from __future__ import annotations

import json

from database.config import MySQLConfig
from database.mysql_client import connect


CHECKS = {
    "profiles": "SELECT COUNT(*) n FROM t_crp_profile",
    "published_profiles": "SELECT COUNT(*) n FROM t_crp_profile WHERE status=3",
    "scored_dimensions": "SELECT COUNT(*) n FROM t_crp_dimension_value WHERE score IS NOT NULL",
    "insufficient_dimensions": "SELECT COUNT(*) n FROM t_crp_dimension_value WHERE score IS NULL AND evidence_state=4",
    "evidence": "SELECT COUNT(*) n FROM t_crp_evidence",
    "dimension_evidence_mappings": "SELECT COUNT(*) n FROM t_crp_dimension_evidence",
    "projections": "SELECT COUNT(*) n FROM t_crp_projection_value",
    "missing_raw_affinity": """
        SELECT COUNT(*) n FROM t_crp_profile
        WHERE JSON_EXTRACT(raw_payload, '$.personality_affinity.Ni') IS NULL
    """,
    "scored_with_insufficient_state": """
        SELECT COUNT(*) n FROM t_crp_dimension_value
        WHERE score IS NOT NULL AND evidence_state=4
    """,
    "scored_without_evidence_mapping": """
        SELECT COUNT(*) n FROM t_crp_dimension_value dv
        LEFT JOIN t_crp_dimension_evidence de
          ON de.profile_id=dv.profile_id AND de.dimension_id=dv.dimension_id
        WHERE dv.score IS NOT NULL AND de.profile_id IS NULL
    """,
}


def main() -> None:
    result: dict[str, object] = {}
    with connect(MySQLConfig.from_env()) as db, db.cursor() as cursor:
        for name, query in CHECKS.items():
            cursor.execute(query)
            result[name] = cursor.fetchone()["n"]
        cursor.execute("""
            SELECT w.work_name name, COUNT(*) projections,
                   MIN(pv.score) min_score, MAX(pv.score) max_score
            FROM t_crp_profile p
            JOIN t_character_work w ON w.id=p.content_id
            JOIN t_crp_projection_value pv ON pv.profile_id=p.id
            GROUP BY p.id,w.work_name ORDER BY w.work_name
        """)
        result["projection_ranges"] = list(cursor.fetchall())

    failures = {
        key: result[key]
        for key in (
            "missing_raw_affinity",
            "scored_with_insufficient_state",
            "scored_without_evidence_mapping",
        )
        if result[key] != 0
    }
    result["valid"] = not failures
    result["failures"] = failures
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
