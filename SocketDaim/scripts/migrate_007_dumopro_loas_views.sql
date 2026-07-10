-- =====================================================================
-- Migration 007: Dumopro 가 LOAS 데이터 (dust_inspection) 를 읽을 수 있도록
-- standard 스키마(sensor_sample / station) 형태로 노출하는 호환 VIEW 두 개.
-- =====================================================================
-- Dumopro Data Analysis WebApp 은 station + sensor_sample 두 테이블만
-- 알고 있다.  LOAS 모드에서는 분진 측정값이 dust_inspection 에 적재되므로
-- 직접 보지 못한다.
--
-- 해결: dust_inspection 을 (id, station_id::uuid, measurement_type, value,
-- unit, sampled_at) 형태로, distinct waypoint 들을 station 형태로 reshape
-- 하는 VIEW 두 개를 추가한다.  Dumopro 측은 환경변수로 source 테이블을
-- 토글한다 (default = LOAS 뷰).
--
-- 개념 매핑:
--   LOAS waypoint_id (int)         ↔  standard station_id (UUID)
--                                       (md5('waypoint:' || id)::uuid 로 합성)
--   LOAS dust_inspection.dust_value↔  sensor_sample.value
--   LOAS dust_inspection.received_at↔ sensor_sample.sampled_at
--   상수 'dust_concentration'      →  measurement_type
--   상수 'mg/m3'                   →  unit
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_007_dumopro_loas_views.sql
--
-- Idempotent.
-- =====================================================================

-- v_loas_stations -----------------------------------------------------
-- dust_inspection 에 등장한 distinct waypoint_id 마다 한 행.
-- station_id 는 결정론적 (waypoint_id 가 같으면 항상 같은 UUID).
CREATE OR REPLACE VIEW v_loas_stations AS
SELECT DISTINCT
    (md5('waypoint:' || waypoint_id::text))::uuid AS station_id,
    'WP-' || waypoint_id::text                    AS station_name,
    'active'::text                                AS status,
    NULL::text                                    AS location_info
  FROM dust_inspection
 WHERE waypoint_id IS NOT NULL;


-- v_loas_sensor_sample ------------------------------------------------
-- dust_inspection 의 모든 dust 측정값을 sensor_sample 형태로 reshape.
-- waypoint_id 가 NULL 인 행 (이동 중 등) 은 station 매핑이 불가능하므로 제외.
-- dust_value NULL 인 행도 제외 (의미 없는 측정).
CREATE OR REPLACE VIEW v_loas_sensor_sample AS
SELECT
    id,
    (md5('waypoint:' || waypoint_id::text))::uuid AS station_id,
    'dust_concentration'::text                    AS measurement_type,
    dust_value                                    AS value,
    'mg/m3'::text                                 AS unit,
    received_at                                   AS sampled_at
  FROM dust_inspection
 WHERE waypoint_id IS NOT NULL
   AND dust_value  IS NOT NULL;


-- 권한 ----------------------------------------------------------------
-- gw_reader 가 두 VIEW 를 SELECT 할 수 있어야 Dumopro 가 조회 가능.
GRANT SELECT ON v_loas_stations, v_loas_sensor_sample TO gw_reader;
GRANT SELECT ON v_loas_stations, v_loas_sensor_sample TO gw_admin;
