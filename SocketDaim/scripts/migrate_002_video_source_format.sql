-- =====================================================================
-- Migration 002: video.source_format 컬럼 추가
-- =====================================================================
-- 기존 dev DB에는 init_db.sql이 자동 재실행되지 않으므로, video 테이블에
-- `source_format` 컬럼을 수동으로 추가한다.  MockImages가 헤더에 보내는
-- source_format(mp4/jpeg/jpeg_seq/raw)을 video 테이블에 저장하기 위함.
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_002_video_source_format.sql
--
-- Idempotent: ADD COLUMN IF NOT EXISTS 로 중복 실행 안전.
-- =====================================================================

ALTER TABLE video
    ADD COLUMN IF NOT EXISTS source_format VARCHAR(16);
