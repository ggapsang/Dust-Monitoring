-- =====================================================================
-- Migration 001: station_request 테이블 추가
-- =====================================================================
-- /docker-entrypoint-initdb.d/ 는 빈 데이터 디렉토리에서만 실행되므로,
-- 기존 dev DB에는 init_db.sql의 신규 블록이 자동 적용되지 않는다.
-- 그런 환경에서는 아래 스크립트를 한 번 실행해 station_request 테이블과
-- 권한을 추가한다.
--
-- 실행 (dev):
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_001_station_request.sql
--
-- Idempotent: IF NOT EXISTS 로 중복 실행 안전.
-- =====================================================================

CREATE TABLE IF NOT EXISTS station_request (
    station_id   UUID         PRIMARY KEY,
    first_seen   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    attempts     INTEGER      NOT NULL DEFAULT 1,
    status       VARCHAR(16)  NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'approved', 'rejected')),
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS idx_station_request_status    ON station_request(status);
CREATE INDEX IF NOT EXISTS idx_station_request_last_seen ON station_request(last_seen DESC);

GRANT INSERT, UPDATE, SELECT          ON station_request TO gw_writer;
GRANT SELECT, INSERT, UPDATE, DELETE  ON station_request TO gw_admin;
-- gw_reader는 SELECT ON ALL TABLES로 자동 커버.
