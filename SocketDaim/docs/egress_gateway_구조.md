# Egress Gateway 구조 분석 (`SocketDaim/egress_gateway`)

> 대상 경로: [SocketDaim/egress_gateway/](SocketDaim/egress_gateway/)
> 분석 일자: 2026-06-17 · 총 ~812 LOC

---

## 1. 한 줄 요약

**Decision DB(`decision_db.decision_record`)에서 "판정 완료 + 미전송" 행을 주기적으로 폴링해, LOAS 측 TCP 엔드포인트로 송출(ANALYSIS_RESULT / ALERT)하는 단방향 송신 전용 컨슈머.** HTTP 서버가 없고, SQLite outbox 로 at-least-once 전달을 보장한다.

PoolerTran 과 동일한 **outbox/poller 패턴**이지만, 전송 채널이 REST 가 아니라 **gw_proto 의 길이-프리픽스 TCP 프로토콜**이고, 소스가 cctv 큐가 아니라 **decision_record** 라는 점이 다르다.

---

## 2. 파일 구조

```
egress_gateway/
├── main.py                       # 엔트리: 풀/outbox/TCP 생성 → outbox drain → poller.run
├── config.py                     # EGW_* 환경변수 (EgressSettings)
├── poller.py                     # 주기 폴링 루프 (fetch_pending → send_record)
├── sender.py                     # 판정 1건 → LOAS 송출 (msg_type 매핑/payload/ACK/재시도)
├── outbox.py                     # SQLite 영속 outbox (멱등 insert, ack 시 delete, 재기동 drain)
├── logging_config.py             # structlog (JSON/console)
├── Dockerfile                    # python:3.11-slim + gw_proto 설치
├── repository/
│   ├── __init__.py               # create_pool(asyncpg) + 재노출
│   └── decision_repo.py          # DecisionRecord, fetch_pending, mark_sent
└── hop/                          # (참고) Apache Hop 으로 대체할 때의 설계 자료
    ├── APACHE_HOP_MIGRATION.md
    └── decision_export_target.sql
```

의존 공유 라이브러리: [SocketDaim/libs/gw_proto/](SocketDaim/libs/gw_proto/) — `TcpClient`, `Message`, `MessageType`, `StandardCodec`, `get_codec`, framing.

---

## 3. 전체 데이터 흐름

```
                         decision_db (PostgreSQL, egress_role)
                                  │  ① SELECT pending & sent_at IS NULL
                                  ▼
        ┌──────────────────────────────────────────────────────┐
        │  Poller (poll_interval_sec=5, batch_size=100)          │
        │   _tick(): fetch_pending → for rec: sender.send_record │
        └──────────────────────────────────────────────────────┘
                                  │  rec
                                  ▼
        ┌──────────────────────────────────────────────────────┐
        │  Sender.send_record(rec)                               │
        │   ② outbox.add (SQLite, 멱등 INSERT OR IGNORE)         │  ← 먼저 영속화
        │   ③ TcpClient.send(Message)  ──── 길이프리픽스 TCP ───▶ │  LOAS
        │   ④ await receive() == ACK ? ◀───────────────────────  │  (수신측)
        │      성공: outbox.remove + repo.mark_sent(sent_at=NOW) │
        │      실패: outbox.bump_attempts + (필요시) reconnect    │
        └──────────────────────────────────────────────────────┘

  재기동 시: main._drain_outbox() 가 이전 실행에서 남은 outbox 행을 먼저 재전송(replay).
```

**처리 순서(불변식): ② outbox 영속화 → ③ 전송 → ④ ACK 확인 후 outbox 삭제 + sent_at 갱신.**
중간에 죽어도 (a) outbox 에 남아 재기동 drain 으로 재전송, (b) `sent_at IS NULL` 이라 다음 폴링이 다시 집어듦 → **at-least-once**. 수신측은 `decision_id` 로 멱등 처리 전제.

---

## 4. 컴포넌트 상세

### 4.1 `config.py` — `EgressSettings` (env prefix `EGW_`)
[SocketDaim/egress_gateway/config.py](SocketDaim/egress_gateway/config.py)

| 변수 | 기본값 | 의미 |
|---|---|---|
| `EGW_LOAS_HOST` | `mock-loas` | 송출 대상(LOAS 수신) 호스트 |
| `EGW_LOAS_PORT` | `9001` | 송출 대상 포트 |
| `EGW_PROTOCOL` | `standard` | 코덱 선택 (`standard` 만 Codec 반환; vendor 는 미사용) |
| `EGW_DB_HOST` | `postgres-decision` | decision_db 호스트 |
| `EGW_DB_PORT` / `EGW_DB_NAME` | `5432` / `decision_db` | |
| `EGW_DB_USER` / `EGW_DB_PASSWORD` | `egress_role` / `dev_egress_pw` | 송신 전용 롤 |
| `EGW_DB_POOL_MIN/MAX` | `2` / `10` | asyncpg 풀 크기 |
| `EGW_POLL_INTERVAL_SEC` | `5.0` | 폴링 주기 |
| `EGW_BATCH_SIZE` | `100` | 틱당 최대 처리 건수 |
| `EGW_OUTBOX_PATH` | `/data/outbox.db` | SQLite outbox 경로(볼륨) |
| `EGW_LOG_LEVEL/FORMAT` | `INFO` / `json` | |

