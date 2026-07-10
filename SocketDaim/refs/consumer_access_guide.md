# 공용 저장소 Consumer 접근 가이드

> 문서 목적: Ingestion Gateway가 수집한 데이터를 **읽어서 사용**하는 개발자를 위한 가이드. 센서값/영상을 학습·추론·분석에 쓰려는 Autoencoder, YOLO, Dumopro 등의 Consumer 팀이 대상.
>
> 자매 문서:
> - [gateway_plan.md](gateway_plan.md) — 전체 아키텍처
> - [gw_protocol_spec.md](gw_protocol_spec.md) — 데이터를 쏘는 쪽(Mock/로아스)용 스펙

---

## 1. Consumer의 역할과 책임 경계

### 1.1 내가 이 문서를 읽어야 하는 사람인가

- 공용 저장소의 센서값(`sensor_sample`), 영상(`video`) 을 SELECT/파일 read로 가져가 처리하는 사람
- 결과는 **자신의 독자 DB** 에 저장함 (Autoencoder DB, YOLO DB, Dumopro DB 등)
- Gateway의 TCP 소켓을 직접 건드리지 않음

### 1.2 접근 원칙 (WORM)

공용 저장소는 **Write Once, Read Many** 원칙을 따릅니다.

- Consumer는 **SELECT만** 허용 (테이블 INSERT/UPDATE/DELETE 불가)
- 영상 파일도 **읽기 전용 마운트** (수정·삭제 금지)
- 권한은 PostgreSQL role 레벨에서 강제됨 (`gw_reader` 계정이 애초에 SELECT 권한만 갖음)

### 1.3 해서는 안 되는 일

| 금지 | 대안 |
|---|---|
| 공용 저장소의 테이블/행을 수정 | 결과는 Consumer 자신의 독자 DB에 씀 |
| Gateway의 TCP 9000 포트를 건드림 | SELECT로 DB만 조회 |
| 영상 파일을 원본 경로에 덮어쓰거나 삭제 | 필요하면 자신의 워크스페이스에 복사 |
| station 테이블에 직접 row 추가 | Station 등록은 관리자 도구의 책임 |

---

## 2. 전체 데이터 흐름 (Consumer 관점)

```
 ┌─────────────────┐   TCP 9000    ┌────────────────────┐
 │ Mock / 로아스   │ ────────────▶ │ Ingestion Gateway  │
 └─────────────────┘                └──────────┬─────────┘
                                               │ INSERT (gw_writer)
                                               ▼
                                    ┌──────────────────────┐
                                    │  공용 저장소          │
                                    │  Postgres + 파일볼륨  │
                                    └──────────┬───────────┘
                                               │ SELECT (gw_reader)
                                               │ 파일 read-only mount
                                               ▼
                              ┌─────────────────────────────────┐
                              │  Consumer (Autoencoder/YOLO/…)  │
                              │   · 공용 저장소는 읽기만        │
                              │   · 결과는 자기 독자 DB에 write │
                              └─────────────────────────────────┘
```

핵심: **공용 저장소 = 읽기**, **독자 DB = 자기가 쓰는 곳**.

---

## 3. 접속 정보

### 3.1 PostgreSQL

| 항목 | 값 | 비고 |
|---|---|---|
| Host | `postgres` (같은 Docker network 내), `localhost` (호스트에서) | |
| Port | `5432` (컨테이너 내부), `2345` (호스트 노출) | |
| Database | `gateway_db` | |
| User | **`gw_reader`** | |
| Password | `dev_reader_pw` | dev 전용. 운영 시 Docker secret/env로 교체 |
| 권한 | 모든 테이블 `SELECT` | INSERT/UPDATE/DELETE는 권한 없음 |

**DSN 예시:**
```
postgresql://gw_reader:dev_reader_pw@postgres:5432/gateway_db     # 컨테이너 내부
postgresql://gw_reader:dev_reader_pw@localhost:2345/gateway_db    # 호스트
```

### 3.2 영상 파일 볼륨

영상 실물은 PostgreSQL이 아니라 **파일 볼륨**에 저장됩니다. DB에는 경로만 있습니다.

