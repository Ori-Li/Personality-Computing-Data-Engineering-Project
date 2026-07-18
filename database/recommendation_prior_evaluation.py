from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.dataset_pipeline import REPORT_ROOT, write_report
from database.mysql_client import connect


TRAITS = ("ni", "te", "fi", "se", "ti", "ne", "si", "fe")
MBTI_LETTERS = "IENSTFJP"


def vector(row: dict[str, Any]) -> list[float]:
    return [float(row.get(name, .5) if row.get(name) is not None else .5) for name in TRAITS]


def similarity(left: list[float], right: list[float]) -> float:
    return max(0.0, min(1.0, 1.0 - sum(abs(a - b) for a, b in zip(left, right)) / len(TRAITS)))


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def audience_aware(content: float, audience: float, confidence: float) -> float:
    weight = .25 * clamp(confidence)
    return content * (1 - weight) + audience * weight


def evidence_confidence(effective_weight: float, prior_strength: float = 20.0) -> float:
    return 0.0 if effective_weight <= 0 else clamp(effective_weight / (effective_weight + prior_strength))


def mbti_crp_affinity(work: list[float], mbti: str) -> float:
    if len(work) != 8 or len(mbti or "") != 4:
        return .5
    ni, te, fi, se, ti, ne, si, fe = work
    axes = ((ni + si + ti + fi, ne + se + te + fe),
            (ni + ne, si + se), (ti + te, fi + fe),
            (ni + si + te + fe, ne + se + ti + fi))
    first_letters = "INTJ"
    values = []
    for selected, first_letter, (first, second) in zip(mbti.upper(), first_letters, axes):
        probability = .5 if first + second <= 0 else first / (first + second)
        values.append(probability if selected == first_letter else 1 - probability)
    return clamp(sum(values) / 4)


def historical_mbti_affinity(weights: list[float], mbti: str) -> float:
    positive = signal = 0.0
    for letter in mbti.upper():
        index = MBTI_LETTERS.find(letter)
        if index < 0:
            continue
        opposite = index + 1 if index % 2 == 0 else index - 1
        difference = weights[index] - weights[opposite]
        positive += difference > 0
        signal += difference
    tier = 3.0 if positive == 4 else 1.8 if positive == 3 else 1.2 if positive == 2 else .5 if positive == 1 else .25
    import math
    return 1 / (1 + math.exp(-(signal * .002 + math.log(tier))))


def table_exists(cursor: Any, database: str, table: str) -> bool:
    cursor.execute("SELECT COUNT(*) count FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                   (database, table))
    return cursor.fetchone()["count"] == 1


def metrics(scores: list[tuple[int, int, float]]) -> dict[str, Any]:
    positives = [score for _, rating, score in scores if rating > 0]
    negatives = [score for _, rating, score in scores if rating < 0]
    by_user: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"positive": [], "negative": []})
    for user, rating, score in scores:
        by_user[user]["positive" if rating > 0 else "negative"].append(score)
    wins = pairs = 0.0
    for values in by_user.values():
        for positive in values["positive"]:
            for negative in values["negative"]:
                pairs += 1
                wins += 1 if positive > negative else .5 if positive == negative else 0
    positive_mean = sum(positives) / len(positives) if positives else None
    negative_mean = sum(negatives) / len(negatives) if negatives else None
    return {
        "positiveCount": len(positives), "negativeCount": len(negatives),
        "positiveMean": positive_mean, "negativeMean": negative_mean,
        "separation": positive_mean - negative_mean if positive_mean is not None and negative_mean is not None else None,
        "pairwiseAuc": wins / pairs if pairs else None, "comparisonPairs": int(pairs),
    }


