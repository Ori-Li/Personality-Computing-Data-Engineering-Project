from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from database.config import MySQLConfig
from database.mysql_client import transaction


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHARACTERS = ROOT / "DataSet" / "evaluation_character_300.json"
DEFAULT_WORKS = ROOT / "DataSet" / "evaluation_work_300.json"

VALID_GENRES = {1,2,3,4,5,6,7,8,9,10,11,12,14}
VALID_RELATIONS = set(range(1, 36)) | {99}
VALID_NAME_LANGUAGES = {0, 1, 2, 3}
VALID_REAL_SUB_TYPES = {1, 2}
VALID_CREATIVE_ENTITY_TYPES = {1,2,3,4,5,6,7,8,9,10,99}
VALID_GENDERS = {0, 1, 2, 3}
VALID_CHINA_FIELDS = set(range(0, 11))
VALID_WORLD_FIELDS = set(range(0, 9))
VALID_DYNASTIES = set(range(1, 22)) - {8}
SUBCATEGORY_RANGE = {
    1: range(1001,1016), 2: range(2001,2016), 3: range(3001,3015),
    4: range(4001,4018), 5: range(5001,5017), 6: range(6001,6016),
    7: range(7001,7015), 8: range(8001,8009), 9: range(9001,9012),
    10: range(10001,10013),
    11: range(11001,11007), 12: range(12001,12011), 14: range(14001,14009),
}
RELATION_GENRES = {
    2:{2,4,5,8},3:{4,5,8},4:{4,5,7},5:{5},6:{1},7:{1},8:{1},
    9:{1,2},10:{1,2,4,5,7,8},11:{7,8},12:{7},13:{7,8},14:{3},
    15:{2},16:{2,4},17:{9},18:{12},19:{12},20:{14},21:{8},22:{8},23:{11},
    24:{4,5,8},25:{7},26:{7,8},27:{2,4,5},28:{4,5},29:{6,10},30:{8},
    31:{6,10},32:{2,4,5,8},33:{1,2,4,5,8},34:{7,8},35:{4,5,8},
}
COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")
CHARACTER_ID_PATTERN = re.compile(r"^realCharacter_[a-z]{3}_(?:unknown|-?\d{1,4})_\d{3}$")
WORK_ID_PATTERN = re.compile(r"^work_[a-z]{3}_(?:unknown|-?\d{1,4})_\d{3}$")
FORBIDDEN_INTRO_PATTERNS = [
    re.compile(pattern) for pattern in (
        r"该实体", r"本条目", r"数据库", r"建模", r"用于后续", r"便于.*分析",
        r"\d+\s*号创作关系", r"relationType", r"JSON", r"Prompt",
        r"人格(?:判断|标签|结论|类型)",
        r"(?:可用于|用于|支持).*(?:分析|研究|判断)", r"分析价值",
        r"不能替代.*判断", r"后续(?:分析|研究|标注)", r"推荐算法",
    )
]


