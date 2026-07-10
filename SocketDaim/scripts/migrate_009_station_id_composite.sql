-- =====================================================================
-- Migration 009: waypoint station identity 를 7-tuple 합성으로 확장
-- =====================================================================
-- 운용 요구사항: 같은 waypoint_id 라도 (x, y, z) 좌표 또는 (pan, tilt, lift)
-- 자세가 다르면 별도의 "관측 개소" 로 취급한다.  AMR 이 같은 waypoint 를
-- 다른 자세로 측정하는 케이스를 분석에서 분리할 수 있게 함.
--
-- 변경:
--   1) loas_station_id() 함수 추가 — 7-tuple → 결정론적 UUID
--   2) waypoint_label 테이블 PK: waypoint_id (int) → station_id (uuid) +
--      참조용 composite 컬럼 추가
--   3) v_loas_stations, v_loas_sensor_sample 가 함수를 호출하도록 재정의
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_009_station_id_composite.sql
--
-- Idempotent (DROP + CREATE).  기존 waypoint_label row 는 PK 가 바뀌므로
-- 보존이 어렵다 — 마이그레이션 직전 백업이 필요하면 별도 추출.
-- =====================================================================

-- 1. station_id 합성 함수 -------------------------------------------------
-- IMMUTABLE → VIEW 안에서 인덱스 친화적. NULL safe.
-- 좌표는 소수 셋째 자리까지만 (벤더 wire 정밀도 = 3 decimals).
CREATE OR REPLACE FUNCTION loas_station_id(
    p_waypoint_id     INTEGER,
    p_waypoint_x      DOUBLE PRECISION,
    p_waypoint_y      DOUBLE PRECISION,
    p_waypoint_z      DOUBLE PRECISION,
    p_inspection_pan  INTEGER,
    p_inspection_tilt INTEGER,
    p_inspection_lift INTEGER
) RETURNS UUID
LANGUAGE SQL IMMUTABLE AS $$
    SELECT md5(
        'wp:'   || COALESCE(p_waypoint_id::text, 'NA')                              ||
        ':xyz:' || COALESCE(to_char(p_waypoint_x, 'FM999990.000'), 'NA')            || ',' ||
                  COALESCE(to_char(p_waypoint_y, 'FM999990.000'), 'NA')            || ',' ||
                  COALESCE(to_char(p_waypoint_z, 'FM999990.000'), 'NA')            ||
        ':ptl:' || COALESCE(p_inspection_pan::text, 'NA')                          || ',' ||
                  COALESCE(p_inspection_tilt::text, 'NA')                          || ',' ||
                  COALESCE(p_inspection_lift::text, 'NA')
    )::uuid;
$$;


-- 2. waypoint_label 재설계 -----------------------------------------------
-- 새 PK = station_id (UUID).  composite 컬럼들은 라벨이 어떤 7-tuple 에
-- 대응하는지 사람이 보고 알 수 있게 함께 저장.
DROP TABLE IF EXISTS waypoint_label CASCADE;
CREATE TABLE waypoint_label (
    station_id      UUID         PRIMARY KEY,
    -- composite source (audit + display)
    waypoint_id     INTEGER,
    waypoint_x      DOUBLE PRECISION,
    waypoint_y      DOUBLE PRECISION,
    waypoint_z      DOUBLE PRECISION,
    inspection_pan  INTEGER,
    inspection_tilt INTEGER,
    inspection_lift INTEGER,
    -- user-supplied
    label           VARCHAR(255) NOT NULL,
    location        TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- 3. VIEW 재정의 ---------------------------------------------------------
DROP VIEW IF EXISTS v_loas_stations CASCADE;
CREATE VIEW v_loas_stations AS
SELECT
    sub.station_id,
    COALESCE(
        wl.label,
        'WP-' || sub.waypoint_id::text
              || '/p' || sub.inspection_pan::text
              || 't'  || sub.inspection_tilt::text
              || 'l'  || sub.inspection_lift::text
    )::text                                                AS station_name,
    'active'::text                                          AS status,
    wl.location::text                                       AS location_info,
    -- 추가 진단용 — UI 가 자세 표시에 사용
    sub.waypoint_id,
    sub.waypoint_x,
    sub.waypoint_y,
    sub.waypoint_z,
    sub.inspection_pan,
    sub.inspection_tilt,
    sub.inspection_lift
  FROM (
    SELECT DISTINCT
        loas_station_id(
            waypoint_id, waypoint_x, waypoint_y, waypoint_z,
            inspection_pan, inspection_tilt, inspection_lift
        ) AS station_id,
        waypoint_id, waypoint_x, waypoint_y, waypoint_z,
        inspection_pan, inspection_tilt, inspection_lift
      FROM dust_inspection
     WHERE waypoint_id IS NOT NULL
  ) sub
  LEFT JOIN waypoint_label wl ON wl.station_id = sub.station_id;

DROP VIEW IF EXISTS v_loas_sensor_sample CASCADE;
CREATE VIEW v_loas_sensor_sample AS
SELECT
    id,
    loas_station_id(
        waypoint_id, waypoint_x, waypoint_y, waypoint_z,
        inspection_pan, inspection_tilt, inspection_lift
    )                                                       AS station_id,
    'dust_concentration'::text                              AS measurement_type,
    dust_value                                              AS value,
    'mg/m3'::text                                           AS unit,
    received_at                                             AS sampled_at
  FROM dust_inspection
 WHERE waypoint_id IS NOT NULL
   AND dust_value  IS NOT NULL;


-- 4. 권한 ----------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON waypoint_label TO gw_admin;
GRANT SELECT ON waypoint_label TO gw_reader;
GRANT SELECT ON v_loas_stations, v_loas_sensor_sample TO gw_reader, gw_admin;
GRANT EXECUTE ON FUNCTION loas_station_id(
    INTEGER, DOUBLE PRECISION, DOUBLE PRECISION, DOUBLE PRECISION,
    INTEGER, INTEGER, INTEGER
) TO PUBLIC;
