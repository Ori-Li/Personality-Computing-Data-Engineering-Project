from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import Dataset

from DataSetsCode.preprocess import build_text, flatten_numeric, get_nested


def _items(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        raise ValueError(f"Dataset is missing or empty: {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    records = payload.get("items", payload) if isinstance(payload, dict) else payload
    if not isinstance(records, list) or not records:
        raise ValueError(f"Expected a non-empty 'items' list in {source}")
    return records


def _name(record: dict[str, Any]) -> str:
    return str(record.get("workName") or record.get("basic", {}).get("name") or "").strip()


def load_examples(
    input_path: str | Path, annotation_path: str | Path, target_path: str
) -> tuple[list[dict[str, Any]], list[str]]:
    inputs = _items(input_path)
    annotations = _items(annotation_path)
    annotation_by_name = {_name(item): item for item in annotations if _name(item)}
    pairs = [(item, annotation_by_name[_name(item)]) for item in inputs if _name(item) in annotation_by_name]
    if not pairs:
        raise ValueError(
            "No input/annotation pairs matched by workName/basic.name. "
            "The prompt output should retain a stable work identifier in a future schema revision."
        )
    if len(pairs) != len(inputs):
        missing = [_name(item) for item in inputs if _name(item) not in annotation_by_name]
        raise ValueError(f"Missing annotations for {len(missing)} inputs: {missing[:5]}")

    flattened = [flatten_numeric(get_nested(annotation, target_path)) for _, annotation in pairs]
    label_names = sorted(flattened[0])
    if not label_names:
        raise ValueError(f"Target '{target_path}' contains no numeric values")
    expected = set(label_names)
    for index, labels in enumerate(flattened):
        if set(labels) != expected:
            raise ValueError(f"Inconsistent target dimensions at item {index}")

    examples = [
        {"text": build_text(raw), "labels": [labels[name] for name in label_names]}
        for (raw, _), labels in zip(pairs, flattened)
    ]
    return examples, label_names


class PersonalityDataset(Dataset):
    def __init__(self, examples: list[dict[str, Any]], tokenizer: Any, max_length: int):
        self.examples = examples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> dict[str, Any]:
        example = self.examples[index]
        encoded = self.tokenizer(
            example["text"], truncation=True, max_length=self.max_length
        )
        encoded["labels"] = torch.tensor(example["labels"], dtype=torch.float32)
        return encoded
