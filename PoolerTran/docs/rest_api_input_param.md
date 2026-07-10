# PoolerTran 배치 REST API — 입력 파라미터 명세

> 대상: PoolerTran 의 `batch_paths` 전송 모드(`PT_REST_MODE=batch_paths`)가 **수신 서버로 보내는**
> 요청 본문 규격. 수신 서버(REST 엔드포인트)를 구현하는 쪽에서 참고한다.
> 구현 근거: [src/poolertran/rest_client.py](../src/poolertran/rest_client.py) `BatchPathsRestClient.send_batch`,
> 전송 시점: [docs/waypoint_transition_batch.md](waypoint_transition_batch.md)

---

## 1. 개요

PoolerTran 은 AMR 이 한 **waypoint 를 떠나는 순간(waypoint 전환)**, 그 waypoint 에서 수집된
CCTV 프레임 목록을 **한 번의 HTTP 요청**으로 수신 서버에 전송한다. 즉 요청 1건 = "한 AMR 의
한 waypoint 분량".

- 전송 주체: PoolerTran (`batch_paths` 모드)
- 전송 대상: `PT_REST_URL` 로 지정한 수신 서버
- 트리거: waypoint 전환 감지 시 (AMR·waypoint 별 1회). 프레임 수가 `PT_BATCH_SIZE` 를
  넘으면 같은 (amr, waypoint) 에 대해 여러 번 나뉘어 호출될 수 있다.

---

## 2. 엔드포인트 / 형식

| 항목 | 값 |
|---|---|
| Method | `POST` |
| URL | `PT_REST_URL` (예: `http://analysis-receiver:8000/batch`) |
| Content-Type | `application/json` |
| Body | 아래 §3 JSON 객체 |

---

## 3. 요청 본문 (입력 파라미터)

### 3.1 최상위 객체

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `amr_id` | string | ✅ | AMR 식별자. 출처: `cctv_frame.amr_id`. 예: `"amr-01"`. 이 배치의 모든 프레임이 같은 AMR. |
| `target_id` | integer | ✅ | **방금 완료된(=AMR 이 떠난) waypoint** 의 ID. 출처: `dust_inspection.target_id`. 이 배치의 모든 프레임이 같은 waypoint. |
| `frames` | array<object> | ✅ | 해당 (amr, waypoint) 에서 수집된 프레임 목록. 1개 이상. 각 원소는 §3.2. |

### 3.2 `frames[]` 원소 객체

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `received_time` | integer | ✅ | 프레임의 **게이트웨이 수신 시각(UTC)**, **epoch milliseconds(밀리초)**. 출처: `cctv_frame.received_at`. 예: `1780899354683`. |
| `file_path` | string | ✅ | 프레임 JPEG 파일의 절대 경로(공유 스토리지 기준). 출처: `cctv_frame.file_path`. 예: `"/data/storage/cctv/amr-01/2026-06-08/06/1780899354683407_V640p.jpg"`. |

> `received_time` 과 `file_path` 는 **프레임 단위로 한 쌍**으로 묶인다(평행 배열이 아님) →
> 시간↔경로 인덱스 어긋남 방지.

---

## 4. 필드 상세

### 4.1 `received_time` — 왜 밀리초(epoch ms)인가
- 단위: **epoch milliseconds**(1970-01-01 UTC 기준 경과 밀리초, 정수).
- 이유: AMR 은 초당 여러 장을 보낼 수 있어 **초 단위로는 동일 시각 프레임을 구분할 수 없다.**
  밀리초 해상도면 **같은 초에 들어온 여러 장도 서로 구분**된다.
  - 예: `1780899354683`(…54.683초)와 `1780899354912`(…54.912초)는 같은 54초지만 구별됨.
- 사람이 읽는 형태로 환산: `1780899354683` → `2026-06-08T06:15:54.683Z`.
- 산출: `int(cctv_frame.received_at.timestamp() * 1000)` (마이크로초는 절삭).

