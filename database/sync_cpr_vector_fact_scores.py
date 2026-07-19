from __future__ import annotations

import argparse,json
from pathlib import Path


def dimensions(item):
    result={f"experience_vector.{k}":v for k,v in item["experience_vector"].items()}
    for group,values in item["psychology_vector"].items():
        for key,value in values.items():result[f"psychology_vector.{group}.{key}"]=value
    for vector,values in item["media_vector"].items():
        for key,value in values.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):result[f"media_vector.{vector}.{key}"]=value
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--input",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);args=parser.parse_args();data=json.loads(args.input.read_text(encoding="utf-8-sig"));responses=data if isinstance(data,list) else [data];changes=[]
    for response in responses:
        for item in response.get("items",[]):
            values=dimensions(item)
            for fact in item.get("vector_facts",[]):
                path=fact.get("dimensionPath")
                if path in values and fact.get("score")!=values[path]:
                    changes.append({"workId":item["basic"].get("workId"),"dimensionPath":path,"old":fact.get("score"),"new":values[path]});fact["score"]=values[path]
    args.output.write_text(json.dumps(responses,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps({"changes":changes,"count":len(changes),"output":str(args.output.resolve())},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
