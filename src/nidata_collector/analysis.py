from __future__ import annotations

import numpy as np


def compute_channel_stats(data: np.ndarray, channels: list[str]) -> list[dict]:
    stats = []
    for index, channel in enumerate(channels):
        values = data[index]
        stats.append(
            {
                "channel": channel,
                "mean_g": float(np.mean(values)),
                "rms_g": float(np.sqrt(np.mean(np.square(values)))),
                "min_g": float(np.min(values)),
                "max_g": float(np.max(values)),
                "peak_to_peak_g": float(np.ptp(values)),
            }
        )
    return stats
