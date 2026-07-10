from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class StationInfo(BaseModel):
    station_id: str
    station_name: str
    status: str | None = None
    location_info: str | None = None


class SampleRow(BaseModel):
    id: int
    station_id: str
    measurement_type: str
    value: float
    unit: str
    sampled_at: datetime


class CandleStats(BaseModel):
    q1: float
    q3: float
    median: float
    iqr: float
    upper_fence: float
    lower_fence: float
    extreme_upper: float
    extreme_lower: float
    whisker_high: float
    whisker_low: float
    outliers: list[float] = Field(default_factory=list)
    extremes: list[float] = Field(default_factory=list)
    count: int
    updated_at: datetime | None = None


Unit = Literal["day", "week", "month"]
