# 운영서버 Docker 설정 가이드 (LOAS 파이프라인)

테스트(개발 PC)에서 검증한 LOAS 파이프라인을 **실제 운영서버(폐쇄망)** 에 배포할 때의
이미지 빌드·복사, **테스트→운영 설정 변경**, 가동 순서를 정리한다.

- 개발/검증 절차는 `시나리오검증.md` 참조.
- 운영은 각 레포의 **`docker-compose.deploy.yml`**(image + `pull_policy: never`)을 사용하며,
  개발용 `docker-compose.yml` 이나 로컬 `docker-compose.override.yml`(mock)은 **쓰지 않는다.**

---

## 1. 구성 / 컨테이너

| 프로그램 | 컨테이너 | 이미지 | 비고 |
|---|---|---|---|
| SocketDaim | sd-postgres | postgres:16 | gateway_db |
| SocketDaim | sd-ingestion-gw | socketdaim-ingestion-gw | LOAS 수신+Correlator |
| SocketDaim | sd-egress-gw | socketdaim-egress-gw | LOAS MariaDB 적재 |
| SocketDaim | sd-admin-ui | socketdaim-admin-ui | 관리 UI :9108 |
| SocketDaim | sd-cleaner | socketdaim-cleaner | retention 정리 |
| decision_agent | da-postgres-decision | postgres:16 | decision_db |
| decision_agent | da-decision-agent | decision_agent-decision-agent | 판정 :9107 |
| PoolerTran | poolertran | poolertran-poolertran | 큐 컨슈머 :9109 |
| (선택) Dumopro WebApp | dumopro-redis/poller/api | redis:7-alpine + 자체빌드2 | 시각화 :9105 |

> **LOAS MariaDB(t_inspection)** 는 운영에선 **외부 실 서버**(예: 10.5.21.141:3306)다.
> 개발용 `loas-mariadb`(mariadb:11) mock 은 운영에서 **띄우지 않는다.**

---

## 2. 이미지 빌드 & 복사 (폐쇄망 air-gap)

### 2-1. (외부망 빌드 PC) 빌드 + 베이스 이미지 확보
```bash
cd <repos_root>
docker compose -f SocketDaim/docker-compose.yml                          build
docker compose -f decision_agent/docker-compose.yml                      build
docker compose -f PoolerTran/docker-compose.yml                          build
docker compose -f Dumopro_Data_Analysis_WebApp/docker/docker-compose.yml build   # WebApp 운영 시
docker pull postgres:16 && docker pull redis:7-alpine
```
> 빌드/운영 아키텍처 일치 필수(amd64). ARM 서버면 `docker buildx --platform linux/amd64`.

### 2-2. (외부망) 이미지 추출 — docker save
```bash
docker save -o loas-images.tar \
  socketdaim-ingestion-gw:latest socketdaim-egress-gw:latest \
  socketdaim-admin-ui:latest socketdaim-cleaner:latest \
  decision_agent-decision-agent:latest poolertran-poolertran:latest \
  dumopro_data_analysis_webapp-dumopro-api:latest \
  dumopro_data_analysis_webapp-dumopro-poller:latest \
  postgres:16 redis:7-alpine
#   용량 절감:  ... | gzip > loas-images.tar.gz
```
> mariadb:11 은 포함하지 않는다(운영은 외부 실 LOAS DB).

### 2-3. 운영서버로 복사할 것
1. **이미지 tar** (`loas-images.tar`)
2. **레포의 설정/SQL 파일** (이미지에 없고 compose 가 마운트/실행함):
   - `SocketDaim/`: `docker-compose.deploy.yml`, `init_db.sql`, `storage/`(빈 디렉터리)
   - `decision_agent/`: `docker-compose.deploy.yml`, `init_db.sql`, `seed_mapping.sql`
     - ⚠️ `seed_test_decisions.sql` 은 **마운트 안 함**(이미 deploy compose 에서 제외 — ST-001~004 더미 미생성)
   - `PoolerTran/`: `docker-compose.deploy.yml`, `migrations/migrate_010_cctv_transfer_queue.sql`
   - (WebApp 운영 시) `Dumopro_Data_Analysis_WebApp/docker/`, `redis.conf`, `.env.example`
   > 소스 코드는 이미지에 구워져 있어 복사 불필요. SQL/compose/storage 만 있으면 된다.

