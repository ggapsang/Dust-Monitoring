# 클린 재시작 (전체 정지 → 순서대로 기동) — 폐쇄망 운영서버

운영서버(`daim@daim-ecopro`, 소스 `~/svc/<레포>`)의 4개 스택을 **전부 깨끗하게 내렸다가
deploy 단일 설정으로 순서대로 올리는** 절차. compose 라벨이 섞이는 문제 없이
모든 컨테이너를 새로 생성한다.

- **정지 순서 = 의존 역순**(의존자 먼저 → 네트워크/DB 소유자 SocketDaim 마지막)
- **기동 순서 = 의존 순서**(SocketDaim 먼저 → 나머지)
- 모든 명령에 **`-f docker-compose.deploy.yml`** 명시 → `docker-compose.override.yml` 안 끼고 deploy 단독 확정.

> 의존 관계: decision_agent·PoolerTran·Dumopro 의 deploy.yml 은 `socketdaim_gw-net`(external)
> 과 `sd-postgres`(gateway_db)를 참조한다. 그래서 SocketDaim 이 네트워크·DB 의 소유자다.

---

## 0) 사전 확인 — SocketDaim egress 실제 LOAS 값

override(로컬 mock `loas-mariadb`)가 빠지므로 egress 는 **실제 LOAS** 로 송신한다.
placeholder면 먼저 수정한다.
```bash
grep -nE 'EGW_TARGET_DB_(HOST|USER|PASSWORD)' ~/svc/SocketDaim/docker-compose.deploy.yml
#   기대값(placeholder 아니어야 함):
#     EGW_TARGET_DB_HOST     = 실제 LOAS IP
#     EGW_TARGET_DB_USER     = 실제 LOAS 계정
#     EGW_TARGET_DB_PASSWORD = 실제 비번  ← 서버 로컬 deploy.yml 에만, GitHub 금지
```
> ⚠️ 실제 IP·계정·비밀번호는 **서버 로컬 `docker-compose.deploy.yml` 에만** 두고, 이 문서/GitHub 에는 적지 말 것.

---

## 1) 전체 정지 (의존 역순)

```bash
cd ~/svc/Dumopro_Data_Analysis_WebApp && docker compose -f docker/docker-compose.deploy.yml down --remove-orphans
cd ~/svc/PoolerTran                   && docker compose -f docker-compose.deploy.yml down --remove-orphans
cd ~/svc/decision_agent               && docker compose -f docker-compose.deploy.yml down --remove-orphans
cd ~/svc/SocketDaim                   && docker compose -f docker-compose.deploy.yml down --remove-orphans
```
SocketDaim 을 **마지막**에 내려야 `socketdaim_gw-net` 이 비워진 뒤 깨끗이 제거된다.

정지 확인:
```bash
docker ps --format '{{.Names}}' | grep -E 'sd-|da-|dumopro|poolertran|loas-mariadb' || echo "전부 정지됨(정상)"
```

---

## 2) 전체 기동 (의존 순서, deploy 단일)

```bash
# ① SocketDaim — gw-net + sd-postgres(gateway_db) 생성 (먼저)
cd ~/svc/SocketDaim
docker compose -f docker-compose.deploy.yml up -d --no-build --remove-orphans

# ② decision_agent — decision_db
cd ~/svc/decision_agent
docker compose -f docker-compose.deploy.yml up -d --no-build --remove-orphans

# ③ PoolerTran
cd ~/svc/PoolerTran
docker compose -f docker-compose.deploy.yml up -d --no-build --remove-orphans

# ④ Dumopro
cd ~/svc/Dumopro_Data_Analysis_WebApp
docker compose -f docker/docker-compose.deploy.yml up -d --no-build --remove-orphans
```

---

## 3) 확인

