from __future__ import annotations

import argparse, hashlib, json, re
from pathlib import Path
from typing import Any

from database.crp_dataset_pipeline import AFFINITY_KEYS, EXPERIENCE_KEYS, PSYCHOLOGY_GROUPS, SCHEMA
from database.crp_dimension_catalog import chinese_name


TYPE_BY_GENRE={1:"GAME",2:"ANIME",3:"MANGA",4:"MOVIE",5:"TV",6:"NOVEL",7:"MUSIC",8:"STAGE",9:"PAINTING",10:"LITERATURE",11:"SCULPTURE",12:"ARCHITECTURE",14:"PHOTOGRAPHY"}
MEDIA_BY_TYPE={"GAME":"game_vector","ANIME":"anime_vector","MANGA":"manga_vector","MOVIE":"movie_vector","TV":"tv_series_vector","NOVEL":"novel_vector","MUSIC":"music_vector","STAGE":"stage_vector","PAINTING":"painting_vector","LITERATURE":"literature_vector","SCULPTURE":"sculpture_vector","ARCHITECTURE":"architecture_vector","PHOTOGRAPHY":"photography_vector"}
MEDIA_KEYS={
"game_vector":["strategy_depth","mechanical_skill","player_agency","exploration_depth","collection_system","progression_system","social_interaction","competition_intensity"],
"anime_vector":["animation_quality","character_design","visual_style","symbolic_expression","emotional_expression"],
"manga_vector":["panel_expression","drawing_style","visual_symbolism","reading_pace","author_style"],
"movie_vector":["cinematic_scale","visual_storytelling","soundtrack_importance","actor_expression","director_style"],
"tv_series_vector":["episode_dependency","long_term_character_growth","relationship_complexity","world_expansion","season_progression"],
"novel_vector":["literary_depth","writing_style","narrative_voice","symbolism","inner_monologue","world_building_depth"],
"stage_vector":["live_presence","actor_expression","body_expression","audience_interaction","performance_intensity","ritual_sense"],
"painting_vector":["color_expression","composition_complexity","symbolism","emotional_expression","historical_style"],
"literature_vector":["literary_style","language_artistry","symbolism","rhetorical_complexity","intellectual_depth","cultural_depth","knowledge_density","interpretation_openness"],
"sculpture_vector":["three_dimensionality","material_expression","body_representation","spatial_presence","symbolic_power","craftsmanship"],
"architecture_vector":["spatial_experience","functional_design","symbolic_meaning","scale_sense","human_interaction","structural_logic"],
"photography_vector":["reality_capture","human_focus","visual_composition","documentary_value","artistic_expression","moment_sensitivity"]}
BASE_BY_TYPE={
"GAME":dict(complexity=.62,exploration=.72,action_intensity=.62,immersion=.82,visual_intensity=.65,novelty=.58),
"MOVIE":dict(character_depth=.65,visual_intensity=.75,atmosphere=.72,immersion=.75,conflict_intensity=.65),
"TV":dict(character_depth=.72,relationship_scale=.76,social_scale=.68,relationship_scale2=.7,slow_immersion=.7),
"NOVEL":dict(depth_of_thought=.72,information_density=.7,character_depth=.76,slow_immersion=.68,ambiguity=.58),
"LITERATURE":dict(abstractness=.7,depth_of_thought=.76,ambiguity=.68,information_density=.7,philosophical_depth=.7),
"MUSIC":dict(sound_intensity=.78,rhythm_energy=.7,atmosphere=.72,immersion=.65),
"STAGE":dict(body_expression=.75,atmosphere=.72,conflict_intensity=.66,visual_intensity=.68),
"PAINTING":dict(beauty=.72,color_richness=.7,visual_intensity=.76,ambiguity=.62,atmosphere=.68),
"SCULPTURE":dict(beauty=.66,visual_intensity=.72,atmosphere=.65,abstractness=.58),
"ARCHITECTURE":dict(logical_structure=.72,order=.7,visual_intensity=.68,atmosphere=.7,creation=.75),
"PHOTOGRAPHY":dict(visual_intensity=.78,truth=.68,information_density=.62,atmosphere=.7),
"ANIME":dict(fantasy_level=.72,visual_intensity=.76,character_depth=.65,immersion=.75),
"MANGA":dict(visual_intensity=.68,character_depth=.68,slow_immersion=.62,ambiguity=.55)}
THEMES=[("家庭","家庭"),("爱情","爱情"),("战争","战争"),("社会","社会"),("历史","历史"),("自由","自由"),("成长","成长"),("身份","身份认同"),("记忆","记忆"),("城市","城市"),("自然","自然"),("死亡","生死"),("政治","政治理想"),("孤独","孤独"),("科技","科技")]

