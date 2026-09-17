"""Core 证据的纯编译与复验；模型只引用候选，代码铸造稳定 ID 和坐标。

每份正式产物拥有自己的 anchors。相同原文区间在同一产物内只出现一次；
定位失败不猜证据、不用默认坐标补齐，人工编辑在任何写盘前拒绝。
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from contracts.schema.core_evidence import CoreEvidenceAnchor
from novel.core.spans import locate_unique_span
from novel.stages.stage2.shared.evidence_candidates import GlobalEvidenceCandidateTable


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def evidence_id(anchor: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(anchor), ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False)
    return 'ev_' + _sha(raw)[:24]


def evidence_slots(value: Any, path: str = '') -> Iterator[tuple[str, dict, str]]:
    """只遍历明确登记的引用列表，不把任意字符串解释成证据。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'evidence_anchors' or key == '_provenance':
                continue
            current = f'{path}.{key}' if path else key
            if key == 'evidence_refs' or key.endswith('_evidence_refs'):
                yield current, value, key
            else:
                yield from evidence_slots(item, current)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from evidence_slots(item, f'{path}.{index}')


def _anchor(chapter: int, start: int, end: int, text: str) -> dict[str, Any]:
    return CoreEvidenceAnchor(chapter_ordinal=chapter, start=start, end=end,
                              text_sha256=_sha(text)).model_dump(mode='json')


def compile_evidence_refs(value: Any, table: GlobalEvidenceCandidateTable
                          ) -> tuple[Any, dict[str, dict[str, Any]]]:
    result, anchors = copy.deepcopy(value), {}
    for path, owner, field in evidence_slots(result):
        refs = owner[field]
        if not isinstance(refs, list):
            raise ValueError(f'{path} 必须是证据候选列表')
        compiled = []
        for candidate in refs:
            candidate_id = (candidate.get('evidence_candidate_id')
                            if isinstance(candidate, dict) else candidate)
            row = table.lookup(candidate_id)
            anchor = _anchor(row.chapter_ordinal, row.start, row.end, row.text)
            ref = evidence_id(anchor)
            anchors[ref] = anchor
            if ref not in compiled:
                compiled.append(ref)
        owner[field] = compiled
    return result, dict(sorted(anchors.items()))


def _texts(main_text: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    texts = {}
    for row in main_text:
        ordinal, text = row.get('chapter_ordinal'), row.get('text')
        if type(ordinal) is not int or ordinal < 1 or ordinal in texts or not isinstance(text, str):
            raise ValueError('证据原文必须具有唯一合法章号与正文')
        texts[ordinal] = text
    return texts


def evidence_snippet(anchor: CoreEvidenceAnchor | Mapping[str, Any],
                     main_text: Sequence[Mapping[str, Any]]) -> str:
    row = (anchor if isinstance(anchor, CoreEvidenceAnchor)
           else CoreEvidenceAnchor.model_validate(anchor))
    source = _texts(main_text).get(row.chapter_ordinal)
    if source is None or row.end > len(source):
        raise ValueError('证据章或区间不在当前 MainText')
    text = source[row.start:row.end]
    if _sha(text) != row.text_sha256:
        raise ValueError('证据原文摘要漂移')
    return text


def validate_evidence_anchors(value: Any, anchors: Mapping[str, Any],
                              main_text: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    problems, used = {}, set()
    for path, owner, field in evidence_slots(value):
        refs = owner[field]
        if not isinstance(refs, list):
            problems[path] = '证据引用必须是列表'
            continue
        for ref in refs:
            if not isinstance(ref, str) or ref not in anchors:
                problems[path] = f'证据引用不存在：{ref!r}'
                continue
            used.add(ref)
            try:
                anchor = CoreEvidenceAnchor.model_validate(anchors[ref]).model_dump(mode='json')
                if evidence_id(anchor) != ref:
                    raise ValueError('证据 ID 不属于当前锚点')
                evidence_snippet(anchor, main_text)
            except ValueError as exc:
                problems[path] = str(exc)
    for ref in set(anchors) - used:
        problems[f'evidence_anchors.{ref}'] = '未被任何主体引用的证据锚点'
    return problems


def compile_manual_evidence_refs(value: Any, anchors: Mapping[str, Any],
                                 main_text: Sequence[Mapping[str, Any]]
                                 ) -> tuple[Any, dict[str, dict[str, Any]]]:
    result, available, used = copy.deepcopy(value), copy.deepcopy(dict(anchors)), {}
    texts = _texts(main_text)
    for path, owner, field in evidence_slots(result):
        if not isinstance(owner[field], list):
            raise ValueError(f'{path} 证据引用必须是列表')
        resolved = []
        for item in owner[field]:
            if isinstance(item, str):
                if item not in available:
                    raise ValueError(f'{path} 引用了当前产物没有的证据：{item}')
                anchor = CoreEvidenceAnchor.model_validate(available[item]).model_dump(mode='json')
                evidence_snippet(anchor, main_text)
                if evidence_id(anchor) != item:
                    raise ValueError(f'{path} 证据 ID 与锚点不一致')
                ref = item
            else:
                chapter, locator = item['chapter_ordinal'], item['locator']
                if chapter not in texts:
                    raise ValueError(f'{path} 的定位章不在 MainText')
                found = locate_unique_span(texts[chapter], locator)
                if not isinstance(found, tuple):
                    raise ValueError(f'{path} 人工定位没有章内唯一命中：{found}')
                start, end, _ = found
                anchor = _anchor(chapter, start, end, texts[chapter][start:end])
                ref = evidence_id(anchor)
            used[ref] = anchor
            if ref not in resolved:
                resolved.append(ref)
        owner[field] = resolved
    return result, dict(sorted(used.items()))
