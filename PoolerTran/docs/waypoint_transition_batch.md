# PoolerTran — waypoint 전환 배치 감지 설계

> 목적: PoolerTran 폴링 중 `dust_inspection.waypoint_id` 가 바뀌는 순간을 감지하여,
> **직전 waypoint 에 속했던 모든 프레임**을 `(frame_id, dust_id, amr_id, received_at, file_path)`
> 리스트로 얻는 로직.

---

## 1. 요구사항 (요약)

1. poller 가 상태값을 유지: **`last_waypoint_id`, `last_frame_id`, `last_dust_id`**.
2. 매 폴링 시 `dust_inspection` 의 **현재 waypoint_id** 를 읽어 `last_waypoint_id` 와 비교.
3. **변경되었다면**, "변경 직전(`last_waypoint_id`)" 과 같은 waypoint_id 를 가진 **모든 data** 를
   `cctv_transfer_queue · cctv_frame · dust_inspection` 조인으로 찾아
   **`(frame_id, dust_id, amr_id, received_at, file_path)` 리스트**로 반환.
4. `last_frame_id`/`last_dust_id` 는 가장 최근 처리된 큐 행의 값으로 갱신(처리 high-water mark).

---

## 2. 데이터 모델 / 관계

| 테이블 | 키 컬럼 | 수명 |
|---|---|---|
| `dust_inspection` (gateway_db) | `id`(PK), `waypoint_id` | **영속** (cleaner retention 전까지 유지) |
| `cctv_frame` (gateway_db) | `id`(PK), `amr_id`, `file_path`, `received_at`, `dust_inspection_id`→di.id | **영속** |
| `cctv_transfer_queue` (gateway_db) | `frame_id`(PK)→cf.id, `dust_id`→di.id | **전이적** (처리 성공 시 DELETE) |

관계: `queue.frame_id = cctv_frame.id`, `cctv_frame.dust_inspection_id = dust_inspection.id`.
트리거(`migrate_010`)가 enqueue 시 `queue.dust_id = NEW.dust_inspection_id` 로 복사하므로
**`queue.frame_id == cctv_frame.id`, `queue.dust_id == cctv_frame.dust_inspection_id`** 가 항상 성립.

### ⚠️ 핵심 설계 결정 — 큐는 전이적, 프레임/dust 는 영속

PoolerTran 은 매 사이클 큐를 **드레인(처리 후 DELETE)** 한다. 따라서 waypoint 가 바뀌는
시점에는 **직전 waypoint 의 프레임이 이미 큐에서 삭제됐을 가능성이 높다.** "직전 waypoint 의
**모든** data" 를 얻으려면 **전이적인 `cctv_transfer_queue` 가 아니라 영속 테이블
`cctv_frame ⋈ dust_inspection` 을 기준(base)** 으로 조회해야 한다.

→ 본 설계의 배치 쿼리는 **`cctv_frame ⋈ dust_inspection` 을 base 로, `cctv_transfer_queue`
는 LEFT JOIN**(아직 큐에 남아있는지 부가정보)으로 둔다. 출력의 `frame_id`/`dust_id` 는
위 동치성에 따라 `cctv_frame.id` / `cctv_frame.dust_inspection_id` 에서 취한다(항상 존재).

> 대안(엄격 모드): 큐에 **아직 남아있는** 프레임만 원하면 `cctv_transfer_queue` 를 base 로
> 한 INNER JOIN 으로 바꾸면 된다(아래 §5 참고). 기본은 "모든 data" 이므로 영속 base 를 쓴다.

---

## 3. 처리 모델 — waypoint-batch (전환 시 처리·삭제)

**중요(변경됨):** PoolerTran 은 더 이상 매 사이클 per-row 로 처리/삭제하지 않는다.
평상시엔 큐에 **쌓기만** 하고, **waypoint 가 바뀌는 순간에만** 직전 waypoint 의 큐
행을 처리(① REST → ② decision_record(decision_db) → ③ 큐 DELETE)한다.  즉 **큐 DELETE 는 오직 waypoint
전환 시점에서만** 발생한다.

