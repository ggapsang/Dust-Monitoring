# Gateway Protocol 명세서 (Mock 송신기 개발자용)

> 문서 목적: Ingestion Gateway에 데이터를 전송하는 외부 프로그램(Mock 송신기, 로아스 측 송신기 등) 개발자를 위한 프로토콜 스펙.
>
> 대상 독자: 이 문서만 보고 어떤 언어로든 송신기를 구현할 수 있어야 한다.
>
> 현재 Gateway 구현: `libs/gw_proto` (Python), `ingestion_gateway/`
>
> 프로토콜 버전: `standard` (임시 표준, 로아스 스펙 확정 전 사용)

---

## 1. 접속 정보

| 항목 | 값 |
|---|---|
| 프로토콜 | TCP/IPv4 |
| 호스트 | Ingestion Gateway 컨테이너 (기본 dev: `localhost`) |
| 포트 | `9000` |
| 연결 모델 | 영속 연결 (persistent). 한 번 connect 후 세션 동안 계속 사용 |
| 암호화 | 없음 (Phase G-2 기준; 추후 TLS 고려) |
| 인증 | 없음 (추후 별도 모듈) |
| 바이트 오더 | **Big-endian** (네트워크 표준) |
| 문자 인코딩 | **UTF-8** |

---

## 2. 프레이밍 (Length-Prefixed Framing)

모든 메시지는 **8바이트 고정 헤더 + 가변 페이로드** 구조입니다.

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      payload_length (uint32 BE)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      message_type   (uint32 BE)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|              payload (payload_length 바이트)                  |
|                             ...                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

### 2.1 헤더 필드

| 필드 | 크기 | 설명 |
|---|---|---|
| `payload_length` | 4 bytes | 뒤따르는 payload의 바이트 수 (uint32, Big-endian) |
| `message_type` | 4 bytes | 메시지 타입 코드 (uint32, Big-endian) — §3 참조 |

### 2.2 제약

- `payload_length` 최대값: **536,870,912 바이트 (512 MiB)**. 초과 시 Gateway가 연결 차단.
- `payload_length = 0` 허용 (Heartbeat 등 빈 메시지 사용 가능, 단 실제 구현은 `{}` JSON 사용 권장).

### 2.3 인코딩 예시

Python:
```python
import struct
header = struct.pack("!II", payload_length, message_type)
#                     ^^  ^^ network(big-endian) + two uint32
```

C:
```c
uint32_t be_len  = htonl(payload_length);
uint32_t be_type = htonl(message_type);
send(sock, &be_len,  4, 0);
send(sock, &be_type, 4, 0);
send(sock, payload, payload_length, 0);
```

---

## 3. 메시지 타입

| 코드 | 이름 | 방향 | 페이로드 형식 | 용도 |
|---|---|---|---|---|
| `0x0001` | `VIDEO_CHUNK` | → Gateway | JSON 헤더 + `\n` + binary | 영상 청크 |
| `0x0002` | `VIDEO_COMPLETE` | → Gateway | JSON | 영상 수신 완료 신호 |
| `0x0010` | `SENSOR_SAMPLE` | → Gateway | JSON | 센서 단일 측정치 |
| `0x0100` | `ANALYSIS_RESULT` | Gateway → | JSON | 분석 결과 송출 (Egress) |
| `0x0101` | `ALERT` | Gateway → | JSON | 경보 송출 (Egress) |
| `0x0F00` | `HEARTBEAT` | 양방향 | `{}` | Keep-alive |
| `0x0F01` | `ACK` | 양방향 | `{}` 또는 JSON | 수신 확인 응답 |
| `0x0FFF` | `ERROR` | 양방향 | `{"error": "..."}` | 에러 응답/보고 |

> **Mock 송신기 관점**: `VIDEO_CHUNK`, `VIDEO_COMPLETE`, `SENSOR_SAMPLE`을 "보내고", Gateway로부터 `ACK` 또는 `ERROR`를 "받습니다". Gateway가 주기적으로 `HEARTBEAT`를 보내므로 Mock도 주기적으로 `HEARTBEAT` 답신/송신을 해야 합니다.

---

## 4. 메시지별 페이로드 스펙

모든 JSON은 **UTF-8**로 직렬화합니다.

### 4.1 `SENSOR_SAMPLE` (0x0010)

**Mock이 보내는 JSON:**
```json
{
  "station_name": "FL-A01-NORTH",
  "measurement_type": "temperature",
  "value": 23.5,
  "unit": "C",
  "sampled_at": "2026-04-17T10:00:00+00:00"
}
```

| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `station_name` | string | ✓ | DB `station` 테이블에 등록된 활성 station 이름. **wire에는 name만** — UUID는 각 모듈 DB의 내부 PK이며 송신자/수신자 간 사전 합의 불필요 |
| `measurement_type` | string | ✓ | 예: `"temperature"`, `"dust_density"` |
| `value` | number (float) | ✓ | 측정값 |
| `unit` | string | ✓ | 예: `"C"`, `"ppm"` |
| `sampled_at` | string (ISO 8601) | ✓ | UTC 권장. `Z` 또는 `+00:00` 사용 |

**응답:** `ACK` (성공) / `ERROR` (station 없음, 형식 오류 등)

---

### 4.2 `VIDEO_CHUNK` (0x0001)

영상 파일을 N개 청크로 분할해 순서대로 전송합니다.

**페이로드 구조:**
```
<JSON 헤더 (UTF-8)>\n<binary 청크 바이트...>
```

- JSON 헤더와 binary body는 **첫 번째 `\n` (0x0A)** 로 구분
- binary body 안에 `\n`(0x0A)이 있어도 무관 (첫 번째만 구분자)

**JSON 헤더 필드:**
```json
{
  "video_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "chunk_seq": 0,
  "total_chunks": 5,
  "station_name": "FL-A01-NORTH",
  "captured_at": "2026-04-17T10:00:00+00:00",
  "amr_id": "amr-01",
  "source_format": "mp4",
  "amr_position": {"x": 12.5, "y": 8.3, "heading": 90.0}
}
```

| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `video_id` | string (UUID) | ✓ | 한 영상 전체에서 동일. Mock이 생성 |
| `chunk_seq` | int | ✓ | 0부터 시작, `total_chunks - 1`까지 |
| `total_chunks` | int | ✓ | 전체 청크 수 |
| `station_name` | string | ✓ | 등록된 활성 station 이름 (UUID는 wire에 안 실음) |
| `captured_at` | string (ISO 8601) | ✗ | 촬영 시각 (UTC 권장) |
| `amr_id` | string | ✗ | 송신 AMR 식별자. video 테이블의 `amr_id`로 저장 |
| `source_format` | string | ✗ | `mp4` / `jpeg` / `jpeg_seq` 등. video 테이블의 `source_format`으로 저장 |
| `amr_position` | object | ✗ | 자유 JSONB. video 테이블의 `amr_position`으로 저장 |

**주의:**
- `chunk_seq`는 순서대로 보낼 필요는 없지만, **같은 seq를 두 번 보내면 덮어씀**
- 청크 크기 권장: 4 KiB ~ 4 MiB. 최대 512 MiB (프레임 제한과 동일)
- Gateway는 **첫 청크에서만** `station_name` 유효성 검사. 이후 청크는 통과

**응답:** 매 청크마다 `ACK` / `ERROR`

---

### 4.3 `VIDEO_COMPLETE` (0x0002)

모든 청크 송신 완료 후 전송. Gateway가 이 메시지를 받으면 청크 조립 → 파일 저장 → DB INSERT.

