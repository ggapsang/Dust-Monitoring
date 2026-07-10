-- =====================================================================
-- Decision DB seeds: role_mapping (3 rows) + alarm_mapping (12 rows)
-- =====================================================================
-- plan doc §2.1 (initial role assignments) and §3.2 (12 alarm combinations).
-- Idempotent: ON CONFLICT DO NOTHING on the unique keys.
-- =====================================================================


-- ---------------------------------------------------------------------
-- role_mapping
-- ---------------------------------------------------------------------
INSERT INTO role_mapping (detection_role, component_name, description) VALUES
    ('static_dust',  'anomaly_detection',  'anomaly_detection module -> static dust detection role'),
    ('dynamic_dust', 'object_detection',   'object_detection module  -> dynamic dust detection role'),
    ('iot_sensor',   'sensor_analysis',    'sensor_analysis module   -> IoT sensor role')
ON CONFLICT (detection_role) DO NOTHING;


-- ---------------------------------------------------------------------
-- alarm_mapping (8 combinations: 2 sensor × 2 static × 2 dynamic)
-- ---------------------------------------------------------------------
-- 센서 2단계(normal/abnormal).  위험(danger) 규칙(고객 지정):
--   sensor=abnormal AND (static=abnormal OR dynamic=abnormal) → danger.
-- 나머지는 원래 로직 환산: sensor=normal → 원래 iot=normal 블록,
--                          sensor=abnormal(위험 아님) → 원래 iot=warning 블록.
-- (decision_agent_2x2x2_구현계획.md §4)
INSERT INTO alarm_mapping
    (iot_sensor_level, static_model_result, dynamic_model_result, final_decision, description)
VALUES
-- sensor: normal (원래 iot=normal 블록)
('normal',   'normal',   'normal',   'normal',  '센서 정상 + 비전 모두 정상'),
('normal',   'normal',   'abnormal', 'caution', '센서 정상 + 동적 분진 이상 — 비전 단독 탐지(주의)'),
('normal',   'abnormal', 'normal',   'caution', '센서 정상 + 정적 분진 이상 — 비전 단독 탐지(주의)'),
('normal',   'abnormal', 'abnormal', 'caution', '센서 정상 + 비전 모두 이상 — 센서 미확인(주의)'),

-- sensor: abnormal (확산 확인)
('abnormal', 'normal',   'normal',   'warning', '센서 이상(확산) + 비전 정상 — 위험규칙 미충족, 경고'),
('abnormal', 'normal',   'abnormal', 'danger',  '위험: 센서 이상 + 동적 분진 이상'),
('abnormal', 'abnormal', 'normal',   'danger',  '위험: 센서 이상 + 정적 분진 이상'),
('abnormal', 'abnormal', 'abnormal', 'danger',  '위험: 센서 이상 + 정적·동적 분진 모두 이상')
ON CONFLICT (iot_sensor_level, static_model_result, dynamic_model_result) DO NOTHING;


-- ---------------------------------------------------------------------
-- classification_threshold (분류 임계 기본값 — admin UI 에서 변경 가능)
-- ---------------------------------------------------------------------
-- value > threshold ? abnormal : normal.
--  dust(IOT 센서): 기준 임계값 2 (운영 시 admin UI 로 조정).
--  static(정적분진)/dynamic(동적분진): AI score 0~1 가정 → 0.5.
INSERT INTO classification_threshold (key, threshold) VALUES
    ('dust',    2),
    ('static',  0.5),
    ('dynamic', 0.5)
ON CONFLICT (key) DO NOTHING;
