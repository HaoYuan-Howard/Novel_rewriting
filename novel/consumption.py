#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点级 ``consumed`` 投影注册表。

同一条投影同时服务两件事：

* :class:`novel.store.ArtifactStore` 读取上游时，把本节点真正消费的字段写进来源账；
* freshness 查询时，拿同一个函数对盘上当前上游重算指纹。

过去前一半由各 stage 在读取调用点零散声明，后一半没有任何
注册入口，因此一份刚产出的制品也会显示 ``consumed_unresolved``。把函数按
``(node, input-rel)`` 注册后，两边不再有机会各算一份。

投影只吃未经模型校验的 JSON 值（或字节制品解码后的字符串）。一份陈旧上游可能已经
不合当前 schema，而 freshness 恰恰必须在这种情况下仍能回答。
"""
from __future__ import annotations

import re
from typing import Callable, Final

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REMOVAL_REPORT, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_GROUNDING,
    STAGE3_STYLE_EDIT,
    STAGE3_STYLE_GROUNDING, STAGE4_PLAN_CHAPTERS, STAGE4_PLAN_EDIT,
    STAGE4_WRITER_CONTEXT, STAGE5_REVIEW, STAGE5_TRIM_AUDIT,
    STAGE6_HOOK, STAGE6_HOOK_EDIT,
    STAGE7_NAME_MAP_EDIT,
)
from contracts.invariants import NODES

Projection = Callable[[object], object]


def artifact_payload(data: object) -> object:
    """消费整份业务载荷，但不把上游自己的来源账当成业务输入。"""
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if key != '_provenance'}
    return data


# ── stage3.style ─────────────────────────────────────────────────────────────

def stage3_main_text(data: object) -> object:
    """正文只消费 ``text``；原文标签变化不影响文风测量。"""
    return [s.get('text') for s in data] if isinstance(data, list) else None


def stage3_hook_copy(data: object) -> object:
    """原作文案段只消费正文。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


def stage3_grounding(data: object) -> object:
    """文风判据只消费名表所需字段与秘密揭示时间线。"""
    if not isinstance(data, dict):
        return None
    chars = ((data.get('entities') or {}).get('characters') or {})
    return {
        'characters': sorted(
            (cid, c.get('canonical'), c.get('name_distinctiveness'),
             tuple(sorted((a or {}).get('surface') or '' for a in (c.get('address') or []))))
            for cid, c in chars.items() if isinstance(c, dict)),
        'secrets': sorted(
            (s.get('secret_id'), s.get('fact'),
             tuple(sorted((e or {}).get('chapter') or 0
                          for e in (s.get('reader_reveal_events') or []))))
            for s in ((data.get('canon') or {}).get('secrets') or [])
            if isinstance(s, dict)),
    }


# ── stage4.plan / stage4.context ─────────────────────────────────────────────

def _stage2_source(source: object) -> object:
    if not isinstance(source, dict):
        return None
    return (source.get('text'), source.get('start'), source.get('end'),
            source.get('normalized'), source.get('recovery'))


def _stage2_hinge(hinge: object) -> object:
    if not isinstance(hinge, dict):
        return None
    source = hinge.get('source')
    text = source.get('text') if isinstance(source, dict) else None
    return hinge.get('kind'), text


def _stage4_beats(blueprint: dict) -> tuple:
    return tuple(
        (be.get('beat_id'), be.get('event'), be.get('time_layer'),
         be.get('place'), tuple(be.get('present_char_ids') or []),
         _stage2_source(be.get('span')),
         tuple(_stage2_hinge(h) for h in (be.get('hinges') or [])))
        for be in (blueprint.get('beats') or []) if isinstance(be, dict)
    )


def stage4_plan_blueprints(data: object) -> object:
    """规划消费章摘要与拍；叙述人称只在写作上下文组装时消费。"""
    if not isinstance(data, dict):
        return None
    return sorted(
        (b.get('chapter_ordinal'), b.get('summary'), _stage4_beats(b))
        for b in (data.get('blueprints') or []) if isinstance(b, dict))


def stage4_context_blueprints(data: object) -> object:
    """写作上下文消费叙述信息与拍；原章摘要明确不下发。"""
    if not isinstance(data, dict):
        return None
    return sorted(
        (b.get('chapter_ordinal'),
         tuple((n.get('grammatical_person'), n.get('narrator_char_id'),
                n.get('focalizer_char_id'), tuple(n.get('pronoun_surfaces') or []))
               for n in (b.get('narration') or []) if isinstance(n, dict)),
         _stage4_beats(b))
        for b in (data.get('blueprints') or []) if isinstance(b, dict))


