# PoolerTran

Correlator 가 페어링을 완료한 `cctv_frame` 을 감지하여 **waypoint 전환 시점에**
그 waypoint 의 프레임 묶음을 REST API 로 **한 번의 배치 POST**(`batch_paths` 모드)로
전송하고, 그 결과를 decision_agent 의 `decision_db.decision_record` 테이블에 적재하는
컨슈머.  결과/포이즌 메시지는 모두 decision_db 에 둔다(gateway_db 는 큐/소스 읽기 전용).

> 설계 근거: [PoolerTran_설계.md](../PoolerTran_설계.md)

## 동작 요약

1. SocketDaim 의 Correlator 가 `cctv_frame` 을 `dust_inspection` 과 페어링(UPDATE)하면,
   같은 트랜잭션에서 **AFTER UPDATE 트리거**가 `cctv_transfer_queue` 에 `frame_id` 를
   원자적으로 적재한다(백로그 큐).
2. PoolerTran 이 큐를 폴링(또는 `LISTEN cctv_transfer`)하다가 **waypoint 가 바뀌는
   순간에만** 직전 waypoint 의 큐 행을 claim(`FOR UPDATE OF q SKIP LOCKED`)하고,
   그 waypoint 의 프레임 묶음을 **한 번의 배치 POST**(`batch_paths`)로 REST 전송한다.
3. REST 성공 → 배치 응답(정적/동적 score 2쌍)을 임계로 분류해
   `decision_db.decision_record` 에 1행 INSERT → **큐 행 DELETE**.
4. 실패 → `attempts++` 후 행 유지(다음 폴링 재시도). `PT_MAX_ATTEMPTS` 초과 시
   `decision_db.transfer_dlq` 로 이동하고 큐에서 제거(포이즌 메시지 격리).

처리 순서는 항상 **① REST → ② decision_record(decision_db) → ③ 큐 DELETE** 이며,
②와 ③ 사이 크래시는 `dust_id` UNIQUE INSERT(멱등)로 흡수된다(at-least-once).

## 디렉토리

```
PoolerTran/
├── docker-compose.yml            # poolertran (gw-net external; DB 는 gateway_db 공유)
├── Dockerfile
├── migrations/
│   └── migrate_010_cctv_transfer_queue.sql   # gateway_db: 큐 + 트리거 + cctv_forwarder 롤만
│                                              #  (결과/DLQ 테이블 생성 없음 — decision_db 가 보유)
│                                              #  (SocketDaim 측에 적용해야 하는 선행 스키마)
├── src/poolertran/
│   ├── main.py            # 엔트리: 풀 생성 → poller + health gather, 시그널 처리
│   ├── config.py          # PT_* 환경변수
│   ├── db.py              # gateway / decision asyncpg 풀
│   ├── poller.py          # 폴링 루프 (waypoint 전환 → 배치 REST → decision_record → DELETE, DLQ, LISTEN)
│   ├── rest_client.py     # httpx 배치 POST (batch_paths)
│   ├── health.py          # FastAPI /health (queue depth + stats)
│   └── repository/
│       ├── queue_repo.py        # cctv_transfer_queue (gateway_db)
│       └── decision_producer.py # decision_record / transfer_dlq (decision_db)
└── tests/
```

## 부팅 순서

PoolerTran 은 SocketDaim 과 **독립적으로 가며, 전체 파이프라인에서 가장 마지막에
설치**한다. SocketDaim 및 다른 컨슈머가 모두 올라온 뒤 PoolerTran 을 설치하는데,
이때 `migrate_010` 적용 → PoolerTran 기동 순서로 진행한다.

```
# --- 먼저 떠 있어야 하는 것들 ---
1. SocketDaim        docker compose up -d            # gw-net + sd-postgres 생성
2. decision_agent    docker compose up -d            # decision_db(decision_record/transfer_dlq + detector 롤)
3. (기타 컨슈머: Dumopro 등)
```

