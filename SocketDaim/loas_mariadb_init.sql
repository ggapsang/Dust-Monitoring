-- =====================================================================
-- LOAS mock MariaDB 초기화 (로컬 테스트 전용 — loas-mariadb 서비스)
-- =====================================================================
-- 실제 LOAS 측 tfoi_web_db_v1.t_inspection 대용.  egress_gateway 가 INSERT 한다.
-- 컬럼/타입은 명세서 v0.1 "3.데이터 매핑정보 / 4. Sample Query" 기준(27 데이터 컬럼).
-- ⚠️ 실제 LOAS 테이블에는 FK(plant_id/target_index 등)가 있으나, 로컬에서는 INSERT
--    검증 편의를 위해 FK 없이 생성한다.
-- MARIADB_DATABASE=tfoi_web_db_v1 에 대해 실행됨.
-- =====================================================================

CREATE TABLE IF NOT EXISTS t_inspection (
    id                  BIGINT       AUTO_INCREMENT PRIMARY KEY,
    inspection_datetime DATETIME(6),
    event_id            INT,
    sensor_type         INT,
    sensor_index        INT,
    target_index        INT,
    waypoint_x          FLOAT,
    waypoint_y          FLOAT,
    waypoint_z          FLOAT,
    location_id         INT,
    map_id              INT,
    navigation_id       INT,
    exec_id             BIGINT,
    plant_id            INT,
    target_id           INT,
    ugv_id              INT,
    waypoint_id         INT,
    inspection_loacl_id BIGINT,
    inspection_pan      INT,
    inspection_tilt     INT,
    inspection_lift     INT,
    object_id           INT,
    rot_x               FLOAT,
    rot_y               FLOAT,
    rot_z               FLOAT,
    rot_w               FLOAT,
    inspection_value    VARCHAR(255),
    image_data          LONGBLOB
);

-- 접근 계정: daimresearch / daimresearch1234! — 전체 권한.
CREATE USER IF NOT EXISTS 'daimresearch'@'%' IDENTIFIED BY 'daimresearch1234!';
GRANT ALL PRIVILEGES ON *.* TO 'daimresearch'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
