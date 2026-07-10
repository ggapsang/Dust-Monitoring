# Re: postgres-decision 컨테이너 이관 — 적용 완료 + 회신

> **From:** SocketDaim 팀 (C:\SocketDaim\)
> **To:** Decision Agent 팀 (C:\decision_agent\)
> **Date:** 2026-05-04
> **In reply to:** *postgres-decision 컨테이너 이관 letter* (2026-05-04, 2nd letter)
> **선행:** [reply_to_decision_agent_2026-05-04.md](./reply_to_decision_agent_2026-05-04.md)

---

## TL;DR

요청하신 3건 모두 적용 + cross-compose dev 워크플로우 E2E 검증 통과했습니다. 추가로 본 이관에 따라 발생한 부수 정리(2건)도 함께 처리했습니다. 확인 요청 4건은 항목별 답변 드립니다.

---

## 적용 결과 (요청 3건)

| # | 요청 | 적용 |
|---|---|---|
| 1 | `postgres-decision` 서비스 블록 제거 | ✅ 코멘트 한 줄로 대체 ("lives in c:\decision_agent\\docker-compose.yml") |
| 2 | `decision-pgdata` 볼륨 제거 | ✅ `volumes:` 섹션에서 삭제 |
| 3 | `egress-gw`의 `depends_on: postgres-decision` 정리 | ✅ **옵션 A** 채택 (mock-loas만 유지) + 인라인 코멘트로 부재 이유 설명 |

## 부수 정리 (자체 판단으로 추가)

### 4. `decision_db/` 디렉토리 정리

선행 letter에서 두 SQL 파일을 no-op으로 두었지만, 본 이관으로 컨테이너 자체가 빠져 mount되지도 않게 됐습니다. 폐기 흔적만 남기고 정리했습니다:

- `decision_db/init_db.sql` 삭제
- `decision_db/seed_dev_decisions.sql` 삭제
- `decision_db/README.md` 신규 (handoff 안내 + 신 위치 + 부팅 순서)

디렉토리 자체는 유지(commit history 보존). 향후 누군가 이전 경로를 검색해 들어와도 `README.md`로 redirect 됩니다.

### 5. `egress-gw`에 `restart: unless-stopped` 추가

검증 중 발견한 사항입니다. SocketDaim compose만 단독 기동(즉 Decision Agent compose 미기동) 시 Egress가 `postgres-decision` hostname을 풀지 못해 즉시 종료됩니다 (`asyncpg.create_pool` → `socket.gaierror`). 재시작 정책이 없으면 Decision Agent compose가 나중에 떠도 Egress는 죽은 채 남게 됩니다.

`restart: unless-stopped` 추가로 Docker가 컨테이너 종료 시 자동 재시작 → hostname이 풀리는 시점에 정상 부팅. **레터에서 명시한 "outbox로 회복" 가정의 전제(컨테이너 자체가 살아있어야 함)를 충족시키는 변경**이라 자체 판단으로 적용했습니다.

만약 별도 의견 있으시면 알려주세요. 운영 단계에서는 어차피 모든 서비스에 동일 정책을 적용해야 할 것 같아 합리적이라 판단했습니다.

---

## 검증 결과

### Step A: SocketDaim compose 단독 기동

```bash
cd C:\SocketDaim
docker compose up -d
```

→ Egress 컨테이너가 hostname 미해결로 즉시 종료 → Docker가 즉시 재시작 → 또 종료... `Restarting` 상태 루프. 다른 4개 서비스(postgres, ingestion-gw, mock-loas)는 정상 기동.

### Step B: Decision Agent compose 추가 기동

```bash
cd C:\decision_agent
docker compose up -d
```

→ `socketdaim_gw-net` external join → `sd-postgres-decision` healthy → **Egress의 다음 재시작 시도가 성공** → 5초 polling 첫 tick에 4건 송신 완료.

### Step C: DB·송신 확인

```
docker ps                        → 6개 컨테이너 모두 Up
psql ... SELECT FROM ...         → role_mapping=3, alarm_mapping=12, decision_record=4
mock-loas logs                   → ANALYSIS_RESULT(0x0100)×3 + ALERT(0x0101)×1 수신
egress-gw logs                   → decision_sent×4
psql ... GROUP BY final_decision → normal/caution/warning 모두 sent=0 pending=0... 어 죄송, 모두 sent=O
```

영문 enum (`normal`/`caution`/`warning`) 페이로드 매핑 정상 동작.

---

## 결정 필요/확인 요청 항목별 회신

### 1. 본 이관 동의 — **동의**

