"""REST client — waypoint 배치 결과를 상대 서버로 전송하는 계층.

PoolerTran 은 waypoint 전환마다 그 waypoint 의 프레임 목록을 **한 번의 POST** 로
전송하고(:class:`BatchPathsRestClient`), 응답(정적/동적 결과 2쌍)을 poller 가
decision_db 의 ``decision_record`` 로 적재한다.  전송 contract 는
:class:`BaseRestClient` 로 추상화되어 설정(``PT_REST_MODE``)으로 선택한다
(현재 ``batch_paths`` 단독).

향후 contract 추가: BaseRestClient 를 상속해 ``send_batch`` 구현 + REGISTRY 에 등록.

공통 계약 (poller 동작 전제):
    * 성공(2xx) → ``(status_code, response_json)`` 반환.
    * 전송 오류/비-2xx → 예외(raise_for_status).  poller 가 큐 행을 유지하고
      재시도(at-least-once).  PT_MAX_ATTEMPTS 초과 시 DLQ.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta, timezone
from typing import Any

import httpx
import structlog

from .repository import QueueRow

# 한국시간(KST, UTC+9) — received_time 표기에 사용.
_KST = timezone(timedelta(hours=9))

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# 인터페이스
# ---------------------------------------------------------------------------
class BaseRestClient(ABC):
    """전송 계층 공통 인터페이스 — waypoint 단위 배치 전송."""

    #: 항상 배치 전송(send_batch).  poller/main 의 처리 경로 호환용으로 유지.
    is_batch: bool = True

    @abstractmethod
    async def send_batch(
        self, amr_id: str, waypoint_id: int, rows: list[QueueRow]
    ) -> tuple[int, Any]:
        """waypoint 분량 프레임을 1콜로 전송.  성공 시 (status, body), 실패 시 예외."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """보유한 커넥션 풀 등을 정리한다."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 구현) waypoint 단위 배치 전송  →  POST (amr_id, waypoint_id, frames[])
