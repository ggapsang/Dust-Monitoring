# SocketDaim 측 수정 요청: 판정 DB 스키마 이관 및 Egress 정합화

> **From:** Decision Agent 팀 (c:\decision_agent\)
> **To:** SocketDaim 팀 (C:\SocketDaim\)
> **Date:** 2026-05-04
> **관련 문서:** [decision_agent_plan.md](../decision_agent_plan.md) §4, §7

---

## TL;DR

Decision Agent를 신규 도입하면서 판정 DB 스키마를 plan 문서 §4 스펙대로 재정립했습니다. 그에 따라 **SocketDaim 레포의 4개 파일**이 수정되어야 하며, 다음 사항을 확인 부탁드립니다:

1. `decision_db/init_db.sql`, `decision_db/seed_dev_decisions.sql` — DEPRECATED 처리 (no-op으로 대체됨)
2. `docker-compose.yml` — `postgres-decision` 서비스의 볼륨 마운트 경로 변경
3. `egress_gateway/repository/decision_repo.py` — 새 컬럼명/enum/테이블명에 맞춰 SELECT 쿼리 갱신
4. `egress_gateway/sender.py` — `final_decision` 한글→영문 매핑 변경 (`'경고'` → `'warning'`)

본 작업은 **이미 c:\decision_agent\에서 수정을 적용해두었습니다**. 변경 내용을 검토하시고 의도에 맞지 않으면 알려주세요.

---

## 배경

기존 `SocketDaim/decision_db/init_db.sql`은 plan 문서 §4 스펙과 어긋나 있었습니다:

| 항목 | 기존 (SocketDaim) | 신규 (plan 문서 §4) |
|---|---|---|
| 테이블명 | `decision` | `decision_record` |
| ENUM 값 | 한국어 (`'정상'/'이상'/'미수신'`, `'정상'/'주의'/'경고'/'미판정'`) | 영문 (`normal/abnormal/pending`, `normal/caution/warning/pending`) |
| 채널 컬럼명 | `static_model_result` / `dynamic_model_result` / `sensor_result` | `anomaly_detection_result` / `object_detection_result` / `sensor_analysis_result` |
| 매핑 테이블 | 없음 | `alarm_mapping` (12행) + `role_mapping` (3행) |
| FK | 없음 | `decision_record.mapping_id → alarm_mapping(id)` |
| pending 표현 | NULL 컬럼 | ENUM 값 `'pending'` |

또한 plan 문서 §7.1은 **DDL 소유권을 Decision Agent로 이관**할 것을 명시합니다. 따라서 init/seed 파일은 c:\decision_agent\로 옮기고, SocketDaim의 `postgres-decision` 컨테이너는 그쪽을 bind-mount합니다.

---

## Plan 문서와의 deviation 1건 (사전 공유)

`init_db.sql`에서 plan 문서 §4.1의 `component_result`(normal/abnormal/pending) 대신 **`channel_result`(normal/abnormal/caution/warning/pending) 통합 enum**을 도입했습니다.

- **이유:** plan 문서 §3.1의 `alarm_mapping.iot_sensor_level`은 `sensor_level`(normal/caution/warning) 3단계인데, §4.1의 `sensor_analysis_result` 컬럼은 `component_result`(normal/abnormal)로 정의되어 있어 IoT 채널이 caution/warning을 표현할 수 없었습니다.
- **선택:** `channel_result` 단일 enum이 모든 채널을 수용하도록 하고, judge가 lookup 시 컬럼별로 적합한 값만 받는 것으로 처리.
- **반영:** `init_db.sql` 상단에 코멘트로 deviation 문서화.

이 deviation에 이견이 있으시면 알려주세요.

---

## 수정 사항 상세

### 1. `decision_db/init_db.sql` — DEPRECATED no-op으로 교체

