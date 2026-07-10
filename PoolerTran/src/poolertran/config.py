"""PoolerTran settings (environment-driven).  PoolerTran_설계.md §10."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class PTSettings(BaseSettings):
    """Settings loaded from ``PT_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- gateway_db (SocketDaim 공용 저장소, cctv_forwarder 롤) --------------
    gw_db_host: str = "postgres"          # gw-net 내부 sd-postgres 호스트명
    gw_db_port: int = 5432
    gw_db_name: str = "gateway_db"
    gw_db_user: str = "cctv_forwarder"
    gw_db_password: str = "dev_forwarder_pw"
    gw_db_pool_min: int = 1
    gw_db_pool_max: int = 5

    # -- decision_db (결과 적재: 배치 REST 결과 → decision_record) -----------
    # decision_agent 의 decision_db.  detector 롤(테이블 INSERT 권한 보유) 재사용.
    # 배치 REST 결과(정적/동적 2쌍) → 3채널 분류 → decision_record 1행 INSERT.
    # 포이즌 메시지(재시도 초과)는 decision_db.transfer_dlq 로 격리한다.
    decision_db_host: str = "postgres-decision"
    decision_db_port: int = 5432
    decision_db_name: str = "decision_db"
    decision_db_user: str = "sensor_analysis_role"
    decision_db_password: str = "dev_sensor_pw"
    decision_db_pool_min: int = 1
    decision_db_pool_max: int = 5

    # -- REST 전송 대상 ----------------------------------------------------
    rest_url: str = "http://localhost:8000/ingest"
    rest_timeout_sec: float = 10.0
    # 디버깅: true 면 REST 호출의 실제 입력(payload)·출력(response)을 로그로 남긴다.
    # 실제/데모 클라이언트 모두 적용.  env: PT_API_LOGGING
    api_logging: bool = False
    # 전송 payload 의 file_path 경로 변환 — 외부 수신 서버(AnalysisReceiver)가
    # 접근 가능한 호스트 실제 경로로 바꾼다.  컨테이너 경로 prefix(_from)를
    # 호스트 경로(_to)로 치환.  _to 가 빈 문자열이면 변환 안 함(원본 그대로 = 하위호환).
    #   예) /data/storage/cctv/x.jpg → /home/user1/svc/SocketDaim/storage/cctv/x.jpg
    # ※ PoolerTran 이 이미지를 읽는 경로(_encode_b64)에는 적용되지 않는다
    #    (그건 자기 컨테이너 마운트 /data/storage 기준이어야 하므로).
    # env: PT_PATH_REMAP_FROM / PT_PATH_REMAP_TO
    path_remap_from: str = "/data/storage"
    path_remap_to: str = ""
    # 전송 contract 선택 (rest_client.REGISTRY 키):
    #   "batch_paths" → waypoint 단위 배치 1콜 → decision_record 생산 (현재 단독 지원)
    rest_mode: str = "batch_paths"

    # -- 데모(더미) 응답 모드 ----------------------------------------------
    # 실제 REST API 가 아직 구현되지 않았을 때 사용.  True 면 HTTP 호출을 하지 않고
    # 합의된 배치 출력 형식([{score,path1,path2}(정적), {score,path1,path2}(동적)])의
    # 더미 응답을 반환한다.  실제 API 가동 시 PT_REST_DEMO=false 로만 바꾸면 된다.
    rest_demo: bool = False
    rest_demo_score: float = 0.5           # 더미 응답 score 값 (demo_version=1 에서 정적/동적 공통)
    # 데모 버전: 1=기존(정적/동적 모두 rest_demo_score 고정),
    #           2=waypoint 별 (정적,동적) 프로필(시나리오 검증용).  env: PT_REST_DEMO_VERSION
    rest_demo_version: int = 1
    # 더미 응답 image_path.  비우면 입력 프레임의 file_path 를 그대로 echo 한다.
    rest_demo_image_path: str = ""

    # -- 폴링 / 배치 / 재시도 ----------------------------------------------
    poll_interval_sec: float = 5.0
    batch_size: int = 100
    max_attempts: int = 10
    use_listen: bool = False              # LISTEN/NOTIFY 저지연 모드(설계 §8.5)
    # waypoint 전환 감지의 last_waypoint_id 초기값(sentinel).
    # 실제로 쓰이지 않는 waypoint_id 여야 첫 실제 waypoint 가 항상 "신규"로 인식된다.
    # 예: -1 또는 9999 (docs/waypoint_transition_batch.md §3).
    init_waypoint_id: int = -1

    # -- 큐 정리 (cctv_transfer_queue 최소 크기 유지) -----------------------
    # 기동 시 큐 전체 삭제(재시작하면 이전 waypoint 작업은 버리고 현재/미래만 처리).
    # ⚠️ 단일 인스턴스 전제(다중 인스턴스면 동료 작업까지 삭제됨).
    clear_queue_on_start: bool = True
    # 오래된(=정상 처리 시점을 한참 넘긴) 큐 행 정리 임계값(초).  0 이하면 비활성.
    # ⚠️ "AMR 이 한 waypoint 에 머무는 최대 시간"보다 충분히 커야 한다(안전하게는
    #   ≥ 1 순회 시간 + 여유).  너무 작으면 처리 전 현재-waypoint 프레임이 삭제됨.
    #   기본 21600(6시간) = 최대 순회(~4시간) + 여유. (docs/waypoint_transition_batch.md)
    queue_max_age_sec: int = 21600

    # -- Health HTTP -------------------------------------------------------
    health_host: str = "0.0.0.0"
    health_port: int = 9109

    # -- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "json"

    @property
    def gw_dsn(self) -> str:
        return (
            f"postgresql://{self.gw_db_user}:{self.gw_db_password}"
            f"@{self.gw_db_host}:{self.gw_db_port}/{self.gw_db_name}"
        )

    @property
    def decision_dsn(self) -> str:
        return (
            f"postgresql://{self.decision_db_user}:{self.decision_db_password}"
            f"@{self.decision_db_host}:{self.decision_db_port}/{self.decision_db_name}"
        )
