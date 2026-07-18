from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.crp_dimension_catalog import chinese_name, group_chinese_name
from database.import_prompt_dataset import stable_id
from database.mysql_client import connect, transaction


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "rgmj-crp-dataset/v1"
SCHEMA_VERSION = "cpr-1.2"
CONTENT_TYPE_WORK = 11

PSYCHOLOGY_GROUPS = {
    "cognitive": ["abstractness","complexity","depth_of_thought","logical_structure","novelty","exploration","ambiguity","predictability","information_density","learning_value"],
    "worldview": ["personal_scale","relationship_scale","social_scale","civilization_scale","cosmic_scale","time_span","fantasy_level"],
    "emotion": ["happiness","sadness","loneliness","warmth","fear","tension","awe","nostalgia","hope","melancholy","anger","peace"],
    "aesthetic": ["beauty","minimalism","luxury","darkness","brightness","dreamlike","mystery","experimental","classical","modern"],
    "narrative": ["plot_complexity","character_depth","character_growth","world_building","nonlinear_structure","slow_immersion","action_intensity","conflict_intensity","philosophical_depth"],
    "social_relationship": ["individualism","collectivism","family","friendship","love","community","competition","cooperation","power_relationship","identity"],
    "value": ["freedom","order","truth","beauty","achievement","justice","sacrifice","self_exploration","tradition","change","survival","meaning"],
    "behavior": ["curiosity","creation","adventure","reflection","competition","social","discipline","risk","calm","escape"],
    "sensory": ["visual_intensity","color_richness","sound_intensity","rhythm_energy","atmosphere","immersion"],
}
EXPERIENCE_KEYS = ["passion","healing","awe","nostalgia","reflection","romance","tension","warmth","loneliness","hope"]
AFFINITY_KEYS = ["Ni","Ne","Ti","Te","Fi","Fe","Si","Se","Assertive","Turbulent"]


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_missing(config: MySQLConfig, output: Path) -> dict[str, Any]:
    with connect(config) as db, db.cursor() as cursor:
        cursor.execute("""
            SELECT w.id AS databaseWorkId,w.work_name AS workName,w.original_name AS originalName,
                   w.genre,w.subcategory,w.year,c.code AS countryCode,w.introduction
            FROM t_character_work w LEFT JOIN t_word_countries c ON c.id=w.country_id
            WHERE w.deleted=0 AND NOT EXISTS (
                SELECT 1 FROM t_crp_profile p
                WHERE p.content_type=11 AND p.content_id=w.id AND p.schema_version=%s
                  AND p.is_current=1 AND p.status IN (2,3)
            ) ORDER BY w.id
        """, (SCHEMA_VERSION,))
        works = list(cursor.fetchall())
        for work in works:
            cursor.execute("""
                SELECT n.character_name AS name,r.relation_type AS relationType
                FROM t_work_creator_relation r JOIN t_character_name n
                  ON n.character_info_id=r.character_id AND n.deleted=0
                WHERE r.work_id=%s AND n.language IN (0,1,2)
                ORDER BY r.sort_order,n.language
            """, (work["databaseWorkId"],))
            seen = set(); creators=[]
            for row in cursor.fetchall():
                key=(row["name"],row["relationType"])
                if key not in seen: creators.append(row); seen.add(key)
            work["creators"] = creators
    payload={"schema":"rgmj-crp-input/v1","schemaVersion":SCHEMA_VERSION,"generatedAt":datetime.now(timezone.utc).isoformat(),"works":works}
    dump_json(output,payload)
    return {"output":str(output.resolve()),"missingWorks":len(works)}