**왜:** DDL 소유권을 c:\decision_agent\init_db.sql로 이관 (plan 문서 §7.1).
**무엇:** 파일 내용 전체를 `SELECT 1;` + 헤더 코멘트로 교체. 실제 스키마는 c:\decision_agent\init_db.sql 참조.

### 2. `decision_db/seed_dev_decisions.sql` — DEPRECATED no-op으로 교체

**왜:** Egress dev seed도 함께 이관됨. 새 위치는 [c:\decision_agent\seed_test_decisions.sql](../../seed_test_decisions.sql).
**무엇:** 파일 내용 전체를 `SELECT 1;` + 헤더 코멘트로 교체.

### 3. `docker-compose.yml` — `postgres-decision` 볼륨 마운트 경로 변경

**왜:** init/seed 파일 owner가 c:\decision_agent\로 이동. SocketDaim의 컨테이너는 sibling 디렉토리에서 mount.
**전제:** `C:\SocketDaim\` 와 `C:\decision_agent\`가 디스크상 sibling으로 존재.

```diff
   postgres-decision:
     ...
     volumes:
-      - ./decision_db/init_db.sql:/docker-entrypoint-initdb.d/01_init_db.sql:ro
-      - ./decision_db/seed_dev_decisions.sql:/docker-entrypoint-initdb.d/02_seed_dev_decisions.sql:ro
+      # Schema ownership lives in the Decision Agent repo (sibling on disk).
+      - ../decision_agent/init_db.sql:/docker-entrypoint-initdb.d/01_init_db.sql:ro
+      - ../decision_agent/seed_mapping.sql:/docker-entrypoint-initdb.d/02_seed_mapping.sql:ro
+      - ../decision_agent/seed_test_decisions.sql:/docker-entrypoint-initdb.d/03_seed_test_decisions.sql:ro
       - decision-pgdata:/var/lib/postgresql/data
```

**주의: 기존 `decision-pgdata` 볼륨은 구 스키마를 가지고 있습니다.** 새 스키마를 적용하려면 한 번 wipe가 필요합니다:

```bash
cd C:\SocketDaim
docker compose down
docker volume rm socketdaim_decision-pgdata   # 또는: docker compose down -v
docker compose up -d postgres-decision
```

데이터 손실은 dev seed에 한정됩니다(prod 데이터 없음).

### 4. `egress_gateway/repository/decision_repo.py` — SELECT 쿼리 갱신

**왜:** 테이블명 `decision` → `decision_record`, 컬럼명 컴포넌트 기반으로 변경, WHERE `final_decision IS NOT NULL` → `final_decision <> 'pending'`.

**dataclass `DecisionRecord` 필드 의미는 그대로 유지** (sender.py가 사용하는 인터페이스 고정). SELECT의 alias로 컴포넌트 컬럼 → 역할 이름으로 매핑:

```python
SELECT id,
       station_id,
       observation_timestamp        AS timestamp,
       anomaly_detection_result::text AS static_model_result,
       object_detection_result::text  AS dynamic_model_result,
       sensor_analysis_result::text   AS sensor_result,
       final_decision::text         AS final_decision,
       decided_at
  FROM decision_record
 WHERE final_decision <> 'pending'
   AND sent_at IS NULL
 ORDER BY decided_at
 LIMIT $1
```

UPDATE 쿼리도 `decision` → `decision_record` 한 단어 변경:

```python
UPDATE decision_record SET sent_at = NOW() WHERE id = $1 AND sent_at IS NULL
```

추가로 `DecisionRecord.station_id` 타입을 `uuid.UUID` → `str`로 변경했습니다 (새 스키마는 `VARCHAR(50)`). `sender._build_payload`의 `str(rec.station_id)`는 변경 불필요 (str→str도 정상).

**가정한 부분:** 위 alias 매핑은 "anomaly_detection 모듈 → static dust 역할" 이라는 plan 문서 §2.1의 **초기** role_mapping 시드를 가정합니다. role_mapping이 변경되면 Egress의 alias도 따라 바꾸거나, alias를 빼고 컴포넌트 명 그대로 sender에 전달하도록 리팩터링이 필요할 수 있습니다. v1에서는 초기 매핑이 고정이라 가정했지만, **검토가 필요한 지점**입니다.

### 5. `egress_gateway/sender.py` — final_decision 매핑 갱신

**왜:** ENUM 값이 한국어 → 영문으로 변경.

```diff
 def _msg_type_for(final_decision: str) -> MessageType:
