"""sd-cleaner settings (env-driven, SDC_* prefix)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class CleanerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SDC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- PostgreSQL (gw_cleaner role) -------------------------------------
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "gateway_db"
    db_user: str = "gw_cleaner"
    db_password: str = "dev_cleaner_pw"
    db_pool_min: int = 1
    db_pool_max: int = 2

    # -- Storage (bind-mount root) ----------------------------------------
    storage_root: str = "/data/storage"

    # -- Retention policy (gateway_plan.md §9.3) --------------------------
    video_normal_days: int = 14
    video_anomaly_days: int = 180
    sensor_days: int = 180
    ingestion_log_days: int = 180

    # -- Schedule ---------------------------------------------------------
    run_at_hour_kst: int = 3        # 매일 03:00 KST
    run_at_minute_kst: int = 0
    batch_size: int = 200            # video는 LIMIT N 배치로 처리

    # -- Emergency disk-pressure purge ------------------------------------
    # 일반 retention(나이 기반)으론 못 막는 부하 폭주 시 안전장치.
    # storage_root 사용률이 emergency_purge_at_percent를 넘으면 label/나이
    # 무시하고 captured_at 오래된 순으로 emergency_target_percent까지 삭제.
    emergency_purge_at_percent: int = 85
    emergency_target_percent: int = 70
    # main loop가 매 N초마다 디스크 사용률을 체크 (스케줄/NOTIFY와 별개).
    disk_check_interval_sec: int = 300

    # -- Logging ----------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "json"   # "json" | "console"

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