def _score(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not 0 <= value <= 1:
        errors.append(f"{path} 必须是 0.0—1.0 数值")
    elif round(float(value),2) != float(value): errors.append(f"{path} 最多两位小数")


def validate_dataset(data: dict[str, Any], expected_ids: set[int] | None=None) -> list[str]:
    errors=[]
    if data.get("schema") != SCHEMA: errors.append(f"schema 必须是 {SCHEMA}")
    items=data.get("items")
    if not isinstance(items,list): return errors+["items 必须是数组"]
    seen=set()
    for i,item in enumerate(items):
        base=f"items[{i}]"; wid=item.get("databaseWorkId")
        if not isinstance(wid,int): errors.append(f"{base}.databaseWorkId 缺失")
        elif wid in seen: errors.append(f"{base}.databaseWorkId 重复")
        seen.add(wid)
        basic=item.get("basic",{}); semantic=item.get("semantic",{})
        for key in ("name","type","countryName","ontologyVersion","promptVersion","annotationModelVersion","generatorDate","evidenceVersion","annotationLanguage","primaryMediaType"):
            if not isinstance(basic.get(key),str) or not basic[key]: errors.append(f"{base}.basic.{key} 缺失")
        _score(basic.get("confidence"),f"{base}.basic.confidence",errors)
        for key in ("summary","core_experience"):
            if not isinstance(semantic.get(key),str) or not semantic[key]: errors.append(f"{base}.semantic.{key} 缺失")
        for key in ("themes","keywords"):
            if not isinstance(semantic.get(key),list) or not semantic[key]: errors.append(f"{base}.semantic.{key} 缺失")
        exp=item.get("experience_vector",{})
        for key in EXPERIENCE_KEYS: _score(exp.get(key),f"{base}.experience_vector.{key}",errors)
        psych=item.get("psychology_vector",{})
        for group,keys in PSYCHOLOGY_GROUPS.items():
            values=psych.get(group,{})
            for key in keys: _score(values.get(key),f"{base}.psychology_vector.{group}.{key}",errors)
        media=item.get("media_vector")
        if not isinstance(media,dict) or len(media)!=1: errors.append(f"{base}.media_vector 必须只含一种媒介向量")
        affinity=item.get("personality_affinity",{})
        for key in AFFINITY_KEYS: _score(affinity.get(key),f"{base}.personality_affinity.{key}",errors)
        evidence=item.get("supporting_evidence")
        if not isinstance(evidence,list) or not evidence or any(not isinstance(x,str) or not x.strip() for x in evidence): errors.append(f"{base}.supporting_evidence 非法")
        expected_codes={code for code,_,_ in _dimension_rows(item)}
        mappings=item.get("dimension_evidence")
        if not isinstance(mappings,list): errors.append(f"{base}.dimension_evidence 必须是数组"); mappings=[]
        mapped_codes=set()
        for mapping_index,mapping in enumerate(mappings):
            path=f"{base}.dimension_evidence[{mapping_index}]"; code=mapping.get("dimensionCode")
            if code not in expected_codes: errors.append(f"{path}.dimensionCode 不属于当前向量：{code}")
            elif code in mapped_codes: errors.append(f"{path}.dimensionCode 重复：{code}")
            mapped_codes.add(code)
            refs=mapping.get("evidenceIndexes")
            if not isinstance(refs,list) or not refs: errors.append(f"{path}.evidenceIndexes 必须是非空数组")
            elif any(not isinstance(ref,int) or ref<0 or ref>=len(evidence) for ref in refs): errors.append(f"{path}.evidenceIndexes 越界")
            if mapping.get("relationType") not in (1,2,3,4): errors.append(f"{path}.relationType 非法")
            _score(mapping.get("evidenceWeight"),f"{path}.evidenceWeight",errors)
            if not isinstance(mapping.get("explanation"),str) or not mapping["explanation"].strip(): errors.append(f"{path}.explanation 缺失")
        if mapped_codes != expected_codes: errors.append(f"{base}.dimension_evidence 覆盖不完整：缺少 {len(expected_codes-mapped_codes)} 个维度")
    if expected_ids is not None:
        if seen != expected_ids: errors.append(f"作品覆盖不一致：缺少 {len(expected_ids-seen)}，多出 {len(seen-expected_ids)}")
    return errors


def _dimension_rows(item: dict[str,Any]):
    for key in EXPERIENCE_KEYS: yield f"experience.{key}","experience",item["experience_vector"][key]
    for group,keys in PSYCHOLOGY_GROUPS.items():
        for key in keys: yield f"psychology.{group}.{key}",f"psychology.{group}",item["psychology_vector"][group][key]
    for vector_name,values in item["media_vector"].items():
        for key,value in values.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool): yield f"media.{vector_name}.{key}",f"media.{vector_name}",value


