"""Egress sink — 판정 결과를 LOAS MariaDB(t_inspection)에 INSERT.

LOAS 권한이 Insert+Select 뿐(Update 없음)이라 upsert 불가 → **순수 INSERT**.
중복 방지 키는 없다(관측 시간값이 달라 중복되지 않는다는 전제 — 고객 확인).
sender 가 t_inspection 컬럼명을 키로 하는 값 dict 을 만들어 넘기면, 그 컬럼들을
그대로 INSERT 한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import aiomysql
import structlog

_log = structlog.get_logger(__name__)


class Sink(ABC):
    async def open(self) -> None:
        return None

    @abstractmethod
    async def write(self, values: dict[str, Any]) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class MariaDbSink(Sink):
    """t_inspection 에 1행 INSERT.  values 의 키 = 대상 컬럼명."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        db: str,
        table: str,
        user: str,
        password: str,
        pool_min: int = 1,
        pool_max: int = 5,
        sql_log: bool = False,
    ) -> None:
        self._conn_params = dict(
            host=host, port=port, db=db, user=user, password=password
        )
        self._table = table
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._sql_log = sql_log          # True → 실제 INSERT 전체 구문 로깅
        self._pool: aiomysql.Pool | None = None

    async def open(self) -> None:
        self._pool = await aiomysql.create_pool(
            minsize=self._pool_min,
            maxsize=self._pool_max,
            autocommit=False,
            **self._conn_params,
        )
        _log.info("egress_target_ready", target_table=self._table)

    async def write(self, values: dict[str, Any]) -> bool:
        """values(키=컬럼명) 1행 INSERT.  성공 True, 실패는 예외."""
        assert self._pool is not None, "open() 을 먼저 호출해야 합니다"
        cols = list(values.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        col_list = ", ".join(f"`{c}`" for c in cols)
        sql = f"INSERT INTO `{self._table}` ({col_list}) VALUES ({placeholders})"
        params = [values[c] for c in cols]
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                if self._sql_log:
                    self._log_statement(cur, sql, cols, params)
                await cur.execute(sql, params)
            await conn.commit()
        return True

    def _log_statement(self, cur, sql: str, cols: list[str], params: list) -> None:
        """EGW_SQL_LOG_ENABLE=true 일 때 값이 치환된 실제 INSERT 전체 구문을 로깅.

        image_data(base64)는 통째로 찍으면 로그가 폭주하므로 앞 64자 + 길이만 표기.
        나머지 값은 그대로 두어 컬럼/값/FK 디버깅에 쓸 수 있게 한다.
        """
        shown = [
            (f"{v[:64]}...<{len(v)}B>"
             if c == "image_data" and isinstance(v, str) and len(v) > 64 else v)
            for c, v in zip(cols, params)
        ]
        try:
            rendered = cur.mogrify(sql, shown)   # 값 치환된 최종 SQL 문자열
        except Exception:                        # mogrify 미지원/실패 시 폴백
            rendered = f"{sql}  -- VALUES={shown!r}"
        _log.info("egress_insert_sql", statement=rendered)

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
