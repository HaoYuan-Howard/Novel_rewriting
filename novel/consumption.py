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
from typing import Any, Callable, Final, Mapping

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
    RETIRED_STAGE3_STYLE_GROUNDING, STAGE4_BLIND_CANDIDATE,
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


# ── stage2.blueprint ─────────────────────────────────────────────────────────


def stage1_stage0_source(data: object) -> object:
    """Stage1 只继承原文身份；正文是独立的 clean_text 字节边。"""
    if not isinstance(data, dict):
        return None
    source = data.get('source')
    if not isinstance(source, dict):
        return None
    return source.get('normalized_source_sha1')

# ── 退役 Stage3 v2 的冻结投影 ───────────────────────────────────────────────

def legacy_stage3_main_text(data: object) -> object:
    """正文文本与测量契约共同决定机械基线的新鲜度。"""
    return {
        'measurement_contract': STYLE_MEASUREMENT_CONTRACT,
        'texts': [s.get('text') for s in data] if isinstance(data, list) else None,
    }


def legacy_stage3_hook_copy(data: object) -> object:
    """原作文案段只消费正文。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


# ── stage4.plan / stage4.context ─────────────────────────────────────────────

def _stage2_source(source: object) -> object:
    if not isinstance(source, dict):
        return None
    return source.get('text'), source.get('start'), source.get('end')


def _stage2_hinge(hinge: object) -> object:
    if not isinstance(hinge, dict):
        return None
    source = hinge.get('source')
    text = source.get('text') if isinstance(source, dict) else None
    return hinge.get('hinge_id'), hinge.get('kind'), text


def _stage4_stages(blueprint: dict) -> tuple:
    return tuple(
        (be.get('beat_id'), be.get('time_layer'),
         be.get('place'), tuple(be.get('present_char_ids') or []),
         _stage2_source(be.get('span')),
         tuple(_stage2_hinge(h) for h in (be.get('hinges') or [])))
        for be in (blueprint.get('beats') or []) if isinstance(be, dict)
    )


def _stage4_atoms(blueprint: dict) -> tuple:
    return tuple(
        (atom.get('fact_id'), atom.get('claim'), atom.get('kind'), atom.get('obligation'),
         atom.get('source_beat_ref'), _stage2_source(atom.get('source')),
         tuple(atom.get('participant_char_ids') or []))
        for atom in (blueprint.get('fact_atoms') or []) if isinstance(atom, dict)
    )


def _stage4_contract(blueprint: dict) -> tuple:
    return (
        blueprint.get('chapter_function'),
        _stage4_atoms(blueprint),
        tuple((edge.get('before'), edge.get('after'))
              for edge in (blueprint.get('causal_edges') or []) if isinstance(edge, dict)),
        _stage4_narrative_relations(blueprint),
        tuple(blueprint.get('entry_state_refs') or []),
        tuple(blueprint.get('ending_state_refs') or []),
    )


def _stage4_narrative_relations(blueprint: dict) -> tuple:
    """Stage4 真正消费的 typed 关系；不携带任何 SourceSpan。"""
    fields = {
        'story_before': ('before_fact_ref', 'after_fact_ref'),
        'response_to': ('stimulus_fact_ref', 'response_fact_ref'),
        'same_scene': ('left_fact_ref', 'right_fact_ref'),
        'observes': (
            'observer_char_id', 'observed_fact_ref', 'observation_fact_ref'),
    }
    rows = []
    for relation in blueprint.get('narrative_relations') or []:
        if not isinstance(relation, dict):
            rows.append(('?', None, repr(relation)))
            continue
        kind = relation.get('kind')
        rows.append((
            kind,
            relation.get('enforcement'),
            *(relation.get(field) for field in fields.get(kind, ())),
        ))
    return tuple(sorted(rows, key=repr))


def stage4_plan_blueprints(data: object) -> object:
    """规划消费原子事实契约与舞台；叙述人称由 context 消费。"""
    if not isinstance(data, dict):
        return None
    return sorted(
        (b.get('chapter_ordinal'), _stage4_contract(b), _stage4_stages(b))
        for b in (data.get('blueprints') or []) if isinstance(b, dict))


def _frozen_v6_stage4_contract(blueprint: dict) -> tuple:
    """v6 当时供 Plan/Context 消费的契约形状；没有 relations 槽位。"""
    return (
        blueprint.get('chapter_function'),
        _stage4_atoms(blueprint),
        tuple((edge.get('before'), edge.get('after'))
              for edge in (blueprint.get('causal_edges') or [])
              if isinstance(edge, dict)),
        tuple(blueprint.get('entry_state_refs') or []),
        tuple(blueprint.get('ending_state_refs') or []),
    )


def frozen_v6_stage4_plan_blueprints(data: object) -> object:
    """冻结 v6 的 Stage4B consumed；只供现存正式制品的 exact 对证。"""
    if not isinstance(data, dict):
        return None
    return sorted(
        (b.get('chapter_ordinal'), _frozen_v6_stage4_contract(b),
         _stage4_stages(b))
        for b in (data.get('blueprints') or []) if isinstance(b, dict))


def stage4_design_blueprints(data: object) -> object:
    """盲设计只消费去序真相图；不消费源位置、舞台、材料或数组顺序。"""
    if not isinstance(data, dict):
        return None
    rows = []
    for blueprint in (data.get('blueprints') or []):
        if not isinstance(blueprint, dict):
            continue
        atoms = sorted(
            (atom.get('fact_id'), atom.get('claim'), atom.get('kind'),
             atom.get('obligation'), tuple(sorted(atom.get('participant_char_ids') or [])))
            for atom in (blueprint.get('fact_atoms') or []) if isinstance(atom, dict)
        )
        edges = sorted(
            (edge.get('before'), edge.get('after'))
            for edge in (blueprint.get('causal_edges') or []) if isinstance(edge, dict)
        )
        rows.append((
            blueprint.get('chapter_ordinal'), blueprint.get('chapter_function'),
            tuple(atoms), tuple(edges),
            _stage4_narrative_relations(blueprint),
            tuple(sorted(blueprint.get('entry_state_refs') or [])),
            tuple(sorted(blueprint.get('ending_state_refs') or [])),
        ))
    return sorted(rows)


def frozen_v6_stage4_design_blueprints(data: object) -> object:
    """冻结 v6 的 Stage4A consumed 投影；只供历史主体对证。

    v7 把 ``narrative_relations`` 加进活动投影后，即便旧书在内存里补一个空数组，
    tuple 形状也会变化，旧候选记录的 consumed 指纹便永远对不上。这里逐字保留
    v6 当时的投影形状，不登记进普通 ``PROJECTIONS``，因此现役 producer 不可能
    因为这个兼容口而把 v6 当成 v7 消费。
    """
    if not isinstance(data, dict):
        return None
    rows = []
    for blueprint in (data.get('blueprints') or []):
        if not isinstance(blueprint, dict):
            continue
        atoms = sorted(
            (atom.get('fact_id'), atom.get('claim'), atom.get('kind'),
             atom.get('obligation'),
             tuple(sorted(atom.get('participant_char_ids') or [])))
            for atom in (blueprint.get('fact_atoms') or [])
            if isinstance(atom, dict)
        )
        edges = sorted(
            (edge.get('before'), edge.get('after'))
            for edge in (blueprint.get('causal_edges') or [])
            if isinstance(edge, dict)
        )
        rows.append((
            blueprint.get('chapter_ordinal'), blueprint.get('chapter_function'),
            tuple(atoms), tuple(edges),
            tuple(sorted(blueprint.get('entry_state_refs') or [])),
            tuple(sorted(blueprint.get('ending_state_refs') or [])),
        ))
    return sorted(rows)


def stage4_context_blueprints(data: object) -> object:
    """写作上下文消费叙述信息、原子事实契约与舞台。"""
    if not isinstance(data, dict):
        return None
    return sorted(
        (b.get('chapter_ordinal'),
         tuple((n.get('grammatical_person'), n.get('narrator_char_id'),
                n.get('focalizer_char_id'), tuple(n.get('pronoun_surfaces') or []))
               for n in (b.get('narration') or []) if isinstance(n, dict)),
          _stage4_contract(b), _stage4_stages(b))
        for b in (data.get('blueprints') or []) if isinstance(b, dict))


def frozen_v6_stage4_context_blueprints(data: object) -> object:
    """冻结 v6 的 Context consumed；不注册给活动 producer。"""
    if not isinstance(data, dict):
        return None
    return sorted(
        (b.get('chapter_ordinal'),
         tuple((n.get('grammatical_person'), n.get('narrator_char_id'),
                n.get('focalizer_char_id'), tuple(n.get('pronoun_surfaces') or []))
               for n in (b.get('narration') or []) if isinstance(n, dict)),
         _frozen_v6_stage4_contract(b), _stage4_stages(b))
        for b in (data.get('blueprints') or []) if isinstance(b, dict))


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
    """计划/上下文组装消费的人物、关系、秘密、设定、规则与弧线字段。

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
        'arc': {
            'conflict_stages': tuple(
                ((stage or {}).get('stage'), tuple((stage or {}).get('chapters') or []))
                for stage in ((data.get('arc') or {}).get('conflict_stages') or [])),
            'climax': (data.get('arc') or {}).get('climax'),
            'ending': (data.get('arc') or {}).get('ending'),
            'arc_tracks': tuple(
                ((track or {}).get('subject'), (track or {}).get('trajectory'))
                for track in ((data.get('arc') or {}).get('arc_tracks') or [])),
        },
    }


