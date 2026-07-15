from __future__ import annotations

from typing import Any

import torch


class RegressionCollator:
    def __init__(self, tokenizer: Any):
        self.tokenizer = tokenizer

    def __call__(self, features: list[dict[str, Any]]) -> dict[str, torch.Tensor]:
        labels = torch.stack([feature.pop("labels") for feature in features])
        batch = self.tokenizer.pad(features, padding=True, return_tensors="pt")
        batch["labels"] = labels
        return batch

