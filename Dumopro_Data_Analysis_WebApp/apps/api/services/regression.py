from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures

from dumopro_core.buckets import Unit
from dumopro_core.redis_client import RedisClient

log = logging.getLogger(__name__)

Target = Literal["median", "max", "q3"]

TARGET_FIELD = {
    "median": "median",
    "max": "whisker_high",
    "q3": "q3",
}

MIN_CANDLES = 5


@dataclass
class RegressionResult:
    trend: list[float]
    band_upper: list[float]
    band_lower: list[float]
    residuals: list[float]
    rmse: float
    threshold: float
    highlighted_bucket_keys: list[str]
    target: Target
    degree: int
    band_n: float
    percentile: float
    n: int


def _extract_target(stats_list: list[dict], target: Target) -> np.ndarray:
    field = TARGET_FIELD[target]
    return np.array([float(s[field]) for s in stats_list], dtype=np.float64)


async def run_regression(
    redis: RedisClient,
    station: str,
    unit: Unit,
    candles: list[dict],
    target: Target = "median",
    degree: int = 2,
    band_n: float = 2.0,
    percentile: float = 95.0,
) -> RegressionResult:
    """candles: list of {bucket_key, stats{...}} in chronological order (live last)."""
    if len(candles) < MIN_CANDLES:
        raise ValueError(f"at least {MIN_CANDLES} candles required, got {len(candles)}")

    bucket_keys = [c["bucket_key"] for c in candles]
    stats_list = [c["stats"] for c in candles]
    y = _extract_target(stats_list, target)
    x = np.arange(len(y), dtype=np.float64).reshape(-1, 1)

    pipe_x = PolynomialFeatures(degree=degree, include_bias=True).fit_transform(x)
    model = LinearRegression(fit_intercept=False).fit(pipe_x, y)
    y_pred = model.predict(pipe_x)
    residuals = (y - y_pred)
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    band_upper = (y_pred + band_n * rmse).tolist()
    band_lower = (y_pred - band_n * rmse).tolist()

    # Accumulate residuals in Redis and compute threshold from full history
    await redis.residual_push(station, unit, target, residuals.tolist())
    all_res = await redis.residual_all(station, unit, target)
    all_abs = np.abs(np.asarray(all_res, dtype=np.float64))
    threshold = float(np.percentile(all_abs, percentile)) if all_abs.size else 0.0

    highlighted = [
        bucket_keys[i]
        for i in range(len(residuals))
        if abs(residuals[i]) > threshold
    ]

    return RegressionResult(
        trend=y_pred.tolist(),
        band_upper=band_upper,
        band_lower=band_lower,
        residuals=residuals.tolist(),
        rmse=rmse,
        threshold=threshold,
        highlighted_bucket_keys=highlighted,
        target=target,
        degree=degree,
        band_n=band_n,
        percentile=percentile,
        n=len(y),
    )