def import_dataset(config: MySQLConfig,path:Path,dataset_name:str,replace_current:bool=False)->dict[str,Any]:
    data=json.loads(path.read_text(encoding="utf-8-sig")); errors=validate_dataset(data)
    if errors: raise ValueError("\n".join(errors[:100]))
    raw=path.read_bytes(); run_id=stable_id("crp-run",f"{dataset_name}:{hashlib.sha256(raw).hexdigest()}")
    with transaction(config) as db:
        with db.cursor() as cursor:
            work_ids={x["databaseWorkId"] for x in data["items"]}
            if work_ids:
                marks=",".join(["%s"]*len(work_ids)); cursor.execute(f"SELECT id FROM t_character_work WHERE deleted=0 AND id IN ({marks})",tuple(work_ids)); existing={x["id"] for x in cursor.fetchall()}
                if existing!=work_ids: raise ValueError(f"数据库作品缺失：{work_ids-existing}")
                cursor.execute(f"SELECT DISTINCT content_id FROM t_crp_profile WHERE content_type=11 AND schema_version=%s AND is_current=1 AND status IN (2,3) AND content_id IN ({marks})",(SCHEMA_VERSION,*work_ids))
                already={x["content_id"] for x in cursor.fetchall()}
                if already and not replace_current: raise ValueError(f"输入包含已经具有当前 CPR 的作品：{len(already)} 个；请重新执行 export-missing 或显式使用 --replace-current")
            cursor.execute("""INSERT INTO t_crp_generation_run
                (id,pipeline_version,prompt_version,model_provider,model_name,model_version,web_search_used,input_hash,output_hash,status,generation_metadata,finished_at)
                VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,3,%s,NOW()) ON DUPLICATE KEY UPDATE status=3,finished_at=NOW(),output_hash=VALUES(output_hash)""",
                (run_id,"crp-dataset-pipeline/v1","CPR-1.2","openai","GPT-5","offline-knowledge",hashlib.sha256(raw).hexdigest(),hashlib.sha256(raw).hexdigest(),json.dumps({"datasetName":dataset_name},ensure_ascii=False)))
            definitions={}
            all_dims=[]
            for item in data["items"]:
                all_dims.extend(_dimension_rows(item))
            for order,(code,group) in enumerate(dict.fromkeys((a,b) for a,b,_ in all_dims),1):
                cursor.execute("""INSERT INTO t_crp_dimension_definition
                    (dimension_code,group_code,name_zh,name_en,description,schema_version,display_order,is_active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,1)
                    ON DUPLICATE KEY UPDATE group_code=VALUES(group_code),display_order=VALUES(display_order),is_active=1""",
                    (code,group,chinese_name(code),code.split('.')[-1],f"{group_chinese_name(group)}：由 CPR-1.2 Prompt 定义的归一化维度",SCHEMA_VERSION,order))
            cursor.execute("SELECT id,dimension_code FROM t_crp_dimension_definition WHERE schema_version=%s",(SCHEMA_VERSION,)); definitions={x["dimension_code"]:x["id"] for x in cursor.fetchall()}
            counts={"profiles":0,"dimensions":0,"evidence":0,"dimensionEvidence":0,"projections":0}
            for item in data["items"]:
                wid=item["databaseWorkId"]
                cursor.execute("UPDATE t_crp_profile SET is_current=0,status=5 WHERE content_type=11 AND content_id=%s AND schema_version=%s AND is_current=1",(wid,SCHEMA_VERSION))
                cursor.execute("SELECT COALESCE(MAX(profile_version),0)+1 version FROM t_crp_profile WHERE content_type=11 AND content_id=%s AND schema_version=%s",(wid,SCHEMA_VERSION)); version=cursor.fetchone()["version"]
                pid=stable_id("crp-profile",f"{wid}:{SCHEMA_VERSION}:{version}:{run_id}")
                confidence=float(item["basic"]["confidence"]); evidence=item["supporting_evidence"]
                cursor.execute("""INSERT INTO t_crp_profile
                    (id,content_type,content_id,schema_version,profile_version,status,is_current,summary_text,overall_confidence,evidence_coverage,generation_run_id,raw_payload,validate_time)
                    VALUES (%s,11,%s,%s,%s,2,1,%s,%s,%s,%s,%s,NOW())""",
                    (pid,wid,SCHEMA_VERSION,version,item["semantic"]["summary"],confidence,min(1,len(evidence)/8),run_id,json.dumps(item,ensure_ascii=False)))
                counts["profiles"]+=1
                dimension_ids=[]
                mapping_by_code={mapping["dimensionCode"]:mapping for mapping in item["dimension_evidence"]}
                for code,_,score in _dimension_rows(item):
                    dimension_id=definitions[code]; dimension_ids.append(dimension_id)
                    mapping=mapping_by_code[code]; state=2 if mapping["relationType"]==4 else (3 if mapping["relationType"]==3 else 1)
                    cursor.execute("INSERT INTO t_crp_dimension_value (profile_id,dimension_id,score,confidence,evidence_state,evidence_count,rationale) VALUES (%s,%s,%s,%s,%s,%s,%s)",(pid,dimension_id,score,confidence,state,len(mapping["evidenceIndexes"]),mapping["explanation"])); counts["dimensions"]+=1
                evidence_ids=[]
                for order,text in enumerate(evidence):
                    eid=stable_id("crp-evidence",f"{pid}:{order}:{text}")
                    evidence_ids.append(eid)
                    cursor.execute("INSERT INTO t_crp_evidence (id,profile_id,source_id,evidence_type,fact_text,source_locator,verification_status,quality_score) VALUES (%s,%s,NULL,1,%s,%s,1,%s)",(eid,pid,text,f"supporting_evidence[{order}]",confidence)); counts["evidence"]+=1
                for code,_,_ in _dimension_rows(item):
                    mapping=mapping_by_code[code]; dimension_id=definitions[code]
                    for evidence_index in mapping["evidenceIndexes"]:
                        cursor.execute("INSERT INTO t_crp_dimension_evidence (profile_id,dimension_id,evidence_id,relation_type,evidence_weight,explanation) VALUES (%s,%s,%s,%s,%s,%s)",(pid,dimension_id,evidence_ids[evidence_index],mapping["relationType"],mapping["evidenceWeight"],mapping["explanation"])); counts["dimensionEvidence"]+=1
                for trait,score in item["personality_affinity"].items():
                    system=1 if trait in AFFINITY_KEYS[:8] else 4
                    cursor.execute("INSERT INTO t_crp_projection_value (profile_id,projection_system,trait_code,score,confidence,projection_model,model_version) VALUES (%s,%s,%s,%s,%s,%s,%s)",(pid,system,trait,score,confidence,"CPR-1.2 affinity mapping","v1")); counts["projections"]+=1
    return {"runId":run_id,**counts}


