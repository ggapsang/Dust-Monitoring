# PoolerTran REST API 입·출력 구조

PoolerTran 이 waypoint 배치를 전송할 때 호출하는 REST API 의 **입력(요청 payload)** 과
**출력(응답 body)** 구조 정리. `PT_API_LOGGING=true` 일 때 `docker logs poolertran` 에
JSON 한 줄씩 찍히는 두 이벤트(`rest_api_request` / `rest_api_response`)를 기준으로 한다.

- 전송 단위: **waypoint 전환마다 그 waypoint 의 프레임 목록을 한 번의 POST** 로 전송 (`PT_REST_MODE=batch_paths`).
- 구현: [rest_client.py](../src/poolertran/rest_client.py) `BatchPathsRestClient`(실제) / `DemoRestClient`(데모).
- 데모 모드(`PT_REST_DEMO=true`): HTTP 호출 없이 동일 형식의 더미 응답을 반환. 로그엔 `"demo": true` 가 붙는다.

---

## 1. 로그 공통(envelope) 필드

structlog JSON(`PT_LOG_FORMAT=json`). 실제 API 데이터는 `payload`(입력)·`body`(출력) 안에 들어 있고,
바깥 필드는 로그 메타데이터다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `event` | string | `rest_api_request` 또는 `rest_api_response` |
| `level` | string | 로그 레벨(`info`) |
| `timestamp` | string | ISO8601 + KST 오프셋(`+09:00`) |
| `target_id` | int | 이번 배치의 관측 개소 ID |
| `demo` | bool | 데모 모드일 때만 `true`. 실제 호출이면 없음 |
| `url` | string | 실제 호출일 때 전송 대상 URL. 데모면 없음 |

---

## 2. 입력 — `rest_api_request`

POST 본문(`payload`)은 **waypoint 단위 프레임 배치**다.

### 구조
```json
{
  "amr_id": "amr-01",
  "target_id": 301,
  "frames": [
    { "received_time": "20260623160151_302",
      "file_path": "/.../SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070151_302.jpg" },
    "... (해당 waypoint 체류 동안 수신된 모든 CCTV 프레임)"
  ]
}
```

### 필드
| 경로 | 타입 | 설명 |
|---|---|---|
| `amr_id` | string | AMR 식별자 (예: `amr-01`) |
| `target_id` | int | 관측 개소 ID (예: 101/201/301/401) |
| `frames[]` | array | 그 waypoint 에서 캡처된 프레임 목록 |
| `frames[].received_time` | string | **수신 시각(KST)** `yyyymmddHHMMSS_sss`. 같은 초 내 다중 프레임도 `_sss` 로 구분 |
| `frames[].file_path` | string | CCTV 원본 이미지 **호스트 경로**(경로 변환 적용 후, 아래 §4 참고) |

> ⚠️ `received_time`(KST)과 `file_path` 안의 시각(UTC)은 다르다.
> 예: `received_time=20260623160151`(KST 16:01:51) ↔ `file_path` 의 `...070151...`(UTC 07:01:51). 동일 순간이다.

### 로그 예시 (데모)
```json
{"demo": true, "target_id": 301,
 "payload": {"amr_id": "amr-01", "target_id": 301,
   "frames": [{"received_time": "20260623160151_302",
     "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070151_302.jpg"},
     {"received_time": "20260623160151_801", "file_path": "...070151_801.jpg"}]},
 "event": "rest_api_request", "level": "info", "timestamp": "2026-06-23T16:02:42.711326+09:00"}
```

### 실제 캡처 예시 (운영서버 로그 전문)

운영서버에서 `docker logs poolertran` 으로 캡처한 실제 `rest_api_request` 한 줄(waypoint 301, 프레임 24개).
경로 prefix 는 그 서버의 `PT_PATH_REMAP_TO`(`/home/duckking/echopro/SocketDaim/storage`) 설정값이다.