> **참고 — PoolerTran 은 decision_db 에 결과를 적재한다.**
> PoolerTran 은 gateway_db(cctv_transfer_queue/cctv_frame/dust_inspection 읽기)에서
> 큐/소스를 읽고, 배치 REST 결과를 decision_agent 의 decision_db 에
> `decision_record` 1행으로 INSERT 한다(`sensor_analysis_role`=detector 롤 재사용).
> 포이즌 메시지는 `decision_db.transfer_dlq` 로 격리한다.  `final_decision` 은
> pending 으로 남기고 decision_agent 가 판정한다(PoolerTran 은 final_decision 을
> 쓰지 않는다).  따라서 **decision_db 스키마(decision_record/transfer_dlq/
> classification_threshold + detector 롤)가 먼저 존재해야 한다**
> (`decision_agent/init_db.sql` 이 생성).

```

# --- 가장 마지막: PoolerTran 설치 ---
4. migrate_010 적용   docker exec -i sd-postgres psql -U postgres -d gateway_db \
                          < migrations/migrate_010_cctv_transfer_queue.sql
5. PoolerTran        docker compose up -d --build    # poolertran
```

> ### ⚠️ 필수 규칙 — 반드시 `migrate_010 적용 → PoolerTran 기동` 순서를 지킬 것
>
> PoolerTran 은 `migrate_010` 이 생성하는 `cctv_forwarder` 롤로 gateway_db 에 접속하고,
> 같은 마이그레이션이 만든 `cctv_transfer_queue` 큐/트리거를 폴링한다. 따라서:
>
> - **migrate_010 을 적용하지 않은 채 PoolerTran 을 기동하면, 접속할 롤이 없어 부팅
>   단계에서 즉시 실패·종료한다.** (이는 재시도 로직으로 우회하지 않고, 운영 절차로
>   순서를 지키는 것을 전제로 한 설계다.)
> - 그러므로 4번(migrate_010)은 **반드시** 5번(PoolerTran 기동) **직전**에 적용한다.
> - migrate_010 미적용 상태는 SocketDaim 본체 동작에는 영향이 없으므로, PoolerTran
>   도입 전까지는 적용하지 않는다. (도입하는 그 시점에 위 순서로 함께 적용)
>
> **소유 경계**: `migrate_010` 은 `gateway_db` 를 변경하지만 SocketDaim 레포에는
> 넣지 않고 **PoolerTran 이 독립 소유·관리**한다. (설계 §5 의 "SocketDaim scripts/ 에
> 버전 관리" 방침은 SocketDaim 비침투 원칙으로 조정됨.)

## 접속

| 항목 | 위치 |
|---|---|
| Health / 모니터링 | `http://localhost:9109/health` |
| 결과 테이블 | decision_db 안 `decision_record` (`psql -U sensor_analysis_role -d decision_db`) |

`/health` 응답: `queue_depth`(백로그 적체), `stats`(processed_ok / failed /
dead_lettered / orphans_purged), DB 연결 상태.

## 환경변수 (`PT_` prefix)

전체 목록은 [.env.example](.env.example) 참조. 주요 항목:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PT_GW_DB_*` | `cctv_forwarder@postgres/gateway_db` | 큐/소스 접속 |
| `PT_DECISION_DB_*` | `sensor_analysis_role@postgres-decision/decision_db` | 결과 적재(decision_record/transfer_dlq) |
| `PT_REST_URL` | — | 전송 대상 (실제 엔드포인트로 교체) |
| `PT_REST_MODE` | `batch_paths` | 전송 모드 (현재 단독 지원) |
| `PT_POLL_INTERVAL_SEC` | `5` | 폴링 주기 |
| `PT_BATCH_SIZE` | `100` | 배치 claim 크기 |
| `PT_MAX_ATTEMPTS` | `10` | 초과 시 DLQ |
| `PT_USE_LISTEN` | `false` | LISTEN/NOTIFY 저지연 모드 |

## 테스트

```
pip install -r requirements.txt
PYTHONPATH=src pytest         # 단위 테스트
```

통합 테스트(실 DB 필요)는 `PT_TEST_GW_DSN` / `PT_TEST_DECISION_DSN` 설정 시 동작하며,
미설정 시 스킵된다.

## 미해결 / 후속 (설계 §13)

- 배치 REST 응답 스펙(정적/동적 score 2쌍) 최종 확정 — 인증
- 대량 트래픽 시 트리거 statement-level 전환 검토
- `migrate_010` 롤백 스크립트
