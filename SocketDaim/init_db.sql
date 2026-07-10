-- =====================================================================
-- SocketDaim Gateway – PostgreSQL Schema
-- =====================================================================
-- Runs once on first container start via
--   /docker-entrypoint-initdb.d/init_db.sql
-- Tables:  station, video, sensor_sample, ingestion_log
-- Roles:   gw_writer (Ingestion Gateway), gw_reader (Consumers),
--          gw_admin  (Station admin tool)
-- =====================================================================

-- Extensions ----------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()


-- =====================================================================
-- Roles
-- Dev-only default passwords.  Replace with Docker secrets / env vars
-- before production deployment.
-- =====================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gw_writer') THEN
        CREATE ROLE gw_writer LOGIN PASSWORD 'dev_writer_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gw_reader') THEN
        CREATE ROLE gw_reader LOGIN PASSWORD 'dev_reader_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gw_admin') THEN
        CREATE ROLE gw_admin  LOGIN PASSWORD 'dev_admin_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gw_cleaner') THEN
        CREATE ROLE gw_cleaner LOGIN PASSWORD 'dev_cleaner_pw';
    END IF;
END $$;


-- =====================================================================
-- Tables
-- =====================================================================

-- station --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS station (
    station_id      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    station_name    VARCHAR(255) NOT NULL,
    location_info   TEXT,
    amr_id          VARCHAR(128),
    capture_cycle   INTEGER,                    -- seconds between captures
    description     TEXT,
    status          VARCHAR(32)  NOT NULL DEFAULT 'collecting',
                                                -- collecting / waiting / training / inferring / inactive
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- video ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video (
    video_id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    station_id            UUID         NOT NULL REFERENCES station(station_id),
    amr_id                VARCHAR(128),
    captured_at           TIMESTAMPTZ,
    file_path             TEXT         NOT NULL,
    duration_sec          DOUBLE PRECISION,
    resolution            VARCHAR(32),
    source_format         VARCHAR(16),                  -- 'mp4' | 'jpeg' | 'jpeg_seq' | 'raw' | NULL
    amr_position          JSONB,
    quality_check_result  JSONB,
    is_valid              BOOLEAN      NOT NULL DEFAULT true,
    is_excluded           BOOLEAN      NOT NULL DEFAULT false,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- sensor_sample --------------------------------------------------------
CREATE TABLE IF NOT EXISTS sensor_sample (
    id                BIGSERIAL            PRIMARY KEY,
    station_id        UUID                 NOT NULL REFERENCES station(station_id),
    measurement_type  VARCHAR(64)          NOT NULL,
    value             DOUBLE PRECISION     NOT NULL,
    unit              VARCHAR(32)          NOT NULL,
    sampled_at        TIMESTAMPTZ          NOT NULL,
    received_at       TIMESTAMPTZ          NOT NULL DEFAULT NOW()
);

-- ingestion_log --------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_log (
    id             BIGSERIAL    PRIMARY KEY,
    station_id     UUID,                          -- nullable: some errors have no station context
    message_type   VARCHAR(32)  NOT NULL,
    status         VARCHAR(32)  NOT NULL,         -- 'success' | 'error'
    error_message  TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- station_request ------------------------------------------------------
-- Tracks send attempts whose station_name is NOT in `station`.  Wire
-- traffic identifies stations by name (UUIDs are private to each side's
-- DB), so this triage queue is also keyed by name.
--   'pending'  : awaiting admin decision (default on first sighting)
--   'approved' : admin accepted; corresponding station row was created
--   'rejected' : admin rejected; further attempts still bump last_seen
--                / attempts but stay visibility-only.
-- gw_writer (Ingestion Gateway) only INSERTs new rows or bumps
-- attempts/last_seen via ON CONFLICT.  status is mutated only by gw_admin.
CREATE TABLE IF NOT EXISTS station_request (
    station_name VARCHAR(255) PRIMARY KEY,
    first_seen   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    attempts     INTEGER      NOT NULL DEFAULT 1,
    status       VARCHAR(16)  NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'approved', 'rejected')),
    notes        TEXT
);

-- dust_inspection (LOAS Tfoi v4a) ---------------------------------------
-- DUST_INSPECTION_INFOR XML 1건당 1행.  ugv_id, mission_id 등 모든
-- 메타데이터가 여기 모인다.  CCTV 측은 메타가 없어 시간 기반 페어링으로
-- cctv_frame.dust_inspection_id가 이 테이블의 id를 참조한다.
CREATE TABLE IF NOT EXISTS dust_inspection (
    id                  BIGSERIAL    PRIMARY KEY,
    received_at         TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    sensor_datetime     TIMESTAMPTZ,
    sensor_epoch_sec    BIGINT,
    cmd_id              VARCHAR(64)  NOT NULL,
    data_object_id      INTEGER,                        -- uint16 wire (0xD002 > int16 max)
    protocol_version    SMALLINT,
    dust_value          DOUBLE PRECISION,
    dust_alarm          SMALLINT,                       -- 0=Fault,1=Maint,2=Alert,3=Normal
    sensor_type         SMALLINT,
    sensor_index        SMALLINT,
    target_index        SMALLINT,
    waypoint_x          DOUBLE PRECISION,
    waypoint_y          DOUBLE PRECISION,
    waypoint_z          DOUBLE PRECISION,
    rot_x               DOUBLE PRECISION,
    rot_y               DOUBLE PRECISION,
    rot_z               DOUBLE PRECISION,
    rot_w               DOUBLE PRECISION,
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
    raw_xml             TEXT
);

-- cctv_frame (LOAS Tfoi v4a) --------------------------------------------
-- AMR 카메라가 push한 JPG 1장당 1행.  단방향 스트림이라 페어링은 별도
-- Correlator가 dust_inspection.received_at ±window 안의 행과 매칭하여
-- dust_inspection_id를 사후에 채운다.
--   amr_id   : 단일-AMR 모드에서는 settings.loas_amr_id 상수.
--              다대 AMR 전환 시 amr 레지스트리 도입 + FK.
--   source_ip: audit/debug 용.
CREATE TABLE IF NOT EXISTS cctv_frame (
    id                  BIGSERIAL    PRIMARY KEY,
    received_at         TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    amr_id              VARCHAR(50)  NOT NULL,
    source_ip           INET,
    resolution          VARCHAR(8)   NOT NULL,
    file_path           TEXT         NOT NULL,
    byte_size           INTEGER      NOT NULL,
    dust_inspection_id  BIGINT       REFERENCES dust_inspection(id) ON DELETE SET NULL,
    paired_at           TIMESTAMPTZ
);

-- loas_station_id() ------------------------------------------------------
-- LOAS "관측 개소" 식별자 합성.  개소 기준값 = target_id (waypoint_id != NULL 행).
-- 같은 target_id = 같은 개소.  station_id 는 target_id 의 결정론적 UUID(타입 UUID 유지).
CREATE OR REPLACE FUNCTION loas_station_id(p_target_id INTEGER) RETURNS UUID
LANGUAGE SQL IMMUTABLE AS $$
    SELECT md5('target:' || p_target_id::text)::uuid;
$$;

-- waypoint_label --------------------------------------------------------
-- LOAS 모드용 운용 라벨.  PK = station_id (loas_station_id(target_id) 결과).
-- 개소 기준이 target_id 로 바뀌어, 7-tuple(x/y/z/pan/tilt/lift) 컬럼은 제거하고
-- target_id 만 함께 보관(사람이 어떤 개소인지 식별).
CREATE TABLE IF NOT EXISTS waypoint_label (
    station_id      UUID         PRIMARY KEY,
    target_id       INTEGER,                     -- 개소 기준값
    label           VARCHAR(255) NOT NULL,
    location        TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- v_loas_stations -------------------------------------------------------
-- Dumopro WebApp 호환 VIEW.  관측 개소 = target_id (waypoint_id != NULL).
CREATE OR REPLACE VIEW v_loas_stations AS
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
       -- (fallback 비활성: target_id IS NULL 행을 별도 개소로 묶고 싶으면 위 조건을
       --  완화하고 여기서 합성키를 따로 정의 — 현재는 의도적으로 막아둠.)
  ) sub
  LEFT JOIN waypoint_label wl ON wl.station_id = sub.station_id;

-- v_loas_sensor_sample --------------------------------------------------
-- Dumopro WebApp 호환 VIEW.  dust_inspection 을 sensor_sample 모양으로
-- reshape (measurement_type='dust_concentration', unit='mg/m3' 상수).
-- station_id 는 target_id 합성.
CREATE OR REPLACE VIEW v_loas_sensor_sample AS
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

-- v_inspection_with_frames ----------------------------------------------
-- Decision Agent / Anomaly 모듈이 join 없이 바로 쓰는 편의 VIEW.
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


-- =====================================================================
-- Indexes
-- =====================================================================
CREATE INDEX IF NOT EXISTS idx_video_station_id          ON video(station_id);
CREATE INDEX IF NOT EXISTS idx_video_captured_at         ON video(captured_at);
CREATE INDEX IF NOT EXISTS idx_sensor_station_id         ON sensor_sample(station_id);
CREATE INDEX IF NOT EXISTS idx_sensor_sampled_at         ON sensor_sample(sampled_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_created_at  ON ingestion_log(created_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_station_id  ON ingestion_log(station_id);
CREATE INDEX IF NOT EXISTS idx_station_request_status    ON station_request(status);
CREATE INDEX IF NOT EXISTS idx_station_request_last_seen ON station_request(last_seen DESC);
-- LOAS Tfoi v4a tables
CREATE INDEX IF NOT EXISTS idx_dust_received_at          ON dust_inspection(received_at);
CREATE INDEX IF NOT EXISTS idx_dust_ugv_received         ON dust_inspection(ugv_id, received_at);
CREATE INDEX IF NOT EXISTS idx_dust_alarm                ON dust_inspection(dust_alarm) WHERE dust_alarm < 3;
CREATE INDEX IF NOT EXISTS idx_dust_mission              ON dust_inspection(mission_id);
CREATE INDEX IF NOT EXISTS idx_cctv_received             ON cctv_frame(received_at);
CREATE INDEX IF NOT EXISTS idx_cctv_dust                 ON cctv_frame(dust_inspection_id) WHERE dust_inspection_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cctv_unpaired             ON cctv_frame(received_at) WHERE dust_inspection_id IS NULL;


-- =====================================================================
-- Role grants
-- =====================================================================

-- gw_writer: Ingestion Gateway
--   INSERT on video / sensor_sample / ingestion_log
--   SELECT on station (for station_id validation only)
--   INSERT/UPDATE/SELECT on station_request (so unknown-UUID send attempts
--     can be upserted with attempts++ via ON CONFLICT).  status field is
--     never written by the gateway by code convention.
GRANT CONNECT ON DATABASE gateway_db TO gw_writer;
GRANT USAGE  ON SCHEMA public         TO gw_writer;

GRANT SELECT ON station                               TO gw_writer;
GRANT INSERT ON video, sensor_sample, ingestion_log   TO gw_writer;
GRANT INSERT, UPDATE, SELECT ON station_request       TO gw_writer;
GRANT USAGE, SELECT ON SEQUENCE sensor_sample_id_seq  TO gw_writer;
GRANT USAGE, SELECT ON SEQUENCE ingestion_log_id_seq  TO gw_writer;
-- LOAS tables: INSERT + SELECT (Correlator needs SELECT to evaluate its
-- time-window join in the UPDATE WHERE clause).  UPDATE is column-scoped
-- so only the two pairing columns of cctv_frame can be mutated.
GRANT INSERT, SELECT ON dust_inspection, cctv_frame   TO gw_writer;
GRANT UPDATE (dust_inspection_id, paired_at) ON cctv_frame TO gw_writer;
GRANT USAGE, SELECT ON SEQUENCE dust_inspection_id_seq TO gw_writer;
GRANT USAGE, SELECT ON SEQUENCE cctv_frame_id_seq      TO gw_writer;

-- gw_reader: Consumers (Autoencoder / YOLO / Dumopro)
--   Read-only access to all shared tables
GRANT CONNECT ON DATABASE gateway_db TO gw_reader;
GRANT USAGE  ON SCHEMA public         TO gw_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO gw_reader;

-- gw_admin: Station management tool (Admin UI)
--   Full RW on station + station_request; video는 라벨링 UPDATE만, 나머지는 SELECT
GRANT CONNECT ON DATABASE gateway_db TO gw_admin;
GRANT USAGE  ON SCHEMA public         TO gw_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON station         TO gw_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON station_request TO gw_admin;
GRANT SELECT, UPDATE ON video                           TO gw_admin;
GRANT SELECT         ON sensor_sample, ingestion_log    TO gw_admin;
GRANT SELECT         ON dust_inspection, cctv_frame, v_inspection_with_frames TO gw_admin;
GRANT SELECT         ON v_loas_stations, v_loas_sensor_sample                  TO gw_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON waypoint_label                         TO gw_admin;

-- gw_cleaner: Retention enforcement (sd-cleaner)
--   SELECT + DELETE on retention 대상 테이블만.  station / station_request
--   같은 메타 데이터는 손댈 수 없도록 최소 권한.
GRANT CONNECT ON DATABASE gateway_db TO gw_cleaner;
GRANT USAGE  ON SCHEMA public         TO gw_cleaner;
GRANT SELECT, DELETE ON video, sensor_sample, ingestion_log TO gw_cleaner;
GRANT SELECT, DELETE ON dust_inspection, cctv_frame          TO gw_cleaner;
