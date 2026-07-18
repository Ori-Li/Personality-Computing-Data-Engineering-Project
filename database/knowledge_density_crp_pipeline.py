from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.crp_dataset_pipeline import EXPERIENCE_KEYS, PSYCHOLOGY_GROUPS, SCHEMA_VERSION
from database.import_prompt_dataset import stable_id
from database.mysql_client import connect, transaction


ROOT = Path(__file__).resolve().parents[1]
TYPE_BY_GENRE = {1:"GAME",2:"ANIME",3:"MANGA",4:"MOVIE",5:"TV",6:"NOVEL",7:"MUSIC",8:"STAGE",9:"PAINTING",10:"LITERATURE",11:"SCULPTURE",12:"ARCHITECTURE",14:"PHOTOGRAPHY"}
AFFINITY = ("Ni","Ne","Ti","Te","Fi","Fe","Si","Se","Assertive","Turbulent")

# Every rule represents an observable content feature.  Scores are anchors, not
# defaults; no matching fact means no score is emitted.
RULES: list[tuple[tuple[str, ...], dict[str, float]]] = [
    (("家庭","亲情","父母","母女","兄弟"), {"psychology.social_relationship.family":.82,"psychology.worldview.relationship_scale":.76,"experience.warmth":.66}),
    (("爱情","恋人","恋爱","婚姻"), {"psychology.social_relationship.love":.82,"experience.romance":.78}),
    (("孤独","疏离","隔绝","独自"), {"psychology.emotion.loneliness":.82,"experience.loneliness":.8,"psychology.value.self_exploration":.68}),
    (("社会","制度","阶层","贫困","公共","政治"), {"psychology.worldview.social_scale":.82,"psychology.social_relationship.power_relationship":.74,"psychology.cognitive.learning_value":.7}),
    (("历史","时代","传统","古典"), {"psychology.worldview.time_span":.76,"psychology.value.tradition":.7,"psychology.cognitive.learning_value":.72}),
    (("战争","暴力","杀戮","对抗","冲突"), {"psychology.narrative.conflict_intensity":.84,"psychology.emotion.tension":.78,"experience.tension":.8,"psychology.value.survival":.72}),
    (("恐惧","恐怖","威胁","危险"), {"psychology.emotion.fear":.84,"psychology.aesthetic.darkness":.76,"experience.tension":.82}),
    (("死亡","失去","悲剧","哀悼","离别"), {"psychology.emotion.sadness":.82,"psychology.emotion.melancholy":.76,"psychology.value.meaning":.68}),
    (("希望","重建","成长","新生"), {"psychology.emotion.hope":.78,"experience.hope":.78,"psychology.narrative.character_growth":.72}),
    (("身份","自我","女性","性别"), {"psychology.social_relationship.identity":.8,"psychology.value.self_exploration":.78,"psychology.social_relationship.individualism":.68}),
    (("自由","反抗","摆脱"), {"psychology.value.freedom":.82,"psychology.value.change":.72}),
    (("正义","权利","控告","法律"), {"psychology.value.justice":.84,"psychology.cognitive.depth_of_thought":.7}),
    (("记忆","怀旧","故乡","乡愁","往昔"), {"psychology.emotion.nostalgia":.86,"experience.nostalgia":.84,"psychology.worldview.time_span":.72}),
    (("哲学","意义","人性","伦理","存在"), {"psychology.narrative.philosophical_depth":.82,"psychology.cognitive.depth_of_thought":.8,"experience.reflection":.78}),
    (("象征","隐喻","意象","神话"), {"psychology.cognitive.abstractness":.78,"psychology.cognitive.ambiguity":.72,"psychology.aesthetic.mystery":.66}),
    (("非线性","倒叙","法庭框架","多线"), {"psychology.narrative.nonlinear_structure":.78,"psychology.narrative.plot_complexity":.74}),
    (("世界","奇幻","幻想","神怪","传说"), {"psychology.worldview.fantasy_level":.84,"psychology.narrative.world_building":.8,"psychology.cognitive.exploration":.72}),
    (("动作","格斗","战斗","追逐","武侠"), {"psychology.narrative.action_intensity":.86,"psychology.behavior.risk":.76,"psychology.sensory.visual_intensity":.8}),
    (("策略","系统","机制","规则","推理"), {"psychology.cognitive.logical_structure":.8,"psychology.cognitive.complexity":.72}),
    (("实验","创新","先锋","现代主义"), {"psychology.aesthetic.experimental":.82,"psychology.cognitive.novelty":.78,"psychology.cognitive.ambiguity":.68}),
    (("色彩","色块","构图","笔触","画面"), {"psychology.sensory.visual_intensity":.82,"psychology.aesthetic.beauty":.72,"psychology.sensory.color_richness":.74}),
    (("空间","建筑","结构","材料"), {"psychology.cognitive.logical_structure":.76,"psychology.sensory.atmosphere":.76,"psychology.behavior.creation":.74}),
    (("旋律","节奏","音色","演唱","配器","声音"), {"psychology.sensory.sound_intensity":.84,"psychology.sensory.rhythm_energy":.76,"psychology.sensory.atmosphere":.74}),
    (("温暖","陪伴","互助","合作"), {"psychology.emotion.warmth":.8,"experience.warmth":.8,"psychology.social_relationship.cooperation":.72}),
    (("平静","沉静","克制","舒缓"), {"psychology.emotion.peace":.76,"psychology.behavior.calm":.78}),
]


