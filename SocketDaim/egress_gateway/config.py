"""Egress Gateway settings (environment-driven)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class EgressSettings(BaseSettings):
    """Settings loaded from ``EGW_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="EGW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- 타깃: LOAS 측 MariaDB (t_inspection, INSERT only) ------------------
    # LOAS 권한이 Insert+Select 뿐(Update 없음)이므로 upsert 불가 → 순수 INSERT.
    # 실제 접속값(IP/DB/User/Pw)은 배포 시 env/시크릿으로 주입(레포에 평문 저장 금지).
    #   spec: tfoi_web_db_v1.t_inspection @ 10.5.21.141:3306 (Allowed Client IP 10.5.20.160)
    target_db_host: str = "loas-mariadb"          # ← 실제 IP 로 주입
    target_db_port: int = 3306
    target_db_name: str = "tfoi_web_db_v1"
    target_db_table: str = "t_inspection"
    target_db_user: str = "loas_writer"           # ← 실제 계정으로 주입
    target_db_password: str = "CHANGE_ME"         # ← 시크릿 주입
    target_db_pool_min: int = 1
    target_db_pool_max: int = 5

    # -- Decision DB (egress_role) — 판정 소스 -----------------------------
    db_host: str = "postgres-decision"
    db_port: int = 5432
    db_name: str = "decision_db"
    db_user: str = "egress_role"
    db_password: str = "dev_egress_pw"
    db_pool_min: int = 2
    db_pool_max: int = 10

    # -- Gateway DB (gw_reader) — dust_inspection 24컬럼 읽기 --------------
    # decision_record.dust_id → dust_inspection.id 조인용(cross-DB, 별도 인스턴스).
    gw_db_host: str = "postgres"                  # SocketDaim sd-postgres (gw-net)
    gw_db_port: int = 5432
    gw_db_name: str = "gateway_db"
    gw_db_user: str = "gw_reader"
    gw_db_password: str = "dev_reader_pw"
    gw_db_pool_min: int = 1
    gw_db_pool_max: int = 5

    # -- Polling -----------------------------------------------------------
    poll_interval_sec: float = 5.0
    batch_size: int = 100

    # -- Outbox ------------------------------------------------------------
    outbox_path: str = "/data/outbox.db"

    # -- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "json"

    # -- 디버깅: 최종 INSERT 전체 구문 로깅 (env: EGW_SQL_LOG_ENABLE) --------
    # 기본값 true → t_inspection 으로 실제 실행되는 INSERT 문(값 치환 후 전체 구문)을
    # INFO 로그(`egress_insert_sql`)로 남긴다.  컬럼/값 불일치·FK 디버깅용.
    # image_data(base64)는 가독성을 위해 앞부분만 잘라 표기한다.
    # 로그를 줄이려면 EGW_SQL_LOG_ENABLE=false 로 끈다.
    sql_log_enable: bool = True

    # -- 조건부 INSERT: 허용 event_id 만 LOAS 에 적재 (env: EGW_EVENT_ID_FILTER) -----
    # final_decision → event_id : normal=0, caution=1, warning=2, danger=3.
    # 이 목록의 event_id 만 t_inspection 에 INSERT 한다.  목록 밖이면 적재하지 않고
    # 처리 완료(sent_at)로 표시해 재시도를 막는다.  쉼표 구분.
    # 기본 "0,1,2,3" = 전부 적재(기존 동작).  예) "2,3" → warning/danger 만, "2" → warning 만.
    event_id_filter: str = "0,1,2,3"

    @property
    def allowed_event_ids(self) -> set[int]:
        """EGW_EVENT_ID_FILTER 파싱.  비었거나 전부 오류면 전체(0~3)로 폴백
        (실수로 전수 차단되는 것을 방지)."""
        out: set[int] = set()
        for tok in self.event_id_filter.split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                out.add(int(tok))
            except ValueError:
                pass
        return out or {0, 1, 2, 3}

    @property
    def dsn(self) -> str:
        """decision_db DSN (판정 소스)."""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def gw_dsn(self) -> str:
        """gateway_db DSN (dust_inspection 읽기)."""
        return (
            f"postgresql://{self.gw_db_user}:{self.gw_db_password}"
            f"@{self.gw_db_host}:{self.gw_db_port}/{self.gw_db_name}"
        )
