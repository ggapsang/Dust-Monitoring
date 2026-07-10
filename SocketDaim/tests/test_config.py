"""Unit tests for ingestion_gateway.config.IngestionSettings.

Verifies that LOAS-specific env vars are picked up under the IGW_ prefix
and that defaults match what main.py wires through.
"""

from __future__ import annotations

import pytest

from ingestion_gateway.config import IngestionSettings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip any IGW_* env vars so tests see pristine defaults regardless
    of the dev shell environment.  Each constructor call also passes
    ``_env_file=None`` to block .env loading."""
    import os
    for key in list(os.environ.keys()):
        if key.startswith("IGW_"):
            monkeypatch.delenv(key, raising=False)


class TestDefaults:
    def test_protocol_default_loas(self):
        """Production default is loas (the deployment target).  Flip to
        standard explicitly only for the legacy MockSensor / Dumopro flow."""
        s = IngestionSettings(_env_file=None)
        assert s.protocol == "loas"

    def test_loas_port_defaults(self):
        s = IngestionSettings(_env_file=None)
        # DUST = 13310 (사용자 분진 mock 송신 대상), CCTV = 13320 (MockImages)
        assert s.loas_dust_port == 13310
        assert s.loas_cctv_port == 13320

    def test_loas_amr_defaults(self):
        s = IngestionSettings(_env_file=None)
        assert s.loas_amr_id == "amr-01"
        assert s.loas_expected_amr_ip is None
        assert s.loas_cctv_subdir == "cctv"

    def test_loas_window_defaults(self):
        s = IngestionSettings(_env_file=None)
        assert s.loas_window_before_sec == 2.0
        assert s.loas_window_after_sec == 2.0

    def test_loas_correlator_defaults(self):
        s = IngestionSettings(_env_file=None)
        assert s.loas_correlator_interval_sec == 10.0
        assert s.loas_lookback_sec == 600.0


class TestEnvOverride:
    def test_protocol_override_to_standard(self, monkeypatch):
        """Override the loas default to standard for legacy deployments."""
        monkeypatch.setenv("IGW_PROTOCOL", "standard")
        s = IngestionSettings(_env_file=None)
        assert s.protocol == "standard"

    def test_loas_ports_override(self, monkeypatch):
        monkeypatch.setenv("IGW_LOAS_DUST_PORT", "23310")
        monkeypatch.setenv("IGW_LOAS_CCTV_PORT", "23320")
        s = IngestionSettings(_env_file=None)
        assert s.loas_dust_port == 23310
        assert s.loas_cctv_port == 23320

    def test_loas_window_override(self, monkeypatch):
        monkeypatch.setenv("IGW_LOAS_WINDOW_BEFORE_SEC", "1.5")
        monkeypatch.setenv("IGW_LOAS_WINDOW_AFTER_SEC", "3.0")
        s = IngestionSettings(_env_file=None)
        assert s.loas_window_before_sec == 1.5
        assert s.loas_window_after_sec == 3.0

    def test_loas_correlator_override(self, monkeypatch):
        monkeypatch.setenv("IGW_LOAS_CORRELATOR_INTERVAL_SEC", "5.0")
        monkeypatch.setenv("IGW_LOAS_LOOKBACK_SEC", "900")
        s = IngestionSettings(_env_file=None)
        assert s.loas_correlator_interval_sec == 5.0
        assert s.loas_lookback_sec == 900.0

    def test_amr_id_override(self, monkeypatch):
        monkeypatch.setenv("IGW_LOAS_AMR_ID", "amr-loas-bench")
        s = IngestionSettings(_env_file=None)
        assert s.loas_amr_id == "amr-loas-bench"

    def test_expected_amr_ip_override(self, monkeypatch):
        monkeypatch.setenv("IGW_LOAS_EXPECTED_AMR_IP", "192.168.1.10")
        s = IngestionSettings(_env_file=None)
        assert s.loas_expected_amr_ip == "192.168.1.10"
