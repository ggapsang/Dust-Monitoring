-- =====================================================================
-- Migration 010: 관측 개소 식별을 7-tuple → target_id 로 변경
-- =====================================================================
-- 변경 요지: "관측 개소" 기준을 (waypoint_id + x,y,z + pan,tilt,lift) 7-tuple 에서
--   **target_id (waypoint_id != NULL 행)** 로 바꾼다.  같은 target_id = 같은 개소.
--
-- 결정사항:
--   1) station_id 타입은 UUID 유지 → md5('target:'||target_id)::uuid.
--   2) target_id 가 NULL 인 관측은 뷰에서 제외(fallback 없음; 주석으로만 표기).
--   3) waypoint_label 의 7-tuple 컬럼 제거 + 기존 라벨 row 전부 삭제(재작성).
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_010_station_by_target.sql
--
-- 주의: 같은 gateway_db 에 적용되는 PoolerTran 의 migrate_010(cctv_transfer_queue)와는
--   별개 파일/목적이다(레포·파일명 suffix 로 구분).
-- =====================================================================

-- 1) 컬럼셋이 바뀌는 뷰는 DROP 후 재생성 (CREATE OR REPLACE 불가).
DROP VIEW IF EXISTS v_loas_sensor_sample CASCADE;
DROP VIEW IF EXISTS v_loas_stations CASCADE;

-- 2) 구 7-tuple 식별 함수 제거.
DROP FUNCTION IF EXISTS loas_station_id(
    integer, double precision, double precision, double precision,
    integer, integer, integer);

-- 3) 신 식별 함수: target_id → UUID.
CREATE OR REPLACE FUNCTION loas_station_id(p_target_id INTEGER) RETURNS UUID
LANGUAGE SQL IMMUTABLE AS $$
    SELECT md5('target:' || p_target_id::text)::uuid;
$$;

-- 4) waypoint_label 재작성 — 7-tuple 컬럼 제거 + target_id 추가, 기존 라벨 전부 제거.
DROP TABLE IF EXISTS waypoint_label CASCADE;
CREATE TABLE waypoint_label (
    station_id UUID         PRIMARY KEY,           -- loas_station_id(target_id)
    target_id  INTEGER,                            -- 개소 기준값
    label      VARCHAR(255) NOT NULL,
    location   TEXT,
    notes      TEXT,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 5) 뷰 재생성 (target_id 기준).
CREATE VIEW v_loas_stations AS
SELECT
    sub.station_id,
    COALESCE(wl.label, 'TGT-' || sub.target_id::text)::text AS station_name,
    'active'::text                                          AS status,
    wl.location::text                                       AS location_info,
    sub.target_id
  FROM (
    SELECT DISTINCT
        loas_station_id(target_id) AS station_id,
        target_id
      FROM dust_inspection
     WHERE waypoint_id IS NOT NULL
       AND target_id  IS NOT NULL   -- target_id 없는 관측은 개소에서 제외(fallback 없음)
       -- (fallback 비활성: target_id IS NULL 을 별도 개소로 묶으려면 위 조건 완화 +
       --  여기서 합성키 별도 정의 — 현재는 의도적으로 막아둠.)
  ) sub
  LEFT JOIN waypoint_label wl ON wl.station_id = sub.station_id;

CREATE VIEW v_loas_sensor_sample AS
SELECT
    id,
    loas_station_id(target_id)                    AS station_id,
    'dust_concentration'::text                    AS measurement_type,
    dust_value                                    AS value,
    'mg/m3'::text                                 AS unit,
    received_at                                   AS sampled_at
  FROM dust_inspection
 WHERE waypoint_id IS NOT NULL
   AND target_id   IS NOT NULL   -- target_id 없는 관측은 제외(fallback 없음)
   AND dust_value  IS NOT NULL;

-- 6) 권한 (migrate_008/009 와 동일 정책).
GRANT SELECT, INSERT, UPDATE, DELETE ON waypoint_label TO gw_admin;
GRANT SELECT ON waypoint_label TO gw_reader;
GRANT SELECT ON v_loas_stations, v_loas_sensor_sample TO gw_reader, gw_admin;
GRANT EXECUTE ON FUNCTION loas_station_id(integer) TO PUBLIC;
