-- =====================================================================
-- Decision DB (decision_db) — Schema
-- =====================================================================
-- Owner: Decision Agent (refs/decision_agent_plan.md §7.1)
--
-- Tables
--   role_mapping     detection role -> component name
--   alarm_mapping    12-row alarm truth table
--   decision_record  one row per observation; consumers + DA + Egress UPDATE
--
-- Roles (passwords are dev-only; replace via Docker secrets in prod)
--   anomaly_detector_role   UPDATE on anomaly_detection_*
--   object_detector_role    UPDATE on object_detection_*
--   sensor_analysis_role    UPDATE on sensor_analysis_*
--   decision_agent_role     SELECT all + UPDATE final_decision/decided_at/mapping_id
--   egress_role             SELECT all + UPDATE sent_at
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- =====================================================================
-- ENUM types
-- =====================================================================
-- Note on channel_result: plan doc §4.1 used component_result(normal/abnormal/
-- pending) for all 3 channel columns, but alarm_mapping (§3.1) needs
-- sensor_level(normal/caution/warning) for the IoT channel. component_result
-- can't express caution/warning. We use channel_result as a superset so a
-- single column type can hold either model output (normal/abnormal) or
-- sensor output (normal/caution/warning), plus pending. judge validates per
-- role at lookup time.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sensor_level') THEN
        CREATE TYPE sensor_level AS ENUM ('normal', 'caution', 'warning');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'model_result') THEN
        CREATE TYPE model_result AS ENUM ('normal', 'abnormal');
    END IF;
    -- final_level / decision_result 에 'danger'(위험) 추가 → LOAS event_id 4단계(0~3).
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'final_level') THEN
        CREATE TYPE final_level AS ENUM ('normal', 'caution', 'warning', 'danger');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'channel_result') THEN
        CREATE TYPE channel_result AS ENUM ('normal', 'abnormal', 'caution', 'warning', 'pending');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'decision_result') THEN
        CREATE TYPE decision_result AS ENUM ('normal', 'caution', 'warning', 'danger', 'pending');
    END IF;
END $$;


-- =====================================================================
-- Roles  (dev-only passwords; replace via Docker secrets in prod)
-- =====================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'anomaly_detector_role') THEN
        CREATE ROLE anomaly_detector_role LOGIN PASSWORD 'dev_anomaly_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'object_detector_role') THEN
        CREATE ROLE object_detector_role LOGIN PASSWORD 'dev_object_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'sensor_analysis_role') THEN
        CREATE ROLE sensor_analysis_role LOGIN PASSWORD 'dev_sensor_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'decision_agent_role') THEN
        CREATE ROLE decision_agent_role LOGIN PASSWORD 'dev_decision_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'egress_role') THEN
        CREATE ROLE egress_role LOGIN PASSWORD 'dev_egress_pw';
    END IF;
END $$;


-- =====================================================================
-- Tables
-- =====================================================================

-- role_mapping: detection role -> component name (plan doc §2.1)
CREATE TABLE IF NOT EXISTS role_mapping (
    id              SERIAL PRIMARY KEY,
    detection_role  VARCHAR(30)  NOT NULL,
    component_name  VARCHAR(50)  NOT NULL,
    description     TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (detection_role)
);


-- alarm_mapping: 8-row truth table (2×2×2).
-- 센서 레벨을 2단계(normal/abnormal, model_result 재사용)로 단순화 →
-- 기존 3×2×2(12행) → 2×2×2(8행).  (decision_agent_2x2x2_구현계획.md §4)
CREATE TABLE IF NOT EXISTS alarm_mapping (
    id                    SERIAL PRIMARY KEY,
    iot_sensor_level      model_result NOT NULL,   -- 2단계: normal/abnormal (dust_value ≷ T_dust)
    static_model_result   model_result NOT NULL,   -- 정적분진 비전
    dynamic_model_result  model_result NOT NULL,   -- 동적분진 비전
    final_decision        final_level  NOT NULL,   -- normal/caution/warning/danger
    description           TEXT,
    UNIQUE (iot_sensor_level, static_model_result, dynamic_model_result)
);