**JSON:**
```json
{
  "video_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `video_id` | string (UUID) | ✓ | 앞서 보낸 청크들과 동일한 값 |

**응답:**
- `ACK` — 청크 모두 수신 + 파일 저장 + DB INSERT 성공
- `ERROR` — 청크 누락, video_id 미존재, 저장 실패 등. `metadata.error`에 사유

저장 경로: `{STORAGE_ROOT}/videos/{station_id}/{YYYY-MM-DD}/{video_id}.bin`

---

### 4.3a `ANALYSIS_RESULT` (0x0100) / `ALERT` (0x0101) — Egress → LOAS

> 이 두 메시지는 **Egress Gateway → LOAS 송신측** 방향. 외부 Mock 송신기가 Gateway로 쏘는 것이 아니라, Gateway가 LOAS 측으로 내보내는 메시지다. 본 절은 LOAS 수신 시스템 또는 LOAS-side mock 서버 개발자를 위한 스펙이다.

#### 매핑 규칙

판정 DB(`decision_record` 테이블)의 `final_decision` 값에 따라 메시지 타입이 결정된다:

| `final_decision` | 메시지 타입 |
|---|---|
| `normal`  | `ANALYSIS_RESULT` (0x0100) |
| `caution` | `ANALYSIS_RESULT` (0x0100) |
| `warning` | `ALERT`           (0x0101) |

단계는 **normal → caution → warning**. warning만 별도 코드로 분리한 것은 수신측에서 다른 처리 라인(예: 알람 트리거)을 적용할 여지를 두기 위함.

`pending` 상태의 record는 송신 대상이 아니다 (Egress는 `final_decision <> 'pending' AND sent_at IS NULL` 인 행만 polling).

#### Payload (두 타입 동일 형식)

```json
{
  "decision_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "station_id":  "ST-001",
  "timestamp":   "2026-05-04T10:00:00+00:00",
  "final_decision": "normal",
  "static_model_result":  "normal",
  "dynamic_model_result": "normal",
  "sensor_result":        "normal",
  "decided_at":  "2026-05-04T10:00:01.234+00:00"
}
```

| 필드 | 타입 | 비고 |
|---|---|---|
| `decision_id` | string (UUID) | 판정 DB의 `decision_record.id`. 멱등 처리 키 |
| `station_id` | string | 개소 식별자 (`VARCHAR(50)`, UUID 또는 임의 식별자 문자열) |
| `timestamp` | string (ISO 8601) | 원 데이터 관측 시각 (`observation_timestamp` alias) |
| `final_decision` | string | `normal` / `caution` / `warning` |
| `static_model_result` | string \| null | `normal` / `abnormal` / `caution` / `warning` / `pending` (`anomaly_detection_result` alias) |
| `dynamic_model_result` | string \| null | 동일 (`object_detection_result` alias) |
| `sensor_result` | string \| null | 동일 (`sensor_analysis_result` alias) |
| `decided_at` | string (ISO 8601) | Decision Agent가 판정한 시각 |

> 컴포넌트 컬럼명(`anomaly_detection_result` 등)은 Decision Agent 측 plan 문서 §2.1의 초기 role_mapping(anomaly→static, object→dynamic, sensor→sensor)을 가정하여 alias 처리됨. role_mapping이 변경되면 alias 매핑도 따라 바뀌어야 한다.

#### 응답

- `ACK` — 수신 성공. Egress가 판정 DB의 `sent_at = NOW()` 마킹
- `ERROR` — 수신 측 거부. Egress가 outbox에 보존하고 다음 tick 재시도

#### 송신 빈도

판정 1건당 1메시지. Egress는 기본 5초 주기로 미송신 건을 polling하여 batch 전송한다.

---

### 4.4 `HEARTBEAT` (0x0F00)

**페이로드:** `{}` (빈 JSON 객체, 2바이트)

Mock은 30초 간격으로 송신 권장. Gateway도 30초 간격으로 Mock에게 송신하며, **60초 이상 아무 메시지도 못 받으면 연결을 끊음**.

**응답:** Gateway는 Mock의 HEARTBEAT에 `ACK`로 응답.

---

### 4.5 `ACK` (0x0F01)

**페이로드:** `{}` 또는 JSON (구현 자유). 현재 Gateway는 `{}`만 보냄.

---

### 4.6 `ERROR` (0x0FFF)

**JSON:**
```json
{"error": "Unknown station: xxx"}
```

Mock이 받는 경우: 바로 앞에 보낸 요청이 실패했다는 의미. 재시도 또는 스킵 판단.

---

## 5. 세션 흐름

### 5.1 정상 흐름 (센서)

```
Mock                          Gateway
 │                              │
 │──── TCP connect ────────────▶│
 │                              │
 │──── SENSOR_SAMPLE ──────────▶│
 │◀─────────── ACK ─────────────│
 │                              │
 │── (30s 뒤) HEARTBEAT ───────▶│
 │◀─────────── ACK ─────────────│
 │◀────── HEARTBEAT (Gateway)───│
 │                              │
 │──── TCP close ──────────────▶│
```

### 5.2 영상 흐름

```
Mock                          Gateway
 │                              │
 │──── TCP connect ────────────▶│
 │                              │
 │─ VIDEO_CHUNK seq=0 ─────────▶│   (첫 청크에서 station 검증)
 │◀─────────── ACK ─────────────│
 │─ VIDEO_CHUNK seq=1 ─────────▶│
 │◀─────────── ACK ─────────────│
 │              ...             │
 │─ VIDEO_CHUNK seq=N-1 ───────▶│
 │◀─────────── ACK ─────────────│
 │                              │
 │─── VIDEO_COMPLETE ──────────▶│   (조립 + 파일저장 + DB INSERT)
 │◀─────────── ACK ─────────────│
 │                              │
 │──── TCP close ──────────────▶│
