#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 编译计划与 A/B 中间产物的完整字节绑定。

普通 ``inputs[].file/consumed`` 回答“生产者实际消费的业务投影有没有变化”；
``PlanProvenance`` 里的两枚 SHA-256 回答另一个更强的问题：盘上当前 A/B 是否就是
当时编译这份计划的那两个**完整版本化文件**。两者不能互相代替，也不能把完整绑定
再走一次 ``artifact_payload`` 后只比较业务载荷。

本模块只做纯比较。freshness、Stage4 HARD 与写盘后置条件都调用
``verify_plan_phase_artifacts``，避免三个入口各自重拼一把尺子。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from contracts.artifacts import STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING

PhaseLinkState = Literal['match', 'mismatch', 'ghost', 'invalid']

_PHASE_LINKS = (
    ('blind_candidate_sha256', STAGE4_BLIND_CANDIDATE),
    ('evidence_binding_sha256', STAGE4_EVIDENCE_BINDING),
)


@dataclass(frozen=True)
class PhaseArtifactLink:
    """一条编译期完整字节绑定的当前复核结果。"""

    provenance_field: str
    key: str
    expected_sha256: str | None
    now_sha256: str | None
    state: PhaseLinkState


def verify_plan_phase_artifacts(
    plan: Mapping[str, Any],
    *,
    sha256_now: Callable[[str], str | None],
) -> tuple[PhaseArtifactLink, ...]:
    """按计划保存的完整 SHA-256 复核盘上当前 A/B 字节。

    ``origin=human`` 的计划明确不再冒充 A+B 的确定性编译物，因此没有这两条边；
    其普通 provenance 输入仍由通用 freshness 判定。未知/旧来源也不在这里猜成
    ``compiled``，让既有 schema/freshness 如实处理。
    """
    provenance = plan.get('_provenance')
    if not isinstance(provenance, Mapping) or provenance.get('origin') != 'compiled':
        return ()

    links: list[PhaseArtifactLink] = []
    for field, key in _PHASE_LINKS:
        expected = provenance.get(field)
        expected_sha = expected if _is_sha256(expected) else None
        current = sha256_now(key)
        current_sha = current if _is_sha256(current) else None
        if expected_sha is None:
            state: PhaseLinkState = 'invalid'
        elif current is None:
            state = 'ghost'
        elif current_sha is None or current_sha != expected_sha:
            state = 'mismatch'
        else:
            state = 'match'
        links.append(PhaseArtifactLink(
            provenance_field=field,
            key=key,
            expected_sha256=expected_sha,
            now_sha256=current_sha,
            state=state,
        ))
    return tuple(links)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in '0123456789abcdef' for ch in value)
    )


__all__ = ['PhaseArtifactLink', 'verify_plan_phase_artifacts']
