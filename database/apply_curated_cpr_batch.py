from __future__ import annotations

import argparse, json
from pathlib import Path

from database.config import MySQLConfig
from database.generate_missing_crp import select_evidence_index
from database.import_prompt_dataset import stable_id
from database.mysql_client import transaction


def set_path(payload,code,value):
    parts=code.split(".")
    if parts[0]=="experience": payload["experience_vector"][parts[1]]=value
    elif parts[0]=="psychology": payload["psychology_vector"][parts[1]][parts[2]]=value
    elif parts[0]=="media": payload["media_vector"][parts[1]][parts[2]]=value
    else: raise ValueError(f"未知维度路径：{code}")

def clear_vectors(payload):
    for key in payload["experience_vector"]:payload["experience_vector"][key]=None
    for values in payload["psychology_vector"].values():
        for key in values:values[key]=None
    for values in payload["media_vector"].values():
        for key,value in values.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):values[key]=None
    payload["personality_affinity"]={key:None for key in ("Ni","Ne","Ti","Te","Fi","Fe","Si","Se","Assertive","Turbulent")}

def main():
    p=argparse.ArgumentParser();p.add_argument("--anchors",type=Path,required=True);a=p.parse_args()
    source=json.loads(a.anchors.read_text(encoding="utf-8-sig"));anchors=source["works"]
    names=[x["workName"] for x in anchors]
    if len(names)!=len(set(names)):raise ValueError("anchor workName 重复")
    config=MySQLConfig.from_env();kept_profiles=[];mapping_count=0;evidence_count=0
    with transaction(config) as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT id,dimension_code FROM t_crp_dimension_definition WHERE schema_version='cpr-1.2'");definitions={x["dimension_code"]:x["id"] for x in cursor.fetchall()}
            unknown={code for x in anchors for code in x["scores"] if code not in definitions}
            if unknown:raise ValueError(f"anchor 包含未知维度：{sorted(unknown)}")
            for anchor in anchors:
                cursor.execute("""SELECT p.id,p.raw_payload FROM t_crp_profile p JOIN t_character_work w ON w.id=p.content_id
                  WHERE p.is_current=1 AND p.content_type=11 AND w.work_name=%s""",(anchor["workName"],));rows=list(cursor.fetchall())
                if len(rows)!=1:raise ValueError(f"作品必须唯一命中当前 CPR：{anchor['workName']} -> {len(rows)}")
                pid=rows[0]["id"];kept_profiles.append(pid);payload=json.loads(rows[0]["raw_payload"]) if isinstance(rows[0]["raw_payload"],str) else rows[0]["raw_payload"]
                clear_vectors(payload)
                for code,score in anchor["scores"].items():set_path(payload,code,score)
                payload["supporting_evidence"]=anchor["evidence"];payload["dimension_evidence"]=[];payload["basic"]["confidence"]=anchor["confidence"];payload["curation"]={"status":"human_knowledge_anchored","batch":"batch1","unscoredDimensions":"insufficient"}
                cursor.execute("DELETE FROM t_crp_dimension_evidence WHERE profile_id=%s",(pid,));cursor.execute("DELETE FROM t_crp_evidence WHERE profile_id=%s",(pid,))
                cursor.execute("UPDATE t_crp_dimension_value SET score=NULL,confidence=NULL,evidence_state=4,evidence_count=0,rationale=NULL WHERE profile_id=%s",(pid,))
                evidence_ids=[]
                for index,text in enumerate(anchor["evidence"]):
                    eid=stable_id("curated-crp-evidence",f"{pid}:{index}:{text}");evidence_ids.append(eid)
                    cursor.execute("INSERT INTO t_crp_evidence (id,profile_id,source_id,evidence_type,fact_text,source_locator,verification_status,quality_score) VALUES (%s,%s,NULL,1,%s,%s,2,%s)",(eid,pid,text,f"curated_batch1[{index}]",anchor["confidence"]));evidence_count+=1
                for code,score in anchor["scores"].items():
                    index=select_evidence_index(code,anchor["evidence"]);relation=1 if score>=.5 else 2;weight=round(max(.65,abs(score-.5)+.5),2)
                    explanation=f"依据“{anchor['evidence'][index].rstrip('。')}”，该可观察事实{'支持该维度的高强度表达' if relation==1 else '限制该维度获得高分'}。"
                    payload["dimension_evidence"].append({"dimensionCode":code,"evidenceIndexes":[index],"relationType":relation,"evidenceWeight":weight,"explanation":explanation})
                    did=definitions[code]
                    cursor.execute("UPDATE t_crp_dimension_value SET score=%s,confidence=%s,evidence_state=1,evidence_count=1,rationale=%s WHERE profile_id=%s AND dimension_id=%s",(score,anchor["confidence"],explanation,pid,did))
                    cursor.execute("INSERT INTO t_crp_dimension_evidence (profile_id,dimension_id,evidence_id,relation_type,evidence_weight,explanation) VALUES (%s,%s,%s,%s,%s,%s)",(pid,did,evidence_ids[index],relation,weight,explanation));mapping_count+=1
                cursor.execute("UPDATE t_crp_profile SET status=3,summary_text=%s,overall_confidence=%s,evidence_coverage=%s,raw_payload=%s,validate_time=NOW() WHERE id=%s",("".join(anchor["evidence"][:2]),anchor["confidence"],round(len(anchor["scores"])/100,5),json.dumps(payload,ensure_ascii=False),pid))
            marks=",".join(["%s"]*len(kept_profiles))
            cursor.execute(f"DELETE FROM t_crp_profile WHERE id NOT IN ({marks})",kept_profiles);deleted_profiles=cursor.rowcount
            cursor.execute("DELETE r FROM t_crp_generation_run r LEFT JOIN t_crp_profile p ON p.generation_run_id=r.id WHERE p.id IS NULL");deleted_runs=cursor.rowcount
    print(json.dumps({"reliableProfiles":len(kept_profiles),"deletedUnreliableProfiles":deleted_profiles,"curatedEvidence":evidence_count,"curatedDimensionMappings":mapping_count,"deletedOrphanRuns":deleted_runs},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