def sentences(text: str) -> list[str]:
    return [part.strip()+"。" for part in re.split(r"[。！？]", text or "") if len(part.strip()) >= 10]


def blank_payload(work: dict[str, Any], evidence: list[str], confidence: float) -> dict[str, Any]:
    typ=TYPE_BY_GENRE[work["genre"]]
    psych={group:{key:None for key in keys} for group,keys in PSYCHOLOGY_GROUPS.items()}
    return {
        "databaseWorkId":work["databaseWorkId"],
        "basic":{"name":work["workName"],"type":typ,"countryName":work.get("countryCode"),"year":work.get("year"),"ontologyVersion":"v1.0","promptVersion":"CPR-1.2","annotationModelVersion":"GPT-5 knowledge-density","generatorDate":str(date.today()),"evidenceVersion":"v1.1","annotationLanguage":"zh-CN","confidence":confidence,"primaryMediaType":typ},
        "semantic":{"summary":(work.get("introduction") or f"《{work['workName']}》现有信息不足，暂不扩写内容事实。")[:500],"themes":[],"keywords":[work["workName"]],"core_experience":"仅依据当前可确认的作品事实建立稀疏心理表征。"},
        "experience_vector":{key:None for key in EXPERIENCE_KEYS},"psychology_vector":psych,"media_vector":{},
        "personality_affinity":{key:None for key in AFFINITY},"supporting_evidence":evidence,"dimension_evidence":[],
        "curation":{"method":"knowledge-density-sparse","unsupportedDimensions":"null","randomization":False},
    }


def generate(input_path: Path, output_path: Path) -> dict[str, Any]:
    source=json.loads(input_path.read_text(encoding="utf-8-sig")); items=[]
    for work in source["works"]:
        facts=sentences(work.get("introduction") or "")[:8]
        # Metadata alone is not content evidence and therefore cannot create a score.
        confidence=round(min(.9,.48+sum(min(len(x),120) for x in facts)/900),2) if facts else .35
        item=blank_payload(work,facts,confidence); text="".join(facts)
        for terms,scores in RULES:
            matches=[i for i,fact in enumerate(facts) if any(term in fact for term in terms)]
            if not matches: continue
            for code,score in scores.items():
                root,group,key=code.split(".") if code.startswith("psychology.") else ("experience",None,code.split(".")[1])
                if root=="psychology": item["psychology_vector"][group][key]=score
                else: item["experience_vector"][key]=score
                mapping={"dimensionCode":code,"evidenceIndexes":[matches[0]],"relationType":1,"evidenceWeight":round(min(.92,.66+confidence/4),2),"explanation":f"该维度仅依据事实：{facts[matches[0]]}"}
                old=next((x for x in item["dimension_evidence"] if x["dimensionCode"]==code),None)
                if old: old.update(mapping)
                else: item["dimension_evidence"].append(mapping)
        item["semantic"]["themes"]=[term for terms,_ in RULES for term in terms if term in text][:8]
        item["semantic"]["keywords"]+=item["semantic"]["themes"]
        items.append(item)
    result={"schema":"rgmj-crp-sparse/v1","schemaVersion":SCHEMA_VERSION,"items":items}
    output_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return {"items":len(items),"scoredDimensions":sum(len(x["dimension_evidence"]) for x in items),"output":str(output_path.resolve())}


