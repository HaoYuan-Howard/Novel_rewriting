# -*- coding: utf-8 -*-
"""Stage4 运行时失败 witness 的唯一类型、指纹与关闭式路由。

TODO-45 批次 1：validator 的人话判词（``str | None``）保持向后兼容，但新 Stage4
路径同时产出一组 typed failure。三份出口各取所需，**不建第二份审计文件**：

- **完整 witness（带 refs/scene_refs 与判词上下文）** 只去两个地方：交回同一
  provider 的反馈行，与 ``_llm_rejected/<chain>/<attempt>/`` 恢复面；
- **``_service/llm_audit.jsonl``** 只取无正文的 ``code`` / ``chapter_ordinal`` /
  ``failure_fingerprint``（沿用 reject_detail 的 ``loc/type/count`` 形状）；
- **路由** 只按 ``repair_owner``（闭合表在 ``contracts.invariants``）：
  A/Stage2/code owner 的失败在 B 首轮立即停止，不消耗四轮预算；B owner 回同一
  session；同一 fingerprint 集连续两轮未闭合提前终止该 unit。

`failure_fingerprint` 是失败的**语义身份**：``sha256(code, rule, chapter_ordinal,
scene_refs, refs)``。刻意**不含** candidate_sha256——同一个未关闭违约换一份候选
重新出现时指纹必须仍然相等，否则「连续两轮未关闭」永远判不出来；候选身份单独
放在 ``candidate_sha256`` 字段里对证。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from contracts.invariants import (
    FAILURE_REPAIR_OWNERS,
    stage4_failure_owner,
    stage4_failure_retryable,
)


def semantic_fingerprint(*, code: str, rule: str, chapter_ordinal: int | None,
                         scene_refs: Iterable[str], refs: Iterable[str]) -> str:
    """失败语义身份的唯一算法；换候选不换指纹，关掉了才算关闭。"""
    payload = {
        'code': str(code),
        'rule': str(rule),
        'chapter_ordinal': chapter_ordinal,
        'scene_refs': [str(ref) for ref in scene_refs],
        'refs': [str(ref) for ref in refs],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class TypedFailure:
    """一次运行时失败的最小闭合 witness。字段集冻结，不许各调用点自行增删。"""

    candidate_sha256: str
    code: str
    rule: str
    chapter_ordinal: int | None
    scene_refs: tuple[str, ...]
    refs: tuple[str, ...]
    repair_owner: str
    retryable: bool
    failure_fingerprint: str

    def __post_init__(self) -> None:
        if self.repair_owner not in FAILURE_REPAIR_OWNERS:
            raise ValueError(
                f'typed failure owner 越界：{self.repair_owner!r} 不在 '
                f'{list(FAILURE_REPAIR_OWNERS)}')
        if not self.candidate_sha256:
            raise ValueError('typed failure 缺 candidate_sha256；'
                             '连 wire 前失败也要给被判载荷的 SHA-256')

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe 全量 witness 行；进 provider 反馈与 ``_llm_rejected``。"""
        return {
            'candidate_sha256': self.candidate_sha256,
            'code': self.code,
            'rule': self.rule,
            'chapter_ordinal': self.chapter_ordinal,
            'scene_refs': list(self.scene_refs),
            'refs': list(self.refs),
            'repair_owner': self.repair_owner,
            'retryable': self.retryable,
            'failure_fingerprint': self.failure_fingerprint,
        }

    def audit_row(self) -> dict[str, Any]:
        """无正文读数行：并入 ``llm_audit.jsonl`` 的 ``reject_detail``。"""
        return {
            'loc': self.code,
            'type': 'typed_failure',
            'count': 1,
            'code': self.code,
            'chapter_ordinal': self.chapter_ordinal,
            'fingerprint': self.failure_fingerprint,
        }


def make(*, candidate_sha256: str, code: str, rule: str,
         chapter_ordinal: int | None = None,
         scene_refs: Iterable[str] = (), refs: Iterable[str] = (),
         surfacing: str) -> TypedFailure:
    """唯一构造口：owner 查 invariants 闭合表，retryable 只属本节点或 infra。

    ``surfacing`` 是失败被发现的节点（如 ``stage4.plan``）。规则：失败只有
    **由能修它的那一方自己发现**时才值得原地重试——A/Stage2/code owner 的失败
    在 B 的轮次里重试不可能闭合，必须停。
    """
    owner = stage4_failure_owner(code)
    return TypedFailure(
        candidate_sha256=candidate_sha256,
        code=str(code),
        rule=str(rule),
        chapter_ordinal=chapter_ordinal,
        scene_refs=tuple(str(ref) for ref in scene_refs),
        refs=tuple(str(ref) for ref in refs),
        repair_owner=owner,
        retryable=stage4_failure_retryable(code, surfacing=surfacing),
        failure_fingerprint=semantic_fingerprint(
            code=code, rule=rule, chapter_ordinal=chapter_ordinal,
            scene_refs=scene_refs, refs=refs),
    )


@dataclass
class WitnessCollector:
    """判据一次扫描途中的 typed 失败收集器。

    判据在发射违规行的同一个地方顺手 ``add``（那里才知道语义 code 与 refs），
    扫描结束后由调用方 ``finalize`` 补上候选身份与节点面。**不许**事后从
    detail 文本反推章节——那正是批次 1 要关掉的门。
    """

    _rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, *, code: str, rule: str, chapter_ordinal: int | None = None,
            scene_refs: Iterable[str] = (), refs: Iterable[str] = ()) -> None:
        self._rows.append({
            'code': str(code), 'rule': str(rule),
            'chapter_ordinal': chapter_ordinal,
            'scene_refs': [str(ref) for ref in scene_refs],
            'refs': [str(ref) for ref in refs],
        })

    def rows(self) -> tuple[dict[str, Any], ...]:
        """已收集的发射点行（finalize 前的只读视图）。"""
        return tuple(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def finalize(self, *, candidate_sha256: str, surfacing: str,
                 ) -> tuple[TypedFailure, ...]:
        return tuple(
            make(candidate_sha256=candidate_sha256, surfacing=surfacing, **row)
            for row in self._rows)