def status(config:MySQLConfig)->dict[str,int]:
    with connect(config) as db,db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) count FROM t_character_work WHERE deleted=0"); works=cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(DISTINCT content_id) count FROM t_crp_profile WHERE content_type=11 AND schema_version=%s AND is_current=1 AND status IN (2,3)",(SCHEMA_VERSION,)); profiles=cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) count FROM t_crp_profile p LEFT JOIN t_character_work w ON w.id=p.content_id WHERE p.content_type=11 AND p.is_current=1 AND w.id IS NULL"); broken=cursor.fetchone()["count"]
    return {"works":works,"worksWithCurrentCrp":profiles,"worksMissingCurrentCrp":works-profiles,"brokenProfiles":broken}


def main()->None:
    p=argparse.ArgumentParser(); p.add_argument("command",choices=("export-missing","validate","import","status")); p.add_argument("--input",type=Path); p.add_argument("--output",type=Path,default=ROOT/"DataSet"/"crp_missing_work_input.json"); p.add_argument("--dataset-name",default="crp_missing_works"); p.add_argument("--replace-current",action="store_true"); a=p.parse_args()
    if a.command=="export-missing": result=export_missing(MySQLConfig.from_env(),a.output)
    elif a.command=="status": result=status(MySQLConfig.from_env())
    else:
        if not a.input: p.error("validate/import 需要 --input")
        data=json.loads(a.input.read_text(encoding="utf-8-sig")); errors=validate_dataset(data)
        if errors: raise ValueError("\n".join(errors[:100]))
        result={"valid":True,"items":len(data["items"])} if a.command=="validate" else import_dataset(MySQLConfig.from_env(),a.input,a.dataset_name,a.replace_current)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
