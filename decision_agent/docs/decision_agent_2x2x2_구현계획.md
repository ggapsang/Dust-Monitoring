# decision_agent 통합 — 2×2×2 재설계 & 구현 계획

> 고객 요청: **decision_agent 를 실제로 사용**한다. 이를 위해 두 변경을 반영한다.
> 1) **InferenceModule REST 출력 2분할** — 입력 동일, 출력을 **정적분석/동적분석 2개**로
>    (기존 `(score, image)` 가 2회 반복).
> 2) **센서 dust_value 분류 3단계→2단계**(임계 1개) — alarm_mapping truth table 이
>    **3×2×2(12행) → 2×2×2(8행)** 으로.
>
> 이 문서는 이전 [loas_event_id_설계.md](loas_event_id_설계.md)의 "2입력 우회(매트릭스)" 방식을
> **대체**한다. 이제 dust_value·score 두 신호를 **decision_agent 의 채널로 공급**해 truth table
> 이 판정하게 하고, egress 가 그 결과를 LOAS 로 보낸다.

---

## 0. 핵심 통찰 — 두 변경이 기존 난제를 해결

| 기존 난제 | 이번 변경으로 해소 |
|---|---|
| AI score 1개 → 비전 2채널(anomaly/object)을 어떻게 채우나 | **AI 가 정적/동적 2결과**를 주므로 → **anomaly←정적, object←동적** 으로 **독립 충전** |
| 센서 3단계 레벨링이 항상 normal(범위 좁음) | **2단계(임계 1개)** 로 단순화 + "확산 시 상승" 특성을 임계에 반영 → 의미 있는 2분류 |
| decision_record 생산자 부재 | **PoolerTran 이 생산자**(dust_value + 2 score 모두 보유) |

---

## 1. 새 데이터 흐름

```
SocketDaim ─► gateway_db.dust_inspection (dust_value 등)
                         │
cctv_frame ─► cctv_transfer_queue ─► PoolerTran ──REST(입력 동일)──► InferenceModule
                                          │                              │
                                          │            출력 2개: 정적(score,img) / 동적(score,img)
                                          ▼ (분류)
                  decision_db.decision_record 1행 INSERT:
                     sensor_analysis_result  ← dust_value  ≷ T_dust        → normal/abnormal
                     anomaly_detection_result← 정적 score   ≷ T_static      → normal/abnormal
                     object_detection_result ← 동적 score   ≷ T_dynamic     → normal/abnormal
                                          │
                          decision_agent (8행 truth table) → final_decision
                                          │
                          egress_gateway ─► LOAS MariaDB
                             event_id = map(final_decision),  image_data = 정적/동적 이미지(택1)
```

- **변경 폭이 작다**: decision_agent·egress 는 거의 그대로(스키마/매핑만), **신규는 "PoolerTran 생산자 로직"** + **InferenceModule 출력 contract**.

---

## 2. 모듈별 영향 분석

### 2-1. InferenceModule (다른 작업자 소유 — contract 합의 완료)
- 입력 동일. **출력 = 기존 `(score, path1, path2)` 응답이 2개로 반복되는 배열**:
  ```
  [ (score, path1, path2),    # [0] 정적분석(static)  → anomaly_detection
    (score, path1, path2) ]   # [1] 동적분석(dynamic) → object_detection
  ```
  - 원소당 `score`(float) 1개 + 이미지 경로 2개(`path1`, `path2`).
  - **순서 고정**: index 0 = 정적, index 1 = 동적.
- **InferenceModule 은 수정하지 않으며, 이 형식대로 소비**한다.