```bash
# 4개 모두 deploy.yml '단독'(쉼표 없음)이면 완료
docker compose ls

# 컨테이너 상태
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'sd-|da-|dumopro|poolertran'

# override 전용 mock 제거됐는지
docker ps --format '{{.Names}}' | grep loas-mariadb || echo "mock 제거됨(정상)"

# egress 가 실제 LOAS 로 INSERT 하는지
docker logs --since 3m sd-egress-gw 2>&1 | grep -E 'loas_inserted|loas_insert_failed'
```

---

## 안전 포인트 / 주의

- **`down` 에 `-v` 를 붙이지 않음** → DB 볼륨(gateway_db·decision_db·redis) **유지**. 데이터 보존, **마이그레이션 재실행 불필요**.
- **`-f docker-compose.deploy.yml` 를 정지·기동 모두에 명시** → override 안 끼고 deploy 단독 확정.
- 완전 down→up 이라 **모든 컨테이너가 새로 생성** → `docker compose ls` 라벨이 deploy.yml 단독으로 깔끔히 정리(라벨 섞임 해소).
- **SocketDaim 은 반드시 정지 마지막 / 기동 먼저** — 네트워크·DB 소유자.
- 기동 전 **0번(egress 실제 LOAS 값)** 확인 필수. 아니면 egress 연결 실패(비치명적·재시도).
- 이 시점 모드: **REST = 데모**(PoolerTran deploy.yml `PT_REST_DEMO=true`), **egress/MariaDB = 실운영**(실제 LOAS). 외부 REST 준비되면 PoolerTran deploy.yml 의 `PT_REST_DEMO:"false"` + `PT_REST_URL` 수정 후 ③만 다시 실행.

---

## 참고: 순서 요약

| 단계 | 순서 |
|---|---|
| 정지(down) | Dumopro → PoolerTran → decision_agent → **SocketDaim(마지막)** |
| 기동(up) | **SocketDaim(먼저)** → decision_agent → PoolerTran → Dumopro |

---

## 부록 A. 폐쇄망용 Docker 이미지 빌드·반입

> **전제**: 빌드 PC(인터넷 O)에서만 빌드·pull 한다. **운영서버는 air-gap(인터넷 X) → 빌드 불가**,
> tar 로 받아 `docker load` 만 한다. 빌드는 dev `docker-compose.yml`(build: 섹션 보유),
> 폐쇄망 기동은 `docker-compose.deploy.yml`(image + pull_policy:never).

### A-1. 이미지 빌드 (빌드 PC `~/project`)
```bash
cd ~/project/SocketDaim                   && docker compose -f docker-compose.yml build
cd ~/project/decision_agent               && docker compose -f docker-compose.yml build
cd ~/project/PoolerTran                   && docker compose -f docker-compose.yml build
cd ~/project/Dumopro_Data_Analysis_WebApp && docker compose -f docker/docker-compose.yml build
```
- 완전 클린 빌드: `build --no-cache --pull` (베이스까지 최신 재취득).
- ⚠️ 반드시 **해당 레포 디렉터리에서** 빌드해야 이미지명이 `프로젝트명(=디렉터리 소문자)-서비스명`
  으로 나와 deploy.yml 의 `image:` 와 일치한다.

### A-2. 베이스 이미지 확보
```bash
docker pull postgres:16        # SocketDaim · decision_agent
docker pull redis:7-alpine     # Dumopro
# mariadb 는 외부 실 LOAS(10.5.21.141) → 반입 불필요
```

### A-3. tar 저장 (앱 + 베이스 이미지 명시)
```bash
mkdir -p ~/dist/images
docker save socketdaim-ingestion-gw:latest socketdaim-egress-gw:latest socketdaim-admin-ui:latest socketdaim-cleaner:latest postgres:16 | gzip > ~/dist/images/socketdaim-images.tar.gz
docker save decision_agent-decision-agent:latest postgres:16 | gzip > ~/dist/images/decision_agent-images.tar.gz
docker save poolertran-poolertran:latest | gzip > ~/dist/images/poolertran-images.tar.gz
docker save dumopro-dumopro-poller:latest dumopro-dumopro-api:latest redis:7-alpine | gzip > ~/dist/images/dumopro-images.tar.gz
```
> (자동 추출을 원하면 `docker save (docker compose ... config --images)` 형태도 되지만, 미리보기 수식 충돌 방지를 위해 문서에는 이미지명을 명시했다.)

