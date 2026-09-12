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
from typing import Any, Callable, Final, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.schema.dependencies import DependencyManifest, ScopeSelection

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_KNOWLEDGE_LINKS,
    STAGE2_CHARACTERS,
    STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
    STAGE4_BLIND_CANDIDATE,
    STAGE4_BLIND_CHAPTER_EDIT, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_PLAN_EDIT,
    STAGE4_WRITER_CONTEXT, STAGE5_REVIEW, STAGE5_TRIM_AUDIT,
    STAGE6_HOOK, STAGE6_HOOK_EDIT,
    STAGE7_NAME_MAP_EDIT,
)
from contracts.invariants import NODES, STYLE_MEASUREMENT_CONTRACT, review_effect_of
from novel.core.evidence import without_global_evidence

Projection = Callable[[object], object]


def artifact_payload(data: object) -> object:
    """消费整份业务载荷，但不把上游自己的来源账当成业务输入。"""
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if key != '_provenance'}
    return data


def stage2_generation_review(data: object) -> object:
    """下游只消费会改变写作视图的 blocking 决定与隔离动作。

    actor、判词措辞或展示 evidence 改字不会改变模型能看到的事实；state、主体身份
    与 disposition 改变则必须让既有下游 stale。主体身份采用 kind + subject_path，
    不采用内含证据摘要的 item_id；完整审阅身份仍由 require_current 校验。
    """
    if not isinstance(data, dict):
        return None
    rows = []
    for item in data.get('items') or []:
        if not isinstance(item, dict):
            continue
        kind = item.get('kind')
        if not isinstance(kind, str) or review_effect_of(kind) != 'blocking':
            continue
        rows.append({
            'kind': kind,
            'subject_path': item.get('subject_path'),
            'state': item.get('state'),
            'generation_disposition': item.get('generation_disposition'),
        })
    # 审阅主体摘要含证据身份，不能变成付费材料的全对象依赖。业务内容由独立
    # Characters/Global/WriterContext 消费边绑定；此边只负责许可状态与隔离动作。
    return {'blocking_items': sorted(rows, key=lambda row: (
        row['kind'], str(row['subject_path'])))}


def stage2_main_text(data: object) -> object:
    """Stage2 只消费阅读序和正文；不继承 Stage1 的诊断展示字段。"""
    if not isinstance(data, list):
        return None
    return [
        (row.get('chapter_ordinal'), row.get('text'))
        for row in data if isinstance(row, dict)
    ]


def character_knowledge_reference(data: object) -> object:
    """知识请求只读的人物身份；请求、引用 SHA 和 freshness 共用同一投影。

    只传名称闭集与静态身份来解析原文人物，不发送称呼者、语境、生效时点或证据
    原文。那些字段的独立修改不得重新购买未消费它们的知识抽取。
    """
    if not isinstance(data, dict) or not isinstance(data.get('characters'), dict):
        return None
    rows = {}
    for cid, value in sorted(data['characters'].items()):
        if not isinstance(value, dict) or not isinstance(value.get('aliases'), list):
            return None
        surfaces = []
        for address in value['aliases']:
            if not isinstance(address, dict) or not isinstance(address.get('surface'), str):
                return None
            surfaces.append(address['surface'])
        rows[cid] = {'canonical': value.get('canonical'), 'role': value.get('role'),
                     'surfaces': sorted(set(surfaces))}
    return rows


# ── stage2.blueprint ─────────────────────────────────────────────────────────


def stage1_stage0_source(data: object) -> object:
    """Stage1 只继承原文身份；正文是独立的 clean_text 字节边。"""
    if not isinstance(data, dict):
        return None
    source = data.get('source')
    if not isinstance(source, dict):
        return None
    return source.get('normalized_source_sha1')


# ── stage4.plan / stage4.context ─────────────────────────────────────────────













