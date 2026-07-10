from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    pg_dsn: str = Field(
        default="postgresql://gw_reader:dev_reader_pw@localhost:2345/gateway_db",
        alias="PG_DSN",
    )
    redis_url: str = Field(default="redis://localhost:6380/0", alias="REDIS_URL")

    poll_interval_sec: float = Field(default=1.5, alias="POLL_INTERVAL_SEC")
    poll_batch_limit: int = Field(default=500, alias="POLL_BATCH_LIMIT")
    station_refresh_sec: float = Field(default=600.0, alias="STATION_REFRESH_SEC")
    restart_wait_sec: float = Field(default=10.0, alias="RESTART_WAIT_SEC")
    consecutive_failure_cap: int = Field(default=5, alias="CONSECUTIVE_FAILURE_CAP")
    grace_period_sec: float = Field(default=30.0, alias="GRACE_PERIOD_SEC")
    measurement_type: str = Field(default="dust_concentration", alias="MEASUREMENT_TYPE")

    # Source tables.  Defaults point at the LOAS compatibility views so the
    # webapp works out-of-the-box against the production SocketDaim deployment
    # (which runs in loas mode by default).  Flip to "station" / "sensor_sample"
    # for the legacy standard-mode MockSensor flow.
    station_source: str = Field(default="v_loas_stations",      alias="STATION_SOURCE")
    sample_source:  str = Field(default="v_loas_sensor_sample", alias="SAMPLE_SOURCE")

    health_port: int = Field(default=9106, alias="POLLER_HEALTH_PORT")
    api_port: int = Field(default=9105, alias="API_PORT")


@lru_cache
def get_settings() -> Settings:
    return Settings()