### 2-2. PoolerTran (★신규 핵심 — decision_record 생산자, **decision_db 단독 기록**)
- REST 응답 배열 `[(score,p1,p2)정적, (score,p1,p2)동적]` 파싱(현 `_extract_score_image` → 2원소 추출로 교체).
- 입력 신호를 **2단계로 분류**(§3) → `sensor_analysis_result / anomaly_detection_result / object_detection_result`.
- **decision_db 에 decision_record 1행 INSERT**(관측=(amr, waypoint) 단위, §4 granularity) — 채널 3개 + **이미지 경로**(§5.5) 포함.
- **decision_db 접속/롤 추가** — 현재 PoolerTran 은 gateway_db 만 씀. INSERT 권한 롤 필요.
- **gateway_db `transfer_result`(원래 기록처) 는 쓰지 않는다**(이중기록 불필요 — §5.5 분석 결론). 큐 DELETE 의 "결과 확정" 마커가 transfer_result → **decision_record INSERT** 로 이동.
- 실패/포이즌 처리(DLQ)는 **decision_db 측 DLQ** 로 둔다(§5.5).

### 2-3. decision_agent (스키마/매핑 변경, 로직 거의 동일)
- **enum**: `iot_sensor_level` 을 3단계(`sensor_level`)에서 **2단계**로. → **`model_result`(normal/abnormal) 재사용** 권장(별도 enum 불필요).
- **alarm_mapping**: 컬럼 타입 변경(`iot_sensor_level` → model_result) + **8행 재시드**(2×2×2).
- **`decision_record.sensor_analysis_result`**: `channel_result`(superset) 그대로 두되 **값은 normal/abnormal** 만 기록(스키마 변경 불필요).
- **judge.py**: `if len(self._table) != 12` → **8** 로. lookup 키 값이 (normal/abnormal)³ 로 바뀜(로직 동일).
- **final_decision**: 기본 `decision_result`(normal/caution/warning) 유지 → event_id 0/1/2. **위험(3) 필요 시** enum 에 `danger` 추가(§5).
- **admin UI(9107)**: 그대로 — 운영자가 **8행 truth table 을 화면에서 튜닝**.

### 2-4. egress_gateway (final_decision → event_id 매핑 + LOAS 행 조립)
- `decision_record` 읽기는 그대로. 추가: `final_decision` → **event_id 매핑**(normal→0, caution→1, warning→2, danger→3).
- **LOAS 26컬럼 조립**: event_id(=map(final_decision)) + image_data(decision_record 의 이미지 경로 → Base64) + **dust_inspection 24컬럼**.
  - 24컬럼은 SocketDaim 의 **gateway_db.dust_inspection**(원천 센서 데이터)에서 가져온다 → egress 가 **decision_db + gateway_db 양쪽을 읽는 cross-DB 조립**(이는 "PoolerTran 의 원래 기록처(transfer_result)" 사용과는 무관 — §5.5).
- `image_data`: 정적/동적 경로 중 택1(§7-4).

### 2-5. decision_db 스키마 마이그레이션
- decision_db 에 운영 데이터 없음(생산자 부재였음) → **재초기화 저위험**. 또는 소규모 마이그레이션.

---

## 3. 채널 분류 규칙 (2단계, 임계 각 1개)

| 채널 | 입력 | 규칙 | 출력 |
|---|---|---|---|
| `sensor_analysis_result` | `dust_value` | `> T_dust` ? abnormal : normal | normal/abnormal |
| `anomaly_detection_result`(정적) | 정적 `score` | `> T_static` ? abnormal : normal | normal/abnormal |
| `object_detection_result`(동적) | 동적 `score` | `> T_dynamic` ? abnormal : normal | normal/abnormal |

### 임계값 저장·튜닝 (웹UI 변경 가능) — 확정
- **decision_db 에 `classification_threshold` 설정 테이블** 신설(T_dust, T_static, T_dynamic).
- **decision_agent admin UI(9107)에서 편집 + reload**(alarm_mapping 과 동일 패턴 재활용).
- **PoolerTran 이 decision_db 에서 임계값을 읽어** 분류(주기적 reload 또는 폴링 시 조회).
- 기본값: **T_static = T_dynamic = 0.5**(score 0~1 가정), **T_dust = 중간값**(dust_value 범위 중앙/관측 median).
- → 8행 truth table + 분류 임계 **둘 다 같은 admin UI 에서 운영자 튜닝**.

> `T_dust` 는 "확산 시작점"이 이상적이나, 초기엔 중간값으로 두고 실측 보정.