```

---

## 6. 타임아웃 & 재연결 정책

| 항목 | 값 |
|---|---|
| Read timeout | 60초 (Gateway 기준) |
| Write timeout | 30초 |
| Heartbeat interval | 30초 |
| 재연결 백오프 | 1s → 2s → 4s → 8s → 16s → 32s → 60s (이후 최대 60s 유지) |

**Mock 구현 권장:**
- 송신 실패(`ConnectionResetError`, `BrokenPipeError` 등) → 위 백오프로 재연결
- Gateway가 연결을 끊으면 즉시 재연결 시도
- 연결 직후 Heartbeat 한 번 보내 확인 권장

---

## 7. 최소 구현 예시

### 7.1 Python (stdlib만 사용, gw_proto 미의존)

```python
import json
import socket
import struct
import uuid
from datetime import datetime, timezone

HOST, PORT = "localhost", 9000
STATION_ID = "2595693b-6142-49d7-9f13-0bb72d897ca6"

# 타입 코드
VIDEO_CHUNK    = 0x0001
VIDEO_COMPLETE = 0x0002
SENSOR_SAMPLE  = 0x0010
HEARTBEAT      = 0x0F00
ACK            = 0x0F01
ERROR          = 0x0FFF

def send_frame(sock, msg_type: int, payload: bytes) -> None:
    header = struct.pack("!II", len(payload), msg_type)
    sock.sendall(header + payload)

def recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Peer closed")
        buf += chunk
    return buf

def recv_frame(sock) -> tuple[int, bytes]:
    header = recv_exact(sock, 8)
    length, mtype = struct.unpack("!II", header)
    payload = recv_exact(sock, length) if length else b""
    return mtype, payload

# --- 시나리오: 센서 1건 ---
with socket.create_connection((HOST, PORT)) as s:
    payload = json.dumps({
        "station_id": STATION_ID,
        "measurement_type": "temperature",
        "value": 23.5,
        "unit": "C",
        "sampled_at": datetime.now(timezone.utc).isoformat(),
    }).encode()
    send_frame(s, SENSOR_SAMPLE, payload)
    mtype, body = recv_frame(s)
    print(f"response: 0x{mtype:04X} {body!r}")
    assert mtype == ACK

# --- 시나리오: 영상 3청크 ---
with socket.create_connection((HOST, PORT)) as s:
    video_id = str(uuid.uuid4())
    total = 3
    for seq in range(total):
        header = json.dumps({
            "video_id": video_id,
            "chunk_seq": seq,
            "total_chunks": total,
            "station_id": STATION_ID,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }).encode()
        binary = b"\x00" * 1024  # 임의 데이터
        send_frame(s, VIDEO_CHUNK, header + b"\n" + binary)
        assert recv_frame(s)[0] == ACK

    send_frame(s, VIDEO_COMPLETE, json.dumps({"video_id": video_id}).encode())
    mtype, body = recv_frame(s)
    print(f"complete: 0x{mtype:04X} {body!r}")
    assert mtype == ACK
```

### 7.2 gw_proto를 재사용하는 경우 (Python 한정)

`libs/gw_proto`를 pip install 하면 훨씬 간단합니다:

```python
import asyncio, json
from datetime import datetime, timezone
from gw_proto import Message, MessageType, StandardCodec, TcpClient

async def main():
    client = TcpClient("localhost", 9000, StandardCodec())
    await client.connect()
    payload = json.dumps({
        "station_id": "...", "measurement_type": "temperature",
        "value": 23.5, "unit": "C",
        "sampled_at": datetime.now(timezone.utc).isoformat(),
    }).encode()
    await client.send(Message(MessageType.SENSOR_SAMPLE, payload))
    resp = await client.receive()
    print(resp.msg_type, resp.metadata)
    await client.close()

asyncio.run(main())
```

---

## 8. 전송 전 준비사항

Gateway는 **등록된 station_id에 대해서만** 데이터를 받습니다. Mock 송신기가 소켓 데이터를 쏘기 전에 아래 두 단계로 준비합니다.

### 8.1 사전 등록된 테스트 개소

다음 4개 개소가 공용 저장소에 **사전 등록되어 있습니다**. Mock 송신기는 각 `station_name`으로 `station_id(UUID)`를 조회하여 SENSOR_SAMPLE 페이로드의 `station_id` 필드에 사용하세요.

| station_name | location_info |
|---|---|
| `FL-A01-NORTH` | Fab A line 1, north sector |
| `FL-A02-SOUTH` | Fab A line 2, south sector |
| `FL-B01-EAST`  | Fab B line 1, east sector  |
| `FL-C01-WEST`  | Fab C line 1, west sector  |

### 8.2 Mock 송신기의 station_id 조회

Mock은 기동 시점에 **DB에 직접 접속**하여 `station_name → station_id(UUID)`를 조회한 뒤, 그 UUID를 TCP 소켓의 SENSOR_SAMPLE 페이로드에 사용합니다.

**접속 정보 (개발 환경 기준):**

| 항목 | 값 |
|---|---|
| Host | PostgreSQL 컨테이너 (컨테이너 내부: `postgres`, 호스트: `localhost`) |
| Port | 컨테이너 내부 `5432`, 호스트 노출 `2345` |
| Database | `gateway_db` |
| User | **`gw_reader`** |
| Password | `dev_reader_pw` (dev 전용; 운영은 Docker secret/env로 교체) |
| 권한 | 모든 테이블 `SELECT` |

**조회 쿼리 예시 (Python asyncpg):**
```python
import asyncpg

