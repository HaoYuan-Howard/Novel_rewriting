#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 candidate-only ``role/fate`` 的关闭式窄修。

本模块只处理不可信 wire candidate 的局部状态转移：目标集合来自生产判据的
typed helper，证据 ID 来自同一次请求的候选表，最终业务结论仍由
``extract.evaluate_global_candidate`` 重跑完整 schema/normalize/HARD 得出。

把纯合并留在这里，是为了让 dynamic validator 与 accepted 后的真实 applicator
调用同一入口；两边各拼一份 allowlist/掩码会形成“validator 说能收、落地时改了
别处”的第二把尺子。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from contracts.schema.stage2 import (
    GlobalGroundingCandidateResponse,
    GlobalRoleFatePatchTurn,
)
from novel.stages.stage2 import checks
from novel.stages.stage2.global_evidence_candidates import (
    GlobalEvidenceCandidateTable,
)


_PATCH_FIELDS = ('role', 'role_evidence', 'fate', 'fate_evidence')


@dataclass(frozen=True)
class RoleFatePatchScope:
    """一份业务拒稿允许修改的完整关闭集合。"""

    candidate_sha256: str
    evidence_table_sha256: str
    char_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'kind': 'role_fate_patch',
            'candidate_sha256': self.candidate_sha256,
            'evidence_table_sha256': self.evidence_table_sha256,
            'allowed_char_ids': list(self.char_ids),
            'allowed_fields': list(_PATCH_FIELDS),
            'instruction': (
                '逐个且只修 allowed_char_ids。每项必须原子提交 role、role_evidence、'
                'fate、fate_evidence；把 role 中承载的完整经历/结局事实搬入 fate，'
                '禁止只删词或换同义词规避。证据只复制本次候选表 ID。'),
        }


@dataclass(frozen=True)
class AppliedRoleFatePatch:
    response: GlobalGroundingCandidateResponse
    changed_field_count: int


def scope_for(
        response: GlobalGroundingCandidateResponse,
        *, hard_hits: tuple[str, ...], candidate_sha256: str,
        evidence_table: GlobalEvidenceCandidateTable,
) -> RoleFatePatchScope | None:
    """只把精确的 ``role_leaks_fate`` 收成 typed allowlist。"""
    if hard_hits != ('role_leaks_fate',):
        return None
    raw = response.model_dump(by_alias=True, mode='json')
    characters = raw.get('entities', {}).get('characters', {})
    targets = checks.role_fate_leak_targets(characters)
    if not targets:
        raise ValueError(
            'role_leaks_fate HARD 与 typed leak target 集合不一致；拒绝猜 allowlist')
    return RoleFatePatchScope(
        candidate_sha256=candidate_sha256,
        evidence_table_sha256=evidence_table.sha256(),
        char_ids=tuple(target.char_id for target in targets),
    )


def _masked_projection(
        raw: dict[str, Any], char_ids: tuple[str, ...],
) -> dict[str, Any]:
    projected = copy.deepcopy(raw)
    characters = projected['entities']['characters']
    for char_id in char_ids:
        character = characters[char_id]
        for field_name in _PATCH_FIELDS:
            character[field_name] = {
                '_authorized_role_fate_patch': f'{char_id}.{field_name}',
            }
    return projected


def apply_patch(
        response: GlobalGroundingCandidateResponse,
        patch: GlobalRoleFatePatchTurn,
        scope: RoleFatePatchScope,
        evidence_table: GlobalEvidenceCandidateTable,
        *, actual_candidate_sha256: str,
) -> AppliedRoleFatePatch:
    """按双 SHA、精确人物集合与四字段掩码合并一次窄修。

    这里要求 ``role`` 实际变化且修订后 fate 非空。四字段必须原子提交，但不强迫
    已经正确承载同一事实的旧 ``fate`` 为了通过形式门再改写一次；那只会诱导无谓
    同义改写，反而损伤事实稳定性。完整事实是否真的搬全不是机器可判命题，最终由
    workpack 中成对展示的 role/fate 人审确认。
    """
    if actual_candidate_sha256 != scope.candidate_sha256:
        raise ValueError('role/fate patch scope 绑定的 base candidate 已漂移')
    if patch.candidate_sha256 != scope.candidate_sha256:
        raise ValueError('role/fate patch candidate_sha256 与当前候选不一致')
    current_table_sha = evidence_table.sha256()
    if current_table_sha != scope.evidence_table_sha256:
        raise ValueError('role/fate patch scope 绑定的候选表已漂移')
    if patch.evidence_table_sha256 != current_table_sha:
        raise ValueError('role/fate patch evidence_table_sha256 与当前候选表不一致')

    by_char = {change.char_id: change for change in patch.changes}
    allowed = scope.char_ids
    if set(by_char) != set(allowed):
        missing = sorted(set(allowed) - set(by_char))
        extra = sorted(set(by_char) - set(allowed))
        raise ValueError(
            'role/fate patch 人物集合必须与 allowlist 精确相等：'
            f'missing={missing}, extra={extra}')

    before = response.model_dump(by_alias=True, mode='json')
    candidate = copy.deepcopy(before)
    characters = candidate['entities']['characters']
    changed_fields = 0
    for char_id in allowed:
        old = before['entities']['characters'][char_id]
        change = by_char[char_id]
        if change.role == old.get('role'):
            raise ValueError(f'role/fate patch {char_id}.role 是 no-op')
        if change.fate is None or not change.fate.strip():
            raise ValueError(
                f'role/fate patch {char_id}.fate 不能为空；旧 role 的泄漏事实必须有去向')
        # 格式由 schema 守，成员资格只由冻结候选表守；不解析 ID 或猜相邻行。
        evidence_table.lookup(change.role_evidence.evidence_candidate_id)
        assert change.fate_evidence is not None
        evidence_table.lookup(change.fate_evidence.evidence_candidate_id)
        replacement = change.model_dump(mode='json')
        replacement.pop('char_id')
        for field_name in _PATCH_FIELDS:
            if old.get(field_name) != replacement[field_name]:
                changed_fields += 1
            characters[char_id][field_name] = replacement[field_name]

    if _masked_projection(before, allowed) != _masked_projection(candidate, allowed):
        raise ValueError('role/fate patch 改动了 allowlist 四字段之外的候选内容')
    repaired = GlobalGroundingCandidateResponse.model_validate(candidate)
    if changed_fields < len(allowed):
        # role 已逐人强制变化；这条保护未来重构不把逐行 no-op 门移掉。
        raise ValueError('role/fate patch 每个人至少必须实际改变 role')
    return AppliedRoleFatePatch(
        response=repaired,
        changed_field_count=changed_fields,
    )


__all__ = [
    'AppliedRoleFatePatch', 'RoleFatePatchScope', 'apply_patch', 'scope_for',
]
