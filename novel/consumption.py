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
    与 disposition 改变则必须让既有下游 stale。披露项本来就不进写作视图。
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
            'item_id': item.get('item_id'),
            'subject_sha256': item.get('subject_sha256'),
            'state': item.get('state'),
            'generation_disposition': item.get('generation_disposition'),
        })
    # 全 Global 的 source hash 只负责审阅当前性；把它带入消费投影会使未被
    # 本节点消费的 disclosure/fate 改动也触发整链重做。主体 hash 已绑定每项
    # blocking 知识，状态与隔离决定则绑定实际生成视图。
    return {'blocking_items': rows}


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
    """计划的篇幅基准与 span 定位只依赖章号和正文。"""
    if not isinstance(data, list):
        return None
    return sorted((m.get('chapter_ordinal'), m.get('text')) for m in data)


def _secret_setup_key(setup: object) -> object:
    """把 `Secret.setup` 判别联合压成确定性可比较的键。

    ⚠ **不能只取 `chapters`**：`{"kind":"cold"}` 与
    `{"kind":"foreshadowed","chapters":[]}`（理论上载体拒收，但投影吃的是未校验的
    JSON）在章号集合上同形，而前者说「没有隐瞒窗口」、后者是一份坏数据。
    投影分不开它们，改一边就不会让下游 stale。
    """
    if not isinstance(setup, dict):
        return ('?',)
    kind = str(setup.get('kind') or '?')
    return (kind, tuple(sorted(map(str, setup.get('chapters') or []))))


def _relation_visibility_key(visibility: object) -> object:
    """只收 Stage4 真正读取的显式可见性语义。"""
    if not isinstance(visibility, dict):
        return None
    return (visibility.get('kind'), visibility.get('from_chapter'),
            visibility.get('secret_id'))


def _knowledge_subject_key(subject: object) -> object:
    """typed CharacterRef / GroupRef 的确定性 freshness 键。"""
    if not isinstance(subject, dict):
        return None
    return (subject.get('kind'), subject.get('char_id'), subject.get('label'))


def _knowledge_events_key(events: object) -> tuple:
    if not isinstance(events, list):
        return ()
    rows = [
        (_knowledge_subject_key(event.get('subject')),
         event.get('known_from_chapter'))
        for event in events if isinstance(event, dict)
    ]
    return tuple(sorted(rows, key=repr))


def stage4_grounding(data: object) -> object:
    """计划/上下文组装消费的人物、关系、秘密、设定与规则字段。

    原文 ``SourceSpan`` 只给 Stage2 回取与人工复核；Stage4 实际 prompt 也剥掉它们，
    所以 evidence-only 订正不应让两个 Stage4 制品 stale。
    """
    if not isinstance(data, dict):
        return None
    projected = without_global_evidence(data)
    assert isinstance(projected, dict)
    data = projected
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
             (r or {}).get('valid_from_chapter'),
             (r or {}).get('valid_through_chapter'),
             _relation_visibility_key((r or {}).get('visibility')))
            for r in (ent.get('relations') or [])),
        # ⚠ `setup` 整格进投影（不只是章号）：`{"kind":"cold"}` 与
        #   `{"kind":"foreshadowed","chapters":[]}` 在「章号集合」上同形而语义不同。
        #   首次揭示章直接决定下发边界。
        'secrets': sorted(
            ((s or {}).get('secret_id'), (s or {}).get('fact'),
              _knowledge_events_key((s or {}).get('knowledge_events')),
              _secret_setup_key((s or {}).get('setup')),
              (s or {}).get('first_reveal_chapter'))
             for s in (canon.get('secrets') or [])),
        'setting': sorted((canon.get('setting') or {}).items()),
        'world_rules': sorted(
            ((r or {}).get('rule_id'), (r or {}).get('kind'),
             tuple(sorted((str(v.get('effective_from_chapter')), v.get('desc'))
                          for v in ((r or {}).get('versions') or []))))
            for r in (canon.get('world_rules') or [])),

    }


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
    """Stage5 写作只绑定 secret 语义；定位证据不进付费 freshness。"""
    if not isinstance(data, dict):
        return None
    projected = without_global_evidence({
        'canon': {'secrets': (data.get('canon') or {}).get('secrets')},
    })
    return {'secrets': ((projected or {}).get('canon') or {}).get('secrets')}


