-- =====================================================================
-- Migration 004: gw_cleaner 롤 추가 (retention 자동 실행용)
-- =====================================================================
-- /docker-entrypoint-initdb.d/ 는 빈 데이터 디렉토리에서만 실행되므로
-- 기존 dev DB에는 init_db.sql의 새 롤 정의가 자동 적용되지 않는다.
-- 그런 환경에서는 아래 스크립트를 한 번 실행해 gw_cleaner를 추가한다.
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_004_gw_cleaner_role.sql
--
-- Idempotent: 중복 실행 안전.
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gw_cleaner') THEN
        CREATE ROLE gw_cleaner LOGIN PASSWORD 'dev_cleaner_pw';
    END IF;
END $$;

GRANT CONNECT ON DATABASE gateway_db TO gw_cleaner;
GRANT USAGE   ON SCHEMA public        TO gw_cleaner;
GRANT SELECT, DELETE ON video, sensor_sample, ingestion_log TO gw_cleaner;
