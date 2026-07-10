# PoolerTran 설치 가이드

`cctv_transfer_queue` 를 폴링하여 waypoint 전환 시점에 배치 REST 전송 →
decision_db(`decision_record`) 적재까지 수행하는 컨슈머인 **PoolerTran** 의 설치
절차다. 동작 개요·환경변수 전체 목록은 [README.md](README.md),
설계 근거는 [PoolerTran_설계.md](../PoolerTran_설계.md) 를 참조한다.

> ⚠️ **부팅 순서가 핵심이다.** PoolerTran 은 전체 파이프라인에서 **가장 마지막에**
> 설치하며, 반드시 **`migrate_010` 적용 → PoolerTran 기동** 순서를 지켜야 한다.
> 자세한 이유는 아래 [4. 핵심 규칙](#4-핵심-규칙--설치-순서) 참조.

---

## 1. 사전 요구사항

| 구분 | 요구사항 | 비고 |
|---|---|---|
| OS | 임의의 Linux | 특정 우분투/배포판 버전 요구 없음 — Docker 가 동작하면 됨 |
| Docker | Docker Engine + Compose plugin | `docker compose` v2 |
| 선행 서비스 | **SocketDaim 이 먼저 기동** | `gw-net` 네트워크 + `sd-postgres` 컨테이너 생성 |
| 선행 서비스 | **decision_agent 가 먼저 기동** | `decision_db`(decision_record/transfer_dlq + detector 롤)가 존재해야 결과 적재 가능 |
| 선행 스키마 | gateway_db 의 기존 롤 `gw_writer` | `migrate_010` 이 `GRANT INSERT ... TO gw_writer` 수행 |
| (로컬 실행 시) | Python 3.11+ | Docker 미사용 개발/테스트용 |

PoolerTran 컨테이너는 `gw-net` 한 곳에만 접속한다(별도 결과 DB 컨테이너 없음).

- `gw-net` (external) — SocketDaim 의 공용 `gateway_db`(`sd-postgres`)에 `postgres`
  호스트명으로 접속해 **큐/소스를 읽고**, decision_agent 의 `decision_db`
  (`postgres-decision`)에 결과(`decision_record`)·포이즌 메시지(`transfer_dlq`)를
  적재한다.  decision_db 스키마는 `decision_agent/init_db.sql` 이 생성한다.

### Docker 미설치 시

Docker Engine 설치는 배포판별 [Docker 공식 문서](https://docs.docker.com/engine/install/)를
따른다. 아래는 **우분투/데비안 계열의 apt 저장소 방식** 요약이며, 다른 배포판은
각자의 패키지 관리자(dnf/yum, pacman 등)에 맞는 공식 절차를 사용한다:

```bash
# 1) 의존성 + GPG 키
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# 2) 저장소 등록
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 3) 엔진 + Compose plugin 설치
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 4) sudo 없이 사용 (재로그인 또는 newgrp 필요)
sudo usermod -aG docker $USER && newgrp docker

# 5) 확인
docker --version && docker compose version
```

---

## 2. 선행 조건 확인

PoolerTran 설치 전, SocketDaim 이 떠 있고 네트워크/컨테이너가 존재하는지 확인한다.

```bash
# gw-net 네트워크 존재 확인 (Compose 가 만든 이름: socketdaim_gw-net)
docker network ls | grep gw-net

# sd-postgres 컨테이너가 떠 있는지 확인
docker ps --filter name=sd-postgres
```

SocketDaim 이 아직 안 떠 있다면 먼저 기동한다.

```bash
cd /home/duckking/echopro/SocketDaim
docker compose up -d
```

> docker-compose.yml 주석의 `C:\SocketDaim` 등 경로는 원본 Windows 환경 표기다.
> 본 Ubuntu 환경에서는 `/home/duckking/echopro/...` 실제 경로를 사용한다.
> 단, `gw-net` 의 external name(`socketdaim_gw-net`)은 코드에 고정돼 있으므로
> 그대로 둔다.

---

## 3. 설치 (Docker 권장 방식)

### 3-1. (필요 시) 환경값 조정

[docker-compose.yml](docker-compose.yml) 의 `poolertran.environment` 블록에 모든
`PT_*` 값이 인라인으로 들어 있다. 운영 시 **반드시 교체할 항목**:

| 변수 | 기본값 | 조치 |
|---|---|---|
| `PT_REST_URL` | `http://analysis-receiver:8000/ingest` | 실제 전송 엔드포인트로 교체 |
| `PT_GW_DB_PASSWORD` | `dev_forwarder_pw` | 운영 시크릿으로 주입 (migrate_010 의 롤 비번과 일치해야 함) |
| `PT_DECISION_DB_PASSWORD` | `dev_sensor_pw` | decision_db 의 `sensor_analysis_role`(detector 롤) 비번과 일치해야 함 |

> 결과는 decision_agent 의 decision_db 에 적재하므로 `PT_DECISION_DB_*` 는
> decision_db 의 `sensor_analysis_role`(detector 롤) 접속이다.  비밀번호를 바꿀 경우
> ① gateway_db 의 `cctv_forwarder`(migrate_010 의 `CREATE ROLE`), ② decision_db 의
> `sensor_analysis_role`(decision_agent/init_db.sql) 비번을 compose 의
> `PT_GW_DB_PASSWORD` / `PT_DECISION_DB_PASSWORD` 와 각각 맞춘다.

### 3-2. migrate_010 적용 → PoolerTran 기동 (순서 엄수)

```bash
cd /home/duckking/echopro/PoolerTran

# ① gateway_db 에 큐/트리거/cctv_forwarder 롤 생성 (PoolerTran 기동 직전 1회)
docker exec -i sd-postgres psql -U postgres -d gateway_db \
    < migrations/migrate_010_cctv_transfer_queue.sql

# ② PoolerTran 기동 (이미지 빌드 포함) — 별도 결과 DB 컨테이너 없음
docker compose up -d --build
```

`migrate_010` 은 idempotent(`IF NOT EXISTS` / `CREATE OR REPLACE` / `DROP ... IF
EXISTS`)이라 재실행해도 안전하다. `migrate_010` 은 `gateway_db` 에 큐/트리거/
`cctv_forwarder` 롤**만** 생성한다(결과/DLQ 테이블 없음).  결과(`decision_record`)·
포이즌(`transfer_dlq`) 테이블과 detector 롤은 decision_agent 의 `decision_db`
(`decision_agent/init_db.sql`)가 보유하므로, decision_agent 가 먼저 기동돼 있어야 한다.

### 3-3. 기동 확인

```bash
# 컨테이너 상태
docker compose ps

# 로그 (부팅 실패 여부 확인)
docker compose logs -f poolertran

# Health 엔드포인트 (queue_depth, stats, DB 연결 상태)
curl http://localhost:9109/health

# 결과 테이블 직접 조회 (decision_db 안)
psql -h localhost -U sensor_analysis_role -d decision_db -c \
    "SELECT * FROM decision_record ORDER BY observation_timestamp DESC LIMIT 5;"   # 비번: dev_sensor_pw
```

`/health` 가 200 으로 응답하고 DB 연결이 정상이면 설치 완료다.

---

## 4. 핵심 규칙 — 설치 순서

> **반드시 `migrate_010 적용` → `PoolerTran 기동` 순서를 지킬 것.**

- PoolerTran 은 `migrate_010` 이 생성하는 **`cctv_forwarder` 롤**로 gateway_db 에
  접속하고, 같은 마이그레이션이 만든 **`cctv_transfer_queue` 큐/트리거**를 폴링한다.
- 따라서 migrate_010 미적용 상태로 PoolerTran 을 기동하면 **접속할 롤이 없어
  부팅 단계에서 즉시 실패·종료**한다. (재시도로 우회하지 않고 운영 절차로 순서를
  보장하는 설계다.)
- migrate_010 미적용은 SocketDaim 본체 동작에는 영향이 없으므로, **PoolerTran
  도입 시점에** 위 순서로 함께 적용한다.

**소유 경계:** `migrate_010` 은 `gateway_db` 를 변경하지만 SocketDaim 레포가 아닌
**PoolerTran 이 독립 소유·관리**한다.

---

## 5. (대안) 로컬 Python 실행 — 개발/테스트

Docker 없이 호스트에서 직접 구동하는 방식. gateway_db 가 호스트에서 접근 가능해야
한다(SocketDaim 의 `sd-postgres` 가 host port `2345` 로 노출).  결과는
decision_agent 의 decision_db 에 적재하므로 그 DB 도 호스트에서 접근 가능해야 한다.

```bash
cd /home/duckking/echopro/PoolerTran

# 1) 가상환경 (이미 .venv 존재 — 재사용 가능)
python3.11 -m venv .venv
source .venv/bin/activate

# 2) 의존성 설치
pip install -r requirements.txt

# 3) 환경값 — 템플릿 복사 후 수정 (.env 는 .gitignore 처리됨)
cp .env.example .env
$EDITOR .env        # PT_GW_DB_PORT=2345, PT_DECISION_DB_*(decision_db), PT_REST_URL 등 확인

# 4) 실행 (엔트리포인트: poolertran.main)
PYTHONPATH=src python -m poolertran.main
```

### 테스트

```bash
pip install -r requirements.txt
PYTHONPATH=src pytest          # 단위 테스트
```

통합 테스트는 실 DB 가 필요하며 `PT_TEST_GW_DSN` / `PT_TEST_DECISION_DSN` 설정 시에만
동작하고, 미설정 시 자동 스킵된다.

---

## 6. 주요 접속 정보

| 항목 | 위치 | 인증 |
|---|---|---|
| Health / 모니터링 | `http://localhost:9109/health` | — |
| gateway_db (큐 소스) | `localhost:2345` (SocketDaim) | `cctv_forwarder` / `dev_forwarder_pw` |
| decision_db (결과 적재) | decision_agent 의 `postgres-decision` | `sensor_analysis_role` / `dev_sensor_pw` |

---

## 7. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `poolertran` 기동 직후 종료, 로그에 인증/롤 오류 | migrate_010 미적용 또는 `cctv_forwarder`/`sensor_analysis_role` 비밀번호 불일치 → migrate_010 먼저 적용, gateway·decision 양쪽 비번 정합성 확인 |
| `network socketdaim_gw-net not found` | SocketDaim 이 안 떠 있음 → `cd ../SocketDaim && docker compose up -d` 후 재시도 |
| `/health` 의 gateway DB 연결 false | `sd-postgres` 미기동 또는 gw-net 미합류 → `docker network inspect socketdaim_gw-net` 로 멤버 확인 |
| `/health` 의 decision DB 연결 false / 결과 테이블 없음 | decision_agent 미기동 또는 `decision_db` 스키마 미생성 → decision_agent 를 먼저 기동(`decision_agent/init_db.sql`), `PT_DECISION_DB_*` 정합성 확인 |
| 큐가 계속 쌓임(`queue_depth` 증가) | REST 전송 실패 → `PT_REST_URL` 확인, `decision_db.transfer_dlq` 적재 여부 점검 |
| 전송이 안 됨 / 너무 느림 | 저지연 필요 시 `PT_USE_LISTEN=true` (LISTEN/NOTIFY 모드), `PT_POLL_INTERVAL_SEC` 조정 |

### 중지 / 재기동

```bash
docker compose stop poolertran            # 컨슈머만 중지 (큐는 그대로 적체)
docker compose up -d --build poolertran   # 코드 변경 반영 재기동
docker compose down                       # 전체 중지
```
