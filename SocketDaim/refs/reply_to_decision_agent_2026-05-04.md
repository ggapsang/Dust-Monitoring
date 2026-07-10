# Re: 판정 DB 스키마 이관 및 Egress 정합화 — 적용 완료 + 회신

> **From:** SocketDaim 팀 (C:\SocketDaim\)
> **To:** Decision Agent 팀 (C:\decision_agent\)
> **Date:** 2026-05-04
> **In reply to:** *SocketDaim 측 수정 요청: 판정 DB 스키마 이관 및 Egress 정합화* (2026-05-04)

---

## TL;DR

요청하신 4개 파일 변경 모두 적용했고 **E2E 검증까지 통과**했습니다 (4 record 송신 → mock-loas 수신 → `sent_at` 마킹). 추가로 명세서 1개를 함께 갱신했습니다(아래 §"추가 변경" 참조). 결정 필요/확인 요청 5건은 항목별로 답변드립니다.

---

## 적용 결과

| 파일 | 상태 | 비고 |
|---|---|---|
| `decision_db/init_db.sql` | ✅ no-op (DEPRECATED) | 그대로 보관 |
| `decision_db/seed_dev_decisions.sql` | ✅ no-op (DEPRECATED) | 그대로 보관 |
| `docker-compose.yml` | ✅ `../decision_agent/` 3-파일 마운트 적용 | `init`/`seed_mapping`/`seed_test_decisions` 순서로 `01/02/03_` 접두 |
| `egress_gateway/repository/decision_repo.py` | ✅ alias-기반 SELECT + `final_decision <> 'pending'` + `decision_record` 테이블명 | `station_id: str` 변경도 반영 |
| `egress_gateway/sender.py` | ✅ `'경고' → 'warning'` 매핑 | |

### 검증 결과 (2026-05-04 SocketDaim 환경)

```
docker compose down (postgres-decision, egress-gw)
docker volume rm socketdaim_decision-pgdata socketdaim_egress-data
docker compose up -d --build postgres-decision egress-gw
```

- 신 스키마 적용: `\dt` → `alarm_mapping`, `decision_record`, `role_mapping` 3개 테이블 확인
- seed 자동 적재: `role_mapping=3`, `alarm_mapping=12`, `decision_record=4`
- Egress 송신 결과:
  - `normal` 1건 → `0x0100 ANALYSIS_RESULT`
  - `caution` 2건 → `0x0100 ANALYSIS_RESULT`
  - `warning` 1건 → `0x0101 ALERT`
- mock-loas 수신 로그에 `decision_id` / `station_id`(`ST-001`~`ST-004`) / `final_decision`(영문) 모두 정상 출력
- `decision_record.sent_at` 4행 모두 마킹 (`SELECT ... GROUP BY final_decision` 으로 확인)

---

## 추가 변경 (자체 판단으로 적용)

### `refs/gw_protocol_spec.md` §4.3a 갱신

LOAS-side 개발자/Mock 송신기 개발자가 참조하는 와이어 명세 문서입니다. 본 변경에 의해 페이로드 값이 바뀌었으므로 함께 갱신했습니다.

- 테이블명 `decision` → `decision_record`
- ENUM 값 한국어 → 영문 (`normal`/`caution`/`warning`/`pending`)
- 컴포넌트 컬럼명 alias 처리 명시 (예: `static_model_result` ← `anomaly_detection_result`)
- `station_id` 타입 명시 변경 (UUID → `VARCHAR(50)`, "UUID 또는 임의 식별자 문자열")
- alias 매핑이 plan 문서 §2.1 초기 role_mapping 가정에 의존함을 한 줄 경고로 추가
- 변경 이력 v0.3 기록

---

## 결정 필요/확인 요청 항목별 회신

### 1. `channel_result` enum 도입 — **동의**

`component_result`로는 IoT 채널의 `caution`/`warning`을 수용 불가능한 점, 합리적입니다. Egress 측은 superset enum 값을 string으로 그대로 받아 LOAS에 전달하므로 영향 없음. plan 문서 deviation으로 `init_db.sql` 상단에 코멘트 박아주신 것도 추적성 측면에서 좋습니다.

### 2. postgres-decision 볼륨 wipe — **이미 wipe 완료**

`socketdaim_decision-pgdata` 볼륨에 prod 의미의 데이터 없습니다 (Egress 검증용 시드뿐). 함께 `socketdaim_egress-data`(outbox)도 wipe했습니다 — 구 스키마 시점에 outbox에 잔존했을 가능성 있는 stale row 제거 목적. dev 환경 영향 없음.

### 3. sibling 디렉토리 가정 — **dev 한정 OK, CI/배포는 별도 처리 필요**

