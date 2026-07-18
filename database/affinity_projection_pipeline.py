from __future__ import annotations

import argparse, json, math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from database.config import MySQLConfig
from database.mysql_client import connect, transaction


ROOT=Path(__file__).resolve().parents[1]
SCHEMA="rgmj-work-affinity/v1"
TRAITS=("Ni","Ne","Ti","Te","Fi","Fe","Si","Se","Assertive","Turbulent")

KEYWORDS={
"Ni":["象征","隐喻","哲学","命运","意义","梦","神话","精神","存在","多义","政治理想","记忆","生命","未来","时间"],
"Ne":["幻想","探索","未知","实验","新颖","可能","世界","重写","跨类型","超现实","开放","变化"],
"Ti":["逻辑","结构","规则","推理","机制","系统","辩论","秩序","形式","概念","策略"],
"Te":["行动","组织","策略","纪律","成就","竞争","生产","公共","责任","制度","工业"],
"Fi":["自我","身份","孤独","内心","个人","私人","价值","自由","女性","成长","乡愁","放逐","情感","爱意"],
"Fe":["家庭","关系","群体","社会","合作","共同体","爱情","友情","温暖","责任","观众","倾听","对话","顾客","陪伴","传播"],
"Si":["记忆","历史","传统","怀旧","故乡","日常","古典","年代","文化","仪式","乡村","反复","稳定"],
"Se":["动作","身体","视觉","色彩","声音","节奏","表演","空间","材料","镜头","战斗","舞台"],
}

def at(p:dict[str,Any],group:str,key:str)->float:return float(p.get(group,{}).get(key,0))
def keyword_signal(text:str,trait:str)->float:return min(1,sum(1 for x in KEYWORDS[trait] if x in text)/2)
def raw_traits(item:dict[str,Any])->dict[str,float]:
    p=item["psychology_vector"]; e=item["experience_vector"]; text=json.dumps({"semantic":item["semantic"],"evidence":item["supporting_evidence"]},ensure_ascii=False)
    c,w,em,a,n,s,v,b,se=[p[x] for x in ("cognitive","worldview","emotion","aesthetic","narrative","social_relationship","value","behavior","sensory")]
    # Curated CPR intentionally leaves unsupported dimensions as null.  A
    # projection must use only observed values instead of silently converting
    # missing evidence into a synthetic midpoint.
    def q(*xs:Any)->float:
        values=[float(x) for x in xs if x is not None]
        return sum(values)/len(values) if values else .5
    def inv(value:Any)->float|None:
        return None if value is None else 1-float(value)
    raw={
    "Ni":q(c["abstractness"],c["depth_of_thought"],c["ambiguity"],n["philosophical_depth"],v["meaning"],a["mystery"]),
    "Ne":q(c["novelty"],c["exploration"],w["fantasy_level"],b["curiosity"],b["adventure"],a["experimental"]),
    "Ti":q(c["logical_structure"],c["complexity"],c["information_density"],n["plot_complexity"],v["truth"],b["reflection"]),
    "Te":q(v["achievement"],v["order"],s["competition"],b["discipline"],b["competition"],s["power_relationship"]),
    "Fi":q(v["self_exploration"],v["freedom"],v["meaning"],s["identity"],s["love"],em["loneliness"]),
    "Fe":q(s["family"],s["friendship"],s["community"],s["cooperation"],em["warmth"],e["warmth"]),
    "Si":q(em["nostalgia"],e["nostalgia"],v["tradition"],a["classical"],b["calm"],w["time_span"]),
    "Se":q(n["action_intensity"],b["risk"],se["visual_intensity"],se["sound_intensity"],se["rhythm_energy"],se["immersion"]),
    }
    for trait in raw: raw[trait]=.48*raw[trait]+.52*keyword_signal(text,trait)
    positive_relation=sum(1 for word in ("倾听","陪伴","温暖","友情","家庭","合作","理解","爱意","对话","顾客","共同体") if word in text)
    hostile_relation=sum(1 for word in ("恐怖","威胁","猎场","压迫","权力斗争","对抗","战争","杀戮","疏离") if word in text)
    inner_value=sum(1 for word in ("身份","自我","私人","生命权","尊严","内心","个人价值","记忆") if word in text)
    raw["Fe"]+=min(.18,positive_relation*.045)-min(.2,hostile_relation*.05)
    raw["Fi"]+=min(.16,inner_value*.04)
    typ=item["basic"]["type"]
    type_boost={"GAME":{"Ne":.03,"Ti":.02,"Se":.03},"MOVIE":{"Se":.03},"TV":{"Fe":.03},"NOVEL":{"Ni":.03,"Fi":.02},"LITERATURE":{"Ni":.04,"Fi":.02,"Si":.02},"MUSIC":{"Fi":.02,"Se":.03},"STAGE":{"Fe":.02,"Se":.04},"PAINTING":{"Ni":.02,"Se":.03},"SCULPTURE":{"Se":.03},"ARCHITECTURE":{"Ti":.04,"Te":.03},"PHOTOGRAPHY":{"Se":.03,"Si":.02},"ANIME":{"Ne":.03,"Fi":.02},"MANGA":{"Ne":.02,"Fi":.02}}.get(typ,{})
    for trait,boost in type_boost.items():raw[trait]+=boost
    raw["Assertive"]=q(em["hope"],v["achievement"],b["discipline"],inv(em["fear"]),inv(em["tension"]))
    raw["Turbulent"]=q(em["tension"],em["melancholy"],em["fear"],c["ambiguity"],v["self_exploration"])
    return raw

