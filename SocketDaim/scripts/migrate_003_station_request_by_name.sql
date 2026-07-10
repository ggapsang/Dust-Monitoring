-- =====================================================================
-- Migration 003: station_request 테이블의 키를 station_id(UUID) → station_name으로 변경
-- =====================================================================
-- 와이어 프로토콜이 station_name을 식별자로 사용하도록 바뀌었으므로
-- 트리아지 큐의 PK도 동일하게 station_name으로 정렬한다.
--
-- 기존 station_request 행 데이터(UUID 기반)는 의미가 사라지므로 폐기한다
-- (아직 admin이 결정 안 한 pending UUID는 더 이상 wire와 매칭되지 않음).
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_003_station_request_by_name.sql
-- =====================================================================

DROP TABLE IF EXISTS station_request;

CREATE TABLE station_request (
    station_name VARCHAR(255) PRIMARY KEY,
    first_seen   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    attempts     INTEGER      NOT NULL DEFAULT 1,
    status       VARCHAR(16)  NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'approved', 'rejected')),
    notes        TEXT
);

CREATE INDEX idx_station_request_status    ON station_request(status);
CREATE INDEX idx_station_request_last_seen ON station_request(last_seen DESC);

GRANT INSERT, UPDATE, SELECT          ON station_request TO gw_writer;
GRANT SELECT, INSERT, UPDATE, DELETE  ON station_request TO gw_admin;