- Docker named volume: `socketdaim_video-storage`
- Ingestion Gateway 컨테이너 내부 마운트 경로: `/data/storage`
- 경로 규칙: `/data/storage/videos/{station_id}/{YYYY-MM-DD}/{video_id}.{ext}`

Consumer 컨테이너도 이 볼륨을 **read-only**로 마운트해야 영상 파일을 읽을 수 있습니다 ([§6](#6-영상-파일-읽기) 참조).

---

## 4. 테이블 구조 요약

Consumer 관점에서 자주 쓰는 컬럼만 요약. 상세 스키마는 [init_db.sql](../init_db.sql).

### 4.1 `station` — 개소 메타

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `station_id` | UUID | PK. 모든 JOIN의 기준 |
| `station_name` | varchar | 식별자 문자열 (예: `FL-A01-NORTH`) |
| `location_info` | text | 위치 설명 |
| `status` | varchar | `collecting` / `waiting` / `training` / `inferring` / `inactive` |
| `amr_id` | varchar | 촬영 AMR ID (있을 경우) |
| `capture_cycle` | int | 촬영 주기(초) |

### 4.2 `video` — 수신 영상 메타

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `video_id` | UUID | PK |
| `station_id` | UUID | FK → `station` |
| `file_path` | text | **영상 실물 경로** (`/data/storage/videos/...`) |
| `captured_at` | timestamptz | 촬영 시각 (송신측이 제공) |
| `duration_sec` | double | 재생 시간 |
| `resolution` | varchar | `"1920x1080"` 등 |
| `amr_position` | jsonb | AMR 위치 정보 |
| `quality_check_result` | jsonb | 자동 품질 검사 결과 |
| `is_valid` | bool | 품질 OK 여부 |
| `is_excluded` | bool | 학습 제외 플래그 (관리자 지정) |
| `created_at` | timestamptz | Gateway가 수신·저장한 시각 |

> 주의: `is_valid=false` 또는 `is_excluded=true` 영상은 **학습용으로 쓰면 안 됩니다**. 쿼리에 기본 필터 넣으세요.

### 4.3 `sensor_sample` — 센서 측정치

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | bigserial | PK (증가 순서 = 도착 순서) |
| `station_id` | UUID | FK → `station` |
| `measurement_type` | varchar | `"temperature"`, `"dust_density"` 등 |
| `value` | double | 측정값 |
| `unit` | varchar | `"C"`, `"ppm"` 등 |
| `sampled_at` | timestamptz | 센서가 측정한 시각 (송신측 제공) |
| `received_at` | timestamptz | Gateway가 수신·INSERT한 시각 |

> `sampled_at` 과 `received_at` 을 구분해 쓰세요. 네트워크 지연·재전송 시 두 값이 다를 수 있습니다. 분석은 보통 `sampled_at` 기준, 신규 데이터 감지(폴링)는 `received_at` 기준이 무난합니다.

### 4.4 `ingestion_log` — Gateway 에러 로그

Consumer가 일반적으로 볼 필요 없는 테이블. 성공 로그는 기록 안 함(structlog stdout으로만). 에러만 남습니다. 수신 실패 패턴을 조사할 때만 참조.

---

## 5. 자주 쓰는 쿼리 패턴

### 5.1 최근 센서값 (station별)

```sql
-- 지정 station 최근 1시간 센서값
SELECT ss.measurement_type, ss.value, ss.unit, ss.sampled_at
FROM sensor_sample ss
JOIN station s USING (station_id)
WHERE s.station_name = $1
  AND ss.sampled_at >= NOW() - INTERVAL '1 hour'
ORDER BY ss.sampled_at;
```

### 5.2 시간 윈도우 시계열

```sql
-- station + measurement_type별, 시간 범위 시계열
SELECT sampled_at, value
FROM sensor_sample
WHERE station_id = $1
  AND measurement_type = $2
  AND sampled_at >= $3 AND sampled_at < $4
ORDER BY sampled_at;
```

### 5.3 학습 가능한 영상 목록

```sql
-- 지정 station의 유효 영상 (학습 제외 아님)
SELECT video_id, file_path, captured_at, duration_sec
FROM video
WHERE station_id = $1
  AND is_valid = true
  AND is_excluded = false
ORDER BY captured_at;
```

### 5.4 신규 데이터 폴링 (이후 도착분 가져오기)

```sql
-- 마지막으로 본 id 이후 새 센서값
SELECT id, station_id, measurement_type, value, sampled_at
FROM sensor_sample
WHERE id > $1              -- 마지막으로 처리한 id
ORDER BY id
LIMIT 1000;

-- 마지막으로 본 created_at 이후 새 영상
SELECT video_id, station_id, file_path, captured_at
FROM video
WHERE created_at > $1
ORDER BY created_at;
```

### 5.5 집계 예시

```sql
-- station별 최근 24시간 샘플 수
SELECT s.station_name, COUNT(*) AS samples
FROM sensor_sample ss
JOIN station s USING (station_id)
WHERE ss.sampled_at >= NOW() - INTERVAL '24 hours'
GROUP BY s.station_name
ORDER BY samples DESC;

-- measurement_type별 평균/분위수
SELECT measurement_type,
       COUNT(*) AS n,
       AVG(value) AS mean,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY value) AS p95
FROM sensor_sample
WHERE sampled_at >= NOW() - INTERVAL '1 hour'
GROUP BY measurement_type;
```

---

## 6. 영상 파일 읽기

### 6.1 파일 경로 해석

`video.file_path` 컬럼 값은 Gateway 컨테이너 기준 절대 경로입니다 (예: `/data/storage/videos/<station_id>/2026-04-18/<video_id>.bin`). Consumer가 같은 경로로 접근하려면 **동일 볼륨을 마운트**해야 합니다.

### 6.2 Docker Compose에서 볼륨 공유 (권장)

Consumer 서비스 정의에 다음 추가:
```yaml
services:
  autoencoder-consumer:
    image: ...
    volumes:
      - video-storage:/data/storage:ro    # read-only 마운트
    depends_on:
      - postgres
    networks:
      - gw-net

volumes:
  video-storage:
    external: true
    name: socketdaim_video-storage        # Gateway가 생성한 볼륨 이름
```

`:ro` 플래그로 읽기 전용 보장. 쓰기 시도하면 OS 레벨에서 거부됩니다.

### 6.3 파이썬에서 파일 열기

```python
import asyncpg

async def load_latest_video(pool, station_id):
    row = await pool.fetchrow(
        "SELECT video_id, file_path FROM video "
        "WHERE station_id=$1 AND is_valid AND NOT is_excluded "
        "ORDER BY captured_at DESC LIMIT 1",
        station_id,
    )
    if row is None:
        return None
    with open(row["file_path"], "rb") as f:
        return row["video_id"], f.read()
```

### 6.4 호스트에서 직접 확인 (디버깅용)

```bash
# Gateway 컨테이너 내부 쉘로 들어가서 보기
docker exec -it sd-ingestion-gw ls -la /data/storage/videos

# 또는 볼륨을 임시 컨테이너에 마운트해서 보기
docker run --rm -v socketdaim_video-storage:/data alpine ls -la /data/videos
```

---

## 7. 신규 데이터 감지 전략

Gateway는 현재 **이벤트 알림을 쏘지 않습니다**. Consumer는 다음 중 하나를 선택합니다.

### 7.1 주기 폴링 (현재 권장)

간단하고 충분. 5~10초 간격으로 `received_at > 마지막_체크시점` 쿼리:

```python
last_seen_id = 0
while True:
    rows = await pool.fetch(
        "SELECT id, station_id, measurement_type, value, sampled_at "
        "FROM sensor_sample WHERE id > $1 ORDER BY id LIMIT 500",
        last_seen_id,
    )
    for r in rows:
        await process(r)
        last_seen_id = r["id"]
    if not rows:
        await asyncio.sleep(5)
```

### 7.2 PostgreSQL `LISTEN`/`NOTIFY` (장래 확장)

현재 Gateway는 NOTIFY를 발행하지 않습니다. 필요 시 Consumer 요청을 받아 Gateway 쪽에 `pg_notify(...)` 호출을 추가할 수 있습니다. 별도 요구사항으로 제기해 주세요.

### 7.3 Redis Pub/Sub (장래 확장)

Gateway 부하 분리가 필요해지면 Redis를 도입하여 수신 즉시 이벤트 발행하는 구조로 확장 가능. 역시 현재는 미적용.

---

## 8. 개소(Station) 참조

### 8.1 사전 등록된 4개 테스트 개소

개발·테스트 환경에는 다음 4개가 상시 존재합니다:

| station_name | 용도 |
|---|---|
| `FL-A01-NORTH` | Mock 시뮬레이션 및 소켓 수신 테스트 |
| `FL-A02-SOUTH` | 동일 |
| `FL-B01-EAST` | 동일 |
| `FL-C01-WEST` | 동일 |

### 8.2 station_name → station_id 조회 예시

```python
STATIONS = ["FL-A01-NORTH", "FL-A02-SOUTH", "FL-B01-EAST", "FL-C01-WEST"]
rows = await pool.fetch(
    "SELECT station_name, station_id FROM station WHERE station_name = ANY($1::text[])",
    STATIONS,
)
name_to_id = {r["station_name"]: r["station_id"] for r in rows}
```

운영 환경에서는 추가 station이 관리자 도구로 등록될 수 있으니 코드에 station_name을 하드코딩하지 말고 DB에서 조회하세요.

---

## 9. Consumer별 독자 DB (본 가이드 범위 외)

각 Consumer가 분석 결과를 저장할 **자기 전용 DB**는 본 저장소와 별개입니다:

| Consumer | 독자 DB 예시 테이블 (책임 범위 밖) |
|---|---|
| Autoencoder | `training_job`, `model`, `inference_result` |
| YOLO | `detection_result` 등 |
| Dumopro | `dumopro_result` 등 |

본 문서는 **공용 저장소 읽기**까지만 다루며, Consumer 독자 DB 스키마는 각 팀의 구현 영역입니다. 결과를 Egress Gateway가 로아스로 송신하는 방식은 [gateway_plan.md §6](gateway_plan.md) 참조.

---

## 10. 문제 상황별 점검 체크리스트

| 증상 | 점검 |
|---|---|
| 연결 거부 | 호스트 접속이면 포트 `2345` 썼는지 (5432 아님). 컨테이너끼리면 `postgres:5432` |
| `permission denied for table` | `gw_reader` 쓰고 있는지. `gw_admin`/`gw_writer`로 조회하지 말 것 |
| 영상 파일을 열 수 없음 (`FileNotFoundError`) | Consumer 컨테이너에 `socketdaim_video-storage` 볼륨 마운트 되어 있는지 |
| `file_path`는 있는데 파일이 없음 | Gateway가 기동 중 크래시 등으로 INSERT는 됐지만 파일 write 실패 가능성. `ingestion_log`에 에러 있는지 확인 |
| 새 데이터가 안 보임 | Gateway가 살아있는지 (`docker compose ps`). Mock 송신기가 실제로 ACK를 받고 있는지 |
| 쿼리가 느림 | `sampled_at`, `station_id` 인덱스 사용 여부 확인 (`EXPLAIN` 찍어보기) |

---

## 11. 권한 경계 재확인

Consumer가 `gw_reader`로 접속하면 DB가 다음을 차단합니다:

```sql
-- 아래는 모두 permission denied
INSERT INTO sensor_sample (...) VALUES (...);
UPDATE video SET is_excluded = true WHERE ...;
DELETE FROM station WHERE ...;
```

이는 설계상 **의도된 제약**입니다. Consumer가 공용 저장소를 오염시키는 것을 PostgreSQL 레벨에서 원천 차단하기 위함입니다.

- 공용 저장소 쓰기: Gateway (`gw_writer`) 만
- station 수정: 관리자 도구 (`gw_admin`) 만
- Consumer 결과 쓰기: 각자의 독자 DB

---

## 12. 변경 이력

| 버전 | 날짜 | 변경 |
|---|---|---|
| 0.1 | 2026-04-18 | 초안. 공용 저장소 SELECT + 영상 파일 read-only 접근 가이드. |
