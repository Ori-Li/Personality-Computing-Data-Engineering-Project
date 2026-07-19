from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.crp_dimension_catalog import chinese_name, group_chinese_name
from database.import_prompt_dataset import stable_id
from database.mysql_client import connect, transaction


SCHEMA_VERSION="cpr-1.6"
JUNG={"Ni","Ne","Ti","Te","Fi","Fe","Si","Se"}
BIG_FIVE={"openness","conscientiousness","extraversion","agreeableness","neuroticism"}


def items(data:Any)->list[dict[str,Any]]:
    responses=data if isinstance(data,list) else [data]
    return [item for response in responses for item in response.get("items",[])]


def canonical(path:str)->str:
    if path=="psychology_vector.social.love":
        path="psychology_vector.social_relationship.love"
    if path.startswith("experience_vector."): return "experience."+path.removeprefix("experience_vector.")
    if path.startswith("psychology_vector."): return "psychology."+path.removeprefix("psychology_vector.")
    if path.startswith("media_vector."): return "media."+path.removeprefix("media_vector.")
    raise ValueError(f"未知维度路径：{path}")


def numeric_dimensions(item:dict[str,Any])->dict[str,float]:
    result={f"experience_vector.{key}":value for key,value in item["experience_vector"].items()}
    for group,values in item["psychology_vector"].items():
        for key,value in values.items(): result[f"psychology_vector.{group}.{key}"]=value
    for vector,values in item["media_vector"].items():
        for key,value in values.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool): result[f"media_vector.{vector}.{key}"]=value
    return result


def validate(data:Any,config:MySQLConfig|None=None)->list[str]:
    errors=[];seen=set();all_items=items(data)
    for index,item in enumerate(all_items):
        base=f"items[{index}]";basic=item.get("basic",{});wid=basic.get("workId")
        if not isinstance(wid,str) or not wid: errors.append(f"{base}.basic.workId 缺失")
        elif wid in seen: errors.append(f"{base}.basic.workId 重复：{wid}")
        seen.add(wid)
        dims=numeric_dimensions(item);facts=item.get("vector_facts",[]);paths=[x.get("dimensionPath") for x in facts if isinstance(x,dict)]
        if len(paths)!=len(set(paths)): errors.append(f"{base}.vector_facts 存在重复路径")
        # Mixed external batches may contain obsolete fact paths.  They are
        # ignored unless the corresponding numeric dimension exists.
        for fact in facts:
            path=fact.get("dimensionPath");score=fact.get("score")
            if path in dims and score!=dims[path]: errors.append(f"{base}.{path} 的 score 与向量不一致")
        for path,value in dims.items():
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not 0<=value<=1: errors.append(f"{base}.{path} 越界")
        affinity=item.get("personality_affinity",{})
        required=JUNG|{"Assertive","Turbulent"};allowed=required|BIG_FIVE
        if not required<=set(affinity)<=allowed: errors.append(f"{base}.personality_affinity 集合非法")
    if config and not errors:
        expected_ids={stable_id("work",wid) for wid in seen}
        with connect(config) as db,db.cursor() as cursor:
            marks=",".join(["%s"]*len(expected_ids));cursor.execute(f"SELECT id FROM t_character_work WHERE deleted=0 AND id IN ({marks})",tuple(expected_ids));found={x["id"] for x in cursor.fetchall()}
            if found!=expected_ids: errors.append(f"数据库缺少 {len(expected_ids-found)} 个 CPR 对应作品")
            cursor.execute(f"SELECT content_id FROM t_crp_profile WHERE schema_version=%s AND is_current=1 AND content_id IN ({marks})",(SCHEMA_VERSION,*expected_ids));existing=cursor.fetchall()
            if existing: errors.append(f"已有 {len(existing)} 个当前 CPR-1.6 档案")
    return errors


