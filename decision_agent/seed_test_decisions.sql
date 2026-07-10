-- =====================================================================
-- Egress dev-only seed for decision_record
-- =====================================================================
-- 채널 3개 모두 채워지고 final_decision까지 산출된 더미 record.
-- sent_at IS NULL → Egress polling 대상.
-- station_id는 임의 문자열 (decision DB는 cross-DB FK 없음).
-- =====================================================================

INSERT INTO decision_record (
    station_id, observation_timestamp,
    anomaly_detection_result, anomaly_detection_at,
    object_detection_result,  object_detection_at,
    sensor_analysis_result,   sensor_analysis_at,
    final_decision, decided_at, mapping_id
)
SELECT
    v.station_id,
    v.observation_ts,
    v.anomaly_result,    v.observation_ts + INTERVAL '5 seconds',
    v.object_result,     v.observation_ts + INTERVAL '6 seconds',
    v.sensor_result,     v.observation_ts + INTERVAL '7 seconds',
    v.final_decision,    v.observation_ts + INTERVAL '10 seconds',
    am.id
FROM (VALUES
    ('ST-001', NOW() - INTERVAL '5 minutes',
        'normal'::channel_result, 'normal'::channel_result, 'normal'::channel_result,
        'normal'::decision_result,
        'normal'::model_result, 'normal'::model_result, 'normal'::model_result),

    ('ST-002', NOW() - INTERVAL '3 minutes',
        'abnormal'::channel_result, 'normal'::channel_result, 'caution'::channel_result,
        'caution'::decision_result,
        'abnormal'::model_result, 'abnormal'::model_result, 'normal'::model_result),

    ('ST-003', NOW() - INTERVAL '1 minute',
        'normal'::channel_result, 'abnormal'::channel_result, 'normal'::channel_result,
        'caution'::decision_result,
        'normal'::model_result, 'normal'::model_result, 'abnormal'::model_result),

    ('ST-004', NOW() - INTERVAL '30 seconds',
        'abnormal'::channel_result, 'abnormal'::channel_result, 'warning'::channel_result,
        'warning'::decision_result,
        'abnormal'::model_result, 'abnormal'::model_result, 'abnormal'::model_result)
) AS v(
    station_id, observation_ts,
    anomaly_result, object_result, sensor_result,
    final_decision,
    iot_lvl, static_lvl, dynamic_lvl
)
JOIN alarm_mapping am
  ON am.iot_sensor_level     = v.iot_lvl
 AND am.static_model_result  = v.static_lvl
 AND am.dynamic_model_result = v.dynamic_lvl;
