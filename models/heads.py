from torch import nn


class RegressionHead(nn.Module):
    def __init__(self, hidden_size: int, output_size: int, dropout: float):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(hidden_size, hidden_size),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, output_size),
        )

    def forward(self, pooled):
        return self.layers(pooled)

