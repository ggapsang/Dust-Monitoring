-- =====================================================================
-- Egress 타깃 DB — decision_export (외부 단방향 수신 테이블)
-- =====================================================================
-- 소유: 외부 수신 시스템.  egress_gateway(Python) 를 Apache Hop 파이프라인으로
--   대체할 때, Hop 이 decision_db.decision_record(pending+미전송) 을 읽어 이 표에
--   멱등(upsert)으로 적재한다.
--
-- 핵심 불변식:
--   1) PK = decision_id  → 멱등키.  at-least-once 재전송이 와도 중복 없음.
--   2) Hop 의 Insert/Update 트랜스폼이 decision_id 로 lookup 하여 insert/update.
--   3) 소스의 sent_at 갱신은 "이 표 적재 성공 후"에만 한다(파이프라인 순서/에러처리).
--
-- 아래는 PostgreSQL 방언.  다른 DB 로 바꿀 때 §방언 노트 참조.
-- =====================================================================

CREATE TABLE IF NOT EXISTS decision_export (
    decision_id           UUID         PRIMARY KEY,   -- = decision_record.id (멱등키)
    station_id            VARCHAR(50)  NOT NULL,
    observation_timestamp TIMESTAMPTZ  NOT NULL,
    static_model_result   TEXT,                       -- anomaly_detection_result::text
    dynamic_model_result  TEXT,                       -- object_detection_result::text
    sensor_result         TEXT,                       -- sensor_analysis_result::text
    final_decision        TEXT         NOT NULL,
    decided_at            TIMESTAMPTZ,
    -- 운영 추적용(선택): 원래 TCP 메시지 타입 매핑을 보존하고 싶을 때
    msg_type              VARCHAR(20),                -- 'ALERT' | 'ANALYSIS_RESULT'
    exported_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_decision_export_station_time
    ON decision_export (station_id, observation_timestamp);


-- =====================================================================
-- 방언 노트 (타깃 DB 변경 시 이 부분만 교체)
-- =====================================================================
-- PostgreSQL : UUID / TIMESTAMPTZ / now()                         (위 정의)
-- Oracle     : decision_id RAW(16) 또는 VARCHAR2(36) PK,
--              TIMESTAMP WITH TIME ZONE, exported_at DEFAULT systimestamp
-- SQL Server : decision_id UNIQUEIDENTIFIER PK,
--              DATETIMEOFFSET, exported_at DEFAULT sysdatetimeoffset()
-- MySQL/Maria: decision_id CHAR(36) PK,
--              DATETIME(6), exported_at DEFAULT CURRENT_TIMESTAMP(6)
--
-- upsert 자체는 Hop 의 Insert/Update 트랜스폼이 처리하므로 SQL 작성 불필요.
-- DB 별로 달라지는 것은 (1) 위 타입 방언 (2) JDBC 드라이버 (3) Hop 연결의 DB 종류뿐.
-- =====================================================================
