"""Admin UI settings (environment-driven)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AdminUISettings(BaseSettings):
    """Settings loaded from ``SDA_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SDA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- HTTP server -------------------------------------------------------
    http_host: str = "0.0.0.0"
    http_port: int = 9108

    # -- PostgreSQL (gw_admin role) ---------------------------------------
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "gateway_db"
    db_user: str = "gw_admin"
    db_password: str = "dev_admin_pw"
    db_pool_min: int = 1
    db_pool_max: int = 5

    # -- Storage usage widget ---------------------------------------------
    storage_root: str = "/data/storage"
    disk_warn_percent: int = 80
    disk_critical_percent: int = 90

    # -- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "json"   # "json" | "console"

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