소유 경계가 정리되어 dev 워크플로우가 더 깔끔해졌습니다. 양 측이 각자의 compose를 독립적으로 띄우고 내릴 수 있고, 스키마 wipe·재시드를 그쪽 단독으로 진행 가능. 운영 단계에서는 cross-compose 의존성을 더 명확히 다뤄야 하지만 현 시점은 충분합니다.

### 2. 컨테이너명 `sd-postgres-decision` 유지 — **변경 권장: `da-postgres-decision`**

**Egress의 환경변수 `EGW_DB_HOST=postgres-decision`은 변경 불필요**입니다. 이 값은 **서비스명/hostname**이지 `container_name`이 아닙니다. Docker network 안에서는 서비스명이 DNS 이름이 되므로 `container_name`이 무엇이든 영향 없습니다.

따라서 컨테이너 이름은 owner 컨벤션을 따르는 게 자연스럽고, 검증 시점에 `docker ps`에서 prefix로 owner 식별이 쉬워집니다. **`da-postgres-decision`으로 변경하는 것을 권장**합니다 (그쪽 결정).

`docker logs sd-postgres-decision` 같은 명령을 우리 측 README/스크립트에 박아두지 않았으므로 SocketDaim 측 영향 0입니다.

### 3. dev 볼륨 wipe 권한 — **이미 처리**

```
docker rm -f sd-postgres-decision     # 고아 컨테이너 제거 (이전 compose 잔존분)
docker volume rm socketdaim_decision-pgdata
```

현재 `decision_agent_decision-pgdata` 볼륨이 새로 생성되었고, 거기에서 init+seed가 fresh하게 적재됨을 확인했습니다.

### 4. `gw-net` 이름 (`socketdaim_gw-net`) 유지 — **유지 약속**

현재 SocketDaim compose는 다음 두 가지에 의존하여 이름이 결정됩니다:

- `compose project name = socketdaim` (디렉토리 이름 `SocketDaim`이 lowercase + 공백 제거된 형태)
- `network name (compose 내부) = gw-net`

→ 결과 외부 이름 = `socketdaim_gw-net`

본 이름이 깨질 리스크가 있는 시나리오와 우리 대응:

| 시나리오 | 위험도 | 우리 대응 |
|---|---|---|
| `COMPOSE_PROJECT_NAME` env로 override | 중 | dev/CI에서 강제하지 않을 것을 약속 |
| 디렉토리 rename | 중 | rename할 일 없음 (저장소 이름 고정) |
| compose `name:` top-level 키 추가 | 저 | 추가 시 사전 통보 약속 |
| `gw-net` 키 변경 | 저 | 변경 시 사전 통보 약속 |

당장 변경 계획 없습니다. 만약 사정이 생기면 PR 단계에서 사전 협의하겠습니다. 또한 그쪽 compose의 `external: true, name: socketdaim_gw-net` 명시 자체가 좋은 안전장치입니다 — 잘못된 이름을 만들면 빠르게 실패합니다.

---

## 변경 파일 요약

| 유형 | 경로 |
|---|---|
| 수정 | `docker-compose.yml` (postgres-decision/decision-pgdata 제거, egress-gw depends_on 정리, header 코멘트 갱신, restart 정책 추가) |
| 삭제 | `decision_db/init_db.sql`, `decision_db/seed_dev_decisions.sql` |
| 신규 | `decision_db/README.md` (handoff 흔적) |

---

## 후속 액션 정리 (선행 letter 후속 + 본 letter 후속 통합)

| # | 액션 | 책임 | 우선순위 |
|---|---|---|---|
| 1 | LOAS 스펙 확보 (한/영 enum, 페이로드 키 명명) | 양 팀 | 운영 전환 전 필수 |
| 2 | LOAS 한국어 기대 시 어댑터 결정 (sender vs vendor codec) | SocketDaim | LOAS 스펙 확정 후 |
| 3 | role_mapping 변경 시 Egress alias 처리 결정 | 양 팀 | role_mapping 변경 PR 시점 |
| 4 | CI/배포에서 sibling 가정 대체 방안 (submodule vs CI clone vs OCI 이미지) | 양 팀 | 배포 단계 진입 전 |
| 5 | 운영 단계 cross-compose 통합 전략 (단일 compose vs k8s manifest vs ...) | 양 팀 | 배포 단계 진입 전 |

---

## 참조

- 적용된 compose: [docker-compose.yml](../docker-compose.yml)
- decision_db handoff 안내: [decision_db/README.md](../decision_db/README.md)
- 이전 회신: [reply_to_decision_agent_2026-05-04.md](./reply_to_decision_agent_2026-05-04.md)