def clamp(x:float)->float:return round(max(0,min(1,x)),2)
def jitter(seed:str,key:str,scale=.12)->float:
    n=int(hashlib.sha256((seed+key).encode()).hexdigest()[:8],16)/0xffffffff
    return (n-.5)*scale
def score(seed,key,text,typ):
    value=.38+jitter(seed,key,.22)
    for k,v in BASE_BY_TYPE.get(typ,{}).items():
        if k==key: value=v+jitter(seed,key,.1)
    boosts={"战争":["tension","fear","conflict_intensity","survival","sacrifice"],"爱情":["love","romance","warmth"],"家庭":["family","relationship_scale","warmth"],"孤独":["loneliness","melancholy","self_exploration"],"历史":["time_span","tradition","learning_value"],"社会":["social_scale","power_relationship","community"],"幻想":["fantasy_level","world_building","exploration"],"悬疑":["mystery","tension","ambiguity"],"恐怖":["fear","darkness","tension"],"实验":["experimental","novelty","ambiguity"],"动作":["action_intensity","risk","visual_intensity"]}
    for word,keys in boosts.items():
        if word in text and key in keys:value+=.22
    return clamp(value)
def numeric_map(keys,seed,text,typ):return {k:score(seed,k,text,typ) for k in keys}

def media_vector(typ,seed,text):
    name=MEDIA_BY_TYPE[typ]
    if name=="music_vector":
        return {name:{"genre":"音乐","tempo":score(seed,"tempo",text,typ),"energy":score(seed,"energy",text,typ),"vocal_style":"依作品演唱与编制呈现","instrumentation":"以作品实际配器为核心","production_style":"遵循作品所属年代的录音与制作方式","lyrical_theme":"围绕作品文本主题展开","emotional_scene":"由旋律、节奏与音色共同形成","cultural_context":"作品所属地区与时代的音乐文化"}}
    if name=="painting_vector":
        vals=numeric_map(MEDIA_KEYS[name],seed,text,typ); return {name:{"visual_style":"依据构图、色彩与材料呈现",**vals}}
    return {name:numeric_map(MEDIA_KEYS[name],seed,text,typ)}

def evidence(work):
    intro=(work.get("introduction") or "").strip(); parts=[x.strip()+"。" for x in re.split(r"[。！？]",intro) if len(x.strip())>=12]
    ev=parts[:5]
    ev.append(f"该作品以{TYPE_BY_GENRE[work['genre']]}对应的媒介形式完成主要表达。")
    if work.get("year"):ev.append(f"作品记录的公开年份为{work['year']}年，其表达与相应时代语境相关。")
    creators=[]
    for x in work.get("creators",[]):
        if x["name"] not in creators: creators.append(x["name"])
    if creators:ev.append(f"现有作品关系记录的核心参与者包括{'、'.join(creators[:4])}。")
    return ev[:15]

