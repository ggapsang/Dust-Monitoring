# egress_gateway → Apache Hop 전환 설계 (시나리오 A: 순수 DB 타깃)

현재 Python `egress_gateway`(decision_db 폴링 → LOAS TCP 단방향 전송)를, **외부 관계형
DB로 단방향 적재**하는 Apache Hop 파이프라인으로 대체하는 구체 설계.

- 타깃 DB: **PostgreSQL 기본** (Oracle/MSSQL/MySQL 으로 변경 가능 — DB별 차이는 [§7](#7-타깃-db-방언-교체-지점) 한 곳에 격리)
- 보존해야 할 불변식 2개: **① 멱등키 `decision_id`(타깃 PK+upsert)**, **② "타깃 적재 성공 후에만 `sent_at` 갱신"**

---

## 1. 무엇이 사라지고 무엇이 남는가

| 현재 (Python) | 전환 후 (Hop) |
|---|---|
| `gw_proto` TCP 클라이언트 + ACK 핸드셰이크 | ❌ 제거 (DB 적재로 대체) |
| SQLite outbox (durable buffer) | ❌ 제거 → "소스 `sent_at` 플래그 + 타깃 PK upsert"로 동등 보장 |
| 재연결/백프레셔 루프 | ❌ 제거 → Hop 에러처리 + 스케줄 재시도 |
| `_build_payload`(JSON), `_msg_type_for` | 🔁 Hop 트랜스폼(Value Mapper/Select Values)으로 이전 (DB 적재면 JSON 불필요) |
| `fetch_pending` SELECT | ✅ Table Input 으로 그대로 |
| `mark_sent` UPDATE | ✅ Update 트랜스폼으로 그대로 (적재 성공 후) |

---

## 2. 파이프라인 설계 — `egress_decisions.hpl`

행 단위 스트림. 트랜스폼 순서:

```
[1] Table Input            decision_db 에서 미전송 결정 읽기
        │
[2] Value Mapper (선택)     final_decision → msg_type (구 매핑 보존용; 불필요시 생략)
        │
[3] Insert / Update        외부 DB decision_export 에 decision_id 키로 upsert
        │  (성공 행만 다음으로 — 에러처리 활성화)
        ├──[에러 행]──▶ [3e] Text/Table 로 dead-letter 적재 + 로그
        │
[4] Get System Info        now() 를 sent_ts 필드로 주입 (파이프라인 시작 시각 고정)
        │
[5] Update                 decision_db.decision_record.sent_at = sent_ts  (key: id=decision_id)
```

### [1] Table Input — `read-pending-decisions`
- 연결: `decision_db`(소스, `egress_role`)
- SQL (현 `fetch_pending` 과 동일 의미):
  ```sql
  SELECT id                              AS decision_id,
         station_id,
         observation_timestamp,
         anomaly_detection_result::text  AS static_model_result,
         object_detection_result::text   AS dynamic_model_result,
         sensor_analysis_result::text    AS sensor_result,
         final_decision::text            AS final_decision,
         decided_at
    FROM decision_record
   WHERE final_decision <> 'pending'
     AND sent_at IS NULL
   ORDER BY decided_at
   LIMIT 100        -- 배치 크기(구 batch_size). 미지정 시 매 실행마다 전량.
  ```
  > 소스에 이미 `idx_decision_unsent (decided_at) WHERE sent_at IS NULL AND final_decision<>'pending'` 인덱스가 있어 이 SELECT 가 인덱스를 그대로 탄다.

### [2] Value Mapper (선택) — `map-msg-type`
- 입력 필드 `final_decision` → 출력 `msg_type`:
  - `warning` → `ALERT`
  - 그 외(`normal`/`caution`) → `ANALYSIS_RESULT`
- DB 타깃이 msg_type 을 안 쓰면 **생략**(타깃 표에서 `msg_type` 컬럼 제거).

### [3] Insert / Update — `upsert-decision-export`  ★멱등 지점
- 연결: `external_db`(타깃)
- 대상 테이블: `decision_export`
- **Key (lookup)**: `decision_id`
- Update 필드: station_id, observation_timestamp, static/dynamic/sensor_result, final_decision, decided_at, (msg_type)
- 효과: 같은 `decision_id` 재유입 시 insert 대신 update → **중복 없음(멱등)**.
- **에러 처리 활성화**: 적재 실패 행은 `[3e]` 로 분기 → `sent_at` 갱신으로 흘러가지 않음. **이것이 불변식 ②를 보장한다.**

### [4] Get System Info — `now`
- 시스템 날짜/시간을 필드 `sent_ts` 로 추가(파이프라인 시작 시 고정값).

### [5] Update — `mark-sent`
- 연결: `decision_db`
- 대상: `decision_record`, Key: `id = decision_id`
- Set: `sent_at = sent_ts`
- (`egress_role` 이 sent_at UPDATE 권한 보유 — 현행 그대로)

---

## 3. 워크플로 / 스케줄 — `egress.hwf`

```
Start (repeat, 5s)  ──▶  Pipeline: egress_decisions.hpl  ──성공──▶  (대기)
                                          └──실패──▶  로그 + 다음 주기 재시도
```
- 구 `poll_interval_sec=5` 를 Start 의 repeat 간격으로 이전.
- 실행 방식 3택1:
  1. **Hop Server** 에 워크플로 등록 + 스케줄 (권장: REST/모니터링 내장)
  2. 장기 실행 워크플로(Start repeat)
  3. 외부 cron 이 `hop-run.sh` 호출

---

## 4. at-least-once 보장 흐름

```
타깃 upsert 커밋  ──성공──▶  sent_at 갱신
      │
      └─ 실패 ─▶ 에러 분기 → sent_at 미갱신 → 다음 주기 재시도
```
- 크래시가 "타깃 커밋 ~ sent_at 갱신" 사이에 나도, 다음 실행에서 같은 행을 재전송 →
  타깃 PK upsert 가 흡수(중복 없음). = 현재 `send→ACK→mark_sent` 의미론과 동일.
- 교차 DB 단일 트랜잭션은 불가하므로(소스/타깃 별개), **순서 규율 + 멱등키**로 보장.

---

## 5. docker-compose 추가분 (예시)

```yaml
  hop-egress:
    image: apache/hop:latest                 # Hop 2.x (PostgreSQL JDBC 내장)
    container_name: sd-hop-egress
    environment:
      HOP_PROJECT_FOLDER: /project
      HOP_RUN_CONFIG: local
      # 연결 비밀번호 등은 Hop 변수/시크릿으로 주입
      EGW_SRC_DB_HOST: postgres-decision
      EGW_SRC_DB_NAME: decision_db
      EGW_SRC_DB_USER: egress_role
      EGW_TGT_DB_HOST: <외부 DB 호스트>
      EGW_TGT_DB_NAME: <외부 DB 이름>
    volumes:
      - ./egress_gateway/hop:/project:ro      # .hpl/.hwf + metadata + 이 문서/DDL
      # 타깃이 Oracle/MSSQL/MySQL 이면 드라이버 jar 추가 마운트:
      # - ./egress_gateway/hop/jdbc:/opt/hop/lib/jdbc:ro
    networks:
      - gw-net                                # decision_db 접근 (+ 외부 DB 경로)
    restart: unless-stopped
```
- 기존 `sd-egress-gw` 서비스는 컷오버 후 **제거**.

---

## 6. 컷오버(점진 전환) 절차

1. 타깃 DB 에 [decision_export_target.sql](decision_export_target.sql) 적용 (PK=decision_id).
2. Hop 연결 2개(소스/타깃) + 파이프라인/워크플로 작성.
3. **그림자 모드**: `[5] Update(mark-sent)` 를 비활성화한 채 가동 → 타깃에 적재만 하고 `sent_at` 은 안 건드림. 기존 Python egress 와 **병행** 운영하며 타깃 데이터 일치 검증.
4. 검증 완료 후: `mark-sent` 활성화 + 기존 `sd-egress-gw` 중지 → 스위치오버.
5. `outbox.db`, `gw_proto` TCP 경로는 egress 에서 미사용 처리.

> ⚠️ 그림자/병행 단계에서 **Python egress 와 Hop 이 동시에 `sent_at` 을 갱신하면 안 됨**.
> 한쪽만 sent_at 의 소유자가 되도록(=Hop 은 3단계에서 sent_at 비활성) 반드시 분리.

---

## 7. 타깃 DB 방언 교체 지점

DB 가 바뀌어도 변하는 것은 **딱 3가지**뿐:

| 변경점 | 위치 |
|---|---|
| 1) 타깃 테이블 타입 방언 | [decision_export_target.sql](decision_export_target.sql) 의 §방언 노트 |
| 2) JDBC 드라이버 jar | compose 의 `jdbc` 볼륨 마운트 (Postgres 는 내장, 그 외 추가) |
| 3) Hop 연결의 "Database type" | Hop 메타데이터 `external_db` 연결 설정 |

파이프라인 로직(SELECT/upsert/mark-sent)·불변식은 **DB 종류와 무관하게 동일**.
upsert 는 Hop Insert/Update 트랜스폼이 방언에 맞게 생성하므로 SQL 재작성 불필요.

---

## 8. 리스크 / 검증 포인트

- **enum→text 캐스팅**: 소스 `channel_result`/`decision_result` enum 을 `::text` 로 뽑아 타깃은 TEXT 로 받음(위 SELECT/DDL 반영). 타깃에서 enum 강제 시 매핑 추가 필요.
- **순서 보장**: `[3]` 에러처리로 "성공 행만 `[5]` 로" 가 핵심. 비활성 시 불변식 ② 깨짐.
- **commit size**: `[3]`/`[5]` 의 commit size 를 맞춰 부분 커밋 창을 최소화(멱등키가 잔여 위험 흡수).
- **운영 부담**: JVM 기반 Hop 런타임 + 저작 GUI 학습. 566줄 파이썬 대비 무거움 — 이득(시각적 ETL/모니터링/다중 타깃 확장)과 trade-off.
- **모니터링 재구축**: 구 structlog JSON → Hop Server 로그/메트릭/실행 이력으로 이전.