class DatasetValidationError(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        preview = "；".join(issues[:10])
        if len(issues) > 10:
            preview += f"；另有 {len(issues) - 10} 项"
        super().__init__(preview)


def stable_id(namespace: str, logical_id: str) -> int:
    digest = hashlib.sha256(f"rgmj:{namespace}:{logical_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def load_array(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, list):
        raise ValueError(f"{path} 顶层必须是 JSON 数组")
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validation_issues(characters: list[dict[str, Any]], works: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    character_by_id: dict[str,dict[str,Any]] = {}
    work_by_id: dict[str,dict[str,Any]] = {}
    for index, character in enumerate(characters):
        if not isinstance(character, dict):
            issues.append(f"characters[{index}] 必须是对象")
            continue
        cid = character.get("id")
        if not isinstance(cid, str) or not cid.strip():
            issues.append(f"characters[{index}].id 缺失或不是字符串")
            continue
        cid = cid.strip()
        if CHARACTER_ID_PATTERN.fullmatch(cid) is None: issues.append(f"人物 id 不符合 v1 契约：{cid}")
        if cid in character_by_id:
            issues.append(f"人物 id 重复：{cid}")
            continue
        character_by_id[cid] = character
        if character.get("characterType") != 1: issues.append(f"人物 {cid} 必须是现实创作者")
        subtype = character.get("realSubType")
        if not _is_int(subtype) or subtype not in VALID_REAL_SUB_TYPES: issues.append(f"人物 {cid} realSubType 非法")
        entity_type = character.get("creativeEntityType")
        if not _is_int(entity_type) or entity_type not in VALID_CREATIVE_ENTITY_TYPES: issues.append(f"人物 {cid} creativeEntityType 非法")
        gender = character.get("gender")
        if not _is_int(gender) or gender not in VALID_GENDERS: issues.append(f"人物 {cid} gender 非法")
        if entity_type in VALID_CREATIVE_ENTITY_TYPES - {1} and gender != 0: issues.append(f"创作组织 {cid} gender 必须为 0")
        field=character.get("field"); dynasty=character.get("dynasty")
        valid_fields=VALID_CHINA_FIELDS if subtype==2 else VALID_WORLD_FIELDS
        if not _is_int(field) or field not in valid_fields: issues.append(f"人物 {cid} field 与人物地域枚举不匹配：{field}")
        if dynasty is not None and (not _is_int(dynasty) or dynasty not in VALID_DYNASTIES): issues.append(f"人物 {cid} dynasty 非法：{dynasty}")
        country = character.get("countryCode")
        if country is not None and (not isinstance(country, str) or COUNTRY_CODE_PATTERN.fullmatch(country) is None): issues.append(f"人物 {cid} countryCode 必须是大写三字母代码或 null")
        if subtype==2 and country!="CHN": issues.append(f"中国人物 {cid} countryCode 必须为 CHN")
        if subtype==2 and dynasty is None: issues.append(f"中国人物 {cid} dynasty 必须填写朝代或近现代/现代枚举")
        if subtype==1 and dynasty is not None: issues.append(f"世界人物 {cid} 不应填写中国朝代枚举")
        for key in ("beginCentury", "endCentury"):
            value = character.get(key)
            if value is not None and (not _is_int(value) or value == 0 or value < -5000 or value > 2100): issues.append(f"人物 {cid} {key} 必须是合理的具体公历年份或 null")
        begin=character.get("beginCentury"); finish=character.get("endCentury")
        if _is_int(begin) and _is_int(finish) and finish < begin: issues.append(f"人物 {cid} endCentury 不得早于 beginCentury")
        id_year=cid.split("_")[-2] if CHARACTER_ID_PATTERN.fullmatch(cid) else None
        if _is_int(begin) and id_year not in (None,str(begin)): issues.append(f"人物 {cid} 的 ID 年份必须与 beginCentury={begin} 一致")
        if begin is None and id_year not in (None,"unknown"): issues.append(f"人物 {cid} 出生年份未知时 ID 必须使用 unknown")
        names=character.get("names")
        if not isinstance(names,list) or not names:
            issues.append(f"人物 {cid} 缺少 names")
            names=[]
        seen=set(); standard=set()
        for name_index, name in enumerate(names):
            if not isinstance(name, dict):
                issues.append(f"人物 {cid} names[{name_index}] 必须是对象")
                continue
            language=name.get("language"); raw_text=name.get("name")
            text=raw_text.strip() if isinstance(raw_text, str) else ""
            if not _is_int(language) or language not in VALID_NAME_LANGUAGES or not text:
                issues.append(f"人物 {cid} names[{name_index}] 非法")
                continue
            key=(language,normalized(text))
            if key in seen: issues.append(f"人物 {cid} 名称重复：{text}")
            if language in {0,1,2} and language in standard: issues.append(f"人物 {cid} 同标准语言多名称：language={language}")
            seen.add(key); standard.add(language)
        introduction = character.get("introduction")
        if not isinstance(introduction, str) or not introduction.strip(): issues.append(f"人物 {cid} introduction 缺失")
        elif len(introduction.strip()) < 60: issues.append(f"人物 {cid} introduction 信息密度不足（少于 60 字）")
        elif any(pattern.search(introduction) for pattern in FORBIDDEN_INTRO_PATTERNS): issues.append(f"人物 {cid} introduction 含数据工程或模板化措辞")
        own_work = character.get("ownWork")
        if not isinstance(own_work, list): issues.append(f"人物 {cid} ownWork 必须是数组")
    for index, work in enumerate(works):
        if not isinstance(work, dict):
            issues.append(f"works[{index}] 必须是对象")
            continue
        wid=work.get("workId")
        if not isinstance(wid, str) or not wid.strip():
            issues.append(f"works[{index}].workId 缺失或不是字符串")
            continue
        wid=wid.strip()
        if WORK_ID_PATTERN.fullmatch(wid) is None: issues.append(f"作品 workId 不符合 v1 契约：{wid}")
        if wid in work_by_id:
            issues.append(f"作品 workId 重复：{wid}")
            continue
        work_by_id[wid]=work
        if not isinstance(work.get("workName"), str) or not work["workName"].strip(): issues.append(f"作品 {wid} workName 缺失")
        original_name=work.get("originalName")
        if original_name is not None and (not isinstance(original_name,str) or not original_name.strip()): issues.append(f"作品 {wid} originalName 必须是非空字符串或 null")
        if not isinstance(work.get("introduction"), str) or not work["introduction"].strip(): issues.append(f"作品 {wid} introduction 缺失")
        elif len(work["introduction"].strip()) < 40: issues.append(f"作品 {wid} introduction 信息密度不足（少于 40 字）")
        elif any(pattern.search(work["introduction"]) for pattern in FORBIDDEN_INTRO_PATTERNS): issues.append(f"作品 {wid} introduction 含数据工程或模板化措辞")
        g=work.get("genre"); sub=work.get("subcategory")
        if not _is_int(g) or g not in VALID_GENRES: issues.append(f"作品 {wid} genre 非法：{g}")
        if sub is not None and (not _is_int(sub) or sub not in SUBCATEGORY_RANGE.get(g,[])): issues.append(f"作品 {wid} subcategory 与 genre 不匹配")
        subcategories = work.get("subcategories", [] if sub is None else [sub])
        if not isinstance(subcategories, list):
            issues.append(f"作品 {wid} subcategories 必须是数组")
            subcategories=[]
        seen_subcategories=set()
        for tag in subcategories:
            if not _is_int(tag) or tag not in SUBCATEGORY_RANGE.get(g,[]): issues.append(f"作品 {wid} 多标签子领域 {tag} 与 genre 不匹配")
            if tag in seen_subcategories: issues.append(f"作品 {wid} subcategories 重复：{tag}")
            seen_subcategories.add(tag)
        if sub is not None and sub not in seen_subcategories: issues.append(f"作品 {wid} 主分类 subcategory 必须包含在 subcategories 中")
        year = work.get("year")
        if year is not None and (not _is_int(year) or year == 0): issues.append(f"作品 {wid} year 必须是非零整数或 null")
        country = work.get("countryCode")
        if country is not None and (not isinstance(country, str) or COUNTRY_CODE_PATTERN.fullmatch(country) is None): issues.append(f"作品 {wid} countryCode 必须是大写三字母代码或 null")
        creators=work.get("creators")
        if not isinstance(creators,list) or not creators:
            issues.append(f"作品 {wid} 缺少 creators")
            creators=[]
        seen_rel=set(); seen_orders=set()
        for creator_index, creator in enumerate(creators):
            if not isinstance(creator, dict):
                issues.append(f"作品 {wid} creators[{creator_index}] 必须是对象")
                continue
            raw_cid=creator.get("characterId"); cid=raw_cid.strip() if isinstance(raw_cid, str) else ""
            rel=creator.get("relationType"); order=creator.get("sortOrder",0)
            if cid not in character_by_id: issues.append(f"作品 {wid} 引用不存在人物 {cid!r}")
            if not _is_int(rel) or rel not in VALID_RELATIONS: issues.append(f"作品 {wid} relationType 非法：{rel}")
            elif rel not in {1,99} and g not in RELATION_GENRES.get(rel,set()): issues.append(f"作品 {wid} 的关系 {rel} 与 genre {g} 不匹配")
            if not _is_int(order) or order < 0: issues.append(f"作品 {wid} creators[{creator_index}].sortOrder 必须是非负整数")
            elif order in seen_orders: issues.append(f"作品 {wid} sortOrder 重复：{order}")
            seen_rel.add((cid,rel))
            seen_orders.add(order)
            if list(seen_rel).count((cid,rel)) > 1: issues.append(f"作品 {wid} 存在重复创作者关系")
        if len(seen_rel) != len([x for x in creators if isinstance(x, dict)]): issues.append(f"作品 {wid} 存在重复创作者关系")
        if seen_orders and seen_orders != set(range(len(seen_orders))): issues.append(f"作品 {wid} sortOrder 必须从 0 连续递增")
        primary=work.get("primaryAuthorId")
        if primary is not None and (not isinstance(primary, str) or not any(isinstance(x,dict) and x.get("characterId")==primary and x.get("relationType")==1 for x in creators)):
            issues.append(f"作品 {wid} primaryAuthorId 没有对应作者关系")
    expected=set()
    for cid, character in character_by_id.items():
        for index, item in enumerate(character.get("ownWork", []) if isinstance(character.get("ownWork"),list) else []):
            if not isinstance(item, dict) or not isinstance(item.get("workId"),str) or not _is_int(item.get("relationType")):
                issues.append(f"人物 {cid} ownWork[{index}] 结构非法")
                continue
            key=(cid,item["workId"],item["relationType"])
            if key in expected: issues.append(f"人物 {cid} ownWork 关系重复：{item['workId']}/{item['relationType']}")
            if item["workId"] in work_by_id:
                expected.add(key)
    actual={(str(x.get("characterId")),wid,x.get("relationType")) for wid,w in work_by_id.items() for x in w.get("creators",[]) if isinstance(x,dict)}
    if expected != actual: issues.append(f"人物 ownWork 与作品 creators 不一致：人物侧缺少 {len(actual-expected)}，人物侧多出 {len(expected-actual)}")
    return issues


def validate(characters: list[dict[str, Any]], works: list[dict[str, Any]]) -> tuple[dict[str,dict[str,Any]],dict[str,dict[str,Any]]]:
    issues = validation_issues(characters, works)
    if issues:
        raise DatasetValidationError(issues)
    character_by_id = {str(character["id"]): character for character in characters}
    work_by_id = {str(work["workId"]): work for work in works}
    return character_by_id,work_by_id


def table_columns(cursor: Any,database: str,table: str)->set[str]:
    cursor.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",(database,table))
    return {x["COLUMN_NAME"] for x in cursor.fetchall()}


def require_tables(cursor: Any,database: str)->None:
    required={"t_character_info","t_character_name","t_real_character_attribute","t_character_work","t_work_creator_relation","t_word_countries"}
    cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",(database,))
    missing=required-{x["TABLE_NAME"] for x in cursor.fetchall()}
    if missing: raise RuntimeError(f"数据库缺少必要表：{', '.join(sorted(missing))}")
    required_columns = {
        "t_real_character_attribute": {"creative_entity_type"},
        "t_character_work": {"original_name", "subcategory"},
    }
    for table, expected in required_columns.items():
        missing_columns = expected - table_columns(cursor, database, table)
        if missing_columns:
            raise RuntimeError(f"数据库表 {table} 缺少新契约字段：{', '.join(sorted(missing_columns))}")


def country_ids(cursor: Any)->dict[str,int]:
    cursor.execute("SELECT id,UPPER(code) code FROM t_word_countries")
    return {x["code"]:x["id"] for x in cursor.fetchall()}


def upsert(cursor: Any,table: str,values: dict[str,Any],update: list[str])->None:
    names=list(values); columns=", ".join(f"`{x}`" for x in names); placeholders=", ".join(["%s"]*len(names))
    updates=", ".join(f"`{x}`=VALUES(`{x}`)" for x in update)
    cursor.execute(f"INSERT INTO `{table}` ({columns}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}",[values[x] for x in names])


def check_natural_key_conflicts(cursor: Any, characters: list[dict[str,Any]], works: list[dict[str,Any]], numeric: dict[str,int], countries: dict[str,int])->None:
    """Reject ambiguous matches instead of silently duplicating/merging existing business entities."""
    for c in characters:
        cid=str(c["id"]); expected=numeric[cid]
        for n in c["names"]:
            cursor.execute("SELECT character_info_id,character_name FROM t_character_name WHERE TRIM(character_name)=%s AND language=%s AND deleted=0",(n["name"].strip(),n["language"]))
            foreign={row["character_info_id"] for row in cursor.fetchall() if row["character_info_id"]!=expected}
            if foreign: raise ValueError(f"人物 {cid} 自然键与已有人物冲突：{n['name']} -> {sorted(foreign)}")
    work_cols=table_columns(cursor,cursor.connection.db.decode() if isinstance(cursor.connection.db,bytes) else cursor.connection.db,"t_character_work")
    if "original_name" not in work_cols: return
    for w in works:
        wid=str(w["workId"]); expected=stable_id("work",wid); code=w.get("countryCode"); country=countries.get(code) if code else None
        cursor.execute("SELECT id FROM t_character_work WHERE TRIM(original_name)=%s AND genre=%s AND (year <=> %s) AND (country_id <=> %s) AND deleted=0",((w.get("originalName") or w["workName"]).strip(),w["genre"],w.get("year"),country))
        foreign={row["id"] for row in cursor.fetchall() if row["id"]!=expected}
        if foreign: raise ValueError(f"作品 {wid} 自然键与已有作品冲突：{w['workName']} -> {sorted(foreign)}")


def import_dataset(cursor: Any,config: MySQLConfig,characters: list[dict[str,Any]],works: list[dict[str,Any]])->dict[str,int]:
    require_tables(cursor,config.database); character_by_id,work_by_id=validate(characters,works)
    countries=country_ids(cursor); real_cols=table_columns(cursor,config.database,"t_real_character_attribute"); work_cols=table_columns(cursor,config.database,"t_character_work")
    has_subcategory_relation=bool(table_columns(cursor,config.database,"t_work_subcategory_relation"))
    numeric={cid:stable_id("character",cid) for cid in character_by_id}
    check_natural_key_conflicts(cursor,characters,works,numeric,countries)
    for cid,c in character_by_id.items():
        character_id=numeric[cid]
        upsert(cursor,"t_character_info",{"id":character_id,"character_type":1,"gender":c["gender"],"introduction":c.get("introduction") or None,"creator":config.creator_id,"deleted":0},["character_type","gender","introduction","creator","deleted"])
        for order,name in enumerate(c["names"]):
            upsert(cursor,"t_character_name",{"id":stable_id("character-name",f"{cid}:{order}"),"character_name":name["name"].strip(),"character_info_id":character_id,"top":0,"language":name["language"],"creator":config.creator_id,"deleted":0},["character_name","character_info_id","top","language","creator","deleted"])
        code=c.get("countryCode"); area=countries.get(code) if code else None
        if code and area is None: raise ValueError(f"人物 {cid} 国家代码不存在：{code}")
        attr={"id":stable_id("real-attribute",cid),"character_info_id":character_id,"begin_century":c.get("beginCentury"),"end_century":c.get("endCentury"),"area":area,"field":c.get("field",0),"sub_type":c["realSubType"],"dynasty":c.get("dynasty"),"creator":config.creator_id,"deleted":0}
        if "creative_entity_type" in real_cols: attr["creative_entity_type"]=c["creativeEntityType"]
        upsert(cursor,"t_real_character_attribute",attr,[x for x in attr if x!="id"])
    for w in works:
        wid=str(w["workId"]); work_id=stable_id("work",wid); code=w.get("countryCode"); country=countries.get(code) if code else None
        if code and country is None: raise ValueError(f"作品 {wid} 国家代码不存在：{code}")
        primary=w.get("primaryAuthorId")
        values={"id":work_id,"work_name":w["workName"].strip(),"genre":w["genre"],"year":w.get("year"),"country_id":country,"auth_character_id":numeric[str(primary)] if primary else None,"introduction":w.get("introduction") or None,"creator":config.creator_id,"deleted":0}
        if "original_name" in work_cols: values["original_name"]=w.get("originalName") or None
        if "subcategory" in work_cols: values["subcategory"]=w.get("subcategory")
        upsert(cursor,"t_character_work",values,[x for x in values if x!="id"])
        cursor.execute("DELETE FROM t_work_creator_relation WHERE work_id=%s",(work_id,))
        for cr in w["creators"]:
            cid=str(cr["characterId"]); rel=cr["relationType"]
            cursor.execute("INSERT INTO t_work_creator_relation (id,work_id,character_id,relation_type,sort_order,creator) VALUES (%s,%s,%s,%s,%s,%s)",(stable_id("work-creator",f"{wid}:{cid}:{rel}"),work_id,numeric[cid],rel,cr.get("sortOrder",0),config.creator_id))
        if has_subcategory_relation:
            cursor.execute("DELETE FROM t_work_subcategory_relation WHERE work_id=%s",(work_id,))
            tags=w.get("subcategories", [] if w.get("subcategory") is None else [w["subcategory"]])
            for order,tag in enumerate(tags):
                cursor.execute("INSERT INTO t_work_subcategory_relation (id,work_id,subcategory,is_primary,sort_order,creator) VALUES (%s,%s,%s,%s,%s,%s)",(stable_id("work-subcategory",f"{wid}:{tag}"),work_id,tag,tag==w.get("subcategory"),order,config.creator_id))
    external_relations=0
    for cid,character in character_by_id.items():
        for own in character.get("ownWork",[]):
            wid=str(own["workId"])
            if wid in work_by_id: continue
            work_id=stable_id("work",wid);rel=own["relationType"]
            cursor.execute("SELECT genre FROM t_character_work WHERE id=%s AND deleted=0",(work_id,));existing=cursor.fetchone()
            if not existing: raise ValueError(f"人物 {cid} ownWork 指向不存在的历史作品：{wid}")
            if rel not in (1,99) and existing["genre"] not in RELATION_GENRES.get(rel,set()): raise ValueError(f"人物 {cid} 的跨批次关系 {rel} 与历史作品 {wid} 体裁不匹配")
            cursor.execute("SELECT COALESCE(MAX(sort_order),-1)+1 next_order FROM t_work_creator_relation WHERE work_id=%s",(work_id,));order=cursor.fetchone()["next_order"]
            upsert(cursor,"t_work_creator_relation",{"id":stable_id("work-creator",f"{wid}:{cid}:{rel}"),"work_id":work_id,"character_id":numeric[cid],"relation_type":rel,"sort_order":order,"creator":config.creator_id},["work_id","character_id","relation_type","sort_order","creator"])
            external_relations+=1
    return {"characters":len(characters),"works":len(works),"relations":sum(len(w["creators"]) for w in works)+external_relations,"crossBatchRelations":external_relations,"subcategoryTags":sum(len(w.get("subcategories", [] if w.get("subcategory") is None else [w["subcategory"]])) for w in works)}


def main()->None:
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    p=argparse.ArgumentParser(); p.add_argument("--characters",type=Path,default=DEFAULT_CHARACTERS); p.add_argument("--works",type=Path,default=DEFAULT_WORKS); p.add_argument("--validate-only",action="store_true"); a=p.parse_args()
    cs=load_array(a.characters); ws=load_array(a.works); validate(cs,ws)
    if a.validate_only: print(json.dumps({"characters":len(cs),"works":len(ws),"relations":sum(len(w["creators"]) for w in ws),"valid":True},ensure_ascii=False)); return
    cfg=MySQLConfig.from_env()
    with transaction(cfg) as db:
        with db.cursor() as cur: result=import_dataset(cur,cfg,cs,ws)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
