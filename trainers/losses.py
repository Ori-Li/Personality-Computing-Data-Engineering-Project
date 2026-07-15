import torch


def mean_squared_error(predictions: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.mse_loss(predictions, targets)

