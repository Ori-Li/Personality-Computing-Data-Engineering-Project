from __future__ import annotations

import json

from database.config import MySQLConfig
from database.import_prompt_dataset import stable_id
from database.mysql_client import transaction


WORK_ID="work_chn_1968_001"
OLD="psychology_vector.social.love"
NEW="psychology_vector.social_relationship.love"
DIMENSION_CODE="psychology.social_relationship.love"


def main()->None:
    with transaction(MySQLConfig.from_env()) as db,db.cursor() as cursor:
        work_id=stable_id("work",WORK_ID)
        cursor.execute("SELECT id,raw_payload,overall_confidence FROM t_crp_profile WHERE content_id=%s AND schema_version='cpr-1.6' AND is_current=1 FOR UPDATE",(work_id,));profile=cursor.fetchone()
        if not profile: raise ValueError("未找到目标 CPR")
        payload=json.loads(profile["raw_payload"]) if isinstance(profile["raw_payload"],str) else profile["raw_payload"]
        fact=next((x for x in payload.get("vector_facts",[]) if x.get("dimensionPath") in (OLD,NEW)),None)
        if not fact: raise ValueError("raw_payload 中未找到误命名事实")
        fact["dimensionPath"]=NEW;explanation=fact["explanation"]
        cursor.execute("UPDATE t_crp_profile SET raw_payload=%s WHERE id=%s",(json.dumps(payload,ensure_ascii=False),profile["id"]))
        cursor.execute("SELECT id FROM t_crp_dimension_definition WHERE schema_version='cpr-1.6' AND dimension_code=%s",(DIMENSION_CODE,));dimension=cursor.fetchone()
        if not dimension: raise ValueError("未找到规定维度定义")
        cursor.execute("SELECT COUNT(*) n FROM t_crp_dimension_evidence WHERE profile_id=%s AND dimension_id=%s",(profile["id"],dimension["id"]));exists=cursor.fetchone()["n"]
        if not exists:
            eid=stable_id("crp-evidence",f"{profile['id']}:{NEW}:{explanation}")
            cursor.execute("INSERT INTO t_crp_evidence (id,profile_id,source_id,evidence_type,fact_text,source_locator,verification_status,quality_score) VALUES (%s,%s,NULL,2,%s,%s,1,%s)",(eid,profile["id"],explanation,f"vector_facts[{NEW}]",profile["overall_confidence"]))
            cursor.execute("INSERT INTO t_crp_dimension_evidence (profile_id,dimension_id,evidence_id,relation_type,evidence_weight,explanation) VALUES (%s,%s,%s,1,%s,%s)",(profile["id"],dimension["id"],eid,profile["overall_confidence"],explanation))
            cursor.execute("UPDATE t_crp_dimension_value SET evidence_count=1,rationale=%s WHERE profile_id=%s AND dimension_id=%s",(explanation,profile["id"],dimension["id"]))
    print(json.dumps({"workId":WORK_ID,"oldPath":OLD,"newPath":NEW,"mappingInserted":not bool(exists)},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