### A-4. 서버 전송 → load
```bash
# 빌드 PC → 서버
rsync -avz -e 'ssh -p 50022' ~/dist/images/*.tar.gz daim@10.5.20.160:~/dist/images/
# 서버에서 적재 (docker load 는 .gz 자동 해제)
ls ~/dist/images/*.tar.gz | xargs -n1 docker load -i
```
이후 본문 **§1~§2(클린 재시작)** 또는 각 스택 `up -d --no-build --force-recreate` 로 기동.

### A-5. 한 레포만 갱신할 때 (예: PoolerTran 코드 수정)
```bash
# 빌드 PC
cd ~/project/PoolerTran && docker compose -f docker-compose.yml build
docker save poolertran-poolertran:latest | gzip > ~/dist/images/poolertran-images.tar.gz
rsync -avz -e 'ssh -p 50022' ~/dist/images/poolertran-images.tar.gz daim@10.5.20.160:~/dist/images/
# 서버
docker load -i ~/dist/images/poolertran-images.tar.gz
cd ~/svc/PoolerTran && docker compose -f docker-compose.deploy.yml up -d --no-build --force-recreate
```

### A-6. (예제) Dumopro 갱신 — 프론트/백엔드 모두 api 이미지에 포함
`frontend/`(JS) 와 `apps/api`(백엔드) 는 **둘 다 `dumopro-dumopro-api` 이미지에 `COPY` 로 베이킹**된다.
따라서 프론트만 고쳐도 **api 이미지를 재빌드**해야 반영된다(poller 변경 없으면 poller 재빌드 불필요).
```bash
# 빌드 PC — api(프론트+백엔드) 재빌드
cd ~/project/Dumopro_Data_Analysis_WebApp && docker compose -f docker/docker-compose.yml build
docker save dumopro-dumopro-api:latest | gzip > ~/dist/images/dumopro-api-images.tar.gz
rsync -avz -e 'ssh -p 50022' ~/dist/images/dumopro-api-images.tar.gz daim@10.5.20.160:~/dist/images/
# 서버 — load 후 dumopro 스택 재생성(새 api 이미지 강제 적용)
docker load -i ~/dist/images/dumopro-api-images.tar.gz
cd ~/svc/Dumopro_Data_Analysis_WebApp && docker compose -f docker/docker-compose.deploy.yml up -d --no-build --force-recreate
```
> 적용 확인: 브라우저 DevTools → Network 에서 **`/api/stream` EventSource 가 1개만** 열리면 정상(단일 멀티플렉스 SSE).

### A-7. (예제) SocketDaim 전체 재배포 — egress 코드 + EGW_EVENT_ID_FILTER 반영
`egress_gateway/` 코드는 `socketdaim-egress-gw` 이미지에 `COPY` 된다.  egress-gw 단일 tar 반입으로
잘 안 잡힐 때는 **통합 tar(`socketdaim-images.tar.gz`)로 전체 재배포**가 깔끔하다.
> ⚠️ **이미지만 바꿔선 부족하다** — `EGW_EVENT_ID_FILTER` 는 **서버 compose 에 그 줄이 있어야** env 로
> 주입된다(없으면 새 코드라도 기본 "0,1,2,3" 으로만 돌고 `docker exec … env` 엔 안 뜸).

