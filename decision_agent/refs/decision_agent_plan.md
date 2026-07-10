# Decision Agent 구현 계획

> 작성일: 2026-05-04
> 선행 문서: `gateway_plan.md`, `data_ingestion_architecture.md`

---

## 1. 역할 요약

Decision Agent는 세 분석 채널(정적 분진 탐지 모델, 동적 분진 탐지 모델, IoT 센서)의 결과를 수집하고, 알람 매핑 테이블을 적용하여 최종 판정(정상/주의/경고)을 산출한다. 핵심은 **판정 DB 스키마**와 **Egress Gateway가 polling할 수 있는 인터페이스**이다.

---

## 2. 역할 매핑 테이블

현재 분석 컴포넌트는 이상감지 모듈과 객체감지 모듈이지만, 이 중 어느 것이 정적 분진 탐지/동적 분진 탐지 역할을 맡을지는 확정되지 않았다. 한쪽이 두 역할을 모두 수행할 수도 있고, 향후 별도 로직이 추가될 수도 있다. 이 매핑을 DB 테이블로 관리하여 코드 변경 없이 역할 배정을 전환할 수 있도록 한다.

### 2.1 role_mapping 테이블

```sql
CREATE TABLE role_mapping (
    id              SERIAL PRIMARY KEY,
    detection_role  VARCHAR(30) NOT NULL,   -- 'static_dust' / 'dynamic_dust' / 'iot_sensor'
    component_name  VARCHAR(50) NOT NULL,   -- 'anomaly_detection' / 'object_detection' / 'sensor_analysis'
    description     TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (detection_role)
);

-- 초기 설정 예시 (확정 시 변경)
INSERT INTO role_mapping (detection_role, component_name, description) VALUES
    ('static_dust',   'anomaly_detection',  '이상감지 모듈 → 정적 분진 탐지 역할'),
    ('dynamic_dust',  'object_detection',   '객체감지 모듈 → 동적 분진 탐지 역할'),
    ('iot_sensor',    'sensor_analysis',    '데이터분석 모듈 → IoT 센서 역할');
```

컴포넌트 → 역할 배정이 변경되면 이 테이블의 `component_name`만 UPDATE하면 된다. Decision Agent는 기동 시 또는 주기적으로 이 테이블을 읽어 현재 역할 매핑을 캐시한다.

---

## 3. 알람 매핑 테이블

IoT 센서는 3단계(정상/주의/경고), 정적·동적 분진 탐지 모델은 각 2단계(정상/이상)로, 총 3 x 2 x 2 = **12가지 조합**이다.

### 3.1 alarm_mapping 테이블

```sql
CREATE TYPE sensor_level AS ENUM ('normal', 'caution', 'warning');
CREATE TYPE model_result AS ENUM ('normal', 'abnormal');
CREATE TYPE final_level AS ENUM ('normal', 'caution', 'warning');

CREATE TABLE alarm_mapping (
    id                    SERIAL PRIMARY KEY,
    iot_sensor_level      sensor_level NOT NULL,
    static_model_result   model_result NOT NULL,
    dynamic_model_result  model_result NOT NULL,
    final_decision        final_level NOT NULL,
    description           TEXT,
    UNIQUE (iot_sensor_level, static_model_result, dynamic_model_result)
);
```

### 3.2 초기 데이터 (12개 조합)