---

## 4. alarm_mapping 8행 (확정 — 고객 규칙 반영, admin UI 로 추후 튜닝)

sensor·static·dynamic ∈ {normal, abnormal}.

**위험(danger) 규칙(고객 지정)**: `sensor=abnormal` **AND** (`static=abnormal` **OR** `dynamic=abnormal`) → **위험(3)**.
**나머지는 원래 로직**을 2단계 센서로 환산: sensor=normal → 원래 iot=normal 블록, sensor=abnormal(단, 위험 아님) → 원래 iot=warning 블록.

| # | sensor | static | dynamic | final_decision | event_id | 근거 |
|---|---|---|---|---|---|---|
| 1 | normal | normal | normal | normal | 0 | 원래 (n,n,n)→normal |
| 2 | normal | normal | abnormal | caution | 1 | 원래 normal블록→caution |
| 3 | normal | abnormal | normal | caution | 1 | 원래 normal블록→caution |
| 4 | normal | abnormal | abnormal | caution | 1 | 원래 normal블록→caution |
| 5 | abnormal | normal | normal | warning | 2 | 센서만 이상(위험규칙 미충족) → 원래 warning블록 |
| 6 | abnormal | normal | abnormal | **danger** | **3** | 위험규칙: 센서∧동적 |
| 7 | abnormal | abnormal | normal | **danger** | **3** | 위험규칙: 센서∧정적 |
| 8 | abnormal | abnormal | abnormal | **danger** | **3** | 위험규칙: 센서∧(정적∨동적) |

- 위험(3) = 행 6·7·8(센서 이상 + 비전 중 하나 이상 이상).
- 행 5(센서만 이상): 위험규칙 미충족 → 원래 로직대로 **경고(2)**.
- sensor=normal(행 1~4): 원래 iot=normal 블록 그대로(정상/주의/주의/주의).
- ⚠️ **추후 변경 가능**(고객 명시) — admin UI(9107)에서 8행 편집.

---

## 5. event_id 매핑 & 위험(3) — **위험 사용 확정**

- 매핑: `normal→0, caution→1, warning→2, danger→3` (LOAS 4단계 완전 충족).
- **위험(danger) 활성에 필요한 변경(3곳)**:
  - decision_agent enum: `final_level` 와 `decision_result` 에 **`danger` 값 추가**.
  - `judge.py`: `VALID_FINAL_LEVELS` 에 `"danger"` 추가.
  - egress: `final_decision → event_id` 매핑에 `danger→3` 포함.
- alarm_mapping 8행(§4) 중 6·7·8행이 `danger`.

---

## 5.5 이중기록 분석 — PoolerTran 은 decision_db 단독 기록으로 충분 (✅ 원래 db 불필요)

**질문**: PoolerTran 이 원래 기록처(gateway_db `transfer_result`)와 decision_db 를 **동시에** 써야 하나?
**결론**: **아니다. decision_db(decision_record) 단독 기록으로 충분하며, `transfer_result` 는 제거 가능.** 단 1가지 보강 필요(아래).

### 분석
- `transfer_result` 가 보유하던 것 = score · **image_path** · amr/waypoint · 타임스탬프.
- LOAS 가 그중 필요로 하는 것 = **image_data(이미지 경로 → Base64)** 뿐. (event_id 는 final_decision 에서, 24컬럼은 dust_inspection 에서.)
- 따라서 **이미지 경로를 decision_db 에 담으면** `transfer_result` 가 보유할 고유 정보가 없어진다.

### 보강(필수 1가지)
- `decision_record` 에 **이미지 경로 컬럼 추가**(예: `static_image_path`, `dynamic_image_path`) — 또는 decision_db 내 동반 테이블.
  - decision_record 는 원래 채널 결과만 갖고 이미지가 없으므로, 이게 없으면 egress 가 image_data 를 못 만든다.
- 그러면 egress 는 **decision_record(event_id + 이미지경로) + gateway_db.dust_inspection(24컬럼)** 으로 LOAS 행 완성 → **transfer_result 불필요.**