STATION_NAMES = ["FL-A01-NORTH", "FL-A02-SOUTH", "FL-B01-EAST", "FL-C01-WEST"]

async def load_station_ids(dsn: str) -> dict[str, str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT station_name, station_id FROM station WHERE station_name = ANY($1::text[])",
            STATION_NAMES,
        )
    finally:
        await conn.close()
    return {r["station_name"]: str(r["station_id"]) for r in rows}

# dsn 예시:
#   Mock 컨테이너 내부 → "postgresql://gw_reader:dev_reader_pw@postgres:5432/gateway_db"
#   호스트에서 직접   → "postgresql://gw_reader:dev_reader_pw@localhost:2345/gateway_db"
```

### 8.3 권한 경계 재확인

- Mock은 `gw_reader` 사용 → station 조회만 가능. INSERT/UPDATE 시도 시 PostgreSQL이 차단
- 데이터 송신은 **TCP 소켓 경로로만** (`SENSOR_SAMPLE` 등)
- Gateway가 `gw_writer` 역할로 `sensor_sample`에 INSERT

---

## 9. 검증 방법

전송 후 DB에서 확인:

```bash
# 센서 적재 확인
docker exec -it sd-postgres psql -U postgres -d gateway_db \
  -c "SELECT * FROM sensor_sample ORDER BY id DESC LIMIT 5;"

# 영상 메타데이터 확인
docker exec -it sd-postgres psql -U postgres -d gateway_db \
  -c "SELECT video_id, station_id, file_path FROM video ORDER BY created_at DESC LIMIT 5;"

# 영상 파일 확인
docker exec -it sd-ingestion-gw ls -la /data/storage/videos

# 수신 로그 (실패 사유 포함)
docker exec -it sd-postgres psql -U postgres -d gateway_db \
  -c "SELECT message_type, status, error_message, created_at \
      FROM ingestion_log ORDER BY id DESC LIMIT 20;"
```

---

## 10. 자주 받는 에러

| 응답 `error` 문자열 | 원인 | 대응 |
|---|---|---|
| `Unknown station: xxx` | `station_id`가 DB에 없거나 `status='inactive'` | station 먼저 등록 |
| `Missing station_id` | JSON에 필드 누락 | 페이로드 확인 |
| `Bad sensor payload: ...` | 타입 오류, `sampled_at` 파싱 실패 등 | ISO 8601 포맷 사용 |
| `Malformed video chunk: ...` | JSON 헤더 누락, `\n` 없음, JSON 파싱 실패 | 헤더 + `\n` + body 구조 확인 |
| `No buffered video: xxx` | `VIDEO_COMPLETE`를 보냈는데 해당 `video_id`로 청크를 보낸 적 없음 | `video_id` 일관성 확인 |
| `Incomplete: M/N chunks` | 청크 일부 누락 상태에서 `VIDEO_COMPLETE` 전송 | 모든 seq 전송 확인 |

---

## 11. 변경 이력

| 버전 | 날짜 | 변경 |
|---|---|---|
| 0.1 | 2026-04-17 | 초안 작성. `standard` 프로토콜 기준. |
| 0.2 | 2026-04-18 | §8 확장: 4개 테스트 개소(`FL-A01-NORTH` 외) 자동 seed, Mock용 `gw_reader` DB 접속 안내 추가. |
| 0.3 | 2026-05-04 | §4.3a Egress→LOAS 메시지 명세 갱신. 판정 DB 스키마 이관(SocketDaim → Decision Agent)에 따라 테이블명 `decision`→`decision_record`, ENUM 값 한국어→영문(`normal/caution/warning`), 컬럼명 컴포넌트 기반 alias로 변경. |

향후 로아스 스펙 확정 시 `vendor` 프로토콜(type 코드 0x0001~0x0FFF 재매핑 가능)이 추가될 수 있습니다. 이 문서는 `standard` 기준이며, `vendor`용은 별도 문서로 발행 예정입니다.
