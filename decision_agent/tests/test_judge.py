"""Judge unit tests — verify all 8 alarm_mapping combinations (2×2×2).

These tests exercise the in-memory lookup behavior of Judge by injecting
a hand-built truth table; no DB required. The integration test exercises
the same combinations end-to-end against a live alarm_mapping table.

센서 2단계(normal/abnormal) + 위험(danger) 규칙 반영
(decision_agent_2x2x2_구현계획.md §4).
"""

from __future__ import annotations

import pytest

from decision_agent.judge import Judge, JudgeLookupError


# (sensor_level, static_result, dynamic_result) -> expected final_decision
# 센서 2단계.  위험 = sensor=abnormal AND (static or dynamic = abnormal).
EXPECTED: dict[tuple[str, str, str], str] = {
    ("normal",   "normal",   "normal"):   "normal",
    ("normal",   "normal",   "abnormal"): "caution",
    ("normal",   "abnormal", "normal"):   "caution",
    ("normal",   "abnormal", "abnormal"): "caution",
    ("abnormal", "normal",   "normal"):   "warning",
    ("abnormal", "normal",   "abnormal"): "danger",
    ("abnormal", "abnormal", "normal"):   "danger",
    ("abnormal", "abnormal", "abnormal"): "danger",
}


def _stub_judge() -> Judge:
    j = Judge(pool=None)  # type: ignore[arg-type]
    j._table = {key: (final, idx) for idx, (key, final) in enumerate(EXPECTED.items(), 1)}
    return j


@pytest.mark.parametrize(("key", "expected_final"), list(EXPECTED.items()))
def test_judge_returns_expected_final_decision(
    key: tuple[str, str, str], expected_final: str
) -> None:
    judge = _stub_judge()
    final, mapping_id = judge.judge(*key)
    assert final == expected_final
    assert isinstance(mapping_id, int)


def test_judge_raises_on_unknown_combination() -> None:
    judge = _stub_judge()
    with pytest.raises(JudgeLookupError):
        judge.judge("unknown", "normal", "normal")


def test_judge_raises_on_pending_input() -> None:
    """alarm_mapping has no 'pending' rows; pending should miss."""
    judge = _stub_judge()
    with pytest.raises(JudgeLookupError):
        judge.judge("normal", "pending", "normal")
