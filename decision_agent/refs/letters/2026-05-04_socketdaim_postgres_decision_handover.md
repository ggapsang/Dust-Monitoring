# SocketDaim 측 수정 요청: postgres-decision 컨테이너 이관

> **From:** Decision Agent 팀 (c:\decision_agent\)
> **To:** SocketDaim 팀 (C:\SocketDaim\)
> **Date:** 2026-05-04
> **선행 letter:** [2026-05-04_socketdaim_decision_db_migration.md](./2026-05-04_socketdaim_decision_db_migration.md)
> **선행 회신:** SocketDaim/refs/reply_to_decision_agent_2026-05-04.md

---

## TL;DR

선행 letter에서 **DDL/시드 파일**만 c:\decision_agent\로 이관했지만, **컨테이너 자체**는 SocketDaim/docker-compose.yml에 남겨두었습니다. 이 절반의 이관이 운영상 혼란을 만들고 있어, **`postgres-decision` 컨테이너와 `decision-pgdata` 볼륨도 Decision Agent 쪽으로 완전 이관**하려고 합니다. SocketDaim 측에서 다음 3건만 정리해주시면 됩니다.

이번 letter에서 **SocketDaim 파일은 직접 손대지 않습니다.** 의사결정 후 그쪽에서 적용 부탁드립니다.

---

## 배경

선행 letter §"4. 결정 필요/확인 요청 항목" 3번(sibling 디렉토리 가정)에서 "dev OK / 배포 별도 처리 필요"로 함께 합의했습니다. 다만 현재 dev workflow에서도 다음과 같은 마찰이 있습니다.

1. **소유 경계가 불분명**: DDL은 우리가 owner인데 컨테이너 lifecycle은 SocketDaim 의존. 스키마 wipe·재시드를 우리 단독으로 못 함.
2. **Decision Agent 단독 부팅 불가**: `docker compose up`을 우리 쪽에서 하면 `postgres-decision`이 SocketDaim 쪽에서만 생성되므로 hostname을 못 찾음. SocketDaim compose가 항상 먼저 떠야 함.
3. **`../decision_agent/` 상대경로 의존**: SocketDaim/docker-compose.yml이 우리 레포 내부 파일을 mount하고 있어 cross-repo 결합도가 큼.

→ DB 컨테이너도 우리 쪽에서 owner로 가져가는 게 자연스럽습니다. 통신/네트워크는 그대로 SocketDaim 소유.

---

## 새로운 소유 경계

| 항목 | Owner |
|---|---|
| `postgres-decision` 컨테이너 + `decision-pgdata` 볼륨 | **Decision Agent** |
| `init_db.sql`, `seed_mapping.sql`, `seed_test_decisions.sql` | Decision Agent (선행 letter에서 이미 이관) |
| `decision-agent` 서비스 (admin 포함, 9107) | Decision Agent |
| `gw-net` 네트워크 | **SocketDaim** (그대로 유지) |
| `postgres`(공용 storage), `ingestion-gw`, `mock-loas`, `egress-gw` | SocketDaim |

핵심: **DB 컨테이너만 우리 쪽으로**. 통신/네트워크는 그쪽이 그대로 쥐고, 우리는 `gw-net`을 external로 join.

---

## SocketDaim 측 요청 사항 (3건)

### 1. `postgres-decision` 서비스 제거

`SocketDaim/docker-compose.yml` 의 `postgres-decision:` 서비스 블록 전체 삭제. 자리에 다음 코멘트 한 줄만 남기시면 됩니다.

```yaml
  # ---- Decision pipeline (Egress) -----------------------------------------
  # postgres-decision lives in c:\decision_agent\docker-compose.yml.
  # Reachable on the shared `gw-net` by hostname `postgres-decision`.

  mock-loas:
    ...
```

### 2. `decision-pgdata` 볼륨 제거

`SocketDaim/docker-compose.yml`의 `volumes:` 섹션에서 `decision-pgdata:` 라인 삭제. 컨테이너가 사라졌으므로 어디서도 참조하지 않게 됩니다.

기존 dev 데이터를 정리하려면 한 번만:
```
docker compose down
docker volume rm socketdaim_decision-pgdata
```
(우리 쪽 `da-decision_agent_decision-pgdata` 볼륨이 새로 생성되어 init/seed가 fresh하게 적재됩니다.)

