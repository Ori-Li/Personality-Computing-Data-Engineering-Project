from __future__ import annotations

import argparse
import json
from pathlib import Path


def main()->None:
    parser=argparse.ArgumentParser();parser.add_argument("--input",type=Path,required=True);parser.add_argument("--mapping",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    data=json.loads(args.input.read_text(encoding="utf-8-sig"));mapping=json.loads(args.mapping.read_text(encoding="utf-8-sig"))["workIds"]
    responses=data if isinstance(data,list) else [data];changed=0;unknown=[]
    for response in responses:
        state=response.get("processingState",{})
        for key in ("currentWorkId","nextWorkId","currentContentId","nextContentId"):
            if state.get(key) in mapping: state[key]=mapping[state[key]]
        for item in response.get("items",[]):
            basic=item.get("basic",{});old=basic.get("workId") or basic.get("contentId")
            if old not in mapping: unknown.append(old);continue
            new=mapping[old];basic["workId"]=new
            if "contentId" in basic: basic["contentId"]=new
            changed+=1
    if unknown: raise ValueError(f"存在 {len(unknown)} 个未映射 workId：{unknown[:10]}")
    args.output.write_text(json.dumps(responses,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"items":changed,"output":str(args.output.resolve())},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
