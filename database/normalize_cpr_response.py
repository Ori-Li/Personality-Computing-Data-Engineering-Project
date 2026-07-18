from __future__ import annotations

import argparse
import json
from pathlib import Path


def normalize(source: Path, output: Path) -> dict[str, object]:
    text=source.read_text(encoding="utf-8-sig").rstrip()
    repaired=False
    try:
        data=json.loads(text)
    except json.JSONDecodeError:
        if text.startswith("[") and text.endswith("},"):
            text=text[:-1]+"]"
            data=json.loads(text)
            repaired=True
        else:
            raise
    output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    responses=data if isinstance(data,list) else [data]
    items=[item for response in responses for item in response.get("items",[])]
    return {"output":str(output.resolve()),"syntaxRepaired":repaired,"responses":len(responses),"items":len(items),"processingStates":[x.get("processingState") for x in responses]}


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument("--input",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    print(json.dumps(normalize(args.input,args.output),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
