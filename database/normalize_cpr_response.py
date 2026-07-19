from __future__ import annotations

import argparse
import json
from pathlib import Path


def normalize(source: Path, output: Path, skip: int = 0) -> dict[str, object]:
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
    responses=data if isinstance(data,list) else [data]
    if skip < 0 or skip > len(responses):
        raise ValueError(f"skip 必须位于 0—{len(responses)}")
    selected=responses[skip:]
    output.write_text(json.dumps(selected,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    selected_items=[item for response in selected for item in response.get("items",[])]
    return {"output":str(output.resolve()),"syntaxRepaired":repaired,"sourceResponses":len(responses),"skippedResponses":skip,"responses":len(selected),"items":len(selected_items),"firstProcessingState":selected[0].get("processingState") if selected else None,"lastProcessingState":selected[-1].get("processingState") if selected else None}


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument("--input",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);parser.add_argument("--skip",type=int,default=0);args=parser.parse_args()
    print(json.dumps(normalize(args.input,args.output,args.skip),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
