"""Decision Agent settings (environment-driven)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DASettings(BaseSettings):
    """Settings loaded from ``DA_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Decision DB (decision_agent_role) --------------------------------
    db_host: str = "postgres-decision"
    db_port: int = 5432
    db_name: str = "decision_db"
    db_user: str = "decision_agent_role"
    db_password: str = "dev_decision_pw"
    db_pool_min: int = 2
    db_pool_max: int = 10

    # -- Gateway DB (SocketDaim, read-only) --------------------------------
    # Used ONLY by the admin UI to resolve a station's human label (별명)
    # for the `station` column: decision_record.dust_id → gateway_db
    # dust_inspection.target_id → waypoint_label.label.  Best-effort: if this
    # DB is unreachable the station column falls back to 'TGT-?'.
    # Host = SocketDaim postgres container name on the shared gw-net.
    gateway_db_host: str = "sd-postgres"
    gateway_db_port: int = 5432
    gateway_db_name: str = "gateway_db"
    gateway_db_user: str = "gw_reader"
    gateway_db_password: str = "dev_reader_pw"

    # -- Polling -----------------------------------------------------------
    poll_interval_sec: float = 5.0
    batch_size: int = 100

    # -- Cache refresh -----------------------------------------------------
    role_refresh_sec: float = 300.0

    # -- Admin HTTP server -------------------------------------------------
    admin_host: str = "0.0.0.0"
    admin_port: int = 9107
    admin_stuck_after_sec: int = 300

    # -- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "json"

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def gateway_dsn(self) -> str:
        return (
            f"postgresql://{self.gateway_db_user}:{self.gateway_db_password}"
            f"@{self.gateway_db_host}:{self.gateway_db_port}/{self.gateway_db_name}"
        )