```
매 폴링 사이클(run 루프):
  current_wp = SELECT 최신 dust_inspection.waypoint_id        # = 현재 AMR 위치
  if current_wp is not None:
      if last_waypoint_id is not None and current_wp != last_waypoint_id:
          # 전환 감지 → 직전 waypoint 배치 목록 로깅 + 실제 처리/삭제
          batch_list = waypoint_batch(last_waypoint_id)        # (frame_id,dust_id,amr_id,received_at,file_path)[]  (로깅용)
          process_waypoint_batch(last_waypoint_id):
              while True:
                  rows = claim cctv_transfer_queue WHERE waypoint=last (FOR UPDATE SKIP LOCKED, batch)
                  _process_batch_rows(rows)                     # ①배치REST ②decision_record ③DELETE / 실패 시 attempts++·DLQ
                  if 더 없음 or 이번 패스 제거 0건: break
      last_waypoint_id = current_wp                            # 상태 갱신

_process_batch_rows 성공 시(행마다):
  last_frame_id = row.frame_id ; last_dust_id = row.dust_id     # 처리 high-water mark
```

- **per-row 즉시 처리 없음** → 같은 프레임 중복 REST 없음. waypoint 단위로 모아서 처리.
- 처리 순서는 행마다 여전히 ①→②→③ 이라 at-least-once(② 후 ③ 사이 크래시는 멱등 UPSERT 흡수).
- 미처리 큐 행은 waypoint 가 바뀔 때까지 큐에 남는다(백로그).

- **현재 waypoint_id 정의**: 가장 최근(`ORDER BY id DESC`) `dust_inspection` 행의 `waypoint_id`.
  단일 AMR(loas_amr_id=amr-01) 전제. NULL 이면 비교 스킵.
- **last_waypoint_id 초기값(sentinel)**: 기동 시 `PT_INIT_WAYPOINT_ID`(기본 -1, 실제 미사용
  waypoint)로 초기화. None 특수처리 없이 첫 실제 waypoint 가 항상 "신규"로 인식되며,
  sentinel 의 배치 claim 은 0건이라 무해(no-op). 비교는 `current_wp != last_waypoint_id`.
- **전환 직전 waypoint** = 직전 사이클에 기록해 둔 `last_waypoint_id`.
- **상태는 in-memory**(Poller 인스턴스). 재시작 시 초기화(첫 사이클은 비교 없이 last 만 세팅).
  필요 시 향후 영속화 가능(현재 범위 밖).

---

## 4. SQL

### 4.1 현재 waypoint_id
```sql
SELECT waypoint_id
  FROM dust_inspection
 WHERE waypoint_id IS NOT NULL
 ORDER BY id DESC
 LIMIT 1;
```

### 4.2 직전 waypoint 배치 (3-table join)
```sql
SELECT cf.id                 AS frame_id,
       cf.dust_inspection_id AS dust_id,
       cf.amr_id             AS amr_id,
       cf.received_at        AS received_at,
       cf.file_path          AS file_path
  FROM cctv_frame cf
  JOIN dust_inspection di       ON di.id = cf.dust_inspection_id
  LEFT JOIN cctv_transfer_queue q ON q.frame_id = cf.id      -- 큐 잔존 여부(부가)
 WHERE di.waypoint_id = $1
 ORDER BY cf.received_at, cf.id;
```
- `frame_id`/`dust_id` 는 `cctv_frame` 에서 취함(트리거 동치성으로 queue 값과 동일, 항상 존재).
- 권한: `cctv_forwarder` 가 세 테이블 모두 SELECT 가능(migrate_010 GRANT). 추가 권한 불필요.

---

## 5. 구현 매핑