-    """Map final_decision → wire message type.
-
-    정상 / 주의 → ANALYSIS_RESULT (0x0100)
-    경고        → ALERT           (0x0101)
-    """
-    if final_decision == "경고":
+    """Map final_decision → wire message type.
+
+    normal / caution → ANALYSIS_RESULT (0x0100)
+    warning          → ALERT           (0x0101)
+    """
+    if final_decision == "warning":
         return MessageType.ALERT
     return MessageType.ANALYSIS_RESULT
```

**LOAS와의 wire format 호환성:** ALERT/ANALYSIS_RESULT 메시지 타입(0x0101 / 0x0100) 자체는 그대로지만, payload JSON의 `final_decision` 필드 값이 한국어→영문으로 바뀝니다. LOAS가 한국어 값을 기대하고 있다면 wire format level 호환성이 깨집니다. **LOAS 스펙 확인 필요.**

---

## 검증 결과

`c:\decision_agent\`에서 수행한 검증 (2026-05-04):

- DB 부팅: postgres:16 컨테이너에서 init+seed 스크립트 모두 성공 — 3 role_mapping / 12 alarm_mapping / 5 roles / 4 test decisions 생성 확인
- 단위+통합 테스트: 19/19 통과
  - 12 매핑 진리표 전수 검증
  - role_resolver 동적 remap 검증
  - 부분 도착 record 스킵 검증
  - 중복 판정 방지 (race-safe) 검증
- 런타임 E2E: Decision Agent 컨테이너 띄움 → pending record INSERT → 2초 내 polling → `warning` (mapping_id=12) 판정 → DB UPDATE 확인

Egress 측 변경은 SocketDaim 환경에서 별도 테스트 필요 (수정사항만 적용해두었고, mock-loas와의 통합 테스트는 SocketDaim 측에서 수행 부탁드립니다).

---

## 결정 필요/확인 요청 항목

1. **`channel_result` enum 도입에 동의?** (plan 문서 deviation, 위 §"Plan 문서와의 deviation" 참조)
2. **postgres-decision 볼륨 wipe 권한** — `decision-pgdata` 볼륨에 다른 의미 있는 데이터가 있는지 확인 부탁
3. **sibling 디렉토리 가정** — `C:\SocketDaim\` ↔ `C:\decision_agent\`가 디스크상 sibling으로 유지될 것이라는 가정으로 docker-compose 마운트 경로를 `../decision_agent/`로 작성. CI/배포 환경에서 이 가정이 깨지면 절대경로 또는 별도 처리 필요
4. **LOAS 호환성** — `final_decision` payload 값이 한국어 → 영문으로 바뀜. LOAS 측에서 영문 enum 값을 받을 수 있는지 확인 필요
5. **Egress alias 매핑 가정** — `decision_repo.py`의 SELECT alias가 plan 문서 §2.1 초기 role_mapping을 가정함. role_mapping 변경 시 Egress 동작에 영향이 있는지 검토 필요

---

## 참조 파일

- 신 스키마 owner: [c:\decision_agent\init_db.sql](../../init_db.sql)
- 시드: [c:\decision_agent\seed_mapping.sql](../../seed_mapping.sql), [c:\decision_agent\seed_test_decisions.sql](../../seed_test_decisions.sql)
- Decision Agent 코드: [c:\decision_agent\src\decision_agent\](../../src/decision_agent/)
- Plan 문서: [decision_agent_plan.md](../decision_agent_plan.md)
