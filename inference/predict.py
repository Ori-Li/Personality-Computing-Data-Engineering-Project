from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from transformers import AutoTokenizer

from models.personality_model import ModernBertRegressor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default="outputs/modernbert-personality/final")
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    model_dir = Path(args.model_dir)
    parent = model_dir.parent
    labels = json.loads((parent / "label_names.json").read_text(encoding="utf-8"))
    run_config = json.loads((parent / "run_config.json").read_text(encoding="utf-8"))
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = ModernBertRegressor(run_config["model_name"], len(labels), run_config["dropout"])
    state = torch.load(model_dir / "pytorch_model.bin", map_location="cpu", weights_only=True) \
        if (model_dir / "pytorch_model.bin").exists() else None
    if state is not None:
        model.load_state_dict(state)
    else:
        from safetensors.torch import load_file
        model.load_state_dict(load_file(model_dir / "model.safetensors"))
    model.eval()
    encoded = tokenizer(args.text, return_tensors="pt", truncation=True, max_length=run_config["max_length"])
    with torch.inference_mode():
        values = model(**encoded).logits[0].clamp(0, 1).tolist()
    print(json.dumps(dict(zip(labels, values)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