# ---------------------------------------------------------------------------
class BatchPathsRestClient(BaseRestClient):
    """waypoint 전환마다 그 waypoint 의 프레임 목록을 **한 번의 POST** 로 전송.

    payload:
        {
          "amr_id": "amr-01",
          "waypoint_id": 5,
          "frames": [
            {"received_time": "20260622172813_266", "file_path": "/data/storage/cctv/.../..jpg"},
            ...
          ]
        }
    received_time = 한국시간(KST) 'yyyymmddHHMMSS_sss' → 동일 초 내 다중 프레임도 구분 가능.
    """

    def __init__(
        self, url: str, timeout_sec: float, api_logging: bool = False,
        path_remap: tuple[str, str] = ("", ""),
    ) -> None:
        self._url = url
        self._client = httpx.AsyncClient(timeout=timeout_sec)
        self._api_logging = api_logging   # True → 호출 입력/출력 로깅
        self._remap_from, self._remap_to = path_remap   # 컨테이너→호스트 경로 변환

    async def send_batch(
        self, amr_id: str, waypoint_id: int, rows: list[QueueRow]
    ) -> tuple[int, Any]:
        # 배치 = 한 waypoint = 한 관측 개소 → target_id 는 배치 내 동일.
        target_id = rows[0].target_id if rows else None
        payload = {
            "amr_id": amr_id,
            "target_id": target_id,
            "frames": [
                {
                    "received_time": _kst_received_time(r.received_at),
                    "file_path": _remap_path(r.file_path, self._remap_from, self._remap_to),
                }
                for r in rows
            ],
        }
        if self._api_logging:
            _log.info("rest_api_request", url=self._url,
                      target_id=target_id, payload=payload)
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        body = _safe_json(resp)
        if self._api_logging:
            _log.info("rest_api_response", url=self._url,
                      target_id=target_id, status=resp.status_code, body=body)
        return resp.status_code, body

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# 데모(더미) 구현 — 실제 REST API 미구현 시 HTTP 없이 합의된 출력만 반환
# ---------------------------------------------------------------------------
class DemoRestClient(BaseRestClient):
    """실제 호출 대신 더미 응답을 돌려주는 stand-in (배치 전용).

    실제 API 준비 시 ``PT_REST_DEMO=false`` 로만 바꾸면 된다.

    데모 응답 형식 (실제 API 와 동일 형태): ``list[2]`` =
        ``[{score, path1, path2}(정적), {score, path1, path2}(동적)]``.
        path1·path2 는 배치의 **첫 입력 프레임 경로**(rows[0].file_path)를 echo
        (고정 ``PT_REST_DEMO_IMAGE_PATH`` 지정 시 그것을 우선).
    """

    def __init__(
        self, *, score: float, image_path: str = "", mode: str = "", version: int = 1,
        api_logging: bool = False, path_remap: tuple[str, str] = ("", ""),
    ) -> None:
        self._score = score
        self._fixed_image_path = image_path or None
        self._version = version       # 1=고정 score, 2=waypoint별 프로필
        self._api_logging = api_logging   # True → 호출 입력/출력 로깅
        self._remap_from, self._remap_to = path_remap   # 컨테이너→호스트 경로 변환
        _log.warning(
            "rest_demo_enabled",
            msg="REST 데모 모드: 실제 호출 없이 더미 응답을 반환합니다(운영 전 반드시 비활성화).",
            mode=mode,
            score=score,
            version=version,
        )

    # demo_version=2 전용: waypoint별 (static 정적분진, dynamic 동적분진) 점수 프로필.
    # 등록되지 않은 waypoint 는 기본 score(_score)를 정적/동적 동일 적용.
    # waypoint → 최종판정이 4단계(normal/caution/warning/danger) 전부 나오도록 구성.
    # (정적,동적) 임계 0.5 기준 + 시나리오 dust(iot) 임계 2.0 기준의 조합:
    #   101: dust 2.5(abn) + 정적 abn + 동적 abn → danger
    #   201: dust 0.5(norm)+ 정적 norm+ 동적 norm → normal
    #   301: dust 2.5(abn) + 정적 norm+ 동적 norm → warning  (센서만 이상)
    #   401: dust 0.5(norm)+ 정적 abn + 동적 norm → caution  (비전 단독 탐지)
    _WP_PROFILE: dict[int, tuple[float, float]] = {
        101: (0.7, 0.7),   # danger
        201: (0.2, 0.2),   # normal
        301: (0.2, 0.2),   # warning
        401: (0.7, 0.2),   # caution
    }

    def _batch_body(
        self, waypoint_id: int | None, rows: list[QueueRow]
    ) -> list[dict[str, Any]]:
        """배치 데모 응답 — 실제 API 의 dual 결과 형식과 동일한 ``list[2]``.

        ``[{score, path1, path2}(정적), {score, path1, path2}(동적)]`` 를 반환한다.
        - version 1(기본): 정적/동적 모두 rest_demo_score 고정.
        - version 2: waypoint별 프로필(_WP_PROFILE), 미등록 waypoint 는 기본 score.
        path1·path2 는 배치 첫 프레임 경로 echo. poller 의 _extract_dual /
        _static_p1 이 이 형식을 그대로 소비한다."""
        if self._version >= 2:
            static_s, dynamic_s = self._WP_PROFILE.get(
                waypoint_id, (self._score, self._score)
            )
        else:
            static_s = dynamic_s = self._score      # version 1: 정적/동적 동일 고정
        first_path = self._fixed_image_path or (rows[0].file_path if rows else None)
        return [
            {"score": static_s, "path1": first_path, "path2": first_path},
            {"score": dynamic_s, "path1": first_path, "path2": first_path},
        ]

    async def send_batch(
        self, amr_id: str, waypoint_id: int, rows: list[QueueRow]
    ) -> tuple[int, list[dict[str, Any]]]:
        body = self._batch_body(waypoint_id, rows)
        if self._api_logging:
            # 실제 REST 라면 보냈을 입력(payload) 형태로 동일하게 로깅.
            target_id = rows[0].target_id if rows else None
            payload = {
                "amr_id": amr_id,
                "target_id": target_id,
                "frames": [
                    {
                        "received_time": _kst_received_time(r.received_at),
                        "file_path": _remap_path(r.file_path, self._remap_from, self._remap_to),
                    }
                    for r in rows
                ],
            }
            _log.info("rest_api_request", demo=True,
                      target_id=target_id, payload=payload)
            _log.info("rest_api_response", demo=True,
                      target_id=target_id, status=200, body=body)
        return 200, body

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 팩토리 — 설정으로 구현체 선택
# ---------------------------------------------------------------------------
#: PT_REST_MODE 값 → 구현체.  새 contract 는 여기에 한 줄 추가.
REGISTRY: dict[str, type[BaseRestClient]] = {
    "batch_paths": BatchPathsRestClient,   # waypoint 단위 배치(경로 배열) 1콜
}