### 4.2 `file_path` — 경로 규칙
- 게이트웨이(ingestion_gateway)가 저장한 절대 경로이며 명명 규칙은:
  ```
  {storage_root}/cctv/{amr_id}/{YYYY-MM-DD}/{HH}/{epoch_us}_{resolution}.jpg
  예) /data/storage/cctv/amr-01/2026-06-08/06/1780899354683407_V640p.jpg
  ```
  - `{epoch_us}` = epoch **microseconds**(파일명 고유성용). `received_time`(ms)과 단위가 다름에 유의.
  - `{resolution}` = `V1080` / `V720p` / `V640p`.
- **주의**: 컨테이너 내부 경로(`/data/storage/...`)다. 수신 서버가 이 파일을 직접 읽으려면
  **동일 공유 스토리지를 같은 경로로 마운트**해야 한다(경로 방식).

### 4.3 `target_id` / `amr_id`
- 배치 1건은 항상 **단일 `amr_id` + 단일 `target_id`**. 다중 AMR 환경에서도 AMR 별로
  분리되어 전송된다(섞이지 않음).
- `target_id` 는 "지금 막 끝난" waypoint(직전 체류지)다. 현재 진행 중인 waypoint 는 포함되지 않는다.

---

## 5. 요청 예시

```http
POST /batch HTTP/1.1
Content-Type: application/json
```
```json
{
  "amr_id": "amr-01",
  "target_id": 5,
  "frames": [
    {
      "received_time": 1780899354683,
      "file_path": "/data/storage/cctv/amr-01/2026-06-08/06/1780899354683407_V640p.jpg"
    },
    {
      "received_time": 1780899354912,
      "file_path": "/data/storage/cctv/amr-01/2026-06-08/06/1780899354912330_V640p.jpg"
    },
    {
      "received_time": 1780899355100,
      "file_path": "/data/storage/cctv/amr-01/2026-06-08/06/1780899355100210_V640p.jpg"
    }
  ]
}
```

---

## 6. 응답 규약 (수신 서버가 지켜야 할 것)

PoolerTran 은 응답 상태코드로 흐름을 제어한다(`raise_for_status`).

| 상태코드 | 의미 | PoolerTran 동작 |
|---|---|---|
| `2xx` | 배치 수신 성공 | 배치를 **decision_db.decision_record 1행으로 적재 + 큐에서 프레임 전체 삭제** |
| `4xx` | 영구 오류(잘못된 페이로드 등) | 실패 처리 → 재시도하다 `PT_MAX_ATTEMPTS` 초과 시 DLQ(decision_db.transfer_dlq) |
| `5xx` / 전송오류 | 일시 장애 | 실패 처리 → 재시도(at-least-once) |

- **멱등성 권고**: PoolerTran 은 **at-least-once** 라 같은 배치(같은 frames)가 **재전송될 수 있다.**
  수신 서버는 `file_path`(또는 frame 식별자) 기준으로 **중복 수신을 멱등 처리**할 것.
- **응답 형식**: `list[2]` = `[{score,path1,path2}(정적), {score,path1,path2}(동적)]`. PoolerTran 은
  두 score 를 임계(`classification_threshold`)로 분류해 decision_record 의 3채널 결과에 기록하고,
  정적 결과의 `path1` 이미지를 Base64 로 읽어 `image_b64` 에 저장한다. 응답 본문(JSON)은
  `decision_record.result_payload` 에 그대로 적재된다. (`path1`·`path2` = 입력 프레임 경로.)

---

## 7. 관련 설정

| 환경변수 | 설명 |
|---|---|
| `PT_REST_MODE=batch_paths` | 이 배치 모드 활성화 |
| `PT_REST_URL` | 배치 수신 엔드포인트 URL |
| `PT_REST_TIMEOUT_SEC` | 요청 타임아웃(초) |
| `PT_BATCH_SIZE` | 한 호출에 담는 최대 프레임 수(초과분은 다음 호출로 분할) |
| `PT_MAX_ATTEMPTS` | 실패 재시도 한계(초과 시 DLQ) |

---

## 8. 요약

- 요청 = **한 AMR 의 한 완료 waypoint 분량**.
- 본문: `amr_id`(string), `target_id`(integer), `frames`(array). 각 frame = `received_time`(epoch ms) + `file_path`(string).
- `received_time` 은 **밀리초**라 동일 초 다중 프레임도 구분.
- 수신 서버는 **2xx 응답 + 멱등 처리**를 보장하면 된다.
