-- =====================================================================
-- Migration 008: waypoint_label 테이블 + v_loas_stations VIEW 라벨 적용
-- =====================================================================
-- LOAS 모드에서는 사전 등록 없이 dust_inspection 에 들어온 waypoint_id
-- 들로부터 v_loas_stations VIEW 가 자동으로 station 을 합성한다 (기본 이름
-- 은 'WP-<id>').  운용 중에 사람 친화 이름·위치 설명을 붙이고 싶을 때 쓰는
-- 별도 라벨 테이블.
--
-- Dumopro 측은 변경 불필요 — v_loas_stations 가 LEFT JOIN 으로 라벨을
-- 끌어오므로 라벨이 있는 waypoint 는 라벨 이름으로, 없으면 자동 'WP-<id>'
-- 로 fallback.  station_id (md5 합성 UUID) 는 라벨 유무와 무관하게 동일
-- → Redis 캐시 / cursor 호환.
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_008_waypoint_label.sql
--
-- Idempotent.
-- =====================================================================

CREATE TABLE IF NOT EXISTS waypoint_label (
    waypoint_id  INTEGER       PRIMARY KEY,
    label        VARCHAR(255)  NOT NULL,
    location     TEXT,
    notes        TEXT,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);


-- v_loas_stations 재정의 — LEFT JOIN waypoint_label
-- 주의: CREATE OR REPLACE VIEW 는 컬럼 *타입* 을 못 바꾸므로 기존 VIEW 와
-- 동일한 컬럼 타입 (모두 text) 을 유지하도록 명시적으로 ::text 캐스트.
DROP VIEW IF EXISTS v_loas_stations CASCADE;
CREATE VIEW v_loas_stations AS
SELECT
    (md5('waypoint:' || di.waypoint_id::text))::uuid       AS station_id,
    COALESCE(wl.label, 'WP-' || di.waypoint_id::text)::text AS station_name,
    'active'::text                                         AS status,
    wl.location::text                                      AS location_info
  FROM (
    SELECT DISTINCT waypoint_id
      FROM dust_inspection
     WHERE waypoint_id IS NOT NULL
  ) di
  LEFT JOIN waypoint_label wl ON wl.waypoint_id = di.waypoint_id;


-- 권한 -----------------------------------------------------------------
-- gw_admin: 라벨 CRUD (Admin UI 가 사용)
GRANT SELECT, INSERT, UPDATE, DELETE ON waypoint_label TO gw_admin;
-- gw_reader: Dumopro 가 VIEW 통해 간접 조회 (직접 접근도 허용)
GRANT SELECT ON waypoint_label TO gw_reader;
-- VIEW 의 grant 는 재정의해도 유지되지만 명시적으로 한 번 더 박아 안전
GRANT SELECT ON v_loas_stations TO gw_reader, gw_admin;