```sql
INSERT INTO alarm_mapping (iot_sensor_level, static_model_result, dynamic_model_result, final_decision, description) VALUES
-- IoT 센서: 정상
('normal', 'normal',   'normal',   'normal',  '센서값과 비전 모델 모두 정상 반응'),
('normal', 'normal',   'abnormal', 'caution', '센서값은 정상 판정. 동적 분진 탐지 비전 모델은 이상 판정. 누출이 아닌데 센서값이 오탐인 경우(외부 환경 변화로 인한 IoT 센서값 변화) vs 실제 누출이나 비전 모델 탐지 실패'),
('normal', 'abnormal', 'normal',   'caution', '센서값은 이상 판정. 정적 분진 탐지 비전 모델은 이상 판정. 실제 누출이 되어 쌓였는데 센서값 오탐(기류 등의 약해 탓)으로'),
('normal', 'abnormal', 'abnormal', 'caution', '센서값은 정상 판정. 비전 모델은 모두 이상 판정. 실제 누출 중이고 누출도 쌓였는데 센서값이 오탐(관측 범위 밖에서 분진 누출) vs 누출된 것이 아닌데 비전 모델이 오탐'),

-- IoT 센서: 주의
('caution', 'normal',   'normal',   'caution', '센서값이 이상 판정. 비전 모델은 모두 정상 판정. 누출이 없는데 센서값이 오탐(외부 환경의 변화로 센서값 변화) vs 실제 누출인데 비전 모델이 탐지 실패'),
('caution', 'normal',   'abnormal', 'warning', '센서값과 비전 모델 모두 비정상 탐지. 실시간 미량 누출 가능성 높음'),
('caution', 'abnormal',  'normal',   'caution', '센서값은 이상 판정. 정적 분진 모델 이상 판정. 누출 진행이 끝나고 흔적이 남아 있을 가능성 있음'),
('caution', 'abnormal',  'abnormal', 'warning', '센서값과 모든 모델들이 이상 판정. 실시간 누출이 미량으로 상당히 오래 지속되었을 가능성'),

-- IoT 센서: 경고
('warning', 'normal',   'normal',   'warning', '센서값은 강한 이상 판정. 비전 모델은 모두 정상 판정. 누출이 없는데 센서값이 오탐(외부 환경 또는 이물질의 팬으로 직접 강하게 유입 가능성) vs 실제 대량 누출인데 비전 모델의 탐지 실패'),
('warning', 'normal',   'abnormal', 'warning', '센서값이 강한 이상 판정. 동적 분진 탐지 모델도 이상. 실시간 대량 누출 가능성 높음'),
('warning', 'abnormal',  'normal',   'warning', '센서값이 강한 이상 판정. 정적 분진 탐지 모델도 이상 판정. 대량 누출이 진행되고 끝났을 가능성 높음'),
('warning', 'abnormal',  'abnormal', 'warning', '센서값이 강한 이상 판정. 모든 모델들이 이상 판정. 실시간/대량 누출 진행중일 가능성이 높음');
```

매핑 테이블을 DB화하면 판정 로직 변경 시 코드 수정 없이 테이블 데이터만 UPDATE하면 된다.

---

## 4. 판정 DB 스키마

### 4.1 판정 입력/결과 테이블

```sql
CREATE TYPE component_result AS ENUM ('normal', 'abnormal', 'pending');
CREATE TYPE decision_result AS ENUM ('normal', 'caution', 'warning', 'pending');

CREATE TABLE decision_record (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    station_id              VARCHAR(50) NOT NULL,
    observation_timestamp   TIMESTAMPTZ NOT NULL,

    -- 각 컨슈머가 자기 컬럼만 UPDATE
    anomaly_detection_result    component_result DEFAULT 'pending',
    anomaly_detection_at        TIMESTAMPTZ,
    object_detection_result     component_result DEFAULT 'pending',
    object_detection_at         TIMESTAMPTZ,
    sensor_analysis_result      component_result DEFAULT 'pending',
    sensor_analysis_at          TIMESTAMPTZ,

    -- Decision Agent가 작성
    final_decision          decision_result DEFAULT 'pending',
    decided_at              TIMESTAMPTZ,
    mapping_id              INTEGER REFERENCES alarm_mapping(id),

    -- Egress Gateway가 작성
    sent_at                 TIMESTAMPTZ,

    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_decision_pending ON decision_record (final_decision) WHERE final_decision = 'pending';
CREATE INDEX idx_decision_unsent ON decision_record (sent_at) WHERE sent_at IS NULL AND final_decision != 'pending';
```

### 4.2 접근 권한 설계

| 주체 | 접근 범위 |
|---|---|
| 이상감지 모듈 | `anomaly_detection_result`, `anomaly_detection_at` UPDATE만 |
| 객체감지 모듈 | `object_detection_result`, `object_detection_at` UPDATE만 |
| 데이터분석 모듈 | `sensor_analysis_result`, `sensor_analysis_at` UPDATE만 |
| Decision Agent | 전체 SELECT, `final_decision`, `decided_at`, `mapping_id` UPDATE |
| Egress Gateway | `final_decision != 'pending' AND sent_at IS NULL` 조건 SELECT, `sent_at` UPDATE만 |

