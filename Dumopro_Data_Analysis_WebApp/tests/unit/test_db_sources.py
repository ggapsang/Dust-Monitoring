"""Unit tests for the source-table parameterization in dumopro_core.db.

These tests verify only the table-name allowlist + default values.  Real
SQL execution against postgres lives elsewhere (integration tests).
"""

from __future__ import annotations

import inspect

import pytest

from dumopro_core import db
from dumopro_core.config import Settings


class TestAllowlistConstants:
    def test_station_sources(self):
        assert db._ALLOWED_STATION_SOURCES == frozenset(
            {"station", "v_loas_stations"}
        )

    def test_sample_sources(self):
        assert db._ALLOWED_SAMPLE_SOURCES == frozenset(
            {"sensor_sample", "v_loas_sensor_sample"}
        )


class TestCheckHelper:
    def test_allowed_passes(self):
        assert db._check("station", db._ALLOWED_STATION_SOURCES, "station") == "station"
        assert (
            db._check("v_loas_stations", db._ALLOWED_STATION_SOURCES, "station")
            == "v_loas_stations"
        )

    def test_disallowed_raises(self):
        with pytest.raises(ValueError, match="Disallowed"):
            db._check("DROP TABLE station;--", db._ALLOWED_STATION_SOURCES, "station")
        with pytest.raises(ValueError, match="Disallowed"):
            db._check("nonexistent_view", db._ALLOWED_SAMPLE_SOURCES, "sample")


class TestSettingsDefaults:
    """Production default = LOAS views (matches SocketDaim's loas mode)."""

    def test_station_source_defaults_to_loas(self):
        s = Settings()
        assert s.station_source == "v_loas_stations"

    def test_sample_source_defaults_to_loas(self):
        s = Settings()
        assert s.sample_source == "v_loas_sensor_sample"

    def test_measurement_type_unchanged(self):
        """measurement_type stays 'dust_concentration' — the LOAS view fills
        that string in for every row, so the existing default still matches."""
        s = Settings()
        assert s.measurement_type == "dust_concentration"


class TestSettingsOverride:
    def test_override_to_standard(self, monkeypatch):
        monkeypatch.setenv("STATION_SOURCE", "station")
        monkeypatch.setenv("SAMPLE_SOURCE", "sensor_sample")
        s = Settings()
        assert s.station_source == "station"
        assert s.sample_source == "sensor_sample"


class TestFunctionSignatures:
    """The four DB-access functions all expose a `source` keyword argument
    so that callers can plumb the configured source through end-to-end."""

    def test_fetch_stations_has_source_kw(self):
        sig = inspect.signature(db.fetch_stations)
        assert "source" in sig.parameters
        assert sig.parameters["source"].default == "v_loas_stations"

    def test_fetch_samples_since_has_source_kw(self):
        sig = inspect.signature(db.fetch_samples_since)
        assert "source" in sig.parameters
        assert sig.parameters["source"].default == "v_loas_sensor_sample"

    def test_fetch_samples_latest_has_source_kw(self):
        sig = inspect.signature(db.fetch_samples_latest)
        assert "source" in sig.parameters
        assert sig.parameters["source"].default == "v_loas_sensor_sample"

    def test_iter_all_samples_has_source_kw(self):
        sig = inspect.signature(db.iter_all_samples)
        assert "source" in sig.parameters
        assert sig.parameters["source"].default == "v_loas_sensor_sample"