```bash
# 1) 빌드 PC — SocketDaim 4개 앱 이미지 빌드 + 통합 tar (앱4 + postgres:16)
cd ~/project/SocketDaim && docker compose -f docker-compose.yml build
docker save socketdaim-ingestion-gw:latest socketdaim-egress-gw:latest socketdaim-admin-ui:latest socketdaim-cleaner:latest postgres:16 | gzip > ~/dist/images/socketdaim-images.tar.gz
rsync -avz -e 'ssh -p 50022' ~/dist/images/socketdaim-images.tar.gz daim@10.5.20.160:~/dist/images/

# 2) 서버 — load (기존 통합 tar 덮어씀)
docker load -i ~/dist/images/socketdaim-images.tar.gz

# 3) 서버 compose 에 EGW_EVENT_ID_FILTER 있는지 확인 → 없으면 egress-gw environment 에 추가:
#      EGW_EVENT_ID_FILTER: "0,1,2,3"   (원하는 값, 예 "2,3")
grep -n EGW_EVENT_ID_FILTER ~/svc/SocketDaim/docker-compose.deploy.yml || echo "없음 → 추가 필요"

# 4) 서버 — 앱 서비스만 재생성 (sd-postgres 안 내려가 다른 스택 무중단)
cd ~/svc/SocketDaim
docker compose -f docker-compose.deploy.yml up -d --no-build --force-recreate ingestion-gw egress-gw admin-ui cleaner
```
> 진짜 전체(postgres 포함) 재생성은 서비스명 없이 `… --force-recreate` — 단 **sd-postgres 가 잠깐 내려가**
> decision_agent/poolertran/dumopro 가 잠시 끊긴다.  egress 변경만이면 위처럼 **앱 서비스만** 권장.
>
> 적용 확인:
> `docker exec sd-egress-gw env | grep EGW_EVENT_ID_FILTER` (값 주입) ·
> `docker logs sd-egress-gw 2>&1 | grep egress_event_id_filter` (새 코드 동작 증거) ·
> `… | grep -E 'loas_inserted|loas_skipped_event_id'` (적재/생략).

---

## 부록 B. 폐쇄망 운영 정보 (참고)

| 항목 | 값 |
|---|---|
| 운영서버 | `daim@10.5.20.160` (SSH 포트 `50022`, hostname `daim-ecopro`) |
| 이미지 tar | 서버 `~/dist/images/*.tar.gz` |
| 소스 | 서버 `~/svc/<레포>` |
| 빌드 PC 소스 | `~/project/<레포>` (인터넷 O) |

**이미지명 ↔ deploy.yml 매핑** (불일치 시 "No such image")

| 레포 | 빌드 산출물 이미지 | 베이스 |
|---|---|---|
| SocketDaim | `socketdaim-{ingestion-gw,egress-gw,admin-ui,cleaner}:latest` | `postgres:16` |
| decision_agent | `decision_agent-decision-agent:latest` | `postgres:16` |
| PoolerTran | `poolertran-poolertran:latest` | (postgres 공유) |
| Dumopro | `dumopro-{dumopro-poller,dumopro-api}:latest` | `redis:7-alpine` |

**규칙·주의**

- 서버에선 **절대 빌드 금지** → 모든 `up` 은 `--no-build`. `--build` 시 `docker/dockerfile:1`
  프론트엔드를 인터넷에서 받으려다 실패한다.
- **시크릿**(egress LOAS 비번 `EGW_TARGET_DB_*` 등)은 **서버 로컬 deploy.yml 에만**, GitHub 커밋 금지.
- **DB 마이그레이션**: 볼륨 보존(`down` 에 `-v` 미사용) 시 스키마 유지 → 재실행 불필요.
  단 큐/롤 추가 등 신규 스키마는 별도 적용(예: PoolerTran `migrations/migrate_010_*` → gateway_db).
- **이미지 교체 후엔** `up -d --no-build --force-recreate` 로 새 이미지를 강제 적용(태그 동일해도 반영).
- 네트워크 소유자 **SocketDaim 먼저 기동**(`socketdaim_gw-net`·`sd-postgres` 의존).
