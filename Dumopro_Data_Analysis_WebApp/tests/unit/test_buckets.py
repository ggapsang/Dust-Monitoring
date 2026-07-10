from datetime import datetime, timezone

import pytest

from dumopro_core.buckets import (
    all_bucket_keys,
    bucket_key_day,
    bucket_key_month,
    bucket_key_week,
    bucket_score,
    is_boundary_crossed,
)


def dt(y, m, d, h=0, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def test_day_key():
    assert bucket_key_day(dt(2026, 4, 19)) == "2026-04-19"


def test_week_key_iso():
    # 2026-01-01 (Thu) belongs to ISO week 2026-W01
    assert bucket_key_week(dt(2026, 1, 1)) == "2026-W01"
    # 2025-12-29 (Mon) belongs to ISO week 2026-W01
    assert bucket_key_week(dt(2025, 12, 29)) == "2026-W01"
    # 2020 had 53 ISO weeks; check week 53
    assert bucket_key_week(dt(2020, 12, 31)) == "2020-W53"


def test_month_key():
    assert bucket_key_month(dt(2026, 4, 19)) == "2026-04"
    assert bucket_key_month(dt(2026, 12, 31, 23, 59, 59)) == "2026-12"


def test_all_bucket_keys():
    keys = all_bucket_keys(dt(2026, 4, 19))
    assert keys == {"day": "2026-04-19", "week": "2026-W16", "month": "2026-04"}


def test_boundary_crossing():
    a = dt(2026, 4, 19, 23, 59, 59)
    b = dt(2026, 4, 20, 0, 0, 1)
    assert is_boundary_crossed(a, b, "day") is True
    assert is_boundary_crossed(a, b, "month") is False
    c = dt(2026, 5, 1, 0, 0, 1)
    assert is_boundary_crossed(a, c, "month") is True


def test_bucket_score_monotonic():
    s1 = bucket_score(dt(2026, 4, 19), "day")
    s2 = bucket_score(dt(2026, 4, 20), "day")
    assert s2 > s1
    sw1 = bucket_score(dt(2026, 4, 19), "week")
    sw2 = bucket_score(dt(2026, 4, 26), "week")
    assert sw2 > sw1


def test_naive_datetime_treated_as_utc():
    naive = datetime(2026, 4, 19, 12, 0, 0)
    assert bucket_key_day(naive) == "2026-04-19"