| 항목 | 위치 |
|---|---|
| `WaypointFrame` + SQL 3개(`_CURRENT_WAYPOINT` / `_WAYPOINT_BATCH`(목록용) / `_SELECT_BATCH_FOR_WAYPOINT`(처리 claim용)) + `fetch_current_waypoint_id()` / `fetch_waypoint_batch()` / `fetch_batch_for_waypoint()` | `repository/queue_repo.py` |
| 상태값 `_last_waypoint_id`/`_last_frame_id`/`_last_dust_id`, `_check_waypoint_transition()`(전환 시 처리·삭제), `_process_waypoint_batch()`, run 루프에서 per-row 드레인 제거 | `poller.py` |

- 두 쿼리 역할 분리:
  - `_WAYPOINT_BATCH`(§4.2, 영속 base) → 전환 시 **목록 로깅용**(관측).
  - `_SELECT_BATCH_FOR_WAYPOINT`(큐 base, FOR UPDATE) → **실제 처리 claim용**.
- 행 처리는 `_process_batch_rows`(①배치REST ②decision_record ③DELETE)가 담당하며, 이제 **오직 `_process_waypoint_batch`
  (=전환 시점)에서만 호출**되므로 "DELETE 는 전환 시에만" 이 성립한다.
- run 루프는 더 이상 무조건 드레인하지 않는다(per-row 즉시 처리 제거).

### 무한 루프/포이즌 가드
`_process_waypoint_batch` 는 배치가 batch_size 미만이거나 **이번 패스에서 큐 제거가 0건**
(전부 실패→attempts++만)일 때 종료한다.  포이즌 행은 attempts 가 `PT_MAX_ATTEMPTS` 를
넘으면 DLQ 로 이동·삭제되어 자연 정리된다.

---

## 6. 가정 / 한계

- **다중 AMR 지원**: 모든 상태/쿼리가 **amr_id 를 키로** 사용한다. 현재 waypoint 는
  amr_id 별 최신 enqueue 행(`_CURRENT_WAYPOINTS`, DISTINCT ON amr_id)으로 독립 산출하고,
  `_last_waypoint_by_amr[amr_id]` 로 amr 별 직전값을 추적하며, 배치 claim/처리도
  `(amr_id, waypoint_id)` 로 한정한다. 새 amr 의 직전값 기본은 sentinel.
- **마지막 waypoint 미처리**: 다음 전환이 없으면(AMR 정지/시스템 종료) 그 waypoint 의 프레임은
  큐에 남아 처리되지 않는다. 필요 시 종료 시 flush 또는 타임아웃 기반 강제 처리 추가 검토.
- 상태 in-memory → 재시작 시 직전 waypoint 정보 소실. 단 **기동 시 큐 clear(§7)** 로
  이전 작업을 폐기하므로(현재/미래만 처리하는 모델) stranding 은 발생하지 않는다.
- waypoint 가 **A→B→A** 로 되돌아오면 같은 A 배치가 다시 잡힐 수 있음(되돌아온 시점 기준).
- cleaner retention 으로 cctv_frame/dust_inspection 이 지워지면 배치에서 빠짐(영속이지만 보존기간 내).
- dust 정보 JOIN 은 **`q.dust_id` 기준**(enqueue 시점 고정값)으로 한다.  `cctv_frame.dust_inspection_id`
  는 `ON DELETE SET NULL`(가변)이라 dust 행 purge 시 NULL 이 될 수 있어, cf 경유 JOIN 은
  연관을 잃는다.  `q.dust_id` 는 그로부터 분리된 안정 기록이라 purge 에 견고하다.

---

## 7. 큐 정리 정책 (cctv_transfer_queue 최소 크기 유지)

큐는 "미처리 작업 목록"이라 항상 작게 유지되어야 한다(검색/락/조인 성능).  전환 처리(§3)로
떠난 waypoint 는 정리되지만, 그 외 잔류(현재 waypoint 대기분, orphan, NULL waypoint 등)를
대비해 두 가지 정리 장치를 둔다.

