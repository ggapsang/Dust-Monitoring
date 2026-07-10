"""Ingestion Gateway settings (environment-driven)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    """Settings loaded from ``IGW_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="IGW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- TCP server --------------------------------------------------------
    tcp_host: str = "0.0.0.0"
    tcp_port: int = 9000             # only used in protocol="standard"

    # -- Protocol ----------------------------------------------------------
    # Default = "loas" (the production target).  Flip to "standard" only
    # for the legacy station-name based flow used by MockSensor / Dumopro.
    protocol: str = "loas"           # "standard" | "loas"

    # -- LOAS Tfoi v4a (single-AMR mode) ----------------------------------
    # When protocol="loas", tcp_port (9000) is ignored.  Instead the
    # Gateway opens TWO listeners — DUST and CCTV — on the ports below.
    # The two streams are independent; pairing happens later in the
    # background Correlator task.
    # DUST port = 13310 → 사용자 보유 Dumopro 분진 mock 의 송신 대상 포트
    # CCTV port = 13320  → MockImages 의 송신 대상 포트
    loas_dust_port: int = 13310
    loas_cctv_port: int = 13320
    loas_amr_id: str = "amr-01"            # stamped onto every cctv_frame row
    loas_cctv_subdir: str = "cctv"         # storage_root subfolder for JPEGs
    loas_expected_amr_ip: str | None = None  # advisory only in single-AMR mode

    # Pairing window: a frame whose received_at falls in
    # [dust.received_at - before, dust.received_at + after] joins that dust event.
    loas_window_before_sec: float = 2.0
    loas_window_after_sec: float = 2.0

    # Background correlator
    loas_correlator_interval_sec: float = 10.0   # tick cadence
    loas_lookback_sec: float = 600.0             # how far back to scan unpaired rows

    # -- DUST raw XML dump (audit / debugging) -----------------------------
    # raw_xml is always persisted to the dust_inspection table.  Enabling
    # the file dump *additionally* writes each accepted XML body to disk
    # so it can be inspected with off-the-shelf tools without touching DB.
    # Throttled by interval to keep disk usage bounded (spec is 1 fps).
    loas_dust_dump_enabled: bool = False
    # Minimum seconds between dumped files (per-process; 0 = dump every frame).
    loas_dust_dump_interval_sec: float = 1.0
    # Subfolder under storage_root.  Full path:
    #   {storage_root}/{dust_dump_subdir}/{YYYY-MM-DD}/{HH}/{epoch_us}_wp{id}.xml
    loas_dust_dump_subdir: str = "dust_dump"

    # -- PostgreSQL (gw_writer role) ---------------------------------------
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "gateway_db"
    db_user: str = "gw_writer"
    db_password: str = "dev_writer_pw"
    db_pool_min: int = 2
    db_pool_max: int = 10

    # -- File storage ------------------------------------------------------
    storage_root: str = "/data/storage"

    # -- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "json"       # "json" | "console"

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
