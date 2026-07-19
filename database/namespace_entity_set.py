from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ID_PATTERN=re.compile(r"^(.*_)(\d{3})$")


def shifted(value:str,offset:int)->str:
    match=ID_PATTERN.fullmatch(value)
    if not match: raise ValueError(f"无法命名空间化 ID：{value}")
    suffix=int(match.group(2))+offset
    if suffix>999: raise ValueError(f"ID 后缀溢出：{value}+{offset}")
    return f"{match.group(1)}{suffix:03d}"


def main()->None:
    parser=argparse.ArgumentParser();parser.add_argument("--characters",type=Path,required=True);parser.add_argument("--works",type=Path,required=True);parser.add_argument("--output-characters",type=Path,required=True);parser.add_argument("--output-works",type=Path,required=True);parser.add_argument("--mapping",type=Path,required=True);parser.add_argument("--pending",type=Path,required=True);parser.add_argument("--offset",type=int,required=True);args=parser.parse_args()
    characters=json.loads(args.characters.read_text(encoding="utf-8-sig"));works=json.loads(args.works.read_text(encoding="utf-8-sig"))
    character_map={x["id"]:shifted(x["id"],args.offset) for x in characters};work_map={x["workId"]:shifted(x["workId"],args.offset) for x in works};pending=[]
    for character in characters:
        old=character["id"];character["id"]=character_map[old];kept=[]
        for own in character.get("ownWork",[]):
            wid=own["workId"]
            if wid in work_map:
                own["workId"]=work_map[wid];kept.append(own)
            elif wid=="work_chn_1941_003":
                kept.append(own)
            else:
                pending.append({"characterId":character["id"],**own,"reason":"作品未包含在 set2 且不能唯一解析为既有实体"})
        character["ownWork"]=kept
    for work in works:
        old=work["workId"];work["workId"]=work_map[old]
        if work.get("primaryAuthorId") in character_map: work["primaryAuthorId"]=character_map[work["primaryAuthorId"]]
        for creator in work.get("creators",[]): creator["characterId"]=character_map[creator["characterId"]]
    for path,value in ((args.output_characters,characters),(args.output_works,works),(args.mapping,{"characterIds":character_map,"workIds":work_map}),(args.pending,pending)):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"characters":len(characters),"works":len(works),"mappedCharacterIds":len(character_map),"mappedWorkIds":len(work_map),"pendingRelations":len(pending)},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