### 전달 신뢰성(at-least-once)
- 기존: REST → transfer_result 기록 → 큐 DELETE. → 변경: REST → **decision_record INSERT(멱등)** → 큐 DELETE.
- "결과 확정" 마커가 decision_record 로 옮겨갈 뿐, 순서/멱등은 동일 → 이중기록 없이도 at-least-once 유지.
- 포이즌/실패 격리(DLQ)는 **decision_db 측 DLQ 테이블**로(결과측 상태를 decision_db 로 일원화).

### ⚠️ 용어 정리(혼동 방지)
- "PoolerTran 의 원래 기록처" = **gateway_db `transfer_result`** → **제거 대상**(불필요).
- egress 가 읽는 **gateway_db.dust_inspection** 은 "원래 기록처"가 아니라 **SocketDaim 의 원천 센서 데이터** → egress 는 이건 계속 읽어야 함(24컬럼). 이 둘은 별개다.

### 정리 작업
- 앞서 `migrate_010` 에 추가했던 **gateway_db `transfer_result`/`transfer_dlq` 제거**(되돌리기).
- PoolerTran 의 result repo/풀 → decision_db 기록으로 전환(gateway_db 결과 기록 코드 제거).

> 대안(비권장): decision_record 에 dust 24컬럼까지 전부 denormalize 하면 egress 가 decision_db 단독으로도 가능하나, decision_record 비대화 + dust_inspection 중복이라 권하지 않음. 24컬럼은 dust_inspection 에서 읽는 게 정상.

---

## 6. 구현 계획 (단계별 체크리스트)

### Phase 1 — Contract 합의 (선행, 코드 무관)
- [x] InferenceModule 출력 형식 = **배열 `[(score,p1,p2)정적, (score,p1,p2)동적]`** — §2-1 ✅확정
- [x] event_id **4단계(0/1/2/3, 위험 사용)** — §5 ✅확정
- [x] 위험 규칙 = sensor 이상 ∧ (정적 ∨ 동적 이상), 나머지 원래 로직 — §4 ✅확정
- [x] 이중기록 불필요 → **decision_db 단독**, transfer_result 제거 — §5.5 ✅확정
- [ ] 임계 `T_dust/T_static/T_dynamic` 초기값(보정 전 보수값) — §3
- [ ] granularity = (amr, waypoint) 관측 단위 확정, dust_value 집계(대표/최댓값) — §4
- [ ] image_data 로 보낼 이미지(정적 p1/p2, 동적 중 택1) — §7-4

### Phase 2 — decision_db 스키마
- [ ] enum: `iot_sensor_level` → `model_result`(2단계) + `final_level`·`decision_result` 에 **`danger` 추가**(위험 확정)
- [ ] `decision_record` 에 **이미지 경로 컬럼 추가**(static/dynamic) — §5.5 보강
- [ ] `alarm_mapping` 컬럼 타입 변경 + **8행 재시드**(§4 확정표, seed_mapping.sql)
- [ ] `judge.py`: 12 → 8, `VALID_FINAL_LEVELS` 에 `"danger"` 추가
- [ ] **`classification_threshold` 테이블 신설**(T_dust/T_static/T_dynamic, 기본 0.5/중간값) — §3
- [ ] **admin UI(9107)에 임계 편집/reload 추가**(alarm_mapping UI 패턴 재사용)
- [ ] (운영 데이터 없으니) decision_db 재초기화 또는 마이그레이션 스크립트
- [ ] 검증: judge 가 8행 로드, lookup (normal/abnormal)³ → danger 포함 동작