def stage4_plan_blueprints(data: object) -> object:
    """B 的真实材料包括完整节点、状态、约束及证据；元数据不进入消费身份。"""
    if not isinstance(data, dict):
        return None
    return sorted(data.get('blueprints') or [], key=lambda row: row['chapter_ordinal'])


def stage4_design_blueprints(data: object) -> object:
    """规划消费完整剧情及稀疏语义，位置、原文证据和源数组顺序不属于 A 输入。"""
    from contracts.schema.plot import PlotBlueprint
    if not isinstance(data, dict):
        return None
    rows = []
    for raw in data.get('blueprints', []):
        chapter = PlotBlueprint.model_validate(raw)
        def semantic(values, key):
            result = []
            for value in values:
                row = value.model_dump(mode='json')
                row.pop('evidence_refs', None)
                row.pop('source_contexts', None)
                for field, item in row.items():
                    if isinstance(item, list) and field.endswith(('_refs', '_ids')):
                        row[field] = sorted(item)
                result.append(row)
            return sorted(result, key=lambda r: r[key])
        rows.append({
            'chapter_ordinal': chapter.chapter_ordinal,
            'chapter_function': chapter.chapter_function,
            'actor_bindings': semantic(chapter.actor_bindings, 'presentation_id'),
            'relation_uses': [r.model_dump(mode='json') for r in sorted(chapter.relation_uses, key=lambda r: r.key())],
            'beats': semantic(chapter.beats, 'beat_id'),
            'states': semantic(chapter.states, 'state_id'),
            'constraints': semantic(chapter.constraints, 'constraint_id'),
            'inherited_state_endings': semantic(chapter.inherited_state_endings, 'state_ref'),
            'entry_state_refs': sorted(chapter.entry_state_refs),
            'carry_out_refs': sorted(chapter.carry_out_refs),
        })
    return sorted(rows, key=lambda r: r['chapter_ordinal'])


def stage4_context_blueprints(data: object) -> object:
    """Context 编译同时校验剧情、证据、叙述和端点，不能漏掉新字段变化。"""
    return stage4_plan_blueprints(data)


def stage4_main_text(data: object) -> object:
    """篇幅、span 与截至使用章的已出现称谓候选只依赖章号和正文。"""
    if not isinstance(data, list):
        return None
    return sorted((m.get('chapter_ordinal'), m.get('text')) for m in data)


def stage4_design_main_text(data: object, *, characters: object = None) -> object:
    """A 私有解析只取原文中已出现的称谓候选；普通文字/坐标变化不重购设计。"""
    from novel.core.text import norm
    if not isinstance(data, list) or not isinstance(characters, dict):
        raise ValueError('盲规划称谓投影缺少原文或人物登记')
    people = characters.get('characters')
    if not isinstance(people, dict):
        raise ValueError('盲规划称谓投影缺少当前人物形状')
    names = {}
    for cid, person in people.items():
        aliases = person.get('aliases') if isinstance(person, dict) else None
        if (not isinstance(aliases, list) or any(not isinstance(row, dict)
                or not isinstance(row.get('surface'), str) or not row['surface'] for row in aliases)):
            raise ValueError('盲规划称谓投影含不可解释的登记称谓')
        names[cid] = sorted({row['surface'] for row in aliases})
    chapters = {}
    for row in data:
        if (not isinstance(row, dict) or type(row.get('chapter_ordinal')) is not int
                or row['chapter_ordinal'] < 1 or not isinstance(row.get('text'), str)
                or row['chapter_ordinal'] in chapters):
            raise ValueError('盲规划称谓投影原文章域不唯一或不可读')
        chapters[row['chapter_ordinal']] = row['text']
    cumulative, result = '', []
    for ordinal in sorted(chapters):
        cumulative += norm(chapters[ordinal])
        result.append([ordinal, {cid: [name for name in values if norm(name) in cumulative]
                                 for cid, values in sorted(names.items())}])
    return result