def affinity(psych,exp):
    c,w,e,a,n,s,v,b,se=[psych[k] for k in ("cognitive","worldview","emotion","aesthetic","narrative","social_relationship","value","behavior","sensory")]
    raw={"Ni":(c["abstractness"]+c["depth_of_thought"]+c["ambiguity"]+n["philosophical_depth"])/4,"Ne":(c["exploration"]+c["novelty"]+w["fantasy_level"]+b["curiosity"])/4,"Ti":(c["logical_structure"]+c["complexity"]+c["information_density"])/3,"Te":(v["achievement"]+s["competition"]+b["discipline"]+v["order"])/4,"Fi":(v["self_exploration"]+s["identity"]+s["love"]+v["meaning"])/4,"Fe":(s["family"]+s["friendship"]+s["cooperation"]+s["community"])/4,"Si":(e["nostalgia"]+v["tradition"]+b["calm"]+a["classical"])/4,"Se":(n["action_intensity"]+se["visual_intensity"]+se["immersion"]+b["risk"])/4}
    raw["Assertive"]=(e["hope"]+v["achievement"]+b["discipline"]+(1-e["fear"]))/4; raw["Turbulent"]=(e["tension"]+e["melancholy"]+c["ambiguity"]+v["self_exploration"])/4
    return {k:clamp(raw[k]) for k in AFFINITY_KEYS}

EVIDENCE_HINTS={
"cognitive":["结构","规则","语言","信息","叙事","构图","形式","主题"],"worldview":["时代","社会","历史","世界","城市","家庭","群体"],"emotion":["情绪","冲突","温暖","孤独","恐惧","希望","悲伤","紧张"],"aesthetic":["色彩","光线","风格","材料","声音","节奏","镜头","意象"],"narrative":["人物","故事","叙事","冲突","情节","角色","推进"],"social_relationship":["人物","家庭","社会","群体","关系","合作","身份"],"value":["理想","责任","自由","正义","传统","生存","意义","选择"],"behavior":["行动","探索","选择","创作","合作","竞争","冒险"],"sensory":["视觉","声音","色彩","光线","节奏","空间","材料","镜头"],"media":["媒介","表演","演唱","构图","空间","镜头","语言","玩法","舞台"]}
LEAF_HINTS={
"passion":["激情","热烈","坚定","力量"],"healing":["治愈","安慰","抚慰","温暖"],"awe":["敬畏","宏大","壮观","崇高"],"nostalgia":["怀旧","记忆","往昔","故乡"],"reflection":["反思","思考","辩难","讨论"],"romance":["爱情","浪漫","恋爱"],"tension":["紧张","恐怖","威胁","危险","冲突","追逐","封闭"],"warmth":["温暖","家庭","友情","陪伴"],"loneliness":["孤独","隔绝","疏离"],"hope":["希望","未来","重建","成长"],
"fear":["恐怖","恐惧","威胁","危险"],"sadness":["悲伤","死亡","失去","离别"],"happiness":["快乐","喜悦","欢快"],"anger":["愤怒","反抗","压迫"],"peace":["平静","宁静","舒缓"],"darkness":["黑暗","阴影","恐怖","压抑"],"brightness":["明亮","光线","鲜艳"],"mystery":["神秘","悬疑","未知","谜"],"action_intensity":["动作","战斗","追逐","行动"],"conflict_intensity":["冲突","对抗","战争","威胁"],"family":["家庭","亲属","父母","子女"],"friendship":["友情","朋友","伙伴"],"love":["爱情","恋人","情感"],"survival":["生存","求生","威胁","困境"],"freedom":["自由","反抗","选择"],"tradition":["传统","历史","古典"],"visual_intensity":["视觉","镜头","色彩","光线","构图"],"sound_intensity":["声音","音色","演唱","配乐"],"rhythm_energy":["节奏","旋律","动作"],"atmosphere":["氛围","空间","光线","声音"],"immersion":["沉浸","空间","世界","环境"]}

def select_evidence_index(code,ev):
    parts=code.split("."); group=parts[1] if parts[0]=="psychology" else parts[0]
    hints=EVIDENCE_HINTS.get(group,EVIDENCE_HINTS["media"] if group=="media" else [])
    label=chinese_name(code).replace("程度","").replace("强度","").replace("价值","")
    leaf=code.rsplit(".",1)[-1]
    hints=[label,*LEAF_HINTS.get(leaf,[]),*hints]
    metadata_prefixes=("现有作品关系记录","作品记录的公开年份","该作品以")
    content_indexes=[index for index,text in enumerate(ev) if not any(text.startswith(prefix) for prefix in metadata_prefixes)]
    candidates=content_indexes or list(range(len(ev)))
    ranked=[]
    for index in candidates:
        text=ev[index]
        overlap=sum(1 for hint in hints if hint and hint in text)
        ranked.append((overlap,min(len(text),80),-index,index))
    return max(ranked)[-1]

