# -*- coding: utf-8 -*-
"""源文案独立证据的唯一回取和校验口；语义仍由 Global Commander 决定。

文案不属于任何正文章。仅有钩子支持的主张可用于文案创作，不会因此进入
Blueprint 或成为正文补写义务；正文缺少重复表述也不等于事实冲突。
"""
from __future__ import annotations

from typing import Any

from novel.core.evidence import iter_hook_evidence_slots
from novel.core.spans import exact_span, locate_unique_span
from novel.errors import UpstreamMissing


def normalize_hook_evidence(data: dict[str, Any], source_hook: str | None) -> None:
    """只在 HookCopy 回取；失败保留原定位供 typed 编辑与免费复验，不猜章号。"""
    slots = list(iter_hook_evidence_slots(data))
    if slots and source_hook is None:
        raise UpstreamMissing('源文案独立证据缺少当前 HookCopy，不能归一或提升')
    for _path, owner, field in slots:
        assert source_hook is not None
        evidence = owner.get(field) or {}
        locator = evidence.get('locator') or evidence.get('unresolved_locator')
        source = evidence.get('source')
        if locator is not None:
            got = locate_unique_span(source_hook, locator)
            if isinstance(got, tuple):
                start, end, _recovery = got
                source = {'text': source_hook[start:end], 'start': start, 'end': end}
            else:
                source = None
        elif exact_span(source, source_hook)[2]:
            source = None
        owner[field] = {
            'source': source,
            'unresolved_locator': locator if source is None else None,
        }


def hook_evidence_problems(data: dict[str, Any], source_hook: str | None) -> dict[str, str]:
    """正式证据与输入逐码点对账；缺失仍是缺失，不以原文主张猜支持关系。"""
    problems = {}
    for path, owner, field in iter_hook_evidence_slots(data):
        if source_hook is None:
            problems[path] = '缺少当前源文案 HookCopy，无法验证独立证据'
            continue
        evidence = owner.get(field) or {}
        problem = exact_span(evidence.get('source'), source_hook)[2]
        if problem:
            problems[path] = 'HookCopy: ' + problem
        elif evidence.get('unresolved_locator') is not None:
            problems[path] = '已定位文案证据仍携失败定位，来源状态冲突'
    return problems


def manual_hook_evidence(evidence: dict[str, Any], source_hook: str | None) -> dict[str, str]:
    """从正式锚点生成可原样复验的免费编辑定位；不删除无法定位的问题。"""
    from novel.errors import TerminalState
    if source_hook is None:
        raise UpstreamMissing('源文案编辑模板缺当前 HookCopy')
    failed = evidence.get('unresolved_locator')
    if failed:
        return {'locator': failed}
    start, end, problem = exact_span(evidence.get('source'), source_hook)
    if problem:
        raise TerminalState('源文案正式证据无法生成编辑定位：' + problem)
    literal = source_hook[start:end]
    if len(literal) <= 160 and isinstance(locate_unique_span(source_hook, literal), tuple):
        return {'locator': literal}
    # 与正文模板同样只采用唯一、至多 160 码点的现有证据窗口。
    lo_min, lo_max = max(0, end - 160), min(start, len(source_hook) - 1)
    for lo in range(lo_min, lo_max + 1):
        candidate = source_hook[lo:lo + 160]
        if candidate and isinstance(locate_unique_span(source_hook, candidate), tuple):
            return {'locator': candidate}
    raise TerminalState('源文案证据没有可唯一定位的 ≤160 码点窗口，须人工修订')


def hook_evidence_materials(
        slots: list[tuple[str, object]], source_hook: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """审阅行复用同一 HookCopy 坐标；明确来源，永不造正文引用。"""
    import hashlib
    from contracts.artifacts import STAGE1_HOOK_COPY
    from contracts.schema.evaluation import EvidenceRef

    materials, unresolved = [], []
    digest = hashlib.sha256(source_hook.encode('utf-8')).hexdigest()
    for path, evidence in slots:
        source = evidence.get('source') if isinstance(evidence, dict) else None
        start, end, problem = exact_span(source, source_hook)
        if problem:
            unresolved.append({'claim_path': path, 'artifact': STAGE1_HOOK_COPY,
                               'chapter_ordinal': None, 'reason': problem})
            continue
        ref = EvidenceRef(artifact=STAGE1_HOOK_COPY, subject_sha256=digest, start=start, end=end)
        materials.append({
            'claim_path': path, 'evidence_ref': ref.model_dump(mode='json'),
            'chapter_ordinal': None, 'literal': source_hook[start:end],
            'start': start, 'end': end,
            'context': source_hook[max(0, start - 60):min(len(source_hook), end + 60)],
        })
    return materials, unresolved
