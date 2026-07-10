-- =====================================================================
-- Migration 010: cctv_transfer_queue — Correlator → PoolerTran 작업 큐
-- =====================================================================
-- 소유: 이 마이그레이션은 PoolerTran 이 독립적으로 소유·관리한다(SocketDaim 과
--   분리).  적용 대상 DB 는 SocketDaim 의 gateway_db 이지만, SocketDaim 레포의
--   scripts/ 에는 넣지 않는다.  따라서 전체 시스템 기동에서 SocketDaim 및 다른
--   컨슈머가 모두 올라온 뒤, **가장 마지막에 PoolerTran 을 설치할 때 적용**한다
--   (PoolerTran 컨테이너 기동 직전).  (설계: PoolerTran_설계.md §5, 단 소유
--   경계는 SocketDaim 비침투 방침으로 조정됨.)
--
-- 목적: Correlator(ingestion_gateway/correlator.py)가 cctv_frame 을
--   dust_inspection 과 페어링(UPDATE)하는 바로 그 트랜잭션 안에서, 미처리
--   frame_id 를 작업 큐에 원자적으로 적재한다.  PoolerTran 은 이 백로그
--   큐만 폴링(또는 LISTEN)하여 REST 전송 → 결과 DB 적재 후 큐 행을 DELETE.
--
-- 핵심 원칙:
--   * 생성(INSERT)은 Correlator 트리거(gw_writer)만,
--     삭제(DELETE)는 PoolerTran(cctv_forwarder)만.
--   * cleaner(gw_cleaner)는 이 테이블에 어떤 권한도 갖지 않는다.
--   * FK 를 의도적으로 두지 않는다 — cleaner 의 retention DELETE 가
--     CASCADE 로 큐를 지우거나(원칙 위반) RESTRICT 로 실패하는 것을 방지.
--
-- 실행:
--   docker exec -i sd-postgres psql -U postgres -d gateway_db \
--       < migrations/migrate_010_cctv_transfer_queue.sql
--
-- Idempotent (IF NOT EXISTS / CREATE OR REPLACE / DROP TRIGGER IF EXISTS).
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1) 작업 큐 테이블  (PoolerTran_설계.md §5.1)
-- ---------------------------------------------------------------------
-- frame_id PK → 멱등(트리거 ON CONFLICT DO NOTHING).
-- FK(REFERENCES cctv_frame/dust_inspection) 를 두지 않는다(위 핵심 원칙 참조).
CREATE TABLE IF NOT EXISTS cctv_transfer_queue (
    frame_id    BIGINT      PRIMARY KEY,    -- cctv_frame.id (멱등 키, FK 아님)
    dust_id     BIGINT,                     -- cctv_frame.dust_inspection_id (페어링된 dust 행, FK 아님)
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    attempts    INTEGER     NOT NULL DEFAULT 0  -- 재시도/포이즌 메시지 관측용
);

-- 폴링은 enqueued_at, frame_id 순서로 일어난다(설계 §8.1 ORDER BY).
CREATE INDEX IF NOT EXISTS idx_cctv_transfer_queue_order
    ON cctv_transfer_queue (enqueued_at, frame_id);


-- ---------------------------------------------------------------------
-- 2) 트리거 함수 + 트리거  (PoolerTran_설계.md §5.2)
-- ---------------------------------------------------------------------
-- Correlator 의 UPDATE 트랜잭션 안에서 실행 → 페어링 커밋과 큐 적재가 원자적.
-- pg_notify 는 PoolerTran 의 저지연(LISTEN) 모드(PT_USE_LISTEN=true)를 위한
-- 보조 신호다.  큐 테이블이 source of truth 이고 NOTIFY 는 지연 단축용이므로,
-- 알림이 유실되어도(컨슈머 다운 중) 폴링이 백업으로 동작한다(설계 §8.5).
--
-- SECURITY DEFINER: 트리거를 발화시키는 주체는 Correlator(gw_writer)이지만,
--   큐 INSERT 는 함수 소유자(이 마이그레이션을 적용한 postgres = 큐 소유자) 권한으로
--   실행된다.  이렇게 하면 gw_writer 의 큐 INSERT 권한 부여 여부/런타임 권한 평가와
--   무관하게 적재가 보장된다(아래 78줄 GRANT 는 그래도 명시적으로 유지).
--   SET search_path 는 SECURITY DEFINER 함수의 스키마 하이재킹 방지 권고사항.
CREATE OR REPLACE FUNCTION enqueue_cctv_transfer()
RETURNS TRIGGER LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
    v_waypoint_id integer;
