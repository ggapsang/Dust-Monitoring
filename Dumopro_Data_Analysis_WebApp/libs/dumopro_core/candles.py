from __future__ import annotations

import numpy as np

from .models import CandleStats


def compute_box_stats(values: np.ndarray) -> CandleStats:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    n = arr.size
    if n == 0:
        raise ValueError("compute_box_stats requires at least one value")

    q1 = float(np.percentile(arr, 25))
    q3 = float(np.percentile(arr, 75))
    median = float(np.percentile(arr, 50))
    iqr = q3 - q1

    upper_fence = q3 + 1.5 * iqr
    lower_fence = q1 - 1.5 * iqr
    extreme_upper = q3 + 3.0 * iqr
    extreme_lower = q1 - 3.0 * iqr

    in_fence = arr[(arr >= lower_fence) & (arr <= upper_fence)]
    whisker_high = float(in_fence.max()) if in_fence.size else float(arr.max())
    whisker_low = float(in_fence.min()) if in_fence.size else float(arr.min())

    outlier_mask = ((arr > upper_fence) & (arr <= extreme_upper)) | (
        (arr < lower_fence) & (arr >= extreme_lower)
    )
    extreme_mask = (arr > extreme_upper) | (arr < extreme_lower)

    return CandleStats(
        q1=q1,
        q3=q3,
        median=median,
        iqr=iqr,
        upper_fence=upper_fence,
        lower_fence=lower_fence,
        extreme_upper=extreme_upper,
        extreme_lower=extreme_lower,
        whisker_high=whisker_high,
        whisker_low=whisker_low,
        outliers=arr[outlier_mask].tolist(),
        extremes=arr[extreme_mask].tolist(),
        count=int(n),
    )