### 2-4. (운영서버) 적재 — docker load
```bash
docker load -i loas-images.tar
#   gzip 했다면:  gunzip -c loas-images.tar.gz | docker load
docker images   # 10종 확인
```

---

## 3. ★ 테스트 → 실제 운영 설정 변경 (가장 중요)

운영에선 **deploy compose** 를 쓰되, 아래 값을 **실제 운영값/시크릿으로 교체**한다.
(레포에 평문으로 두지 말고 env 파일·시크릿으로 주입 권장.)

### 3-1. egress → 실제 LOAS MariaDB
`SocketDaim/docker-compose.deploy.yml` 의 `egress-gw`:
| 항목 | 테스트(mock) | 운영(실 LOAS) |
|---|---|---|
| `EGW_TARGET_DB_HOST` | loas-mariadb | **실제 IP**(예: 10.5.21.141) |
| `EGW_TARGET_DB_NAME` | tfoi_web_db_v1 | tfoi_web_db_v1 |
| `EGW_TARGET_DB_USER` | daimresearch(mock) | **실제 계정**(예: loas_writer) |
| `EGW_TARGET_DB_PASSWORD` | daimresearch1234!(mock) | **실제 시크릿** |
| `EGW_SQL_LOG_ENABLE` | true(디버깅) | **false 권장**(로그/데이터 노출 감소) |

> 운영에선 로컬 mock `loas-mariadb` 서비스·`docker-compose.override.yml`·`loas_mariadb_init.sql`
> 을 **사용하지 않는다.**

### 3-2. PoolerTran → 실제 AnalysisReceiver (데모 해제)
`PoolerTran/docker-compose.deploy.yml`:
| 항목 | 테스트 | 운영 |
|---|---|---|
| `PT_REST_DEMO` | true | **false** (실제 REST 호출) |
| `PT_REST_DEMO_VERSION` | (1 또는 로컬 2) | 무관(demo=false 면 미사용) |
| `PT_REST_URL` | http://analysis-receiver:8000/ingest | **실제 AnalysisReceiver 엔드포인트** |
| `PT_GW_DB_PASSWORD` | dev_forwarder_pw | **실제 시크릿**(migrate_010 롤 비번과 일치) |
| `PT_DECISION_DB_PASSWORD` | dev_sensor_pw | **실제 시크릿**(decision_db 롤 비번과 일치) |
| storage 마운트 | `/home/daim/project/SocketDaim/storage` | **운영서버의 실제 SocketDaim storage 절대경로**(ingestion-gw 와 동일 호스트 디렉터리) |

### 3-3. DB 비밀번호 (전부 dev_*_pw → 운영 시크릿)
- `sd-postgres`: `POSTGRES_PASSWORD`, 롤 `gw_writer/gw_reader/gw_admin/gw_cleaner` 비번(init_db.sql)
- `da-postgres-decision`: `POSTGRES_PASSWORD`, 롤 `decision_agent_role/sensor_analysis_role/egress_role`(init_db.sql)
- 각 서비스 env 의 `*_DB_PASSWORD` 를 **롤 생성 SQL 의 비번과 동일하게** 맞춘다.