```json
{"demo": true, "target_id": 301, "payload": {"amr_id": "amr-01", "target_id": 301, "frames": [{"received_time": "20260623160151_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070151_302.jpg"}, {"received_time": "20260623160151_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070151_801.jpg"}, {"received_time": "20260623160152_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070152_302.jpg"}, {"received_time": "20260623160152_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070152_801.jpg"}, {"received_time": "20260623160153_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070153_302.jpg"}, {"received_time": "20260623160153_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070153_801.jpg"}, {"received_time": "20260623160154_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070154_302.jpg"}, {"received_time": "20260623160154_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070154_801.jpg"}, {"received_time": "20260623160155_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070155_302.jpg"}, {"received_time": "20260623160155_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070155_801.jpg"}, {"received_time": "20260623160156_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070156_302.jpg"}, {"received_time": "20260623160156_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070156_801.jpg"}, {"received_time": "20260623160224_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070224_302.jpg"}, {"received_time": "20260623160224_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070224_801.jpg"}, {"received_time": "20260623160226_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070226_302.jpg"}, {"received_time": "20260623160226_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070226_801.jpg"}, {"received_time": "20260623160228_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070228_302.jpg"}, {"received_time": "20260623160228_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070228_801.jpg"}, {"received_time": "20260623160223_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070223_302.jpg"}, {"received_time": "20260623160223_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070223_801.jpg"}, {"received_time": "20260623160225_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070225_302.jpg"}, {"received_time": "20260623160225_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070225_801.jpg"}, {"received_time": "20260623160227_302", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070227_302.jpg"}, {"received_time": "20260623160227_801", "file_path": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070227_801.jpg"}]}, "event": "rest_api_request", "level": "info", "timestamp": "2026-06-23T16:02:42.711326+09:00"}
```

**이 예제로 본 §4 출력 규칙 적용** (예: 첫 프레임 기준):
입력 `.../cctv/amr-01/2026-06-23/07/300_20260623070151_302.jpg` →
```
result/static/amr-01/2026-06-23/07/300_20260623070151_302_1.jpg
result/static/amr-01/2026-06-23/07/300_20260623070151_302_2.jpg
result/dynamic/amr-01/2026-06-23/07/300_20260623070151_302_1.jpg
result/dynamic/amr-01/2026-06-23/07/300_20260623070151_302_2.jpg
```
> 같은 배치의 `rest_api_response` 는 §3 형식으로 직후에 이어진다.

---

## 3. 출력 — `rest_api_response`

응답 `body` 는 **dual 결과** = 길이 2 리스트. **[0]=정적분진(static), [1]=동적분진(dynamic)**.

### 구조
```json
[
  { "score": 0.2, "path1": "<배치 첫 프레임 경로>", "path2": "<배치 첫 프레임 경로>" },
  { "score": 0.2, "path1": "<배치 첫 프레임 경로>", "path2": "<배치 첫 프레임 경로>" }
]
```

### 필드
| 경로 | 타입 | 설명 |
|---|---|---|
| `body[0]` | object | **정적분진(static)** 결과 |
| `body[1]` | object | **동적분진(dynamic)** 결과 |
| `body[*].score` | float | 분진 점수. 분류 임계 **0.5** 기준(≥0.5 → abnormal) |
| `body[*].path1` | string | 결과 이미지 경로 1 (데모는 배치 **첫 프레임 경로** echo) |
| `body[*].path2` | string | 결과 이미지 경로 2 (데모는 동일 echo) |

### 데모 score 규칙
| 모드 | 동작 |
|---|---|
| `PT_REST_DEMO_VERSION=1` (기본) | 정적·동적 모두 `PT_REST_DEMO_SCORE` 고정 |
| `PT_REST_DEMO_VERSION=2` | waypoint별 프로필(`_WP_PROFILE`): 101→(0.7,0.7) danger, 201→(0.2,0.2) normal, 301→(0.2,0.2) warning, 401→(0.7,0.2) caution. 미등록 waypoint 는 기본 score |

### 로그 예시 (데모, waypoint 301 · version 2 → score 0.2/0.2)
```json
{"demo": true, "target_id": 301, "status": 200,
 "body": [
   {"score": 0.2, "path1": "/home/duckking/echopro/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070151_302.jpg", "path2": "...302.jpg"},
   {"score": 0.2, "path1": "...302.jpg", "path2": "...302.jpg"}],
 "event": "rest_api_response", "level": "info", "timestamp": "2026-06-23T16:02:42.713xxx+09:00"}
```
> 실제 호출이면 `demo` 가 없고 `url`, `status`(수신 서버 HTTP 코드)가 붙으며 `body` 는 수신 서버가 반환한 값이다.
> 데모의 `path1`/`path2` 는 입력 첫 프레임 경로 echo 지만, **실제 REST 의 출력 이미지 경로 생성 규칙은 §4** 를 따른다.

---

## 4. 출력 이미지 경로 생성 규칙 (실제 REST)

데모(`PT_REST_DEMO=true`)는 `path1`/`path2` 에 입력 첫 프레임 경로를 **echo** 만 한다.
**실제 REST 서버(AnalysisReceiver)** 는 응답 `path1`/`path2` 가 가리키는 **결과 이미지 4개를 실제로 생성**한다
(정적 path1·path2, 동적 path1·path2). 경로는 **입력 프레임 경로를 그대로 미러링**하되 아래만 바꾼다.