def stage4_main_text(data: object) -> object:
    """计划的篇幅基准与 span 定位只依赖章号和正文。"""
    if not isinstance(data, list):
        return None
    return sorted((m.get('chapter_ordinal'), m.get('text')) for m in data)


def stage4_grounding(data: object) -> object:
    """计划/上下文组装消费的人物、关系、秘密、设定、规则与弧线字段。"""
    if not isinstance(data, dict):
        return None
    ent = data.get('entities') or {}
    canon = data.get('canon') or {}
    return {
        'characters': sorted(
            (cid, (c or {}).get('canonical'), (c or {}).get('role'),
             (c or {}).get('name_distinctiveness'),
             tuple(sorted(((a or {}).get('surface'), (a or {}).get('from_chapter'),
                           (a or {}).get('by'), (a or {}).get('context'))
                          for a in ((c or {}).get('address') or []))))
            for cid, c in (ent.get('characters') or {}).items()),
        'relations': sorted(
            ((r or {}).get('relation_id'), (r or {}).get('subject_id'),
             (r or {}).get('predicate'), (r or {}).get('object_id'),
             (r or {}).get('valid_from_chapter'), (r or {}).get('valid_to_chapter'))
            for r in (ent.get('relations') or [])),
        'secrets': sorted(
            ((s or {}).get('secret_id'), (s or {}).get('fact'),
             tuple(sorted(map(str, (s or {}).get('character_known_by') or []))),
             tuple(sorted(map(str, (s or {}).get('setup_chapters') or []))),
             tuple(sorted(str(e.get('chapter'))
                          for e in ((s or {}).get('reader_reveal_events') or []))))
            for s in (canon.get('secrets') or [])),
        'setting': sorted((canon.get('setting') or {}).items()),
        'world_rules': sorted(
            ((r or {}).get('rule_id'), (r or {}).get('kind'),
             tuple(sorted((str(v.get('effective_from_chapter')), v.get('desc'))
                          for v in ((r or {}).get('versions') or []))))
            for r in (canon.get('world_rules') or [])),
        'arc': sorted(((data.get('arc') or {}).get('conflict_stages') or []),
                      key=lambda st: str(st.get('stage'))),
    }


# ── stage5.write / stage5.review ─────────────────────────────────────────────

def stage5_main_text(data: object) -> object:
    """写作与验收按章消费原文正文，不消费源章显示标签。"""
    if not isinstance(data, list):
        return None
    return [{'chapter_ordinal': s.get('chapter_ordinal'), 'text': s.get('text')}
            for s in data if isinstance(s, dict)]


def stage5_grounding(data: object) -> object:
    """Stage 5 只把全量 secrets 作为泄漏检测的 ground truth。"""
    if not isinstance(data, dict):
        return None
    return {'secrets': (data.get('canon') or {}).get('secrets')}


def stage5_style(data: object) -> object:
    """写作与验收真正读取的文风字段。"""
    if not isinstance(data, dict):
        return None
    return {
        'voice': data.get('voice'),
        'surface_habits': data.get('surface_habits'),
        'chapter_styles': data.get('chapter_styles'),
        'measurable': data.get('measurable'),
    }


def stage5_context(data: object) -> object:
    """写作消费逐章材料和书级人物表，不消费 context 自己的 provenance。"""
    if not isinstance(data, dict):
        return None
    return {
        'chapters': data.get('chapters'),
        'characters': ((data.get('world_facts') or {}).get('characters')),
    }


def stage5_review_context(data: object) -> object:
    """验收还把 context 的代码版本写进 ``context_source.code``。"""
    projected = stage5_context(data)
    if not isinstance(projected, dict) or not isinstance(data, dict):
        return None
    return {
        **projected,
        'code': ((data.get('_provenance') or {}).get('code')),
    }


# ── stage6.hook ───────────────────────────────────────────────────────────────