-- classification_threshold: 분류 임계값(웹UI 편집).  PoolerTran(생산자)이 이 값을 읽어
-- dust_value/score 를 2단계(normal/abnormal)로 분류한다.  admin UI(9107)에서 변경.
CREATE TABLE IF NOT EXISTS classification_threshold (
    key        VARCHAR(20)      PRIMARY KEY,        -- 'dust' | 'static' | 'dynamic'
    threshold  DOUBLE PRECISION NOT NULL,           -- value > threshold ? abnormal : normal
    updated_at TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

-- transfer_dlq: 생산자(PoolerTran) 포이즌 메시지 격리.  REST/INSERT 가 PT_MAX_ATTEMPTS
-- 를 초과한 큐 행을 여기로 이동(원본 gateway_db 큐 행은 DELETE).  decision_db 단독
-- 기록 원칙에 따라 DLQ 도 decision_db 에 둔다(기존 gateway_db transfer_dlq 대체).
CREATE TABLE IF NOT EXISTS transfer_dlq (
    frame_id         BIGINT      PRIMARY KEY,       -- cctv_frame.id
    dead_lettered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts         INTEGER     NOT NULL,
    last_error       TEXT,
    source_row       JSONB
);


-- decision_record: one row per observation (plan doc §4.1)
CREATE TABLE IF NOT EXISTS decision_record (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    station_id                  VARCHAR(50)      NOT NULL,
    observation_timestamp       TIMESTAMPTZ      NOT NULL,

    anomaly_detection_result    channel_result DEFAULT 'pending',
    anomaly_detection_at        TIMESTAMPTZ,
    object_detection_result     channel_result DEFAULT 'pending',
    object_detection_at         TIMESTAMPTZ,
    sensor_analysis_result      channel_result DEFAULT 'pending',
    sensor_analysis_at          TIMESTAMPTZ,

    final_decision              decision_result  DEFAULT 'pending',
    decided_at                  TIMESTAMPTZ,
    mapping_id                  INTEGER REFERENCES alarm_mapping(id),

    -- 원천 dust_inspection 연결(대표 측정행 = waypoint 배치의 dust 최댓값 측정).
    -- egress 가 이 값으로 gateway_db.dust_inspection.id 와 정확 1:1 조인 → LOAS 행 24컬럼.
    -- UNIQUE → 생산자(PoolerTran) INSERT 멱등(ON CONFLICT (dust_id) DO NOTHING).
    dust_id                     BIGINT UNIQUE,

    -- 생산자(PoolerTran)가 기록하는 REST 결과 원본(점수+이미지경로) — 감사/재보정용.
    -- 형식: [[score,p1,p2](정적), [score,p1,p2](동적)]
    result_payload              JSONB,

    -- LOAS image_data 로 그대로 INSERT 할 결과 이미지(정적 p1)의 Base64.
    -- PoolerTran 이 파일을 읽어 인코딩해 둔다 → egress 는 경로/파일접근 없이 직접 INSERT.
    image_b64                   TEXT,

    sent_at                     TIMESTAMPTZ,

    created_at                  TIMESTAMPTZ DEFAULT NOW()
);


-- =====================================================================
-- Indexes
-- =====================================================================
CREATE INDEX IF NOT EXISTS idx_decision_pending
    ON decision_record (final_decision)
    WHERE final_decision = 'pending';

CREATE INDEX IF NOT EXISTS idx_decision_unsent
    ON decision_record (decided_at)
    WHERE sent_at IS NULL AND final_decision <> 'pending';

CREATE INDEX IF NOT EXISTS idx_decision_station_time
    ON decision_record (station_id, observation_timestamp);


-- =====================================================================
-- Role grants  (plan doc §4.2)
-- =====================================================================

-- Common: connect + schema usage for every role.
GRANT CONNECT ON DATABASE decision_db TO
    anomaly_detector_role,
    object_detector_role,
    sensor_analysis_role,
    decision_agent_role,
    egress_role;

GRANT USAGE ON SCHEMA public TO
    anomaly_detector_role,
    object_detector_role,
    sensor_analysis_role,
    decision_agent_role,
    egress_role;

-- All consumer roles need to read role_mapping/alarm_mapping at minimum
-- (they don't strictly need this, but Decision Agent does — kept in one place).
-- Decision Agent's admin page also edits these tables; UPDATE granted here.
GRANT SELECT, UPDATE ON role_mapping  TO decision_agent_role;
GRANT SELECT, UPDATE ON alarm_mapping TO decision_agent_role;

-- Consumer roles: UPDATE only their own component column pair on decision_record.
-- They also need SELECT on the row to find the matching id (typically by
-- station_id + observation_timestamp). UPDATE alone without SELECT works
-- only when the WHERE clause is by primary key already known to the caller.
GRANT SELECT ON decision_record TO
    anomaly_detector_role,
    object_detector_role,
    sensor_analysis_role,
    decision_agent_role,
    egress_role;

-- Consumers can also INSERT new observation rows (the consumer that creates
-- the row first will use INSERT ... ON CONFLICT DO NOTHING; downstream
-- consumers UPDATE their column).
GRANT INSERT ON decision_record TO
    anomaly_detector_role,
    object_detector_role,
    sensor_analysis_role;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO
    anomaly_detector_role,
    object_detector_role,
    sensor_analysis_role,
    decision_agent_role;

GRANT UPDATE (anomaly_detection_result, anomaly_detection_at)
    ON decision_record TO anomaly_detector_role;

GRANT UPDATE (object_detection_result, object_detection_at)
    ON decision_record TO object_detector_role;

GRANT UPDATE (sensor_analysis_result, sensor_analysis_at)
    ON decision_record TO sensor_analysis_role;

-- Decision Agent: read all, write only the verdict columns.
GRANT UPDATE (final_decision, decided_at, mapping_id)
    ON decision_record TO decision_agent_role;

-- 생산자(PoolerTran)는 detector 롤을 재사용해 decision_record 를 INSERT 한다
-- (위 테이블 단위 INSERT 권한으로 3채널 + result_payload 를 한 번에 기록).
-- 분류 임계값(classification_threshold)은 읽어야 하므로 SELECT 부여.
GRANT SELECT ON classification_threshold TO
    anomaly_detector_role,
    object_detector_role,
    sensor_analysis_role,
    decision_agent_role;
-- admin UI(9107)에서 임계 편집 → decision_agent_role 만 UPDATE.
GRANT UPDATE ON classification_threshold TO decision_agent_role;

-- transfer_dlq: 생산자(PoolerTran, detector 롤)가 포이즌 메시지 격리(INSERT/UPDATE 멱등).
GRANT SELECT, INSERT, UPDATE ON transfer_dlq TO
    anomaly_detector_role,
    object_detector_role,
    sensor_analysis_role,
    decision_agent_role;

-- Egress: read all, mark sent.
GRANT UPDATE (sent_at) ON decision_record TO egress_role;