### 7.1 기동 시 전체 삭제 — `PT_CLEAR_QUEUE_ON_START` (기본 true)
- 프로세스 기동 시 `DELETE FROM cctv_transfer_queue` 1회.  재시작하면 **이전 waypoint 작업은
  버리고 현재/미래만 처리**(비실시간·현재중심 모델).  WARNING 으로 명시 로깅(`queue_cleared_on_start`).
- ⚠️ **단일 인스턴스 전제** — 다중 인스턴스면 동료가 처리 중인 행까지 삭제된다.
- 권위 데이터(이미지/`cctv_frame`/`decision_db`)는 보존되며 큐(작업목록)만 비운다.

### 7.2 오래된 행 정리(age sweep) — `PT_QUEUE_MAX_AGE_SEC` (기본 21600s=6시간, 0=비활성)
- 매 사이클 `enqueued_at` 이 임계 초보다 오래된 행 삭제(원인 불문 안전망: 잔류/orphan/NULL 등).
- ⚠️ **임계값은 "AMR 이 한 waypoint 에 머무는 최대 시간"보다 충분히 커야 한다**(안전하게는
  ≥ 1 순회 시간 + 여유).  너무 작으면 **정상 처리 전인 현재-waypoint 프레임이 삭제**된다(데이터 유실).
  위험 비대칭: 작으면 정상 데이터 삭제(치명), 크면 잔류가 좀 더 오래 남을 뿐(경미) → **크게 잡는다**.
- 기본 6시간 = 예상 최대 순회(~4시간) + 여유.  순회가 짧으면 낮춰도 됨.

---

## 8. REST 전송 모드 (`PT_REST_MODE`)

전송 방식은 모드로 선택한다(rest_client.REGISTRY).  현재는 **`batch_paths` 단독 지원**이다.

| 모드 | 방식 | payload |
|---|---|---|
| **`batch_paths`** | **waypoint 단위 1콜** | `{amr_id, waypoint_id, frames:[{received_time, file_path}, …]}` |

### batch_paths 상세
- waypoint 전환 시 그 waypoint 의 프레임 목록을 **한 번의 POST** 로 전송(`BatchPathsRestClient.send_batch`).
- `received_time` = **epoch milliseconds(정수)** → 동일 초 내 다중 프레임도 구분 가능.
- 응답 = **정적/동적 결과 2쌍** `[{score,path1,path2}(정적), {score,path1,path2}(동적)]`(JSON 상 list[2]).
  poller 가 `_extract_dual` 로 두 score 를, `_static_p1` 으로 정적 결과의 첫 이미지 경로(path1)를 추출한다.
  `path1`·`path2` 는 입력 프레임 경로(데모는 첫 입력 프레임 경로를 echo).
- 성공 시: **`decision_db.decision_record` 에 배치당 1행**(`DecisionProducer.insert_decision`) + 큐 프레임 전체 DELETE.
  실패 시: 각 행 `_handle_failure`(attempts++/DLQ → `decision_db.transfer_dlq`).
- 한 waypoint 프레임 수가 `batch_size` 를 넘으면 여러 번의 배치 콜로 나뉜다.

### 결과 저장 (decision_record — 배치당 1행)
결과는 "한 (amr, waypoint) 배치당 1개"라 **`decision_db.decision_record` 에 1행**으로 기록한다
(decision_agent/init_db.sql 소유, detector 롤 재사용).  dust_value(대표=최댓값)·정적/동적 score 를
`classification_threshold`(`dust`/`static`/`dynamic`)로 분류해 3채널 결과
(`sensor_analysis_result`/`anomaly_detection_result`/`object_detection_result`)에 적재하며,
`final_decision` 은 `pending`(decision_agent 가 판정).  멱등 키는 `dust_id` UNIQUE(`ON CONFLICT DO NOTHING`).
정적 결과의 첫 이미지(path1)는 Base64 로 읽어 `image_b64` 에 저장한다.
PoolerTran 은 파일 저장/복사를 하지 않으며, 이미지 경로의 영속성은 AI(InferenceModule) 책임이다.
