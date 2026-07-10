import numpy as np
import pytest

from dumopro_core.candles import compute_box_stats


def test_basic_box_stats():
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    s = compute_box_stats(values)
    assert s.q1 == pytest.approx(3.25)
    assert s.median == pytest.approx(5.5)
    assert s.q3 == pytest.approx(7.75)
    assert s.iqr == pytest.approx(4.5)
    assert s.upper_fence == pytest.approx(14.5)
    assert s.lower_fence == pytest.approx(-3.5)
    assert s.whisker_high == pytest.approx(10.0)
    assert s.whisker_low == pytest.approx(1.0)
    assert s.outliers == []
    assert s.extremes == []
    assert s.count == 10


def test_outliers_and_extremes():
    values = np.array([0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.50, 5.00])
    s = compute_box_stats(values)
    assert s.count == 8
    assert 0.50 in s.outliers or 0.50 in s.extremes
    assert 5.00 in s.extremes
    assert s.whisker_high <= s.upper_fence


def test_all_equal_values():
    values = np.array([0.1] * 20)
    s = compute_box_stats(values)
    assert s.iqr == pytest.approx(0.0)
    assert s.median == pytest.approx(0.1)
    assert s.whisker_high == pytest.approx(0.1)
    assert s.whisker_low == pytest.approx(0.1)
    assert s.outliers == []
    assert s.extremes == []


def test_small_sample():
    s = compute_box_stats(np.array([0.2]))
    assert s.count == 1
    assert s.median == pytest.approx(0.2)
    assert s.whisker_high == pytest.approx(0.2)


def test_nan_filtered():
    s = compute_box_stats(np.array([1.0, 2.0, np.nan, 3.0]))
    assert s.count == 3


def test_empty_raises():
    with pytest.raises(ValueError):
        compute_box_stats(np.array([]))