# ── stage5.write / stage5.review ─────────────────────────────────────────────

def legacy_stage5_main_text(data: object) -> object:
    """退役 Stage3 边存在时，复算旧 Stage5 的 Stage1 consumed 形状。"""
    if not isinstance(data, list):
        return None
    return [{'chapter_ordinal': s.get('chapter_ordinal'), 'text': s.get('text')}
            for s in data if isinstance(s, dict)]


def stage5_main_text(data: object) -> object:
    """正文与即时测量契约共同决定 Stage5 新鲜度。"""
    return {
        'measurement_contract': STYLE_MEASUREMENT_CONTRACT,
        'chapters': legacy_stage5_main_text(data),
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


def legacy_stage5_style(data: object) -> object:
    """旧写作/验收 provenance 真正读取的正文机械基线。

    ⚠ 这条投影同时供来源账写入与 freshness 重算，输入可能是一份已经不合当前
    schema 的旧 raw dict；因此逐层判 ``dict``、缺格投影成 ``None``，不能在这里
    ``model_validate``。Stage5 只消费下面九个叶子：定性画像、文案段实测以及正文计数
    即使变化，也不应让既有成稿误报过期。
    """
    if not isinstance(data, dict):
        return None
    measurable = data.get('measurable')
    if not isinstance(measurable, dict):
        measurable = {}
    body = measurable.get('body')
    if not isinstance(body, dict):
        body = {}
    chapter_chars = body.get('chapter_chars')
    if not isinstance(chapter_chars, dict):
        chapter_chars = {}
    per_chapter = body.get('per_chapter_avg_sentence')
    if not isinstance(per_chapter, dict):
        per_chapter = {}
    return {
        'body': {
            'avg_sentence_chars': body.get('avg_sentence_chars'),
            'avg_paragraph_chars': body.get('avg_paragraph_chars'),
            'paragraph_sep_chars': body.get('paragraph_sep_chars'),
            'dialogue_ratio_applicable': body.get('dialogue_ratio_applicable'),
            'dialogue_ratio': body.get('dialogue_ratio'),
            'quote_styles': body.get('quote_styles'),
            'chapter_chars': {'avg': chapter_chars.get('avg')},
            'per_chapter_avg_sentence': {
                'min': per_chapter.get('min'),
                'max': per_chapter.get('max'),
            },
        },
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

def legacy_stage6_hook_copy(data: object) -> object:
    """退役 Stage3 边存在时，复算旧 Stage6 的 Stage1 consumed 形状。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


def stage6_hook_copy(data: object) -> object:
    """原作文案与即时测量契约共同决定 Stage6 新鲜度。"""
    return {
        'measurement_contract': STYLE_MEASUREMENT_CONTRACT,
        'text': legacy_stage6_hook_copy(data),
    }


def stage6_grounding(data: object) -> object:
    """开篇文案消费完整 claim 真相面，但不消费原文定位证据。"""
    if not isinstance(data, dict):
        return None
    return without_global_evidence({
        'entities': data.get('entities'),
        'canon': data.get('canon'),
        'arc': data.get('arc'),
    })


def legacy_stage6_style(data: object) -> object:
    """旧 Stage6 provenance 对 Stage3 的文案段五叶机械基线。

    生成、机械复检与 freshness 共用这一投影。正文机械面以及已经退役的定性画像
    即使变化，也不应让既有文案或语义复核误报过期。输入可能是已经不合当前
    schema 的旧 raw dict，因此这里逐层收窄，缺格投影成 ``None``。
    """
    if not isinstance(data, dict):
        return None
    measurable = data.get('measurable')
    if not isinstance(measurable, dict):
        measurable = {}
    hook = measurable.get('hook')
    if not isinstance(hook, dict):
        hook = {}
    return {
        'hook': {
            'chars': hook.get('chars'),
            'avg_sentence_chars': hook.get('avg_sentence_chars'),
            'avg_paragraph_chars': hook.get('avg_paragraph_chars'),
            'quote_styles': hook.get('quote_styles'),
            'dialogue_ratio': hook.get('dialogue_ratio'),
        },
    }


def stage6_context(data: object) -> object:
    """文案消费全书故事地图。"""
    if not isinstance(data, dict):
        return None

    def public_goal(value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {key: value.get(key) for key in ('intent', 'facts')}

    return [
        {
            'output_ordinal': chapter.get('output_ordinal'),
            'from_source_ordinals': chapter.get('from_source_ordinals'),
            'semantic_contract': chapter.get('semantic_contract'),
            'chapter_rewrite_brief': chapter.get('chapter_rewrite_brief'),
            'chapter_goal': public_goal(chapter.get('chapter_goal')),
            'open_hook_goal': public_goal(chapter.get('open_hook_goal')),
            'scenes': [
                {
                    'slot': scene.get('slot'),
                    'purpose': scene.get('purpose'),
                    'required_facts': list(scene.get('required_facts') or []),
                    'turn_fact': scene.get('turn_fact'),
                    'exit_state': list(scene.get('exit_state') or []),
                }
                for scene in (chapter.get('scenes') or [])
                if isinstance(scene, dict)
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


# 每一格的键都是 ArtifactStore 写进 ``_provenance.inputs`` 的两级相对路径。
CONSUMED_PROJECTIONS: Final[dict[str, dict[str, Projection]]] = {
    'stage1.partition': {
        STAGE0_REVISIONS: stage1_stage0_source,
        STAGE0_CLEAN_TEXT: artifact_payload,
    },
    'stage2.global': {
        STAGE1_MAIN_TEXT: stage2_main_text,
        # ``--apply-edit`` consumes the typed human decision set as a real
        # provenance edge.  Without this projection a just-written grounding
        # is ``consumed_undeclared`` and the formal-write postcondition must
        # (correctly) roll it back.
        STAGE2_GLOBAL_EDIT: artifact_payload,
    },
    'stage2.blueprint': {
        STAGE1_MAIN_TEXT: stage2_main_text,
        # Blueprint 不再伪记一条 global 边：真正发给模型/validator/compiler 的
        # 是这份版本化、确定性物化的称名闭集。
        STAGE2_BLUEPRINT_CHARACTER_PROJECTION: artifact_payload,
    },
    'stage4.design': {
        STAGE2_CHAPTER_BLUEPRINTS: stage4_design_blueprints,
        STAGE4_BLIND_CHAPTER_EDIT: artifact_payload,
    },
    'stage4.plan': {
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
        STAGE1_MAIN_TEXT: stage4_main_text,
        STAGE2_CHAPTER_BLUEPRINTS: stage4_context_blueprints,
        STAGE2_GLOBAL_GROUNDING: stage4_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_PLAN_CHAPTERS: artifact_payload,
    },
    'stage5.write': {
        STAGE1_MAIN_TEXT: stage5_main_text,
        STAGE2_GLOBAL_GROUNDING: stage5_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_WRITER_CONTEXT: stage5_context,
    },
    'stage5.review': {
        STAGE1_MAIN_TEXT: stage5_main_text,
        STAGE2_GLOBAL_GROUNDING: stage5_review_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_WRITER_CONTEXT: stage5_review_context,
        # retain_pretrim 的人工裁决绑定完整 audit bytes；任一审计事实变化都应过期。
        STAGE5_TRIM_AUDIT: artifact_payload,
    },
    'stage6.hook': {
        STAGE1_HOOK_COPY: stage6_hook_copy,
        STAGE2_GLOBAL_GROUNDING: stage6_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_WRITER_CONTEXT: stage6_context,
        STAGE6_HOOK_EDIT: artifact_payload,
    },
    'stage6.semantic': {
        STAGE1_HOOK_COPY: stage6_hook_copy,
        STAGE2_GLOBAL_GROUNDING: stage6_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_WRITER_CONTEXT: stage6_context,
        STAGE6_HOOK: stage6_semantic_hook,
    },
    'stage7.rename': {
        STAGE1_MAIN_TEXT: stage7_main_text,
        STAGE2_GLOBAL_GROUNDING: stage7_grounding,
        STAGE2_GLOBAL_REVIEW: stage2_generation_review,
        STAGE4_PLAN_CHAPTERS: stage7_plan,
        STAGE4_WRITER_CONTEXT: stage7_context,
        STAGE6_HOOK: stage7_hook,
        STAGE7_NAME_MAP_EDIT: artifact_payload,
    },
}

# 已落盘 Stage5/6 产物可能仍记录退役的 Stage3 边。只要那条边还在来源账里，
# freshness 就必须按旧投影复算整组输入；否则给 Stage1 投影加入测量契约会把旧付费
# 产物无差别判 stale。新运行永远不会查到本表，也不会重新产生 Stage3 边。
LEGACY_STAGE3_PROJECTIONS: Final[dict[str, dict[str, Projection]]] = {
    'stage3.style': {
        STAGE1_MAIN_TEXT: legacy_stage3_main_text,
        STAGE1_HOOK_COPY: legacy_stage3_hook_copy,
    },
    'stage5.write': {
        STAGE1_MAIN_TEXT: legacy_stage5_main_text,
        RETIRED_STAGE3_STYLE_GROUNDING: legacy_stage5_style,
    },
    'stage5.review': {
        STAGE1_MAIN_TEXT: legacy_stage5_main_text,
        RETIRED_STAGE3_STYLE_GROUNDING: legacy_stage5_style,
    },
    'stage6.hook': {
        STAGE1_HOOK_COPY: legacy_stage6_hook_copy,
        RETIRED_STAGE3_STYLE_GROUNDING: legacy_stage6_style,
    },
    'stage6.semantic': {
        STAGE1_HOOK_COPY: legacy_stage6_hook_copy,
        RETIRED_STAGE3_STYLE_GROUNDING: legacy_stage6_style,
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


def legacy_stage3_projection_for(node: str, rel: str) -> Projection | None:
    """旧 provenance 的 Stage1/Stage3 投影；其余边回落到当前契约。"""
    rel = canonical_rel(rel)
    table = LEGACY_STAGE3_PROJECTIONS.get(node, {})
    direct = table.get(rel)
    if direct is not None:
        return direct
    matched = next(
        (project for template, project in table.items() if _matches(template, rel)), None)
    return matched or projection_for(node, rel)


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
    if rel == RETIRED_STAGE3_STYLE_GROUNDING:
        return 'stage3.style'
    for node, spec in NODES.items():
        if any(_matches(template, rel) for template in spec.produces):
            return node
    return fallback


__all__ = [
    'CONSUMED_PROJECTIONS', 'LEGACY_STAGE3_PROJECTIONS', 'Projection',
    'artifact_payload', 'canonical_rel', 'context_inputs_for',
    'legacy_stage3_projection_for', 'producer_node', 'project_consumed',
    'projection_for',
]
