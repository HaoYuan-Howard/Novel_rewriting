"""文案准备的唯一证据编译器：只由所给段号取回精确原文，不模糊搜索或猜坐标。"""
from __future__ import annotations

from contracts.schema.stage2 import SourceBlurbGrounding, FactEvidence, HookFactEvidence
from contracts.schema.stage6_prepare import PrepareDraft
from novel.stages.stage2.shared.paragraphs import span_of, paragraph_spans


def compile_draft(draft: PrepareDraft, main_text: list[dict], hook_text: str):
    raw = draft.model_dump(mode='json')
    texts = {row['chapter_ordinal']: row['text'] for row in main_text}
    issues = []
    def evidence(value, hook=False):
        text = hook_text if hook else texts.get(value['chapter_ordinal'])
        try:
            if text is None:
                raise ValueError('章不在 MainText 内')
            start, end = span_of(text, value['start_para'], value['end_para'])
            source = dict(text=text[start:end], start=start, end=end)
        except ValueError as exc:
            issues.append(str(exc))
            source = None
        return (dict(source=source, unresolved_locator=None if source else str(value)) if hook
                else dict(chapter_ordinal=value['chapter_ordinal'], source=source))
    for fact in raw['facts']:
        if fact['kind'] == 'supported':
            fact['evidence'] = evidence(fact['evidence'])
        elif fact['kind'] == 'hook_supported':
            fact['hook_evidence'] = evidence(fact['hook_evidence'], True)
    opening = raw['opening']
    if opening['kind'] == 'proposed':
        opening['boundary_evidence'] = evidence(opening['boundary_evidence'])
        if opening['through_chapter'] not in texts:
            issues.append('开篇结束章不在 MainText 内')
        if opening['boundary_evidence']['chapter_ordinal'] != opening['through_chapter']:
            issues.append('连续开篇范围的边界证据必须位于结束章')
    return SourceBlurbGrounding[FactEvidence, HookFactEvidence].model_validate(raw), issues


def validate_grounding(grounding, main_text: list[dict], hook_text: str) -> list[str]:
    texts = {row['chapter_ordinal']: row['text'] for row in main_text}
    issues = []
    def check(value, hook=False):
        text = hook_text if hook else texts.get(value.chapter_ordinal)
        source = value.source
        if (text is None or source is None or source.end > len(text)
                or text[source.start:source.end] != source.text):
            issues.append('支持证据缺失、越界或不等于原文切片')
    for fact in grounding.facts:
        if fact.kind == 'supported': check(fact.evidence)
        elif fact.kind == 'hook_supported': check(fact.hook_evidence, True)
    if grounding.opening.kind == 'proposed':
        opening = grounding.opening
        check(opening.boundary_evidence)
        if opening.through_chapter not in texts:
            issues.append('开篇结束章不在 MainText 内')
        if opening.boundary_evidence.chapter_ordinal != opening.through_chapter:
            issues.append('连续开篇范围的边界证据必须位于结束章')
    return issues


def draft_of(grounding, main_text: list[dict], hook_text: str) -> PrepareDraft:
    """精确反投影供免费编辑；坏证据保留为显式未决，不捏造有效候选。"""
    texts = {row['chapter_ordinal']: row['text'] for row in main_text}
    raw = grounding.model_dump(mode='json')
    def locate(value, hook=False):
        source = value['source']
        text = hook_text if hook else texts.get(value['chapter_ordinal'])
        if text is None or source is None: return None
        spans = paragraph_spans(text)
        starts = {start: index for index, (start, end) in enumerate(spans, 1)}
        ends = {end: index for index, (start, end) in enumerate(spans, 1)}
        if source['start'] not in starts or source['end'] not in ends: return None
        if text[source['start']:source['end']] != source['text']: return None
        return dict(start_para=starts[source['start']], end_para=ends[source['end']],
                    **({} if hook else dict(chapter_ordinal=value['chapter_ordinal'])))
    for index, fact in enumerate(raw['facts']):
        key = 'evidence' if fact['kind'] == 'supported' else 'hook_evidence'
        if fact['kind'] == 'unresolved': continue
        candidate = locate(fact[key], fact['kind'] == 'hook_supported')
        if candidate is None:
            raw['facts'][index] = dict(kind='unresolved', claim_id=fact['claim_id'], claim=fact['claim'],
                                      reason='原证据无法反投影，请依据当前材料明确订正支持来源')
        else: fact[key] = candidate
    opening = raw['opening']
    if opening['kind'] == 'proposed':
        candidate = locate(opening['boundary_evidence'])
        if candidate is None:
            raw['opening'] = dict(kind='unresolved', reason='原开篇边界证据无法定位，须免费订正')
        else: opening['boundary_evidence'] = candidate
    return PrepareDraft.model_validate(raw)
