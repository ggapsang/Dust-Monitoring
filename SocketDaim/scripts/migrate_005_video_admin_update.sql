-- =====================================================================
-- Migration 005: gw_admin 롤에 video UPDATE 권한 부여 (영상 라벨링 UI)
-- =====================================================================
-- SocketDaim Admin UI의 "영상 라벨링" 탭이 video.is_valid / is_excluded
-- 플래그를 PATCH 할 수 있도록 gw_admin 롤에 UPDATE 권한을 추가한다.
--
-- 기존 dev DB에는 init_db.sql이 자동 재실행되지 않으므로 수동 실행:
--
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_005_video_admin_update.sql
--
-- INSERT/DELETE는 부여하지 않는다 — 라벨링은 in-place flag 변경만 한다.
-- DELETE는 sd-cleaner의 gw_cleaner 롤이 retention 정책에 따라 수행.
-- Idempotent.
-- =====================================================================

GRANT UPDATE ON video TO gw_admin;