### 3. `egress-gw`의 `depends_on: postgres-decision` 정리

cross-compose dependency는 docker-compose가 표현 못 함. 다음 둘 중 하나로 부탁드립니다.

**옵션 A (권장)**: `depends_on`에서 `postgres-decision` 항목만 빼고 `mock-loas`만 남기기. 운영 순서는 README/주석으로 안내.
```yaml
egress-gw:
  ...
  depends_on:
    mock-loas:
      condition: service_started
```

**옵션 B**: 그대로 두고 `docker compose up egress-gw` 시점에 우리 compose가 먼저 떠 있어야 한다는 가정을 README에 못박기 (현재도 사실상 동일한 가정).

---

## 우리 쪽에서 이미 적용된 사항

c:\decision_agent\docker-compose.yml 가 다음과 같이 작성되어 있습니다 (참고):

```yaml
services:
  postgres-decision:
    image: postgres:16
    container_name: sd-postgres-decision    # 기존 이름 유지 (Egress가 hostname으로 참조)
    environment: { POSTGRES_DB: decision_db, POSTGRES_USER: postgres, POSTGRES_PASSWORD: dev_root_pw }
    ports: ["2346:5432"]
    volumes:
      - ./init_db.sql:/docker-entrypoint-initdb.d/01_init_db.sql:ro
      - ./seed_mapping.sql:/docker-entrypoint-initdb.d/02_seed_mapping.sql:ro
      - ./seed_test_decisions.sql:/docker-entrypoint-initdb.d/03_seed_test_decisions.sql:ro
      - decision-pgdata:/var/lib/postgresql/data
    healthcheck: ...
    networks: [gw-net]

  decision-agent: { ... ports: ["9107:9107"], depends_on: postgres-decision, networks: [gw-net] }

volumes:
  decision-pgdata:

networks:
  gw-net:
    external: true
    name: socketdaim_gw-net      # SocketDaim의 네트워크 그대로 join
```

확인 포인트:
- 컨테이너 이름은 기존 `sd-postgres-decision`을 유지 → Egress 코드 `EGW_DB_HOST=postgres-decision` 무변경
- 호스트 포트 2346:5432 동일
- `gw-net`은 external `socketdaim_gw-net` join (네트워크 owner는 그쪽)

---

## 부팅 순서

1. SocketDaim compose first: `cd C:\SocketDaim && docker compose up -d` → `gw-net` 생성됨 (`socketdaim_gw-net`)
2. Decision Agent compose: `cd C:\decision_agent && docker compose up -d --build` → postgres-decision + decision-agent 기동, gw-net에 join

내려갈 때는 역순. SocketDaim의 egress-gw가 살아있는 동안 우리 postgres-decision을 내리면 Egress가 connection error를 반복합니다 (outbox로 회복되긴 합니다).

---

## 결정 필요/확인 요청 항목

1. **본 이관 동의 여부** — 위 3건 적용해도 되는지
2. **컨테이너명 `sd-postgres-decision` 유지 여부** — 기존 SocketDaim 네이밍 컨벤션을 따랐는데, 우리 쪽으로 옮긴 이상 `da-postgres-decision` 같은 이름이 더 자연스럽다면 알려주세요. 그 경우 Egress의 `EGW_DB_HOST` 도 함께 갱신 필요
3. **dev 볼륨 wipe 권한** — 선행 letter §2에서 한 번 wipe하셨고, 본 이관 시점에 한 번 더 필요합니다 (`socketdaim_decision-pgdata` 제거)
4. **`gw-net` 이름 노출** — 우리 compose는 `socketdaim_gw-net` 이름에 의존합니다. SocketDaim 측에서 `name:` 또는 compose project name이 변경될 일이 있는지 확인 부탁

---

## 참조

- 우리 compose: [c:\decision_agent\docker-compose.yml](../../docker-compose.yml)
- DDL/시드: [init_db.sql](../../init_db.sql), [seed_mapping.sql](../../seed_mapping.sql), [seed_test_decisions.sql](../../seed_test_decisions.sql)
- 선행 letter: [2026-05-04_socketdaim_decision_db_migration.md](./2026-05-04_socketdaim_decision_db_migration.md)