---

## 5. Decision Agent 내부 구조

```
decision-agent/
├── Dockerfile
├── init_db.sql              # 판정 DB DDL (role_mapping, alarm_mapping, decision_record)
├── seed_mapping.sql         # alarm_mapping 초기 12개 조합 INSERT
├── src/
│   ├── main.py              # 엔트리포인트, 스케줄러 기동
│   ├── poller.py            # decision_record에서 미판정 건 조회
│   ├── role_resolver.py     # role_mapping 테이블 조회, 컴포넌트 → 역할 변환
│   ├── judge.py             # alarm_mapping 테이블 조회, 3채널 결과 → 최종 판정 산출
│   ├── writer.py            # final_decision, decided_at, mapping_id UPDATE
│   └── config.py            # DB 접속 정보, polling 주기 등 환경변수
└── docker-compose.yml       # 판정 DB PostgreSQL 컨테이너 포함
```

### 5.1 판정 로직 흐름

1. `poller.py` — `decision_record`에서 세 컬럼이 모두 `pending`이 아니고 `final_decision`이 `pending`인 건 조회
2. `role_resolver.py` — `role_mapping` 테이블을 참조하여 각 컴포넌트 결과를 정적/동적/센서 역할에 매핑
3. `judge.py` — 매핑된 결과를 `alarm_mapping` 테이블에 대입하여 `final_decision` 산출
4. `writer.py` — `decision_record`에 `final_decision`, `decided_at`, `mapping_id` UPDATE

### 5.2 부분 도착 처리

세 컬럼이 모두 채워져야 판정을 내린다. 일부만 도착한 건은 다음 polling까지 대기한다. 특정 모듈이 장시간 결과를 보내지 않는 경우를 대비해, `observation_timestamp` 기준 타임아웃(예: 10분)을 설정하고 타임아웃 초과 시 해당 채널을 `pending` 상태로 둔 채 나머지 채널 결과만으로 판정하거나, 별도 알람을 발생시키는 정책을 추후 정의한다.

---

## 6. Egress Gateway 인터페이스

Egress Gateway는 판정 DB를 polling한다. Decision Agent가 별도 API를 제공하지 않는다.

### 6.1 Egress polling 쿼리

```sql
SELECT id, station_id, observation_timestamp, final_decision, decided_at
FROM decision_record
WHERE final_decision != 'pending'
  AND sent_at IS NULL
ORDER BY decided_at ASC
LIMIT 100;
```

### 6.2 Egress 송신 완료 후

```sql
UPDATE decision_record SET sent_at = NOW() WHERE id = :id;
```

---

## 7. DDL 소유권 및 dev 환경

### 7.1 DDL 소유권

판정 DB의 `init_db.sql`은 Decision Agent 디렉토리에서 관리한다. 현재 Egress Gateway가 먼저 개발 중이므로 DDL 초안은 Egress 개발 과정에서 작성하되, Decision Agent 개발 착수 시 해당 디렉토리로 이관한다. 이후 스키마 변경은 Decision Agent에서 관리한다.

### 7.2 Egress 단독 dev 환경

Decision Agent가 아직 없는 상태에서 Egress를 개발할 때는, `init_db.sql`로 판정 DB 컨테이너를 띄운 뒤 테스트용 시드 스크립트(`seed_test_decisions.sql`)로 `final_decision`이 채워져 있고 `sent_at`이 NULL인 더미 레코드를 넣어서 개발한다.

```sql
-- seed_test_decisions.sql (Egress dev 전용)
INSERT INTO decision_record (station_id, observation_timestamp, 
    anomaly_detection_result, object_detection_result, sensor_analysis_result,
    final_decision, decided_at)
VALUES
    ('ST-001', NOW() - INTERVAL '5 minutes', 'normal', 'normal', 'normal', 'normal', NOW() - INTERVAL '4 minutes'),
    ('ST-002', NOW() - INTERVAL '3 minutes', 'abnormal', 'normal', 'abnormal', 'warning', NOW() - INTERVAL '2 minutes'),
    ('ST-003', NOW() - INTERVAL '1 minute', 'normal', 'abnormal', 'normal', 'caution', NOW() - INTERVAL '30 seconds');
```