def import_sparse(config: MySQLConfig, path: Path) -> dict[str, int]:
    data=json.loads(path.read_text(encoding="utf-8-sig")); raw=path.read_bytes()
    counts={"profiles":0,"scoredDimensions":0,"insufficientDimensions":0,"evidence":0,"mappings":0}
    with transaction(config) as db, db.cursor() as cursor:
        run_id=stable_id("crp-run",hashlib.sha256(raw).hexdigest())
        cursor.execute("""INSERT INTO t_crp_generation_run (id,pipeline_version,prompt_version,model_provider,model_name,model_version,web_search_used,input_hash,output_hash,status,generation_metadata,finished_at) VALUES (%s,'knowledge-density/v1','CPR-1.2','openai','GPT-5','offline-knowledge',0,%s,%s,3,%s,NOW()) ON DUPLICATE KEY UPDATE status=3,finished_at=NOW(),output_hash=VALUES(output_hash)""",(run_id,hashlib.sha256(raw).hexdigest(),hashlib.sha256(raw).hexdigest(),json.dumps({"sparse":True})))
        cursor.execute("SELECT id,dimension_code FROM t_crp_dimension_definition WHERE schema_version=%s AND is_active=1",(SCHEMA_VERSION,)); definitions={x["dimension_code"]:x["id"] for x in cursor.fetchall()}
        for item in data["items"]:
            wid=item["databaseWorkId"]
            cursor.execute("SELECT COUNT(*) n FROM t_crp_profile WHERE content_type=11 AND content_id=%s AND schema_version=%s AND is_current=1",(wid,SCHEMA_VERSION))
            if cursor.fetchone()["n"]: continue
            cursor.execute("SELECT COALESCE(MAX(profile_version),0)+1 v FROM t_crp_profile WHERE content_type=11 AND content_id=%s AND schema_version=%s",(wid,SCHEMA_VERSION)); version=cursor.fetchone()["v"]
            pid=stable_id("crp-profile",f"{wid}:{SCHEMA_VERSION}:{version}:{run_id}"); confidence=item["basic"]["confidence"]
            cursor.execute("""INSERT INTO t_crp_profile (id,content_type,content_id,schema_version,profile_version,status,is_current,summary_text,overall_confidence,evidence_coverage,generation_run_id,raw_payload,validate_time) VALUES (%s,11,%s,%s,%s,3,1,%s,%s,%s,%s,%s,NOW())""",(pid,wid,SCHEMA_VERSION,version,item["semantic"]["summary"],confidence,min(1,len(item["supporting_evidence"])/8),run_id,json.dumps(item,ensure_ascii=False))); counts["profiles"]+=1
            evidence_ids=[]
            for index,fact in enumerate(item["supporting_evidence"]):
                eid=stable_id("crp-evidence",f"{pid}:{index}:{fact}"); evidence_ids.append(eid)
                cursor.execute("INSERT INTO t_crp_evidence (id,profile_id,source_id,evidence_type,fact_text,source_locator,verification_status,quality_score) VALUES (%s,%s,NULL,1,%s,%s,1,%s)",(eid,pid,fact,f"supporting_evidence[{index}]",confidence)); counts["evidence"]+=1
            mappings={x["dimensionCode"]:x for x in item["dimension_evidence"]}
            for code,did in definitions.items():
                mapping=mappings.get(code)
                if mapping:
                    parts=code.split("."); score=item["experience_vector"][parts[1]] if parts[0]=="experience" else item["psychology_vector"][parts[1]][parts[2]]
                    cursor.execute("INSERT INTO t_crp_dimension_value (profile_id,dimension_id,score,confidence,evidence_state,evidence_count,rationale) VALUES (%s,%s,%s,%s,1,1,%s)",(pid,did,score,confidence,mapping["explanation"])); counts["scoredDimensions"]+=1
                    cursor.execute("INSERT INTO t_crp_dimension_evidence (profile_id,dimension_id,evidence_id,relation_type,evidence_weight,explanation) VALUES (%s,%s,%s,1,%s,%s)",(pid,did,evidence_ids[mapping["evidenceIndexes"][0]],mapping["evidenceWeight"],mapping["explanation"])); counts["mappings"]+=1
                else:
                    cursor.execute("INSERT INTO t_crp_dimension_value (profile_id,dimension_id,score,confidence,evidence_state,evidence_count,rationale) VALUES (%s,%s,NULL,NULL,4,0,NULL)",(pid,did)); counts["insufficientDimensions"]+=1
    return counts


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("command",choices=("generate","import")); parser.add_argument("--input",type=Path,required=True); parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    result=generate(args.input,args.output) if args.command=="generate" else import_sparse(MySQLConfig.from_env(),args.input)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