def stage5_review_grounding(data: object) -> object:
    """Stage5 Review 的全书秘密与人名检测基线。

    Review 与写作故意不共用这条边：写作的付费 freshness 仍只绑
    secrets；Review 还会用 ``project_character_names`` 从当前 Stage2 人物表派生
    ``Book.names`` / ``Book.name_stems``，所以必须绑定这个派生真正读到的四格。

    ``address`` 的顺序、重复项以及 ``by/context/from_chapter`` 不影响全书
    display-name 派生；因此只保留去空白后的唯一 surface 集。role/fate
    与所有 evidence 也不属于这条 Review 依赖。
    """
    if not isinstance(data, dict):
        return None
    entities = data.get('entities')
    if not isinstance(entities, dict):
        entities = {}
    raw_characters = entities.get('characters')
    if not isinstance(raw_characters, dict):
        raw_characters = {}

    characters: list[dict[str, object]] = []
    for raw_char_id, raw_character in sorted(
        raw_characters.items(), key=lambda item: str(item[0]),
    ):
        character = raw_character if isinstance(raw_character, dict) else {}
        raw_address = character.get('address')
        if not isinstance(raw_address, list):
            raw_address = []
        surfaces = sorted({
            surface.strip()
            for row in raw_address if isinstance(row, dict)
            for surface in [row.get('surface')]
            if isinstance(surface, str) and surface.strip()
        })
        characters.append({
            'char_id': str(raw_char_id),
            'canonical': character.get('canonical'),
            'address_surfaces': surfaces,
            'name_distinctiveness': character.get('name_distinctiveness'),
        })

    secrets = stage5_grounding(data)
    return {
        'secrets': (secrets or {}).get('secrets') if isinstance(secrets, dict) else None,
        'characters': characters,
    }


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
    projected = stage4_grounding(_character_view(data))
    return projected.get('characters') if isinstance(projected, dict) else None


def stage5_characters(data: object) -> object:
    # 首写与返修也用 Book.names 做候选核验，和 Reviewer 共用有据名字基线。
    projected = stage5_review_grounding(_character_view(data))
    return projected.get('characters') if isinstance(projected, dict) else None


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
        STAGE2_CHAPTER_BLUEPRINTS: stage4_design_blueprints,
        STAGE2_CHARACTERS: stage4_characters,
        STAGE2_KNOWLEDGE_LINKS: artifact_payload,
        STAGE2_GLOBAL_GROUNDING: stage4_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_BLIND_CHAPTER_EDIT: artifact_payload,
    },
    'stage4.plan': {
        STAGE2_KNOWLEDGE_LINKS: artifact_payload,
        STAGE2_CHARACTERS: stage4_characters,
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
_CONTEXT_INPUTS: Final[dict[tuple[str, str], tuple[str, ...]]] = {}


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
    """章节抽取只见截至本章已有原文依据的共享信息，不泄露未来版本或秘密。

    返回的正文就是 prompt 材料，证据摘要另在同一对象记录，供审阅和请求身份使用；
    不重新嵌入原文。没有合法证据的 Global 成员不能成为抽取许可。
    """
    if not isinstance(data, dict):
        raise ValueError('Global 投影输入不可读')
    entities = data.get('entities') or {}
    canon = data.get('canon') or {}
    result = []

    def attested(evidence: object) -> bool:
        return (isinstance(evidence, dict)
                and type(evidence.get('chapter_ordinal')) is int
                and 1 <= evidence['chapter_ordinal'] <= chapter_ordinal
                and isinstance(evidence.get('source'), dict)
                and bool(evidence['source'].get('text')))

    def add(kind: str, ref: str, payload: object, evidence: object) -> None:
        result.append({'kind': kind, 'ref': ref, 'value': payload,
                       'evidence_sha256': consumed_digest(evidence)})

    secrets = {row['secret_id']: row for row in canon.get('secrets', [])}
    for row in entities.get('relations', []):
        visibility = row['visibility']
        if visibility['kind'] != 'public':
            secret = secrets.get(visibility.get('secret_id'))
            if secret is None or secret['first_reveal_chapter'] > chapter_ordinal:
                continue
        elif visibility['from_chapter'] > chapter_ordinal:
            continue
        if attested(row.get('predicate_evidence')):
            add('relation', row['relation_id'],
                {k: v for k, v in row.items() if k != 'predicate_evidence'},
                row['predicate_evidence'])
    for cid, row in entities.get('character_states', entities.get('characters', {})).items():
        if attested(row.get('fate_evidence')):
            add('character_state', cid, {'content': row['fate']}, row['fate_evidence'])
    setting = canon.get('setting') or {}
    for field, evidence_key, ref in (('时代背景', 'era_evidence', '时代背景'),
                                      ('世界观', 'worldview_evidence', '世界观')):
        if attested(setting.get(evidence_key)):
            add('setting', ref, {'content': setting[field]}, setting[evidence_key])
    for row in canon.get('world_rules', []):
        versions = [version for version in row['versions']
                    if attested(version.get('desc_evidence'))]
        if versions:
            add('world_rule', row['rule_id'], {
                'kind': row['kind'],
                'versions': [{k: v for k, v in version.items() if k != 'desc_evidence'}
                             for version in versions],
            }, [version['desc_evidence'] for version in versions])
    for ref, row in secrets.items():
        if (row['first_reveal_chapter'] <= chapter_ordinal
                and attested(row.get('fact_evidence'))):
            # 传播时间线由 KnowledgeLinks 消费；抽取只引用已揭示的共享命题。
            add('secret', ref, {'content': row['fact']}, row['fact_evidence'])
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
        result = []
        for chapter in chapters:
            if chapter['chapter_ordinal'] >= selection.chapter_ordinal:
                continue
            anchors = {a['evidence_id']: a for a in chapter['evidence_anchors']}
            for state in chapter['states']:
                if state['state_id'] in requested:
                    result.append({'chapter_ordinal': chapter['chapter_ordinal'],
                                   'state': state,
                                   'evidence': [{key: value for key, value in anchors[ref].items()
                                                 if key not in ('start', 'end')}
                                                for ref in state['evidence_refs']]})
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