def _core_semantics(value: object) -> object:
    """证据及审计不属于生成材料；不使用退役的 entities/canon 兼容视图。"""
    if isinstance(value, list):
        return [_core_semantics(row) for row in value]
    if not isinstance(value, dict):
        return value
    return {key: _core_semantics(row) for key, row in value.items()
            if key not in {'_provenance', 'evidence_anchors', 'characters_reference_sha256'}
            and not key.endswith('evidence_refs')}


def stage4_grounding(data: object) -> object:
    if not isinstance(data, dict):
        return None
    return {key: _core_semantics(data[key])
            for key in ('setting', 'world_rules', 'relations', 'arc_secrets')}


# ── stage5.write / stage5.review ─────────────────────────────────────────────

def _chapter_texts(data: object) -> object:
    """当前写作/验收消费的章号与正文字段。"""
    if not isinstance(data, list):
        return None
    return [{'chapter_ordinal': s.get('chapter_ordinal'), 'text': s.get('text')}
            for s in data if isinstance(s, dict)]


def stage5_main_text(data: object) -> object:
    """正文与即时测量契约共同决定 Stage5 新鲜度。"""
    return {
        'measurement_contract': STYLE_MEASUREMENT_CONTRACT,
        'chapters': _chapter_texts(data),
    }


def stage5_grounding(data: object) -> object:
    """全量私有泄漏探针依赖；正文只从 WriterContext 下发。"""
    if not isinstance(data, dict):
        return None
    # fact_ref 的指向正文同样参与探针，不能仅绑定秘密指针。
    return stage4_grounding(data)


def stage5_review_grounding(data: object) -> object:
    return stage5_grounding(data)


def stage5_context(data: object) -> object:
    """写作只消费逐章材料；书级名字基线从 Stage2 + Stage1 现场派生。"""
    if not isinstance(data, dict):
        return None
    return {'chapters': data.get('chapters')}


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