---

## 8. 구현 체크리스트

> 진행 현황: 2026-05-04 v1 구현 완료. 상세 변경 내역은 `refs/letters/2026-05-04_socketdaim_decision_db_migration.md` 참조.

- [x] `init_db.sql` DDL 작성 (role_mapping, alarm_mapping, decision_record + 5개 role + GRANT)
  - 채널 컬럼은 plan §4.1의 `component_result` 대신 `channel_result`(normal/abnormal/caution/warning/pending) 통합 enum 사용 — §3.1과의 type 정합을 위한 deviation
- [x] `seed_mapping.sql` 작성 (alarm_mapping 12개 조합 + role_mapping 초기 3행)
- [x] 판정 DB PostgreSQL 컨테이너 docker-compose 구성
  - postgres-decision 컨테이너는 SocketDaim/docker-compose.yml의 것을 재사용. c:\decision_agent\docker-compose.yml은 decision-agent 서비스만 정의하고 `gw-net` external network 공유
- [x] role_resolver 구현 (role_mapping 조회 + 캐시 + `DA_ROLE_REFRESH_SEC` 주기 refresh)
- [x] judge 구현 (alarm_mapping 조회 → 최종 판정 산출, 12행 캐시 + KeyError on miss)
- [x] poller + writer 구현 (미판정 건 조회 → 판정 → UPDATE, race-safe `WHERE final_decision='pending'` guard)
- [x] 단위 테스트 (12가지 매핑 조합 전수 검증 + negative cases)
- [x] Egress dev용 시드 스크립트 작성 (`seed_test_decisions.sql`, 4 시나리오)
- [x] 통합 테스트 (컨슈머 결과 INSERT → Decision Agent 판정 → DB UPDATE 검증, 12 시나리오 전수 + 부분 도착 스킵 + 중복 판정 방지) — 19/19 pytest 통과

### 8.1 SocketDaim 측 정합화 (수정 요청 letter 발송 완료)

- [x] `SocketDaim/decision_db/init_db.sql`, `seed_dev_decisions.sql` DEPRECATED no-op 처리
- [x] `SocketDaim/docker-compose.yml` postgres-decision 볼륨 마운트를 `../decision_agent/`로 변경
- [x] `SocketDaim/egress_gateway/repository/decision_repo.py` SELECT 쿼리/테이블명/컬럼명/WHERE 갱신
- [x] `SocketDaim/egress_gateway/sender.py` final_decision 매핑 `'경고'` → `'warning'`

### 8.2 v2 보류 항목

- [ ] 부분 도착(3채널 중 일부 미수신) 타임아웃 정책 (§5.2 "추후 정의") — v1은 운영자 수동 force-decide(어드민 페이지)로 대체 가능. 자동 타임아웃은 v2에서 정책 확정 후
- [ ] LOAS 호환성 확인 — payload `final_decision` 필드 값이 한국어→영문으로 바뀜에 따른 수신측 영향 검토 (SocketDaim 회신 letter §4 참조). LOAS 스펙 확보 시점에 진행
- [ ] 어드민 페이지 구현 — 캐시 수동 리로드/매핑 편집/decision_record 모니터/stuck 강제 판정. 별도 plan: [admin_page_plan.md](./admin_page_plan.md)

### 8.3 보류 → 폐기

자동 핫 리로드, Postgres role 분리 운영용 배포, Decision Agent 자체 모니터링은 의도적으로 제외한다.

- 핫 리로드: 어드민 페이지의 수동 리로드 버튼으로 대체
- Role 분리 운영: dev/prod를 매번 정리·재구성하는 워크플로 전제로 단일 dev 패스워드 유지 (운영 직전 단계에서 재검토)
- 자체 모니터링: 운영 단계에서 별도 모니터링 시스템(외부 도구) 도입 시 다시 논의