`dsn` 프로퍼티로 `postgresql://egress_role:…@host:port/decision_db` 생성.

### 4.2 `repository/decision_repo.py` — 소스 읽기/표시
[SocketDaim/egress_gateway/repository/decision_repo.py](SocketDaim/egress_gateway/repository/decision_repo.py)

- **`DecisionRecord`** 데이터클래스: `id(UUID)`, `station_id`, `timestamp(=observation_timestamp)`, `static_model_result`, `dynamic_model_result`, `sensor_result`, `final_decision`, `decided_at`.
- **`fetch_pending(limit)`**:
  ```sql
  SELECT id, station_id, observation_timestamp AS timestamp,
         anomaly_detection_result::text AS static_model_result,
         object_detection_result::text  AS dynamic_model_result,
         sensor_analysis_result::text   AS sensor_result,
         final_decision::text, decided_at
    FROM decision_record
   WHERE final_decision <> 'pending' AND sent_at IS NULL
   ORDER BY decided_at
   LIMIT $1
  ```
  → **판정이 끝났고(`<> 'pending'`) 아직 안 보낸(`sent_at IS NULL`)** 행만, 결정 순서(`decided_at`)대로.
- **`mark_sent(id)`**: `UPDATE decision_record SET sent_at = NOW() WHERE id=$1 AND sent_at IS NULL` — 전송 성공 후에만 호출. **egress_role 은 이 `sent_at` UPDATE 와 SELECT 만 가능**(INSERT/DELETE 없음 — 단방향 송신 경계).

> 스키마 소유자는 Decision Agent(`decision_record` 테이블). egress 는 읽고 `sent_at` 만 갱신한다.

### 4.3 `poller.py` — 주기 폴링 루프
[SocketDaim/egress_gateway/poller.py](SocketDaim/egress_gateway/poller.py)

- `run(stop_event)`: `stop_event` 이 set 될 때까지 `_tick()` 반복, 사이에 `asyncio.wait_for(stop_event.wait(), timeout=interval)` 로 대기(틱 중 종료 신호 즉시 반응).
- `_tick()`: `fetch_pending(batch)` → 각 레코드 `sender.send_record(rec)`; **하나라도 실패(False)하면 `break`** → 다운된 피어를 더 두드리지 않고 다음 틱으로 미룸(순서 보존 + 백오프 효과).
- 틱 예외는 `logger.exception` 후 루프 계속(폴러는 죽지 않음).

### 4.4 `sender.py` — 판정 → LOAS 송출
[SocketDaim/egress_gateway/sender.py](SocketDaim/egress_gateway/sender.py)

- **메시지 타입 매핑** `_msg_type_for(final_decision)`:
  - `warning` → `ALERT` (0x0101)
  - 그 외(`normal`/`caution`) → `ANALYSIS_RESULT` (0x0100)
- **페이로드** `_build_payload(rec)`: 아래 JSON 을 UTF-8 bytes 로.
  ```json
  {"decision_id","station_id","timestamp","final_decision",
   "static_model_result","dynamic_model_result","sensor_result","decided_at"}
  ```
- **`send_record(rec)`**: ① `outbox.add()` 로 먼저 영속화 → ② `_send_payload(persist_sent=True)`.
- **`replay(decision_id, msg_type, payload)`**: 재기동 drain 용 — outbox 에 이미 있는 행을 다시 전송.
- **`_send_payload(...)`** (핵심):
  1. `client.send(Message(msg_type, payload))` → `await receive()` (ACK 대기, `ACK_TIMEOUT=10s`).
  2. 예외(`GwProtoError`/타임아웃/`ConnectionError`/`OSError`): `outbox.bump_attempts` + 끊겼으면 `reconnect()`(2s bound) 시도 → `False`.
  3. 응답이 `ACK` 아니면: `bump_attempts` → `False`.
  4. 성공: `outbox.remove(decision_id)` + (`persist_sent`) `repo.mark_sent(id)` → `True`.

### 4.5 `outbox.py` — SQLite 영속 outbox
[SocketDaim/egress_gateway/outbox.py](SocketDaim/egress_gateway/outbox.py)