def evaluate(config: MySQLConfig) -> dict[str, Any]:
    connection = connect(config)
    try:
        with connection.cursor() as cursor:
            required = ["t_audience", "t_user_jung_preference_profile", "t_user_mbti_test_result",
                        "t_user_mbti_relation", "t_work_audience_result",
                        "t_crp_profile", "t_crp_projection_value"]
            missing = [table for table in required if not table_exists(cursor, config.database, table)]
            if missing:
                return {"schema": "rgmj-recommendation-prior-evaluation/v2", "ready": False,
                        "missingTables": missing}
            cursor.execute("SELECT user_info_id," + ",".join(TRAITS) + " FROM t_user_jung_preference_profile")
            users = {int(row["user_info_id"]): vector(row) for row in cursor.fetchall()}
            cursor.execute("SELECT user_info_id," + ",".join(f"AVG({name}) {name}" for name in TRAITS) +
                           " FROM t_user_mbti_test_result WHERE deleted=0 GROUP BY user_info_id")
            tests = {int(row["user_info_id"]): vector(row) for row in cursor.fetchall()}
            cursor.execute("SELECT user_info_id,mbti FROM t_user_mbti_relation WHERE deleted=0")
            user_mbtis = {int(row["user_info_id"]): str(row["mbti"]).upper() for row in cursor.fetchall()
                          if len(str(row.get("mbti") or "")) == 4}
            pivot = ",".join(f"MAX(CASE WHEN v.trait_code='{name.capitalize()}' THEN v.score END) {name}" for name in TRAITS)
            cursor.execute(f"SELECT p.content_id work_id,{pivot} FROM t_crp_profile p JOIN t_crp_projection_value v "
                           "ON v.profile_id=p.id AND v.projection_system=1 WHERE p.content_type=11 "
                           "AND p.is_current=1 AND p.status IN (2,3) GROUP BY p.content_id")
            works = {int(row["work_id"]): vector(row) for row in cursor.fetchall()}
            audience_vectors: dict[int, tuple[list[float], float]] = {}
            if table_exists(cursor, config.database, "t_work_jung_audience_profile"):
                cursor.execute("SELECT work_id," + ",".join(TRAITS) + ",confidence FROM t_work_jung_audience_profile")
                audience_vectors = {int(row["work_id"]): (vector(row), float(row["confidence"])) for row in cursor.fetchall()}
            cursor.execute("SELECT work_id,attitude,mbti_letter,score_count FROM t_work_audience_result")
            historical_mbti: dict[int, tuple[list[float], float]] = {}
            mbti_weights: dict[int, list[float]] = defaultdict(lambda: [0.0] * 8)
            mbti_evidence: dict[int, float] = defaultdict(float)
            for row in cursor.fetchall():
                work_id = int(row["work_id"])
                count = max(0, int(row.get("score_count") or 0))
                contribution = int(row.get("attitude") or 0) * count
                mbti_evidence[work_id] += count
                for letter in str(row.get("mbti_letter") or "").upper():
                    index = MBTI_LETTERS.find(letter)
                    if index >= 0:
                        mbti_weights[work_id][index] += contribution
            historical_mbti = {work_id: (weights, mbti_evidence[work_id])
                               for work_id, weights in mbti_weights.items()}
            cursor.execute("SELECT creator user_id,object_id work_id,attitude FROM (SELECT creator,object_id,attitude,"
                           "ROW_NUMBER() OVER(PARTITION BY creator,object_id ORDER BY create_time DESC,id DESC) rn "
                           "FROM t_audience WHERE object_type=1 AND deleted=0 AND attitude<>0) q WHERE rn=1")
            ratings = list(cursor.fetchall())
    finally:
        connection.close()
    variants: dict[str, list[tuple[int, int, float]]] = defaultdict(list)
    eligible = 0
    for row in ratings:
        user_id, work_id, rating = int(row["user_id"]), int(row["work_id"]), int(row["attitude"])
        if user_id not in users or work_id not in works:
            continue
        eligible += 1
        content = similarity(users[user_id], works[work_id])
        test_content = similarity(tests.get(user_id, [.5] * 8), works[work_id])
        behavior = content
        test = test_content
        if work_id in audience_vectors:
            audience, confidence = audience_vectors[work_id]
            behavior = audience_aware(content, similarity(users[user_id], audience), confidence)
            test = audience_aware(test_content, similarity(tests.get(user_id, [.5] * 8), audience), confidence)
        mbti = user_mbtis.get(user_id)
        cold = .5
        if mbti:
            crp_mbti = mbti_crp_affinity(works[work_id], mbti)
            weights, evidence = historical_mbti.get(work_id, ([0.0] * 8, 0.0))
            audience_mbti = historical_mbti_affinity(weights, mbti)
            confidence = evidence_confidence(evidence)
            cold = crp_mbti * (1 - confidence) + audience_mbti * confidence
        variants["behaviorContentOnly"].append((user_id, rating, content))
        variants["behaviorContentAndAudience"].append((user_id, rating, behavior))
        variants["behaviorAndTestContentAudience"].append((user_id, rating, behavior * .7 + test * .2 + .05))
        variants["productionFull"].append((user_id, rating, behavior * .7 + test * .2 + cold * .1))
    evaluated = {name: metrics(scores) for name, scores in variants.items()}
    base = evaluated.get("behaviorContentOnly", {})
    for name, result in evaluated.items():
        result["separationDeltaVsBehaviorContentOnly"] = (
            result["separation"] - base.get("separation")
            if result.get("separation") is not None and base.get("separation") is not None else None)
    return {
        "schema": "rgmj-recommendation-prior-evaluation/v2", "ready": eligible > 0,
        "coverage": {"latestRatings": len(ratings), "eligibleRatings": eligible,
                     "behaviorUsers": len(users), "testPriorUsers": len(tests), "crpWorks": len(works),
                     "audiencePriorWorks": len(audience_vectors), "fixedMbtiUsers": len(user_mbtis),
                     "historicalAudienceMbtiWorks": len(historical_mbti)},
        "variants": evaluated,
        "limitations": ["这是使用现有评分的离线相关性检查，不是线上因果 A/B 测试。",
                        "用户长期向量可能已经学习过被评估评分，因此结果存在信息泄漏；正式评估应按时间切分。"],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="推荐模型先验的离线消融对照（只读数据库）")
    parser.add_argument("--output", type=Path, default=REPORT_ROOT / "recommendation_prior_evaluation.json")
    args = parser.parse_args()
    result = evaluate(MySQLConfig.from_env())
    write_report(result, args.output)
    print(json.dumps({"report": str(args.output.resolve()), **result}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("ready") else 2)


if __name__ == "__main__":
    main()