def create_rest_client(settings: Any) -> BaseRestClient:
    """settings.rest_mode 에 맞는 RestClient 구현체를 생성한다.

    PT_REST_DEMO=true 면 실제 호출 대신 :class:`DemoRestClient` 를 반환한다.
    (모드 검증은 데모에서도 동일 → 데모 해제 시 같은 모드로 바로 전환.)
    """
    mode = getattr(settings, "rest_mode", "batch_paths")
    if mode not in REGISTRY:
        raise ValueError(
            f"알 수 없는 PT_REST_MODE={mode!r}; 사용 가능: {sorted(REGISTRY)}"
        )

    api_logging = getattr(settings, "api_logging", False)
    path_remap = (
        getattr(settings, "path_remap_from", "/data/storage"),
        getattr(settings, "path_remap_to", ""),
    )
    if getattr(settings, "rest_demo", False):
        return DemoRestClient(
            score=getattr(settings, "rest_demo_score", 0.0),
            image_path=getattr(settings, "rest_demo_image_path", ""),
            mode=mode,
            version=getattr(settings, "rest_demo_version", 1),
            api_logging=api_logging,
            path_remap=path_remap,
        )

    cls = REGISTRY[mode]
    return cls(settings.rest_url, settings.rest_timeout_sec, api_logging, path_remap)


#: 타입 힌트용 별칭.
RestClient = BaseRestClient


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _kst_received_time(dt) -> str | None:  # noqa: ANN001 - datetime|None
    """datetime → 한국시간(KST) 'yyyymmddHHMMSS_sss' 문자열.  None 이면 None.

    예: UTC 2026-06-22 08:28:13.266 → KST '20260622172813_266'.
    밀리초(_sss)로 동일 초 내 다중 프레임도 구분된다.
    """
    if dt is None:
        return None
    k = dt.astimezone(_KST)
    return k.strftime("%Y%m%d%H%M%S") + f"_{k.microsecond // 1000:03d}"


def _remap_path(path: str | None, frm: str, to: str) -> str | None:
    """전송 payload 용 경로 변환: 컨테이너 경로 prefix(frm)를 호스트 경로(to)로 치환.

    PoolerTran 컨테이너는 영상 디렉터리를 ``/data/storage`` 로 마운트하지만,
    외부 수신 서버(AnalysisReceiver)는 호스트 실제 경로로 파일을 찾는다.
    예) frm='/data/storage', to='/home/user1/svc/SocketDaim/storage' →
        '/data/storage/cctv/x.jpg' → '/home/user1/svc/SocketDaim/storage/cctv/x.jpg'.
    to 가 비면(미설정) 원본 그대로 반환(하위호환).  prefix 불일치 경로도 그대로 둔다.
    """
    if not path or not to:
        return path
    f = frm.rstrip("/")
    if path == f or path.startswith(f + "/"):
        return to.rstrip("/") + path[len(f):]
    return path