- 스키마: `outbox(outbox_id PK AUTOINC, decision_id TEXT UNIQUE, msg_type INT, payload BLOB, attempts INT, created_at TEXT)`.
- `add`: **`INSERT OR IGNORE`** (decision_id 멱등 — 중복 무시).
- `remove`: ACK 후 행 삭제.
- `bump_attempts`: 실패 시 시도 횟수 증가(관측용; **자동 폐기/DLQ 는 없음**).
- `iter_pending`: `ORDER BY outbox_id` (FIFO) 전체 조회 → 재기동 drain.
- `count`: 현재 적체 수(기동 로그).

> PoolerTran 의 outbox 가 **PostgreSQL 큐 테이블**인 것과 달리, egress 의 outbox 는 **로컬 SQLite 파일**(`/data/outbox.db`, 볼륨). 소스 DB 와 분리된 송신 측 자체 버퍼.

### 4.6 `main.py` — 기동 시퀀스
[SocketDaim/egress_gateway/main.py](SocketDaim/egress_gateway/main.py)

```
EgressSettings → logging → create_pool(asyncpg) → DecisionRepository
   → Outbox.open() → get_codec(protocol) → TcpClient.connect()
   → Sender(client, outbox, repo)
   → _drain_outbox()  (이전 실행 잔여분 먼저 재전송, 첫 실패 시 중단)
   → Poller.run(stop_event)
   finally: client.close() / outbox.close() / pool.close()
```
SIGTERM/SIGINT → `stop_event.set()` 으로 우아한 종료. **HTTP 서버 없음**(health 엔드포인트 부재 — 관측은 로그).

---

## 5. 전송 프로토콜 (gw_proto, `standard`)

### 5.1 프레이밍 — 8바이트 헤더 + payload (빅엔디안)
[SocketDaim/libs/gw_proto/gw_proto/framing.py](SocketDaim/libs/gw_proto/gw_proto/framing.py)

```
+----------------+----------------+----------------+
| 4B payload len | 4B msg type    | N bytes payload|
| uint32 BE (!I) | uint32 BE (!I) |                |
+----------------+----------------+----------------+
HEADER_STRUCT = struct.Struct("!II")   # 빅엔디안
```
> ⚠️ 이 송신 프로토콜은 **빅엔디안**이다. (이전에 다뤘던 LOAS DUST/CCTV **수신** 프레이밍의 리틀엔디안과는 별개 — 그건 ingestion 측 vendor codec, 여기 egress 는 `standard` codec.)

### 5.2 메시지 타입
[SocketDaim/libs/gw_proto/gw_proto/messages.py](SocketDaim/libs/gw_proto/gw_proto/messages.py)

| 코드 | 이름 | egress 에서 |
|---|---|---|
| `0x0100` | `ANALYSIS_RESULT` | normal/caution 판정 송출 |
| `0x0101` | `ALERT` | warning 판정 송출 |
| `0x0F01` | `ACK` | 수신측 응답(성공 확인) |
| `0x0F00` | `HEARTBEAT` | TcpClient 가 30s 마다 자동 송신 |
| `0x0001/2`, `0x0010`, `0x0FFF` | VIDEO_*, SENSOR_SAMPLE, ERROR | egress 미사용 |

### 5.3 코덱 (`StandardCodec`)
[SocketDaim/libs/gw_proto/gw_proto/codec/standard.py](SocketDaim/libs/gw_proto/gw_proto/codec/standard.py)
- `encode(msg)` → `(msg_type, payload)` 그대로(추가 가공 없음).
- `decode(type, payload)` → JSON 페이로드면 `metadata` 로 파싱(ACK 등).

### 5.4 TcpClient — 연결/재연결/하트비트
[SocketDaim/libs/gw_proto/gw_proto/transport/client.py](SocketDaim/libs/gw_proto/gw_proto/transport/client.py)
- `connect()`: 실패 시 **지수 백오프**(1→2→…→최대 60s) 재시도.
- `reconnect()`: close 후 재연결.
- `_heartbeat_loop()`: 30s 주기 HEARTBEAT 자동 송신(연결 유지).
- `is_connected`: writer 살아있는지.

---

## 6. 신뢰성 모델 (at-least-once)

| 장애 시점 | 동작 | 결과 |
|---|---|---|
| outbox.add 후 send 전 크래시 | 재기동 drain 이 outbox 재전송 | 재전송(중복 가능) |
| send 후 ACK 전 크래시 | `sent_at` 미갱신 → 다음 폴링 재집음 + outbox 잔존 | 재전송(중복 가능) |
| ACK 비정상/타임아웃 | `bump_attempts` + reconnect, 다음 틱 재시도 | 무한 재시도 |
| LOAS 다운 | `_tick` 첫 실패에서 break, 백오프 재연결 | 복구 시 자동 재개 |