def _hook_text(data: object) -> object:
    """当前文案生成消费的原作文案正文。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


def stage6_hook_copy(data: object) -> object:
    """原作文案与即时测量契约共同决定 Stage6 新鲜度。"""
    return {
        'measurement_contract': STYLE_MEASUREMENT_CONTRACT,
        'text': _hook_text(data),
    }


def stage6_grounding(data: object) -> object:
    """开篇文案消费完整 claim 真相面，但不消费原文定位证据。"""
    if not isinstance(data, dict):
        return None
    return without_global_evidence({
        'entities': data.get('entities'),
        'canon': data.get('canon'),
        'source_blurb': data.get('source_blurb'),
    })


def stage6_context(data: object) -> object:
    """文案真实消费的全书剧情地图；正文只在章级节点目录保存一份。

    同一投影直接进入 prompt，身份不能漏掉分场指令、状态性质或知识许可。
    Stage6 文案允许剧透，但仍须区分真相、相信与声称，不将它们压成事实句。
    """
    if not isinstance(data, dict):
        return None
    chapter_fields = (
        'from_source_ordinals', 'semantic_contract', 'chapter_rewrite_brief',
        'chapter_goal', 'open_hook_goal', 'end_hook_goal', 'beats', 'states',
        'constraints', 'knowledge_events',
    )
    scene_fields = (
        'slot', 'purpose', 'role', 'delivery', 'transition', 'deliveries',
        'turn_beat_ref', 'context_beat_refs', 'state_events',
        'knowledge_event_refs', 'constraint_refs',
    )
    return [
        {
            'chapter': chapter.get('output_ordinal'),
            **{key: chapter.get(key) for key in chapter_fields},
            'scenes': [
                {key: scene.get(key) for key in scene_fields}
                for scene in chapter.get('scenes') or [] if isinstance(scene, dict)
            ],
        }
        for chapter in data.get('chapters') or [] if isinstance(chapter, dict)
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
    """改名只消费人物表、关系与时代背景。

    ⚠ ``stage7.prompts.build_material`` 会把 ``role`` 与显示名、称呼一起交给改名模型；
    这里若漏掉它，人工订正 role 后旧 name map 仍会显示 fresh。消费投影必须照真实 prompt，
    不能凭“role 通常不影响取名”的直觉删字段。

    setting 只投影真实下发的 ``时代背景`` 正文；世界观与两份 evidence 没进 prompt，
    不得因为它们改字制造一次无消费者的 stale。
    """
    if not isinstance(data, dict):
        return None
    ent = data.get('entities') or {}
    chars = ent.get('characters') or {}
    return {
        'characters': sorted(
            (cid, (c or {}).get('canonical'),
             (c or {}).get('role'),
             tuple(sorted((a or {}).get('surface') or ''
                          for a in ((c or {}).get('address') or []))))
            for cid, c in chars.items()),
        'relations': sorted(
            ((r or {}).get('subject_id'), (r or {}).get('predicate'), (r or {}).get('object_id'))
            for r in (ent.get('relations') or [])),
        'setting': {
            '时代背景': (((data.get('canon') or {}).get('setting') or {}).get('时代背景')),
        },
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


def _character_view(data: object) -> dict[str, object] | None:
    """将独立登记表交给同一消费公式；不另造与实际材料不一致的字段清单。"""
    if not isinstance(data, dict):
        return None
    return {'entities': {'characters': data.get('characters')}}


def stage4_characters(data: object) -> object:
    """A 与 Context 只解析叙事身份；姓名辨识度由 Stage5 私有检测消费。"""
    projected = _core_semantics(data)
    if isinstance(projected, dict) and isinstance(projected.get('characters'), dict):
        for row in projected['characters'].values():
            if isinstance(row, dict):
                row.pop('name_distinctiveness', None)
    return projected


def stage4_plan_characters(data: object) -> object:
    """B 的审阅材料包含选中人物登记字段，仍须绑定姓名辨识度。"""
    return _core_semantics(data)


def stage5_characters(data: object) -> object:
    if not isinstance(data, dict):
        return None
    return {cid: {'canonical': row['canonical'],
                  'surfaces': sorted({a['surface'] for a in row['aliases']}),
                  'name_distinctiveness': row['name_distinctiveness']}
            for cid, row in sorted(data['characters'].items())}


def stage6_characters(data: object) -> object:
    projected = without_global_evidence(_character_view(data))
    return (projected.get('entities') or {}).get('characters') if isinstance(projected, dict) else None


def stage7_characters(data: object) -> object:
    projected = stage7_grounding(_character_view(data))
    return projected.get('characters') if isinstance(projected, dict) else None


# 每一格的键都是 ArtifactStore 写进 ``_provenance.inputs`` 的两级相对路径。
CONSUMED_PROJECTIONS: Final[dict[str, dict[str, Projection]]] = {
    'stage1.partition': {
        STAGE0_REVISIONS: stage1_stage0_source,
        STAGE0_CLEAN_TEXT: artifact_payload,
    },
    'stage2.analyze': {
        STAGE1_MAIN_TEXT: stage2_main_text,
        STAGE2_CHARACTERS: character_knowledge_reference,
    },
    'stage2.characters': {
        STAGE1_MAIN_TEXT: stage2_main_text,
    },
    'stage2.global': {
        STAGE1_MAIN_TEXT: stage2_main_text,
        STAGE2_CHARACTERS: character_knowledge_reference,
        # ``--apply-edit`` consumes the typed human decision set as a real
        # provenance edge.  Without this projection a just-written grounding
        # is ``consumed_undeclared`` and the formal-write postcondition must
        # (correctly) roll it back.
        STAGE2_GLOBAL_EDIT: artifact_payload,
    },
    'stage2.blueprint': {
        STAGE1_MAIN_TEXT: stage2_main_text,
        # 只由独立关联物化读取；E 抽取不读这两份，因此不会被知识规则返工失效。
        STAGE2_GLOBAL_GROUNDING: artifact_payload,
        STAGE2_CHAPTER_BLUEPRINTS: artifact_payload,
        # Blueprint 不再伪记一条 global 边：真正发给模型/validator/compiler 的
        # 是这份版本化、确定性物化的称名闭集。
        STAGE2_BLUEPRINT_CHARACTER_PROJECTION: artifact_payload,
    },
    'stage4.design': {
        STAGE1_MAIN_TEXT: stage4_design_main_text,
        STAGE2_CHAPTER_BLUEPRINTS: stage4_design_blueprints,
        STAGE2_CHARACTERS: stage4_characters,
        STAGE2_KNOWLEDGE_LINKS: artifact_payload,
        STAGE2_GLOBAL_GROUNDING: stage4_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_BLIND_CHAPTER_EDIT: artifact_payload,
    },
    'stage4.plan': {
        STAGE2_KNOWLEDGE_LINKS: artifact_payload,
        STAGE2_CHARACTERS: stage4_plan_characters,
        STAGE1_MAIN_TEXT: stage4_main_text,
        STAGE2_CHAPTER_BLUEPRINTS: stage4_plan_blueprints,
        STAGE2_GLOBAL_GROUNDING: stage4_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_BLIND_CANDIDATE: artifact_payload,
        # B 的正式 binding 是最终 v3 计划的直接编译输入；不登记这一边，producer
        # 虽然会如实把它写进 provenance，刚写完的 freshness 却只能报
        # ``consumed_undeclared`` 并回滚整份计划。
        STAGE4_EVIDENCE_BINDING: artifact_payload,
        STAGE4_PLAN_EDIT: artifact_payload,
    },
    'stage4.context': {
        STAGE2_KNOWLEDGE_LINKS: artifact_payload,
        STAGE2_CHARACTERS: stage4_characters,
        STAGE1_MAIN_TEXT: stage4_main_text,
        STAGE2_CHAPTER_BLUEPRINTS: stage4_context_blueprints,
        STAGE2_GLOBAL_GROUNDING: stage4_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_PLAN_CHAPTERS: artifact_payload,
    },
    'stage5.write': {
        STAGE2_CHARACTERS: stage5_characters,
        STAGE1_MAIN_TEXT: stage5_main_text,
        STAGE2_GLOBAL_GROUNDING: stage5_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_WRITER_CONTEXT: stage5_context,
    },
    'stage5.review': {
        STAGE2_CHARACTERS: stage5_characters,
        STAGE1_MAIN_TEXT: stage5_main_text,
        STAGE2_GLOBAL_GROUNDING: stage5_review_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_WRITER_CONTEXT: stage5_review_context,
        # retain_pretrim 的人工裁决绑定完整 audit bytes；任一审计事实变化都应过期。
        STAGE5_TRIM_AUDIT: artifact_payload,
    },
    'stage6.hook': {
        STAGE2_CHARACTERS: stage6_characters,
        STAGE1_HOOK_COPY: stage6_hook_copy,
        STAGE2_GLOBAL_GROUNDING: stage6_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_WRITER_CONTEXT: stage6_context,
        STAGE6_HOOK_EDIT: artifact_payload,
    },
    'stage6.semantic': {
        STAGE2_CHARACTERS: stage6_characters,
        STAGE1_HOOK_COPY: stage6_hook_copy,
        STAGE2_GLOBAL_GROUNDING: stage6_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_WRITER_CONTEXT: stage6_context,
        STAGE6_HOOK: stage6_semantic_hook,
    },
    'stage7.rename': {
        STAGE2_CHARACTERS: stage7_characters,
        STAGE1_MAIN_TEXT: stage7_main_text,
        STAGE2_GLOBAL_GROUNDING: stage7_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
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


#: 少数 consumed 投影的值由两份已登记上游共同决定。这只登记
#: 「额外需要读哪个 current 载体」；语义仍在对应的纯投影函数里。
#: 若 store 两侧不共用本表，一侧就会给两份书级输入各自算一份
#: 看似合理、实际无法回放的 fingerprint。
_CONTEXT_INPUTS: Final[dict[tuple[str, str], tuple[str, ...]]] = {
    ('stage4.design', STAGE1_MAIN_TEXT): (STAGE2_CHARACTERS,),
}


def context_inputs_for(node: str, rel: str) -> tuple[str, ...]:
    """这条投影还需哪些 current 载体；store 记账/复算共用。"""
    return _CONTEXT_INPUTS.get((node, canonical_rel(rel)), ())


def project_consumed(
    node: str,
    rel: str,
    data: object,
    *,
    related: Mapping[str, object] | None = None,
) -> tuple[bool, object]:
    """执行节点对一份上游的唯一 consumed 投影。

    返回 ``(resolved, value)``。``resolved=False`` 同时覆盖未登记投影和
    contextual 载体缺失/坏掉；两者在 provenance 中都必须写
    ``consumed=null``，不能对 ``None`` 本身取指纹后伪装成可回放的结论。
    """
    canonical = canonical_rel(rel)
    project = projection_for(node, canonical)
    if project is None:
        return False, None
    if node == 'stage4.design' and canonical == STAGE1_MAIN_TEXT:
        try:
            return True, stage4_design_main_text(data, characters=(related or {}).get(STAGE2_CHARACTERS))
        except (KeyError, TypeError, ValueError):
            return False, None
    return True, project(data)


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
    'CONSUMED_PROJECTIONS', 'Projection',
    'artifact_payload', 'canonical_rel', 'context_inputs_for',
    'producer_node', 'project_consumed',
    'projection_for',
]

# 范围工作包和范围接受复用共享本模块，不能另算一份 ID-only freshness。
def consumed_digest(value: object) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()

def plot_global_projection(data: object, chapter_ordinal: int) -> list[dict[str, Any]]:
    """plot-global-scope-v2：已有证据并到达披露章的命题，包含已结束的历史事实。"""
    if not isinstance(data, dict):
        raise ValueError('Global 投影输入不可读')
    anchors = data['evidence_anchors']
    result = []

    def evidence(refs):
        if any(ref not in anchors for ref in refs):
            raise ValueError('共享命题引用的证据不存在')
        return {ref: anchors[ref] for ref in refs}

    def add(kind, ref, value, refs):
        proof = evidence(refs)
        if proof:
            result.append({'kind': kind, 'ref': ref, 'value': _core_semantics(value),
                           'evidence_sha256': consumed_digest(proof)})

    for row in data['relations']:
        if row['revealed_from'] <= chapter_ordinal:
            add('relation', row['relation_id'], row, row['evidence_refs'])
    setting = data['setting']
    for field in ('era', 'social_context'):
        refs = setting[field + '_evidence_refs']
        if setting[field] is not None and any(anchors[r]['chapter_ordinal'] <= chapter_ordinal for r in refs):
            add('setting', field, {'content': setting[field]}, refs)
    for row in data['world_rules']:
        for version in row['versions']:
            if version['revealed_from'] <= chapter_ordinal:
                add('world_rule', row['rule_id'] + '/' + version['version_id'],
                    {'kind': row['kind'], **version}, version['evidence_refs'])
    for row in data['arc_secrets']:
        if row['revealed_from'] <= chapter_ordinal:
            refs = list(row['evidence_refs'])
            target = row['fact_ref']
            if target is not None:
                if target['kind'] == 'relation':
                    matches = [r for r in data['relations'] if r['relation_id'] == target['relation_id']]
                else:
                    matches = [v for r in data['world_rules'] if r['rule_id'] == target['rule_id']
                               for v in r['versions'] if v['version_id'] == target['version_id']]
                if len(matches) != 1:
                    raise ValueError('秘密引用的证据来源不唯一或缺失')
                refs.extend(matches[0]['evidence_refs'])
            add('secret', row['secret_id'], row, refs)
    return sorted(result, key=lambda row: (row['kind'], row['ref']))


def select_scope_input(selection: 'ScopeSelection', raw: object) -> object:
    if selection.projection == 'plot_characters':
        from novel.stages.stage2.plot_review_dependencies import character_subjects
        return character_subjects(raw, selection.selected_ids)
    """只输出本范围实际消费的字段；非法、缺失、重复成员都拒绝编译。"""
    import copy
    if selection.projection == 'plot_subjects':
        requested = set(selection.selected_ids or ())
        result = []
        for chapter in raw['blueprints']:
            anchors = {row['evidence_id']: row for row in chapter['evidence_anchors']}
            for kind, field, rows in (('beat', 'beat_id', chapter['beats']),
                                       ('state', 'state_id', chapter['states'])):
                for row in rows:
                    if f'{kind}:{row[field]}' in requested:
                        result.append({'chapter_ordinal': chapter['chapter_ordinal'],
                            'subject_ref': {'kind': kind, 'ref': row[field]}, 'value': copy.deepcopy(row),
                            'evidence': [copy.deepcopy(anchors[ref]) for ref in row['evidence_refs']]})
        if {f"{row['subject_ref']['kind']}:{row['subject_ref']['ref']}" for row in result} != requested:
            raise ValueError('必要剧情知识主体缺失')
        return sorted(result, key=lambda row: (row['subject_ref']['kind'], row['subject_ref']['ref']))
    if selection.projection == 'plot_global_review':
        from novel.stages.stage2.plot_review_dependencies import global_subjects
        return global_subjects(raw, selection.selected_ids)
    if selection.projection == 'source_ranges':
        from novel.stages.stage2.paragraphs import span_of
        rows = [row for row in raw if row['chapter_ordinal'] == selection.chapter_ordinal]
        if len(rows) != 1:
            raise ValueError('证据原文章缺失或重复')
        source = rows[0]['text']
        ranges = []
        for ref in selection.selected_ids:
            _, start, end = ref.split(':')
            start, end = int(start), int(end)
            left, right = span_of(source, start, end)
            ranges.append({'evidence_ref': ref, 'start_para': start,
                           'end_para': end, 'text': source[left:right]})
        return {'chapter_ordinal': selection.chapter_ordinal, 'source_ranges': ranges}
    if selection.projection == 'plot_global':
        return plot_global_projection(raw, selection.chapter_ordinal)
    if selection.projection == 'plot_inherited_states':
        if not isinstance(raw, dict) or not isinstance(raw.get('blueprints'), list):
            raise ValueError('持续状态输入不含蓝图集合')
        chapters = raw['blueprints']
        current = [r for r in chapters if r.get('chapter_ordinal') == selection.chapter_ordinal]
        if len(current) != 1:
            raise ValueError('持续状态消费章缺失或重复')
        requested = set(current[0]['entry_state_refs'])
        local = {r['state_id'] for r in current[0]['states']}
        requested -= local
        from novel.stages.stage2.plot_compile import state_materials
        from contracts.schema.plot import PlotBlueprint
        materials = state_materials([PlotBlueprint.model_validate(row) for row in chapters], sorted(requested))
        result = []
        for material in materials:
            result.append({'chapter_ordinal': material['chapter_ordinal'], 'state': material['state'],
                           'evidence': [{k: v for k, v in anchor.items() if k not in ('start', 'end')}
                                        for anchor in material['evidence_anchors']],
                           'actor_bindings': material['actor_bindings']})
        if {r['state']['state_id'] for r in result} != requested:
            raise ValueError('跨章持续状态缺少来源主体')
        return copy.deepcopy(sorted(result, key=lambda r: r['state']['state_id']))
    if selection.projection in ('plot_chapter', 'source_chapter'):
        rows = (raw.get('blueprints') if isinstance(raw, dict) else None
                ) if selection.projection == 'plot_chapter' else raw
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError('范围输入不含合法章节集合')
        matches = [r for r in rows if r.get('chapter_ordinal') == selection.chapter_ordinal]
        if len(matches) != 1:
            raise ValueError('消费范围必须存在唯一章节')
        row = matches[0]
        if selection.projection == 'source_chapter':
            if not isinstance(row.get('text'), str):
                raise ValueError('源章正文缺失')
            return {'chapter_ordinal': selection.chapter_ordinal, 'text': row['text']}
        return copy.deepcopy(row)
    current = raw
    for field in selection.path:
        if not isinstance(current, dict) or field not in current:
            raise ValueError('消费集合路径缺失')
        current = current[field]
    if not isinstance(current, list) or any(not isinstance(r, dict) for r in current):
        raise ValueError('消费集合不是对象列表')
    keyed = {}
    for row in current:
        key = row.get(selection.key_field)
        if not isinstance(key, str) or not key or key in keyed:
            raise ValueError('消费集合的主体标识缺失或重复')
        keyed[key] = row
    selected = (set(keyed) if selection.selected_ids is None
                else set(selection.selected_ids))
    if not selected <= keyed.keys():
        raise ValueError('已登记的消费成员被删除')
    # 全集合选择包含成员目录，新增和删除都会改变摘要。显式子集不扩大无关失效。
    return [copy.deepcopy(keyed[key]) for key in sorted(selected)]


def compile_scope_inputs(
    inputs: Mapping[str, object], selections: tuple['ScopeSelection', ...], *,
    scope: str, contract: str, depends_on_scopes: tuple[str, ...] = (),
) -> tuple[list[object], 'DependencyManifest']:
    from contracts.schema.dependencies import DependencyManifest, ScopeDependency
    materials = []
    dependencies = []
    for selection in selections:
        if selection.input_rel not in inputs:
            raise ValueError(f'实际消费输入缺失：{selection.input_rel}')
        material = select_scope_input(selection, inputs[selection.input_rel])
        materials.append(material)
        dependencies.append(ScopeDependency(
            selection=selection, consumed_sha256=consumed_digest(material)))
    manifest = DependencyManifest(
        contract=contract, scope=scope, inputs=tuple(dependencies),
        depends_on_scopes=depends_on_scopes)
    return materials, manifest


def scope_is_current(manifest: 'DependencyManifest', inputs: Mapping[str, object],
                     *, contract: str) -> bool:
    if manifest.contract != contract:
        return False
    try:
        _, current = compile_scope_inputs(
            inputs, tuple(row.selection for row in manifest.inputs),
            scope=manifest.scope, contract=contract,
            depends_on_scopes=manifest.depends_on_scopes)
    except (KeyError, TypeError, ValueError):
        return False
    return current == manifest


def invalidated_scopes(
    manifests: tuple['DependencyManifest', ...], inputs: Mapping[str, object], *,
    contracts: Mapping[str, str],
) -> frozenset[str]:
    """直接投影变化后沿实际消费者传播；失效不修改预算，也不创造新接受。"""
    indexed = {row.scope: row for row in manifests}
    if len(indexed) != len(manifests):
        raise ValueError('当前范围依赖清单重复')
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(scope: str) -> None:
        if scope in visiting:
            raise ValueError('接受依赖出现环')
        if scope in visited:
            return
        visiting.add(scope)
        for parent in indexed[scope].depends_on_scopes:
            if parent in indexed:
                visit(parent)
        visiting.remove(scope)
        visited.add(scope)

    for scope in indexed:
        visit(scope)
    invalid = {
        row.scope for row in manifests
        if row.scope not in contracts
        or not scope_is_current(row, inputs, contract=contracts[row.scope])
        or any(parent not in indexed for parent in row.depends_on_scopes)}
    while True:
        affected = {row.scope for row in manifests
                    if invalid.intersection(row.depends_on_scopes)}
        if affected <= invalid:
            return frozenset(invalid)
        invalid.update(affected)