BEGIN
    -- 페어링된 dust 행의 waypoint_id 확인 — NULL 이면 큐에 적재하지 않는다.
    -- (waypoint 미지정 프레임은 전송 대상에서 제외.)
    SELECT waypoint_id INTO v_waypoint_id
      FROM dust_inspection
     WHERE id = NEW.dust_inspection_id;

    IF v_waypoint_id IS NULL THEN
        RETURN NULL;                     -- waypoint 미지정 → enqueue 스킵
    END IF;

    INSERT INTO cctv_transfer_queue (frame_id, dust_id)
    VALUES (NEW.id, NEW.dust_inspection_id)
    ON CONFLICT (frame_id) DO NOTHING;   -- 멱등
    PERFORM pg_notify('cctv_transfer', NEW.id::text);
    RETURN NULL;                         -- AFTER 트리거라 반환값 무시
END;
$$;

-- 미페어링 → 페어링 "전이" 시에만 1회 발화 → 중복 적재 방지.
DROP TRIGGER IF EXISTS trg_enqueue_cctv_transfer ON cctv_frame;
CREATE TRIGGER trg_enqueue_cctv_transfer
AFTER UPDATE OF dust_inspection_id ON cctv_frame
FOR EACH ROW
WHEN (OLD.dust_inspection_id IS NULL AND NEW.dust_inspection_id IS NOT NULL)
EXECUTE FUNCTION enqueue_cctv_transfer();


-- ---------------------------------------------------------------------
-- 3) 권한 / 롤  (PoolerTran_설계.md §5.3)
-- ---------------------------------------------------------------------
-- INSERT(생성)는 Correlator 트리거(gw_writer 권한)만 가능.
GRANT INSERT ON cctv_transfer_queue TO gw_writer;

-- PoolerTran 전용 롤.  read-only 롤(gw_reader) 재사용 금지: DELETE 가 필요.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'cctv_forwarder') THEN
        CREATE ROLE cctv_forwarder LOGIN PASSWORD 'dev_forwarder_pw';
    END IF;
END $$;

GRANT CONNECT ON DATABASE gateway_db        TO cctv_forwarder;
GRANT USAGE  ON SCHEMA public               TO cctv_forwarder;
-- 소스 테이블: 읽기만 (단방향 흐름 원칙).
GRANT SELECT ON cctv_frame, dust_inspection TO cctv_forwarder;
-- 큐: 조회/삭제/attempts 갱신만 → 삭제 주체는 오직 PoolerTran.
GRANT SELECT, DELETE, UPDATE (attempts) ON cctv_transfer_queue TO cctv_forwarder;

-- cleaner(gw_cleaner)에는 이 테이블에 어떤 권한도 부여하지 않는다.
-- FK 도 없으므로 cleaner 는 큐를 직접/간접(CASCADE) 어느 쪽으로도 삭제할 수 없다.

-- 운영 비밀번호는 dev_* 대신 시크릿으로 주입한다.

-- ---------------------------------------------------------------------
-- (참고) 결과/DLQ 테이블은 gateway_db 에 두지 않는다.
-- ---------------------------------------------------------------------
-- decision_agent 도입 후 PoolerTran 은 결과(decision_record)와 포이즌 메시지(DLQ)를
-- 모두 **decision_db** 에 기록한다.  과거 여기에 있던 transfer_result / transfer_dlq
-- (gateway_db 직접 적재)는 제거했다 — gateway_db 는 큐/소스 읽기 전용으로만 쓴다.
-- (decision_db 스키마: decision_agent/init_db.sql)