| 구간 | 입력(cctv) | 출력(result) |
|---|---|---|
| 네임스페이스 | `cctv/` | `result/` |
| 카테고리 | (없음) | `static/`(=`body[0]`) 또는 `dynamic/`(=`body[1]`) |
| `<amr>/<UTC date>/<UTC hour>/` | 그대로 | **그대로 유지(미러)** |
| 파일명 | `<stem>.jpg` | `<stem>_1.jpg`(=`path1`) / `<stem>_2.jpg`(=`path2`) |

- 날짜·시(hour)는 **입력과 동일(UTC)** 로 유지 → 입력↔출력 1:1 매핑.
- 파일명 끝 `_1`/`_2` 는 응답의 `path1`/`path2` 에 1:1 대응.

### 변환 예
입력:
```
/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-23/07/300_20260623070151_302.jpg
```
출력 4개 + 대응 응답:
```
정적 path1: /home/daim/svc/SocketDaim/storage/result/static/amr-01/2026-06-23/07/300_20260623070151_302_1.jpg
정적 path2: /home/daim/svc/SocketDaim/storage/result/static/amr-01/2026-06-23/07/300_20260623070151_302_2.jpg
동적 path1: /home/daim/svc/SocketDaim/storage/result/dynamic/amr-01/2026-06-23/07/300_20260623070151_302_1.jpg
동적 path2: /home/daim/svc/SocketDaim/storage/result/dynamic/amr-01/2026-06-23/07/300_20260623070151_302_2.jpg
```
```json
[
  {"score": 0.7, "path1": ".../result/static/amr-01/2026-06-23/07/300_20260623070151_302_1.jpg",
                 "path2": ".../result/static/amr-01/2026-06-23/07/300_20260623070151_302_2.jpg"},
  {"score": 0.7, "path1": ".../result/dynamic/amr-01/2026-06-23/07/300_20260623070151_302_1.jpg",
                 "path2": ".../result/dynamic/amr-01/2026-06-23/07/300_20260623070151_302_2.jpg"}
]
```

### 운영 주의
- **쓰기 주체**: 결과 이미지는 **수신 REST 서버**가 생성하므로 그 컨테이너가 같은 호스트 storage 를 **rw 로 마운트**해야 한다(PoolerTran 은 `:ro`).
- **retention**: `sd-cleaner` 는 `storage/cctv` 만 정리한다. `storage/result/` 는 별도라 정리 대상에 추가하지 않으면 무한 증가한다.

---

## 5. file_path 경로 변환 (PT_PATH_REMAP)

`frames[].file_path` 는 컨테이너 내부 경로를 **수신 서버가 접근할 호스트 경로**로 치환해 전송한다.

- `PT_PATH_REMAP_FROM`(기본 `/data/storage`) prefix → `PT_PATH_REMAP_TO` 로 교체.
- `PT_PATH_REMAP_TO` 가 비면 변환하지 않음.
- 예: `/data/storage/cctv/...` → `/home/<user>/svc/SocketDaim/storage/cctv/...` (운영서버 실제 경로로 설정).

---

## 6. 다음 소비처

응답 `body`(dual list[2]) 는 [poller.py](../src/poolertran/poller.py) 의 `_extract_dual` / `_static_p1`
이 그대로 소비해 `decision_db.decision_record` 에 적재한다.
정적 score + 동적 score + dust(IOT) 임계 조합으로 최종 4단계 판정(normal/caution/warning/danger)이 결정된다.

| waypoint | dust(iot) | static | dynamic | final_decision |
|---|---|---|---|---|
| 201 | 0.5 normal | normal | normal | **normal** |
| 401 | 0.5 normal | abnormal | normal | **caution** |
| 301 | 2.5 abnormal | normal | normal | **warning** |
| 101 | 2.5 abnormal | abnormal | abnormal | **danger** |

---

## 7. 로그 확인 명령

```bash
# 입출력 동시 추적
docker logs -f poolertran 2>&1 | grep -E 'rest_api_request|rest_api_response'

# 입력만 / 출력만
docker logs poolertran 2>&1 | grep rest_api_request
docker logs poolertran 2>&1 | grep rest_api_response

# 보기 좋게(jq 있으면)
docker logs --since 10m poolertran 2>&1 | grep -E 'rest_api_request|rest_api_response' | jq .
```
> 켜기: `PT_API_LOGGING=true` 로 바꾼 뒤 `docker compose -f docker-compose.deploy.yml up -d poolertran` 로 재생성.
> REST 호출은 큐에 waypoint 배치가 들어와야 발생하므로, `send_loas_scenario.py` 송신으로 유발한다.