def apply(config:MySQLConfig,path:Path)->dict[str,Any]:
    data=json.loads(path.read_text(encoding="utf-8-sig"));errors=validate(data,config)
    if errors: raise ValueError("\n".join(errors[:100]))
    raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest();run_id=stable_id("crp-run",f"CPR-1.6:{digest}")
    counts={"profiles":0,"dimensions":0,"evidence":0,"dimensionEvidence":0,"projections":0}
    with transaction(config) as db,db.cursor() as cursor:
        cursor.execute("""INSERT INTO t_crp_generation_run (id,pipeline_version,prompt_version,model_provider,model_name,model_version,web_search_used,input_hash,output_hash,status,generation_metadata,finished_at) VALUES (%s,'crp-v16-pipeline/v1','CPR-1.6','external','unspecified','ACTUAL_MODEL_VERSION',0,%s,%s,3,%s,NOW())""",(run_id,digest,digest,json.dumps({"source":str(path),"items":len(items(data))},ensure_ascii=False)))
        all_codes=[]
        for item in items(data): all_codes.extend(canonical(path) for path in numeric_dimensions(item))
        for order,code in enumerate(dict.fromkeys(all_codes),1):
            group=".".join(code.split(".")[:-1])
            cursor.execute("""INSERT INTO t_crp_dimension_definition (dimension_code,group_code,name_zh,name_en,description,schema_version,display_order,is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,1) ON DUPLICATE KEY UPDATE group_code=VALUES(group_code),display_order=VALUES(display_order),is_active=1""",(code,group,chinese_name(code),code.split(".")[-1],f"{group_chinese_name(group)}：由 CPR-1.6 Prompt 定义",SCHEMA_VERSION,order))
        cursor.execute("SELECT id,dimension_code FROM t_crp_dimension_definition WHERE schema_version=%s",(SCHEMA_VERSION,));definitions={x["dimension_code"]:x["id"] for x in cursor.fetchall()}
        for item in items(data):
            wid=item["basic"]["workId"];work_id=stable_id("work",wid);confidence=float(item["basic"]["confidence"])
            cursor.execute("SELECT COALESCE(MAX(profile_version),0)+1 v FROM t_crp_profile WHERE content_type=11 AND content_id=%s AND schema_version=%s",(work_id,SCHEMA_VERSION));version=cursor.fetchone()["v"]
            pid=stable_id("crp-profile",f"{work_id}:{SCHEMA_VERSION}:{version}:{run_id}")
            cursor.execute("""INSERT INTO t_crp_profile (id,content_type,content_id,schema_version,profile_version,status,is_current,summary_text,overall_confidence,evidence_coverage,generation_run_id,raw_payload,validate_time) VALUES (%s,11,%s,%s,%s,3,1,%s,%s,1,%s,%s,NOW())""",(pid,work_id,SCHEMA_VERSION,version,item["semantic"]["summary"],confidence,run_id,json.dumps(item,ensure_ascii=False)));counts["profiles"]+=1
            fact_by_path={x["dimensionPath"]:x for x in item["vector_facts"] if isinstance(x.get("explanation"),str) and x["explanation"].strip()}
            for path,score in numeric_dimensions(item).items():
                code=canonical(path);did=definitions[code];fact=fact_by_path.get(path)
                if fact is None:
                    cursor.execute("INSERT INTO t_crp_dimension_value (profile_id,dimension_id,score,confidence,evidence_state,evidence_count,rationale) VALUES (%s,%s,%s,%s,1,0,NULL)",(pid,did,score,confidence));counts["dimensions"]+=1
                    continue
                relation=4 if score<=.25 else (2 if score<.5 else 1);state=2 if relation==4 else 1
                eid=stable_id("crp-evidence",f"{pid}:{path}:{fact['explanation']}");etype=7 if relation==4 else 2
                cursor.execute("INSERT INTO t_crp_evidence (id,profile_id,source_id,evidence_type,fact_text,source_locator,verification_status,quality_score) VALUES (%s,%s,NULL,%s,%s,%s,1,%s)",(eid,pid,etype,fact["explanation"],f"vector_facts[{path}]",confidence));counts["evidence"]+=1
                cursor.execute("INSERT INTO t_crp_dimension_value (profile_id,dimension_id,score,confidence,evidence_state,evidence_count,rationale) VALUES (%s,%s,%s,%s,%s,1,%s)",(pid,did,score,confidence,state,fact["explanation"]));counts["dimensions"]+=1
                cursor.execute("INSERT INTO t_crp_dimension_evidence (profile_id,dimension_id,evidence_id,relation_type,evidence_weight,explanation) VALUES (%s,%s,%s,%s,%s,%s)",(pid,did,eid,relation,confidence,fact["explanation"]));counts["dimensionEvidence"]+=1
            for trait,score in item["personality_affinity"].items():
                system=1 if trait in JUNG else (2 if trait in BIG_FIVE else 4)
                cursor.execute("INSERT INTO t_crp_projection_value (profile_id,projection_system,trait_code,score,confidence,projection_model,model_version) VALUES (%s,%s,%s,%s,%s,'CPR-1.6 supplied affinity','v1')",(pid,system,trait,score,confidence));counts["projections"]+=1
    return {"runId":run_id,**counts}


def main()->None:
    parser=argparse.ArgumentParser();parser.add_argument("command",choices=("validate","import"));parser.add_argument("--input",type=Path,required=True);args=parser.parse_args();data=json.loads(args.input.read_text(encoding="utf-8-sig"))
    errors=validate(data,MySQLConfig.from_env() if args.command=="import" else None)
    if errors: raise ValueError("\n".join(errors[:100]))
    result={"valid":True,"items":len(items(data))} if args.command=="validate" else apply(MySQLConfig.from_env(),args.input)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
