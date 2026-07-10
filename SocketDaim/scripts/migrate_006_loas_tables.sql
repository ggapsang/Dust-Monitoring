-- =====================================================================
-- Migration 006: LOAS Tfoi v4a 수신 테이블 추가
-- =====================================================================
-- 신규 ingestion 경로 두 개 (DUST 분진센서 / CCTV 카메라) 를 받기 위한
-- 테이블·인덱스·VIEW·권한.  Standard 프로토콜(`video`, `sensor_sample`)
-- 은 손대지 않는다 — 두 모드는 IGW_PROTOCOL 토글로 택일.
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < scripts/migrate_006_loas_tables.sql
--
-- Idempotent: 중복 실행 안전.
-- =====================================================================

-- ---------------------------------------------------------------------
-- dust_inspection
--   DUST_INSPECTION_INFOR XML 1건당 1행.  ugv_id, mission_id 등 모든
--   메타데이터가 여기 모인다 (CCTV 측에는 메타가 없으므로 시간 기반
--   페어링으로 cctv_frame이 이 테이블을 FK로 참조).
--
--   - received_at: Gateway 도착 시각 (페어링 기준 시간)
--   - sensor_datetime / sensor_epoch_sec: 센서 자체가 찍은 시각 (audit)
--   - raw_xml: 디버그·감사용 원문 보존
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dust_inspection (
    id                  BIGSERIAL    PRIMARY KEY,
    received_at         TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),

    -- audit-only timestamps from sensor side
    sensor_datetime     TIMESTAMPTZ,
    sensor_epoch_sec    BIGINT,

    -- packet identification
    cmd_id              VARCHAR(64)  NOT NULL,
    data_object_id      INTEGER,                        -- uint16 wire (0xD002 > int16 max)
    protocol_version    SMALLINT,

    -- measurement
    dust_value          DOUBLE PRECISION,
    dust_alarm          SMALLINT,    -- 0=Fault, 1=Maint, 2=Alert, 3=Normal
    sensor_type         SMALLINT,
    sensor_index        SMALLINT,
    target_index        SMALLINT,

    -- spatial
    waypoint_x          DOUBLE PRECISION,
    waypoint_y          DOUBLE PRECISION,
    waypoint_z          DOUBLE PRECISION,
    rot_x               DOUBLE PRECISION,
    rot_y               DOUBLE PRECISION,
    rot_z               DOUBLE PRECISION,
    rot_w               DOUBLE PRECISION,

    -- routing / mission identifiers
    ugv_id              INTEGER,
    location_id         INTEGER,
    map_id              INTEGER,
    navigation_id       INTEGER,
    exec_id             INTEGER,
    plant_id            INTEGER,
    target_id           INTEGER,
    waypoint_id         INTEGER,
    inspection_local_id BIGINT,
    object_id           INTEGER,
    mission_id          BIGINT,

    inspection_pan      INTEGER,
    inspection_tilt     INTEGER,
    inspection_lift     INTEGER,

    -- audit
    raw_xml             TEXT
);

CREATE INDEX IF NOT EXISTS idx_dust_received_at
    ON dust_inspection (received_at);
CREATE INDEX IF NOT EXISTS idx_dust_ugv_received
    ON dust_inspection (ugv_id, received_at);
CREATE INDEX IF NOT EXISTS idx_dust_alarm
    ON dust_inspection (dust_alarm) WHERE dust_alarm < 3;  -- 비정상만
CREATE INDEX IF NOT EXISTS idx_dust_mission
    ON dust_inspection (mission_id);


-- ---------------------------------------------------------------------
-- cctv_frame
--   AMR 카메라가 push한 JPG 1장당 1행.  메타데이터 없는 단방향 스트림
--   이므로 페어링은 Correlator(별도 PR)가 dust_inspection.received_at
--   ±window 안의 행과 매칭하여 dust_inspection_id 컬럼을 사후 채운다.
--
--   - amr_id: 단일-AMR 모드에서는 settings.loas_amr_id 상수.
--             다대-AMR 전환 시 amr 레지스트리 테이블 추가하고 FK 부여.
--   - source_ip: audit/debug 용. 단일-AMR 모드에서는 정책 결정 안 함.
--   - dust_inspection_id: NULL (orphan) → 1시간 후 cleaner가 삭제.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cctv_frame (
    id                  BIGSERIAL    PRIMARY KEY,
    received_at         TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    amr_id              VARCHAR(50)  NOT NULL,
    source_ip           INET,
    resolution          VARCHAR(8)   NOT NULL,    -- 'V1080' | 'V720p' | 'V640p'
    file_path           TEXT         NOT NULL,
    byte_size           INTEGER      NOT NULL,
    dust_inspection_id  BIGINT       REFERENCES dust_inspection(id) ON DELETE SET NULL,
    paired_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cctv_received
    ON cctv_frame (received_at);
CREATE INDEX IF NOT EXISTS idx_cctv_dust
    ON cctv_frame (dust_inspection_id) WHERE dust_inspection_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cctv_unpaired
    ON cctv_frame (received_at) WHERE dust_inspection_id IS NULL;


-- ---------------------------------------------------------------------
-- v_inspection_with_frames
--   Decision Agent / Anomaly 모듈이 join 없이 바로 쓰는 편의 VIEW.
--   LEFT JOIN이므로 dust 한 건당 0..N개의 frame 행이 펼쳐진다.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_inspection_with_frames AS
SELECT
    di.id                AS inspection_id,
    di.received_at       AS inspection_at,
    di.ugv_id,
    di.mission_id,
    di.waypoint_id,
    di.target_id,
    di.waypoint_x,
    di.waypoint_y,
    di.waypoint_z,
    di.dust_value,
    di.dust_alarm,
    cf.id                AS frame_id,
    cf.received_at       AS frame_at,
    cf.amr_id,
    cf.file_path,
    cf.resolution,
    cf.byte_size
  FROM dust_inspection di
  LEFT JOIN cctv_frame cf ON cf.dust_inspection_id = di.id;


-- ---------------------------------------------------------------------
-- 권한
--   gw_writer  : INSERT (이 INGW가 쓴다), 그리고 cctv_frame은 Correlator가
--                dust_inspection_id / paired_at 두 컬럼만 UPDATE.
--   gw_reader  : SELECT (Decision Agent / Anomaly 모듈)
--   gw_admin   : SELECT (운영 조회)
--   gw_cleaner : SELECT + DELETE (retention 정책)
-- ---------------------------------------------------------------------
-- Correlator runs inside Ingestion Gateway (gw_writer role) and needs
-- SELECT on both tables to evaluate its time-window join in the UPDATE's
-- WHERE clause.  UPDATE is column-scoped to the two pairing columns; the
-- rest of cctv_frame stays immutable from the writer's perspective.
GRANT INSERT, SELECT ON dust_inspection, cctv_frame TO gw_writer;
GRANT UPDATE (dust_inspection_id, paired_at) ON cctv_frame TO gw_writer;
GRANT USAGE, SELECT ON SEQUENCE dust_inspection_id_seq TO gw_writer;
GRANT USAGE, SELECT ON SEQUENCE cctv_frame_id_seq      TO gw_writer;

GRANT SELECT ON dust_inspection, cctv_frame, v_inspection_with_frames TO gw_reader;
GRANT SELECT ON dust_inspection, cctv_frame, v_inspection_with_frames TO gw_admin;

GRANT SELECT, DELETE ON dust_inspection, cctv_frame TO gw_cleaner;
