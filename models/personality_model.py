from __future__ import annotations

import torch
from torch import nn
from transformers.modeling_outputs import SequenceClassifierOutput

from models.encoder import load_encoder
from models.heads import RegressionHead


class ModernBertRegressor(nn.Module):
    def __init__(self, model_name: str, num_labels: int, dropout: float = 0.1):
        super().__init__()
        self.encoder = load_encoder(model_name)
        self.config = self.encoder.config
        self.config.num_labels = num_labels
        self.head = RegressionHead(self.config.hidden_size, num_labels, dropout)

    def forward(self, input_ids=None, attention_mask=None, labels=None, **kwargs):
        outputs = self.encoder(
            input_ids=input_ids, attention_mask=attention_mask, **kwargs
        )
        # ModernBERT has no pooler; use the first token representation.
        logits = self.head(outputs.last_hidden_state[:, 0])
        loss = nn.functional.mse_loss(logits, labels) if labels is not None else None
        return SequenceClassifierOutput(
            loss=loss, logits=logits, hidden_states=outputs.hidden_states,
            attentions=outputs.attentions,
        )