현재 두 레포가 `C:\SocketDaim\` ↔ `C:\decision_agent\` sibling으로 존재해서 `../decision_agent/` 상대경로가 동작합니다. 다만 향후 다음 시나리오에서는 깨질 수 있어 사전 인지 부탁드립니다:

- **CI**: 보통 단일 레포만 체크아웃하므로 `../decision_agent/`가 없을 수 있음. SocketDaim CI가 decision_agent 의존 테스트를 돌리려면 (a) submodule, (b) CI 단계에서 sibling으로 명시적 clone, (c) Decision Agent 팀이 init/seed SQL을 OCI 이미지로 패키징해서 배포하는 방식 중 하나가 필요
- **고객사 배포**: `docker-compose.yml`이 그대로 가지 않을 가능성이 높음. 운영 단계에서는 init/seed SQL을 별도 ConfigMap/Secret이나 init 컨테이너로 주입하는 형태 권장

당장은 dev workflow만 영향이라 그대로 두지만, 배포 단계 진입 전에 한 번 같이 정리할 필요가 있습니다.

### 4. LOAS 호환성 (한국어 → 영문 enum) — **확인 필요, 우려 있음**

현재 LOAS 스펙 문서는 우리 측에 없어 검증 불가 상태입니다. Egress→LOAS 와이어 포맷의 `final_decision` 페이로드 값이 한국어(`'경고'`)→영문(`'warning'`)으로 바뀌었으므로:

- LOAS가 한국어 값 기대 시: **수신 측에서 거부될 가능성** → 우리는 `ERROR` 수신 → outbox 누적 → drain 안 됨
- LOAS가 영문 값 기대 시: 문제 없음
- LOAS가 자유 문자열로 받음: 문제 없음 (그러나 후처리에서 의미 매칭 필요)

**임시 안전망 제안**: 운영 전환 전까지는 mock-loas 수신 로그가 진실의 원천. 실 LOAS 연동 단계에서 한국어 기대로 판명되면 두 가지 대응 가능:
1. Egress의 sender.py에서 한국어로 재매핑하는 어댑터 추가 (한국어 표시값 vs 내부 enum 분리)
2. 로아스 코덱(`gw_proto/codec/vendor.py`)에서 메시지별 변환

LOAS 스펙을 확보하시면 공유 부탁드립니다. 그쪽 결정에 따라 1번 또는 2번 어느 쪽이 맞는지 판단하여 후속 PR 올리겠습니다.

### 5. Egress alias 매핑 가정 — **인지, 단기 OK / 중기 리팩터 후보**

현재 `decision_repo.py`의 SELECT alias가 `role_mapping`의 초기 매핑(anomaly→static, object→dynamic, sensor→sensor)을 하드코딩하고 있습니다. role_mapping이 동적으로 바뀌면:
- Egress의 alias는 변경 없이 동작하지만, 페이로드의 의미가 어긋남 (예: "static_model_result" 키에 sensor 채널 결과가 담길 수 있음)
- 즉 wire-level 호환성은 유지되나 의미적 정합성 깨짐

**제안 (지금은 적용 안 함, 향후 검토)**:
- 옵션 A: Egress 페이로드 키를 컴포넌트 이름 그대로(`anomaly_detection_result` 등) 보내고 LOAS 측이 의미를 해석. role 가정 제거
- 옵션 B: Egress가 기동 시 `role_mapping` 테이블을 읽어 alias를 동적으로 구성

옵션 A가 단순하고 결합도가 낮아 선호하지만, LOAS 스펙이 "역할 이름"을 기대한다면 옵션 B가 답입니다. 항목 4 (LOAS 스펙 확인) 결과와 같이 결정하면 좋겠습니다.

당장 v1 운영(role_mapping 고정 가정)에서는 현행으로 충분합니다. role_mapping 변경 PR이 발생하는 시점에 다시 의제로 올려주세요.

---

## 후속 액션 정리

| # | 액션 | 책임 | 우선순위 |
|---|---|---|---|
| 1 | LOAS 스펙 확보 (`final_decision` 값 한/영 + payload 키 명명) | Decision Agent or SocketDaim 팀 | 운영 전환 전 필수 |
| 2 | LOAS가 한국어 기대 시 어댑터 결정 (sender vs vendor codec) | SocketDaim | LOAS 스펙 확정 후 |
| 3 | role_mapping 변경 시 Egress alias 처리 결정 (옵션 A vs B) | 합동 | role_mapping 변경 PR 시점 |
| 4 | CI/배포에서 sibling 디렉토리 가정 대체 방안 설계 | 합동 | 배포 단계 진입 전 |

---

## 참조

- 적용 후 명세 문서: [refs/gw_protocol_spec.md §4.3a](./gw_protocol_spec.md)
- Egress 코드 변경점:
  - [egress_gateway/repository/decision_repo.py](../egress_gateway/repository/decision_repo.py)
  - [egress_gateway/sender.py](../egress_gateway/sender.py)
- Compose 변경점: [docker-compose.yml](../docker-compose.yml) (postgres-decision 서비스 volumes)
