-- =====================================================================
-- SocketDaim – Test Station Seed
-- =====================================================================
-- Mock 센서 송신기가 기동 시 `station_name → station_id(UUID)` 를 조회할
-- 대상 4개 테스트 개소를 등록한다.
--
-- 실행 시점:
--   최초 `docker compose up` 시 PostgreSQL이
--   /docker-entrypoint-initdb.d/ 내 파일을 알파벳순으로 실행하면서
--   `init_db.sql` 다음에 자동 실행됨.
--
-- Idempotent: 같은 station_name 이 이미 존재하면 INSERT 스킵.
-- =====================================================================

INSERT INTO station (station_name, location_info, capture_cycle, status)
SELECT 'FL-A01-NORTH', 'Fab A line 1, north sector', 60, 'collecting'
WHERE NOT EXISTS (SELECT 1 FROM station WHERE station_name = 'FL-A01-NORTH');

INSERT INTO station (station_name, location_info, capture_cycle, status)
SELECT 'FL-A02-SOUTH', 'Fab A line 2, south sector', 60, 'collecting'
WHERE NOT EXISTS (SELECT 1 FROM station WHERE station_name = 'FL-A02-SOUTH');

INSERT INTO station (station_name, location_info, capture_cycle, status)
SELECT 'FL-B01-EAST', 'Fab B line 1, east sector', 60, 'collecting'
WHERE NOT EXISTS (SELECT 1 FROM station WHERE station_name = 'FL-B01-EAST');

INSERT INTO station (station_name, location_info, capture_cycle, status)
SELECT 'FL-C01-WEST', 'Fab C line 1, west sector', 60, 'collecting'
WHERE NOT EXISTS (SELECT 1 FROM station WHERE station_name = 'FL-C01-WEST');
