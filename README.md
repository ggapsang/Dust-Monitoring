# Dust Monitoring

기존에 개별 레포지토리로 분리되어 있던 레포들을 하나로 합친 통합 모노레포.
AMR이 수집한 IoT 분진 센서값과 촬영 영상을 받아 저장·분석·판정하고, 그 결과를 수신측으로 송출함

**AI 추론(정적/동적 분진 탐지 모델) 부분은 아직 합쳐지지 않음** (2026.07.10 기준).
    PoolerTran 이 호출하는 AnalysisReceiver(`:8000`)와 decision_record 채널을 채우는 탐지 컨슈머들이 그 파트에 해당하며, 데모 응답으로 대체하도록 설정은 가능

4개 모듈은 하나의 공용 Docker 네트워크(`socketdaim_gw-net`) 위에서 동작
**SocketDaim 이 네트워크와 공용 DB(`sd-postgres`)를 생성**하므로 항상 가장 먼저 기동한다.


## 모듈 · 서비스 정리
### 1. SocketDaim — 수집 / 게이트웨이 (파이프라인의 입·출구)

1. ingestion-gateway : AMR과 AMR 과 TCP 통신. 센서값·메타데이터(13310)와 영상(13320)을 받아 storage 에 파일 저장 + gateway_db 에 기록. 두 채널을 결합해 이미지에 메타 정보를 매핑하는 것이 주 기능
2. egress-gateway : decision_db 의 판정 완료 건을 폴링 → gateway_db 와 조인 → LOAS MariaDB `t_inspection` 에 INSERT(송출).
3. admin-ui : 등록된 관측 지점 목록을 직접 관리(CURD). 미등록 지점에서 온 자동 등록 요청(pending)을 관리자가 승인/거부로 선별. 어드민 UI(9108)
4. cleaner : retention 정책 집행(매일 03:00 KST + 디스크 압박 시 긴급 purge)
5. postgres(sd-postgres) : gateway_db : 센서/영상/메타데이터를 담는 공용 저장 DB

### 2. decision_agent — 판정

1. decision-agent : 세 가지 분석 채널(정적 분진 · 동적 분진 · IoT 센서) 결과가 모두 도착한 건에 대해 알람 매핑 진리표로 최종 판정(normal/caution/warning). poller + 어드민 HTTP 를 한 프로세스에서 실행(9107)
2. postgres-decision : decision_db : 관측 1개가 1행(`decision_record`). 각 채널은 자기 칼럼만 UPDATE

### 3. Dumopro_Data_Analysis_Webapp — 분진 데이터 분석 WebApp
※ Dumopro : IoT 센서의 브랜드 이름. gateway_db 에 쌓인 분진값을 실시간 시각화한다.

1. dumopro-api : 분진값 WebUI + REST API + SSE(실시간 push). http://localhost:9105/
2. dumopro-poller : gateway_db 를 주기적으로 폴링해 Redis 에 적재(9106 은 헬스 체크 전용)
3. dumopro-redis : 캐시 + pub/sub 브로커 

### 4. PoolerTran — CCTV 프레임 전송 컨슈머

Correlator 가 페어링을 끝낸 `cctv_frame` 을 감지해, waypoint 전환 시점에 그 묶음을 배치 REST(`:8000`, AnalysisReceiver)로 전송하고 응답(정적/동적 score)을 분류해 `decision_db.decision_record` 에 적재한다(9109 은 헬스 체크 전용).


## 포트 정리
### 공개 포트 (호스트에 노출 — 방화벽/netstat 대상)
| 포트 (→ 컨테이너) | 서비스 | 모듈 | 용도 |
|---|---|---|---|
| 13310 (→13310) | ingestion-gateway | SocketDaim | TCP 로 AMR 이 보낸 IoT 센서값 + 메타데이터 수신 |
| 13320 (→13320) | ingestion-gateway | SocketDaim | TCP 로 AMR 촬영 영상을 jpg 로 수신(메타 없음). 13310 메타와 결합해 매핑하는 것이 주 기능 |
| 9000 (→9000) | ingestion-gateway | SocketDaim | 레거시. AMR 통신 프로토콜 확정 전 테스트로 열어둔 뒤 그대로 남음(loas 모드에선 idle) |
| 9105 (→9105) | dumopro-api | Dumopro | 분진값 WebUI + REST API + SSE |
| 9106 (→9106) | dumopro-poller | Dumopro | dumopro-poller 헬스 체크 전용 |
| 9107 (→9107) | decision-agent | decision_agent | 판정 어드민 UI + JSON API (FastAPI, `/docs` 는 비활성) |
| 9108 (→9108) | admin-ui | SocketDaim | station CRUD · 요청 트리아지 어드민 UI |
| 9109 (→9109) | poolertran | PoolerTran | 헬스 체크 전용(queue depth + 처리 통계) |
| 2345 (→5432) | postgres (sd-postgres) | SocketDaim | 공용 저장 DB `gateway_db` |
| 2346 (→5432) | postgres-decision | decision_agent | 판정 DB `decision_db` |
| 6380 (→6379) | dumopro-redis | Dumopro | 캐시 + pub/sub(Autoencoder Redis 6379 와 분리하려 호스트 6380 사용) |
| 3306 (→3306) | loas-mariadb | SocketDaim | 로컬 테스트용 mock MariaDB(`override.yml`, git 제외). 운영엔 없음 |

### 내부 전용 포트 (gw-net 내부, 호스트 미노출)
| 포트 | 서비스 | 비고 |
|---|---|---|
| 5432 | postgres / postgres-decision | 컨테이너끼리는 내부 5432 로 접속(호스트만 2345/2346) |
| 6379 | dumopro-redis | `redis://dumopro-redis:6379/0` |

### 외부로 나가는(outbound) 대상 포트
| 대상 | 포트 | 발신 | 설명 |
|---|---|---|---|
| LOAS MariaDB `t_inspection` (10.5.21.141) | 3306 | egress-gateway | 판정 결과 INSERT. 접속값은 시크릿 주입, Allowed Client IP 10.5.20.160 |
| AnalysisReceiver (`http://analysis-receiver:8000/ingest`) | 8000 | PoolerTran | REST 배치 전송 대상. 미배포 시 데모 응답으로 대체(`PT_REST_DEMO=true`) |
