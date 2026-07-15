from __future__ import annotations

import numpy as np


def regression_metrics(eval_prediction):
    predictions, labels = eval_prediction
    predictions = np.asarray(predictions)
    labels = np.asarray(labels)
    error = predictions - labels
    return {
        "mse": float(np.mean(error ** 2)),
        "mae": float(np.mean(np.abs(error))),
    }