### 3-4. 시드/분류 임계값
- `seed_test_decisions.sql`(테스트 더미) **미적용**(deploy compose 에서 이미 제외).
- 분류 임계값은 `seed_mapping.sql` 기본값(dust=2, static=0.5, dynamic=0.5)으로 들어가며,
  운영 중 **admin UI(http://<server>:9107/) Threshold 패널**에서 조정.

### 3-5. 네트워크 이름
- decision_agent/PoolerTran/WebApp 은 external `socketdaim_gw-net` 에 붙는다.
  SocketDaim 프로젝트명이 `socketdaim` 이어야 그 이름이 생성된다(폴더명 `SocketDaim` 유지
  또는 `-p socketdaim`). 다르면 WebApp 은 `GW_NET_NAME` env 로 주입.

---

## 4. 가동 순서 (운영 — deploy compose, 순서 엄수)

> **SocketDaim → decision_agent → migrate_010 → PoolerTran** (+선택 WebApp)

```bash
# 1) SocketDaim (gateway_db + ingestion + egress + admin-ui + cleaner)
cd <root>/SocketDaim
mkdir -p storage
docker compose -f docker-compose.deploy.yml up -d
docker compose -f docker-compose.deploy.yml ps     # postgres healthy 확인

# 2) decision_agent (decision_db + 판정기)
cd <root>/decision_agent
docker compose -f docker-compose.deploy.yml up -d

# 3) migrate_010 (PoolerTran 큐/트리거/cctv_forwarder 롤) — PoolerTran 직전 1회
cd <root>/PoolerTran
docker exec -i sd-postgres psql -U postgres -d gateway_db \
    < migrations/migrate_010_cctv_transfer_queue.sql

# 4) PoolerTran (가장 마지막)
docker compose -f docker-compose.deploy.yml up -d

# 5) (선택) Dumopro WebApp
cd <root>/Dumopro_Data_Analysis_WebApp
docker compose -f docker/docker-compose.yml up -d
```
> migrate_010 미적용 상태로 PoolerTran 을 띄우면 롤/큐 없음으로 즉시 종료된다(설계).

---

## 5. 검증 / 헬스체크

```bash
docker ps                                   # 전 컨테이너 Up/healthy
curl -s http://localhost:9109/health        # PoolerTran (gateway_db/decision_db true)
curl -s http://localhost:9107/              # decision admin
curl -s http://localhost:9108/              # SocketDaim admin
ss -ltn | grep -E '13310|13320'             # LOAS 수신 포트

# 실데이터 적재 확인 (실 LOAS MariaDB)
#   egress 로그: docker logs sd-egress-gw | grep -E 'loas_inserted|loas_insert_failed'
```

---

## 6. 운영 전 체크리스트

- [ ] 모든 `dev_*_pw` / `CHANGE_ME` / mock(daimresearch) 값을 **운영 시크릿**으로 교체
- [ ] `EGW_TARGET_DB_*` = 실제 LOAS MariaDB (IP/DB/계정/비번)
- [ ] `PT_REST_DEMO=false`, `PT_REST_URL` = 실제 AnalysisReceiver
- [ ] `EGW_SQL_LOG_ENABLE=false`(권장)
- [ ] PoolerTran storage 마운트 = ingestion-gw 와 **동일 호스트 디렉터리**
- [ ] 로컬 `docker-compose.override.yml`·`loas_mariadb_init.sql`·`loas-mariadb` mock **미사용**
- [ ] `seed_test_decisions.sql` 미적용(더미 ST-001~004 미생성)
- [ ] 부팅 순서 SocketDaim→decision_agent→migrate_010→PoolerTran 준수
- [ ] 빌드/운영 아키텍처 amd64 일치
- [ ] (LOAS t_inspection) FK 충족 데이터인지 — `target_index`/`plant_id` 등 실제 FK 확인

---

## 7. 업데이트(재배포)

```bash
# (외부망) 변경 이미지 재빌드 → docker save
# (운영서버) docker load -i <new>.tar → 컨테이너 교체
cd <root>/SocketDaim && docker compose -f docker-compose.deploy.yml up -d --force-recreate <서비스>
```
> DB 볼륨(pgdata/decision-pgdata)은 유지되므로 데이터 보존. 스키마 초기화가 필요할 때만 `down -v`.

---

### 부록 — 자체빌드 이미지명
`socketdaim-{ingestion-gw,egress-gw,admin-ui,cleaner}`,
`decision_agent-decision-agent`, `poolertran-poolertran`,
`dumopro_data_analysis_webapp-dumopro-{api,poller}`.
