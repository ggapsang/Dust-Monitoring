from __future__ import annotations

from poolertran.config import PTSettings


def test_defaults():
    s = PTSettings()
    assert s.gw_db_name == "gateway_db"
    assert s.gw_db_user == "cctv_forwarder"
    # 결과는 decision_db 의 decision_record 로 적재 (detector 롤 재사용)
    assert s.decision_db_name == "decision_db"
    assert s.decision_db_user == "sensor_analysis_role"
    assert s.batch_size == 100
    assert s.max_attempts == 10
    assert s.use_listen is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("PT_GW_DB_HOST", "sd-postgres")
    monkeypatch.setenv("PT_BATCH_SIZE", "25")
    monkeypatch.setenv("PT_USE_LISTEN", "true")
    s = PTSettings()
    assert s.gw_db_host == "sd-postgres"
    assert s.batch_size == 25
    assert s.use_listen is True


def test_dsn_construction(monkeypatch):
    monkeypatch.setenv("PT_GW_DB_HOST", "h1")
    monkeypatch.setenv("PT_GW_DB_PORT", "5432")
    monkeypatch.setenv("PT_DECISION_DB_HOST", "h2")
    monkeypatch.setenv("PT_DECISION_DB_PORT", "5433")
    monkeypatch.setenv("PT_DECISION_DB_NAME", "decision_db")
    monkeypatch.setenv("PT_DECISION_DB_USER", "sensor_analysis_role")
    monkeypatch.setenv("PT_DECISION_DB_PASSWORD", "dev_sensor_pw")
    s = PTSettings()
    assert s.gw_dsn == "postgresql://cctv_forwarder:dev_forwarder_pw@h1:5432/gateway_db"
    assert s.decision_dsn == "postgresql://sensor_analysis_role:dev_sensor_pw@h2:5433/decision_db"