### Phase 3 — PoolerTran 생산자 (decision_db 단독 기록)
- [ ] REST client: 응답 배열 `[(score,p1,p2)×2]` 파싱(`_extract_score_image` 교체)
- [ ] 분류 로직 — **임계는 decision_db `classification_threshold` 에서 읽어** 적용(reload)
- [ ] **decision_db 접속 추가**(`PT_DECISION_DB_*`) — **기존 detector 롤 재사용**(신규 롤 불필요)
- [ ] **waypoint 단위 집계**: dust_value **최댓값**으로 sensor 채널 산출(§7 granularity A안)
- [ ] decision_record INSERT(3채널 + REST 응답 이미지경로, (amr,waypoint) 관측 단위, **멱등 키**)
- [ ] **transfer_result/transfer_dlq(gateway_db) 기록 제거** + DLQ 를 decision_db 측으로 — §5.5
- [ ] 큐 DELETE 마커를 decision_record INSERT 기준으로 전환(at-least-once 유지)
- [ ] 단위 테스트(분류 경계, INSERT 멱등, 실패→DLQ)

### Phase 4 — egress
- [ ] `final_decision → event_id` 매핑 추가(danger→3 포함)
- [ ] **gateway_db.dust_inspection 읽기 추가**(24컬럼) — egress cross-DB 조립
- [ ] MariaDB sink COLUMN_MAP: 24컬럼 + event_id + image_data(decision_record 경로→Base64) 결합

### Phase 2.5 — gateway_db 정리
- [ ] `migrate_010` 에 추가했던 `transfer_result`/`transfer_dlq` **제거**(되돌리기) — §5.5

### Phase 5 — 통합 검증
- [ ] 생산(INSERT)→판정(decision_agent)→송출(egress) E2E
- [ ] admin UI(9107)에서 8행 튜닝 반영 확인
- [ ] 임계 보정(실측 라벨 데이터)

---

### ✅ 확정됨
- 센서 레벨 = **2단계**(임계 1개) → truth table **2×2×2 = 8행**. (전 구현 이 전제로 통일)
- InferenceModule 출력 = 배열 `[(score,p1,p2)정적, (score,p1,p2)동적]`.
- event_id **4단계**(위험 사용), 위험 규칙 = sensor 이상 ∧ (정적 ∨ 동적 이상), 나머지 원래 로직(§4).
- 이중기록 불필요 → **decision_db 단독 기록**, gateway_db `transfer_result` 제거(§5.5).

### ✅ 추가 확정 (이번 합의)
1. **쓰기 롤** — 신규 롤 불필요. **기존 detector 롤 재사용**(이미 `INSERT ON decision_record` 보유 → 풀 row INSERT 가능).
2. **image_data 경로** — **REST 응답 경로를 그대로 저장**, egress 가 그 경로 파일을 읽어 Base64. (egress 가 공유 스토리지 동일 경로 마운트 필요.)
3. **granularity** — **A안: waypoint 단위 decision_record + dust 최댓값 집계**. egress 가 dust_inspection(측정별) 조인 시 event_id·image 를 각 측정 행에 broadcast → LOAS 는 측정별 행.
4. **임계값** — 기본 static/dynamic=0.5, dust=중간값. **decision_db `classification_threshold` 테이블 + admin UI(9107) 편집**, PoolerTran 이 읽음(§3).

### ⬜ 남은 미결정 (소소)
- 응답의 여러 경로(정적 p1·p2 / 동적) 중 image_data 로 보낼 **1개 선택 기본값**(예: 정적 p1) — 임계처럼 UI 설정화 가능.
- 임계 **실측 보정값**(초기 기본값으로 가동 후 튜닝).
- LOAS 행 단위가 측정별(A 권장)이 아니라 굳이 측정별 판정을 원하면 B안 재검토.

---

## 8. 리스크 / 주의

- **PoolerTran↔decision_db 신규 결합** — PoolerTran 이 처음으로 decision_db 에 씀(접속/롤/장애격리 고려).
- **다중 writer 회피** — decision_record 생산을 PoolerTran 단독으로(센서 채널까지). 분리하면 `INSERT ON CONFLICT` 합치기 레이스 주의.
- **임계 미보정** — 보정 전엔 오/미탐 가능 → 보수적 기본값 + 실측 보정 필수.
- **위험(3) 확장 시** enum/judge/egress 3곳 동시 수정.
- 본 문서는 **계획**이며, Phase 1 확정 후 Phase 2~ 구현에 착수한다.