- **멱등 키 = `decision_id`** : outbox(UNIQUE) + 수신측(decision_export PK) 양쪽에서 중복 흡수.
- **소실 방지 우선**: 자동 폐기/DLQ 없음 → 보낼 때까지 재시도(`attempts` 는 관측만).
- **순서**: `ORDER BY decided_at`, 실패 시 break 로 FIFO 유사 보존.

---

## 7. DB / 권한

- **소스**: `decision_db.decision_record` (Decision Agent 소유). egress 는 `egress_role` 로 **SELECT + `sent_at` UPDATE 만**.
- **타깃**: LOAS TCP 엔드포인트(외부). DB 가 아니라 소켓.
- compose 상 `postgres-decision` 은 **Decision Agent compose 가 소유** → egress 는 `depends_on` 안 걸고, 연결 실패 시 그냥 재시도.

---

## 8. 배포 / 설정

[SocketDaim/docker-compose.yml](SocketDaim/docker-compose.yml) (`egress-gw` 서비스, 컨테이너 `sd-egress-gw`)

- 빌드: [SocketDaim/egress_gateway/Dockerfile](SocketDaim/egress_gateway/Dockerfile) — `python:3.11-slim` + `gw_proto` 설치 + `/data` 생성.
- 볼륨: `egress-data:/data` (outbox SQLite 영속).
- `restart: unless-stopped`, 메모리 512M 제한.
- 의존성: `asyncpg`, `aiosqlite`, `pydantic(-settings)`, `structlog`.
- **운영 시 교체**: `EGW_LOAS_HOST/PORT`(실제 LOAS), `EGW_DB_PASSWORD`(egress_role 비번).

---

## 9. Apache Hop 대체 자료 (참고)

[SocketDaim/egress_gateway/hop/](SocketDaim/egress_gateway/hop/) — egress_gateway(Python) 를 Apache Hop 파이프라인으로 대체할 때의 설계.
- [decision_export_target.sql](SocketDaim/egress_gateway/hop/decision_export_target.sql): 타깃이 **관계형 DB** 일 때의 수신 테이블 `decision_export`(PK=`decision_id` 멱등, DB 방언 노트 포함).
- [APACHE_HOP_MIGRATION.md](SocketDaim/egress_gateway/hop/APACHE_HOP_MIGRATION.md): 마이그레이션 설계 문서.
- 단, **현재 구현은 타깃이 TCP(LOAS) 소켓**이며 Hop 대체안은 타깃이 DB 인 경우를 가정한다(소스는 동일 `decision_record`).

---

## 10. 한계 · 주의점

- **HTTP health 없음** — 상태 확인은 로그(`decision_sent`, `decision_send_failed`, `outbox_drain_*`)에 의존.
- **DLQ/폐기 없음** — 영구 실패 메시지도 무한 재시도(소스의 `sent_at` 이 NULL 로 남고 outbox 잔존). poison 메시지가 `_tick` break 로 **뒤 큐를 막을 수** 있음.
- **단일 인스턴스 전제** — 동일 decision_record 를 두 인스턴스가 동시에 보내면 중복(수신측 멱등으로만 방어).
- **빅엔디안 송신 프로토콜** — 수신측 LOAS 가 동일 프레이밍(`!II` + JSON, ACK 0x0F01)을 구현해야 함.
- `final_decision` 매핑은 `warning`→ALERT, 그 외→ANALYSIS_RESULT 로 **하드코딩**.

---

## 11. PoolerTran 과의 비교 (참고)

| 항목 | egress_gateway | PoolerTran |
|---|---|---|
| 소스 | `decision_record` (decision_db) | `cctv_transfer_queue` (gateway_db) |
| 트리거 | 폴링(`sent_at IS NULL`) | 폴링/LISTEN(큐 행 존재) |
| 전송 채널 | **gw_proto TCP** (ANALYSIS_RESULT/ALERT + ACK) | **REST**(JSON/multipart/batch) |
| outbox | **로컬 SQLite** | PostgreSQL 큐 + result 테이블 |
| 완료 표시 | `sent_at=NOW()` (UPDATE) | 큐 행 DELETE |
| 결과 적재 | 없음(송신만) | `transfer_result` 기록 |
| 신뢰성 | at-least-once, 멱등=decision_id | at-least-once, 멱등=frame_id, DLQ 있음 |

> 두 모듈은 **outbox/poller 송신 패턴**을 공유하지만 소스·채널·완료표시 방식이 다르다. egress 를 "써야 하는 상황"이라면, 위 §5 프로토콜(빅엔디안 `!II` + JSON + ACK 0x0F01)과 §6 신뢰성 모델, §2 의 `EGW_LOAS_HOST/PORT` 설정이 1차 점검 포인트다.