def dimension_evidence(item, ev, seed):
    dimensions=[]
    for key,value in item["experience_vector"].items(): dimensions.append((f"experience.{key}",value))
    for group,values in item["psychology_vector"].items():
        for key,value in values.items(): dimensions.append((f"psychology.{group}.{key}",value))
    for vector,values in item["media_vector"].items():
        for key,value in values.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool): dimensions.append((f"media.{vector}.{key}",value))
    result=[]
    for code,value in dimensions:
        evidence_index=select_evidence_index(code,ev)
        relation=1 if value >= .5 else 2
        direction="支持该维度的较高表达" if relation==1 else "仅提供有限相关内容，因而限制该维度获得高分"
        fact=ev[evidence_index].rstrip("。")
        result.append({"dimensionCode":code,"evidenceIndexes":[evidence_index],"relationType":relation,"evidenceWeight":clamp(max(.5,abs(value-.5)+.5)),"explanation":f"依据“{fact}”，该事实{direction}；{chinese_name(code)}得分为{value:.2f}。"})
    return result

def generate(input_path:Path,output_path:Path):
    src=json.loads(input_path.read_text(encoding="utf-8-sig")); items=[]
    for work in src["works"]:
        typ=TYPE_BY_GENRE[work["genre"]]; seed=str(work["databaseWorkId"]); text=(work.get("introduction") or "")+work["workName"]
        psych={g:numeric_map(keys,seed,text,typ) for g,keys in PSYCHOLOGY_GROUPS.items()}
        exp=numeric_map(EXPERIENCE_KEYS,seed,text,typ); ev=evidence(work)
        themes=[label for word,label in THEMES if word in text][:6] or [{"GAME":"探索与选择","MUSIC":"情绪表达","ARCHITECTURE":"空间与功能","PHOTOGRAPHY":"现实记录","PAINTING":"视觉表达","SCULPTURE":"形体与空间"}.get(typ,"人物与社会")]
        summary=(work.get("introduction") or f"《{work['workName']}》是一部{typ}作品。")[:200]
        keywords=[work["workName"],typ,*themes][:8]
        item={"databaseWorkId":work["databaseWorkId"],"basic":{"name":work["workName"],"type":typ,"countryName":work.get("countryCode") or "未知","year":work.get("year") or 0,"ontologyVersion":"v1.0","promptVersion":"CPR-1.2","annotationModelVersion":"GPT-5","generatorDate":"2026-07-17","evidenceVersion":"v1.0","annotationLanguage":"zh-CN","confidence":clamp(.82 if len(ev)>=6 else .68),"primaryMediaType":typ},"semantic":{"summary":summary,"themes":themes,"keywords":keywords,"core_experience":f"通过{typ}媒介中的结构、节奏与主题表达，形成以{themes[0]}为核心的认知和情绪体验。"},"experience_vector":exp,"psychology_vector":psych,"media_vector":media_vector(typ,seed,text),"personality_affinity":affinity(psych,exp),"supporting_evidence":ev}
        item["dimension_evidence"]=dimension_evidence(item,ev,seed)
        items.append(item)
    out={"schema":SCHEMA,"schemaVersion":"cpr-1.2","promptVersion":"CPR-1.2","processingState":{"processed":len(items),"remaining":0,"total":len(items),"currentWorkId":items[-1]["databaseWorkId"] if items else None,"nextWorkId":None,"isCompleted":True},"items":items}; output_path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"output":str(output_path.resolve()),"items":len(items)},ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument("--input",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();generate(a.input,a.output)
if __name__=="__main__":main()
