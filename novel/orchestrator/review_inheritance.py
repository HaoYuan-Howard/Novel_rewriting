# -*- coding: utf-8 -*-
"""审阅义务继承的纯证据核验；不产生语义判断，不读盘或写审批。

调用方从唯一当前依赖投影提供义务身份，并从正式历史提供 receipt。
继承只能引用更早且完整有效的相同义务接受。最新否决遮住旧接受，
损坏的可能来源保持 unknown；禁止将漏填、删除或摘要相似推成接受。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from novel.errors import ArtifactUnreadable, HumanEditProtected


@dataclass(frozen=True)
class DecisionUnit:
    subject_sha256: str
    payload: Mapping[str, Any]
    source_decision_sha256: str | None = None

    @property
    def accepted(self) -> bool:
        return self.payload.get('decision') == 'accept'


@dataclass(frozen=True)
class DecisionReceipt:
    decision_sha256: str
    units: Mapping[str, DecisionUnit]
    valid: bool


def validate_history(history: Sequence[DecisionReceipt]) -> None:
    """逐层回到原始裁决；相同业务回执的合法 proof 重绑允许重复摘要。"""
    earlier: dict[str, DecisionReceipt] = {}
    available: dict[str, dict[str, DecisionReceipt]] = {}
    for receipt in history:
        previous = earlier.get(receipt.decision_sha256)
        if previous is not None:
            if previous != receipt:
                raise ArtifactUnreadable('同一审阅摘要指向不同裁决或损坏来源')
            continue  # 相同已验证回执的 proof 重绑，不是又作出一次裁决。
        for key, unit in receipt.units.items():
            ref = unit.source_decision_sha256
            if ref is None:
                continue
            source = earlier.get(ref)
            original = None if source is None else source.units.get(key)
            if (not receipt.valid or source is None or not source.valid
                    or original is None or not original.accepted or not unit.accepted
                    or original.subject_sha256 != unit.subject_sha256
                    or original.payload != unit.payload
                    or available.get(key, {}).get(unit.subject_sha256) != source):
                raise ArtifactUnreadable(f'审阅继承 {key} 的原决定、义务或证据链不闭合')
        earlier[receipt.decision_sha256] = receipt
        for key, unit in receipt.units.items():
            if not receipt.valid or not unit.accepted:
                available[key] = {}
            else:
                available.setdefault(key, {})[unit.subject_sha256] = receipt


def accepted_sources(
    history: Sequence[DecisionReceipt], current: Mapping[str, str],
) -> dict[str, DecisionReceipt]:
    validate_history(history)
    accepted: dict[str, DecisionReceipt] = {}
    seen: set[str] = set()
    for receipt in reversed(history):
        for key, unit in receipt.units.items():
            if key not in current or key in seen:
                continue
            if not receipt.valid:
                raise ArtifactUnreadable(f'审阅义务 {key} 的可能来源损坏，不能采用更早接受')
            if not unit.accepted:
                # 修改问题后即使回到更早接受过的字节，也仍负有修后复审义务。
                seen.add(key)
                continue
            if unit.subject_sha256 != current[key]:
                continue
            seen.add(key)
            if unit.accepted:
                accepted[key] = receipt
    return accepted


def validate_submission(
    history: Sequence[DecisionReceipt], current: Mapping[str, str],
    submitted: Mapping[str, DecisionUnit],
) -> None:
    """新决定与有效继承必须恰好覆盖当前义务；每项继承明确指向原回执。"""
    if set(submitted) != set(current):
        raise HumanEditProtected('本轮决定与继承决定的并集必须完整覆盖当前审阅义务')
    sources = accepted_sources(history, current)
    for key, unit in submitted.items():
        if unit.subject_sha256 != current[key]:
            raise HumanEditProtected(f'审阅义务 {key} 当前主体变化')
        if unit.source_decision_sha256 is None:
            continue
        source = sources.get(key)
        if (source is None or source.decision_sha256 != unit.source_decision_sha256
                or not unit.accepted or source.units[key].payload != unit.payload):
            raise HumanEditProtected(f'审阅义务 {key} 的继承来源过期、被否决或裁决内容变化')