def calibrate(items:list[dict[str,Any]])->None:
    for trait in TRAITS:
        vals=[x["raw"][trait] for x in items]; mu=mean(vals); sd=max(pstdev(vals),.025)
        for x in items:
            z=(x["raw"][trait]-mu)/(sd*1.15); score=.1+.8/(1+math.exp(-z)); x["scores"][trait]=round(score,2)

def generate(config:MySQLConfig,output:Path)->dict[str,Any]:
    with connect(config) as db,db.cursor() as cursor:
        cursor.execute("""SELECT p.id profileId,p.content_id workId,w.work_name workName,p.raw_payload
          FROM t_crp_profile p JOIN t_character_work w ON w.id=p.content_id
          WHERE p.content_type=11 AND p.is_current=1 AND p.status IN (2,3) ORDER BY w.id""")
        rows=list(cursor.fetchall())
    items=[]
    for row in rows:
        payload=json.loads(row["raw_payload"]) if isinstance(row["raw_payload"],str) else row["raw_payload"]
        items.append({"profileId":row["profileId"],"workId":row["workId"],"workName":row["workName"],"raw":raw_traits(payload),"scores":{},"basis":{trait:{"keywords":[x for x in KEYWORDS.get(trait,[]) if x in json.dumps(payload["semantic"],ensure_ascii=False)][:5],"method":"CPR维度、内容语义、媒介特征与跨作品校准"} for trait in TRAITS}})
    calibrate(items)
    for item in items:item.pop("raw")
    data={"schema":SCHEMA,"projectionModel":"work-affinity-calibrated","modelVersion":"v2","items":items};output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return {"output":str(output.resolve()),"items":len(items)}

def validate(data:dict[str,Any])->list[str]:
    errors=[]
    if data.get("schema")!=SCHEMA:errors.append("schema 非法")
    seen=set()
    for i,item in enumerate(data.get("items",[])):
        if item.get("profileId") in seen:errors.append(f"items[{i}] profileId 重复")
        seen.add(item.get("profileId")); scores=item.get("scores",{})
        if set(scores)!=set(TRAITS):errors.append(f"items[{i}] trait 集合不完整")
        for trait,value in scores.items():
            if not isinstance(value,(int,float)) or not 0<=value<=1:errors.append(f"items[{i}].{trait} 越界")
    return errors

def apply(config:MySQLConfig,path:Path)->dict[str,int]:
    data=json.loads(path.read_text(encoding="utf-8-sig"));errors=validate(data)
    if errors:raise ValueError("\n".join(errors))
    with transaction(config) as db:
        with db.cursor() as cursor:
            ids=[x["profileId"] for x in data["items"]]; marks=",".join(["%s"]*len(ids));cursor.execute(f"SELECT id FROM t_crp_profile WHERE is_current=1 AND id IN ({marks})",ids);existing={x["id"] for x in cursor.fetchall()}
            if existing!=set(ids):raise ValueError("输入包含非当前或不存在的 profile")
            cursor.execute(f"DELETE FROM t_crp_projection_value WHERE profile_id IN ({marks}) AND projection_system IN (1,4)",ids)
            count=0
            for item in data["items"]:
                for trait,score in item["scores"].items():
                    system=1 if trait in TRAITS[:8] else 4
                    cursor.execute("INSERT INTO t_crp_projection_value (profile_id,projection_system,trait_code,score,confidence,projection_model,model_version) VALUES (%s,%s,%s,%s,%s,%s,%s)",(item["profileId"],system,trait,score,.82,data["projectionModel"],data["modelVersion"]));count+=1
                cursor.execute("SELECT raw_payload FROM t_crp_profile WHERE id=%s FOR UPDATE",(item["profileId"],))
                row=cursor.fetchone()
                payload=json.loads(row["raw_payload"]) if isinstance(row["raw_payload"],str) else row["raw_payload"]
                payload["personality_affinity"]={trait:item["scores"][trait] for trait in TRAITS}
                cursor.execute("UPDATE t_crp_profile SET raw_payload=%s WHERE id=%s",(json.dumps(payload,ensure_ascii=False),item["profileId"]))
    return {"profiles":len(ids),"projections":count}

def main():
    p=argparse.ArgumentParser();p.add_argument("command",choices=("generate","validate","import"));p.add_argument("--input",type=Path);p.add_argument("--output",type=Path,default=ROOT/"DataSet"/"work_personality_affinity.json");a=p.parse_args()
    if a.command=="generate":result=generate(MySQLConfig.from_env(),a.output)
    else:
        if not a.input:p.error("需要 --input")
        data=json.loads(a.input.read_text(encoding="utf-8-sig"));errors=validate(data)
        if errors:raise ValueError("\n".join(errors))
        result={"valid":True,"items":len(data["items"])} if a.command=="validate" else apply(MySQLConfig.from_env(),a.input)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