def stage6_hook_copy(data: object) -> object:
    """原作文案同时是生成的结构金标准与非抄写判据基准。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


def stage6_grounding(data: object) -> object:
    """开篇文案与质量审判消费完整故事真相面。"""
    if not isinstance(data, dict):
        return None
    return {
        'entities': data.get('entities'),
        'canon': data.get('canon'),
        'arc': data.get('arc'),
    }


def stage6_style(data: object) -> object:
    """文案消费正文声音、surface habits、逐章风格与全部可量风格面。"""
    if not isinstance(data, dict):
        return None
    return {
        'voice': data.get('voice'),
        'surface_habits': data.get('surface_habits'),
        'chapter_styles': data.get('chapter_styles'),
        'hook_style': data.get('hook_style'),
        'measurable': data.get('measurable'),
    }


def stage6_context(data: object) -> object:
    """文案消费全书故事地图，并用开篇源章号选择对应 chapter_styles。"""
    if not isinstance(data, dict):
        return None

    def public_goal(value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {key: value.get(key) for key in ('intent', 'fact_refs')}

    return [
        {
            'output_ordinal': chapter.get('output_ordinal'),
            'from_source_ordinals': chapter.get('from_source_ordinals'),
            'chapter_goal': public_goal(chapter.get('chapter_goal')),
            'open_hook_goal': public_goal(chapter.get('open_hook_goal')),
            'segments': [
                {
                    'slot': segment.get('slot'),
                    'events': list(segment.get('events') or []),
                }
                for segment in (chapter.get('segments') or [])
                if isinstance(segment, dict)
            ],
            'end_hook_goal': public_goal(chapter.get('end_hook_goal')),
        }
        for chapter in (data.get('chapters') or []) if isinstance(chapter, dict)
    ]


def stage6_semantic_hook(data: object) -> object:
    """语义审判只消费文案的**文本字节集合**（去重）。

    `--pick` 定稿只是把某条候选拷进 `approved`，不换任何文本 ⇒ 集合不变，
    语义结论不过期；`--apply-edit` 改了字 ⇒ 集合变，正确触发过期。
    与 `HookSemanticReview.judged_texts` 的 sha1 绑定是同一条口径的两个落点。
    """
    if not isinstance(data, dict):
        return None
    texts = [c.get('text') for c in (data.get('candidates') or []) if isinstance(c, dict)]
    approved = data.get('approved')
    if isinstance(approved, dict):
        texts.append(approved.get('text'))
    return sorted({t for t in texts if isinstance(t, str)})


# ── stage7.rename ─────────────────────────────────────────────────────────────

def stage7_grounding(data: object) -> object:
    """改名只消费人物表、关系与时代背景。"""
    if not isinstance(data, dict):
        return None
    ent = data.get('entities') or {}
    chars = ent.get('characters') or {}
    return {
        'characters': sorted(
            (cid, (c or {}).get('canonical'),
             tuple(sorted((a or {}).get('surface') or ''
                          for a in ((c or {}).get('address') or []))))
            for cid, c in chars.items()),
        'relations': sorted(
            ((r or {}).get('subject_id'), (r or {}).get('predicate'), (r or {}).get('object_id'))
            for r in (ent.get('relations') or [])),
        'setting': sorted(((data.get('canon') or {}).get('setting') or {}).items()),
    }


def stage7_main_text(data: object) -> object:
    """改名的姓氏据与噪声基线只依赖原文正文。"""
    if not isinstance(data, list):
        return None
    return [(c or {}).get('text') for c in data if isinstance(c, dict)]


def stage7_plan(data: object) -> object:
    """改名从计划只消费输出章号，作为缺章判定分母。"""
    if not isinstance(data, dict):
        return None
    return sorted((c or {}).get('output_ordinal')
                  for c in (data.get('output_chapters') or []))


def stage7_context(data: object) -> object:
    """改名只审计写手上下文的代码版本。"""
    if not isinstance(data, dict):
        return None
    return ((data.get('_provenance') or {}).get('code'))


def stage7_hook(data: object) -> object:
    """改名只消费人工定稿的开篇文案正文。"""
    if not isinstance(data, dict):
        return None
    return ((data.get('approved') or {}).get('text') or '')


# 每一格的键都是 ArtifactStore 写进 ``_provenance.inputs`` 的两级相对路径。
CONSUMED_PROJECTIONS: Final[dict[str, dict[str, Projection]]] = {
    'stage1.partition': {
        STAGE0_REMOVAL_REPORT: artifact_payload,
        STAGE0_CLEAN_TEXT: artifact_payload,
    },
    'stage2.global': {
        STAGE1_MAIN_TEXT: artifact_payload,
        # ``--apply-edit`` consumes the typed human decision set as a real
        # provenance edge.  Without this projection a just-written grounding
        # is ``consumed_undeclared`` and the formal-write postcondition must
        # (correctly) roll it back.
        STAGE2_GLOBAL_EDIT: artifact_payload,
    },
    'stage2.blueprint': {
        STAGE1_MAIN_TEXT: artifact_payload,
        STAGE2_GLOBAL_GROUNDING: artifact_payload,
    },
    'stage3.style': {
        STAGE1_MAIN_TEXT: stage3_main_text,
        STAGE1_HOOK_COPY: stage3_hook_copy,
        STAGE2_GLOBAL_GROUNDING: stage3_grounding,
        STAGE3_STYLE_EDIT: artifact_payload,
    },
    'stage4.plan': {
        STAGE1_MAIN_TEXT: stage4_main_text,
        STAGE2_CHAPTER_BLUEPRINTS: stage4_plan_blueprints,
        STAGE2_GLOBAL_GROUNDING: stage4_grounding,
        STAGE4_PLAN_EDIT: artifact_payload,
    },
    'stage4.context': {
        STAGE1_MAIN_TEXT: stage4_main_text,
        STAGE2_CHAPTER_BLUEPRINTS: stage4_context_blueprints,
        STAGE2_GLOBAL_GROUNDING: stage4_grounding,
        STAGE4_PLAN_CHAPTERS: artifact_payload,
    },
    'stage5.write': {
        STAGE1_MAIN_TEXT: stage5_main_text,
        STAGE2_GLOBAL_GROUNDING: stage5_grounding,
        STAGE3_STYLE_GROUNDING: stage5_style,
        STAGE4_WRITER_CONTEXT: stage5_context,
    },
    'stage5.review': {
        STAGE1_MAIN_TEXT: stage5_main_text,
        STAGE2_GLOBAL_GROUNDING: stage5_grounding,
        STAGE3_STYLE_GROUNDING: stage5_style,
        STAGE4_WRITER_CONTEXT: stage5_review_context,
        # retain_pretrim 的人工裁决绑定完整 audit bytes；任一审计事实变化都应过期。
        STAGE5_TRIM_AUDIT: artifact_payload,
    },
    'stage5.semantic': {
        STAGE1_MAIN_TEXT: stage5_main_text,
        STAGE2_GLOBAL_GROUNDING: stage5_grounding,
        STAGE3_STYLE_GROUNDING: stage5_style,
        STAGE4_WRITER_CONTEXT: stage5_review_context,
        STAGE5_REVIEW: artifact_payload,
    },
    'stage6.hook': {
        STAGE1_HOOK_COPY: stage6_hook_copy,
        STAGE2_GLOBAL_GROUNDING: stage6_grounding,
        STAGE3_STYLE_GROUNDING: stage6_style,
        STAGE4_WRITER_CONTEXT: stage6_context,
        STAGE6_HOOK_EDIT: artifact_payload,
    },
    'stage6.semantic': {
        STAGE1_HOOK_COPY: stage6_hook_copy,
        STAGE2_GLOBAL_GROUNDING: stage6_grounding,
        STAGE3_STYLE_GROUNDING: stage6_style,
        STAGE4_WRITER_CONTEXT: stage6_context,
        STAGE6_HOOK: stage6_semantic_hook,
    },
    'stage7.rename': {
        STAGE1_MAIN_TEXT: stage7_main_text,
        STAGE2_GLOBAL_GROUNDING: stage7_grounding,
        STAGE4_PLAN_CHAPTERS: stage7_plan,
        STAGE4_WRITER_CONTEXT: stage7_context,
        STAGE6_HOOK: stage7_hook,
        STAGE7_NAME_MAP_EDIT: artifact_payload,
    },
}


def canonical_rel(rel: str) -> str:
    """``x.INVALID.json`` 与正式孪生共用同一条消费契约。"""
    norm = str(rel).replace('\\', '/')
    return norm.replace('.INVALID.', '.', 1)


def projection_for(node: str, rel: str) -> Projection | None:
    """节点对某份上游的唯一投影；未登记返回 ``None``（诚实地 unknown）。"""
    rel = canonical_rel(rel)
    table = CONSUMED_PROJECTIONS.get(node, {})
    direct = table.get(rel)
    if direct is not None:
        return direct
    return next((project for template, project in table.items() if _matches(template, rel)), None)


def _matches(template: str, rel: str) -> bool:
    pattern = re.escape(template)
    pattern = re.sub(r'\\\{[^}]+\\\}', r'[^/]+', pattern)
    return re.fullmatch(pattern, rel) is not None


def producer_node(rel: str, *, fallback: str | None = None) -> str | None:
    """制品路径 → 生产它的节点。

    同一份制品可由人工批准节点覆盖；声明顺序把原始生产节点放在前面，而它持有真正的
    consumed 契约。找不到（如动态历史轮次）时使用调用方的运行节点。
    """
    rel = canonical_rel(rel)
    for node, spec in NODES.items():
        if any(_matches(template, rel) for template in spec.produces):
            return node
    return fallback


__all__ = [
    'CONSUMED_PROJECTIONS', 'Projection', 'artifact_payload', 'canonical_rel',
    'producer_node', 'projection_for',
]
