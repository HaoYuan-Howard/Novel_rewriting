#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`writer_context` 当前契约的确定性组装与结构守卫。"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
from typing import get_origin, get_type_hints

import pytest

from contracts.artifacts import STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING
from contracts.schema.stage4 import CtxScene, WriterContext
from novel.errors import RefusedByStructure
from novel.orchestrator.state import readonly_ctx
from novel.store import ArtifactStore
from novel.stages.stage4.context import (
    Assembly, build_writer_context,
)
from novel.stages.stage4.plan import compile_plan_view
from novel.core.naming import display_names, display_names_at, name_collisions
from tests import corpus


def _by(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_current(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN: dict[str, object] = {}
BPS: dict[str, object] = {}
GG: dict[str, object] = {}
CHARACTERS: dict[str, object] = {}
PLANS: dict[str, object] = {}
CTX: dict[str, object] = {}
LINKS: dict[str, object] = {}
BOOKS: list[str] = []
_CORPUS_LOADED = False


def _load_corpus() -> None:
    global MAIN, BPS, GG, CHARACTERS, PLANS, CTX, LINKS, BOOKS, _CORPUS_LOADED
    if _CORPUS_LOADED:
        return
    MAIN = _by('stage1/main_text.json')
    BPS = _by('stage2/chapter_blueprints.json')
    from contracts.schema.characters import CharacterRegistry
    from contracts.schema.knowledge import KnowledgeGrounding
    knowledge = _by('stage2/global_grounding.json')
    characters = _by('stage2/characters.json')
    LINKS = _by('stage2/knowledge_links.json')
    assert set(knowledge) <= set(characters), '当前语料缺少独立人物登记，不能伪装组合成功'
    CHARACTERS = {sid: CharacterRegistry.model_validate(raw).model_dump(by_alias=True)
                  for sid, raw in characters.items()}
    GG = {sid: KnowledgeGrounding.model_validate(raw).model_dump(by_alias=True)
          for sid, raw in knowledge.items()}
    from novel import prov
    candidates = _by('stage4/blind_candidate.json')
    bindings = _by('stage4/evidence_binding.json')
    PLANS = {}
    for sid in sorted(set(candidates) & set(bindings)):
        book_dir = next(a.book_dir for a in corpus.iter_artifacts()
                        if a.source_id == sid and a.rel == STAGE4_BLIND_CANDIDATE)
        ctx = readonly_ctx(corpus.corpus_root() / book_dir, 'stage4.context', prov.code_version())
        _plan, plan_raw = compile_plan_view(ArtifactStore(ctx))
        PLANS[sid] = plan_raw
    CTX = _by('stage4/writer_context.json')
    BOOKS = sorted(set(MAIN) & set(BPS) & set(GG) & set(PLANS) & set(CTX))
    assert set(BOOKS) <= set(LINKS), '当前语料缺少正式知识关联，不能跳过披露验收'
    _CORPUS_LOADED = True


def needs_corpus(test):
    @pytest.mark.corpus
    @wraps(test)
    def run(*args, **kwargs):
        _load_corpus()
        if not BOOKS:
            pytest.skip('盘上没有语料——本族对账无从谈起')
        return test(*args, **kwargs)
    return run

#: 来源账由写盘口生成；纯组装器只负责正文形状，因此逐字段对账时单独排除。
_GENERATED_TOP = frozenset({'_provenance'})


def _ctx_scene(**overrides):
    from tests import plot_fixtures as fixture
    return {**deepcopy(fixture.context()['chapters'][0]['scenes'][0]), **overrides}


def test_ctx_scene_preserves_each_selected_stage_instead_of_flattening_them():
    raw = _ctx_scene()
    raw['deliveries'][0]['stages'] = [
        {'time_layer': 'present', 'place': '公寓', 'present': []},
        {'time_layer': 'recall', 'place': '车站', 'present': ['actor_' + '1' * 24]}]
    scene = CtxScene.model_validate(raw).model_dump(mode='json')
    assert scene['deliveries'][0]['stages'][0]['present'] == []
    assert scene['deliveries'][0]['stages'][1]['time_layer'] == 'recall'


def test_ctx_scene_rejects_every_retired_writing_skeleton_key():
    for key, value in (
        ('events', ['旧复合事件']), ('from_beats', ['b01_01']), ('end_beat', 'b01_01'),
        ('source_chars', 100), ('participants', ['主角']),
    ):
        with pytest.raises(ValueError, match=key):
            CtxScene.model_validate({**_ctx_scene(), key: value})


def test_ctx_scene_turn_must_belong_to_current_delivery():
    with pytest.raises(ValueError, match='turn'):
        CtxScene.model_validate(_ctx_scene(turn_beat_ref='b01_99'))


def test_scene_resolution_keeps_parent_content_once_and_stage_boundaries():
    from tests import plot_fixtures as fixture
    raw = fixture.blueprint()
    context = deepcopy(raw['blueprints'][0]['beats'][0]['source_contexts'][0])
    context.update(time_layer='recall', place='旧车站')
    raw['blueprints'][0]['beats'][0]['source_contexts'].append(context)
    from novel.stages.stage4.measure import identity_plan
    plan = identity_plan(raw, fixture.MAIN)
    asm = build_writer_context(plan, fixture.GLOBAL, raw, fixture.MAIN,
                               characters=fixture.REGISTRY)
    chapter = asm.ctx['chapters'][0]
    assert chapter['beats'][0]['content'] == raw['blueprints'][0]['beats'][0]['content']
    delivery = chapter['scenes'][0]['deliveries'][0]
    assert {stage['place'] for stage in delivery['stages']} == {'门外', '旧车站'}
    assert 'content' not in delivery and 'source_chars' not in delivery


@needs_corpus
def test_the_assembler_reproduces_every_context_on_disk():
    """⭐⭐ **本层最值钱的一条免费等价测试，3/3 本差异 0。**

    盘上那几份 `writer_context.json` 本仓重组一遍，逐字段比对。
    它一次钉住：六道逐章闸门 · 名字表的书级/章级两个方向 · 保真材料的拆点与去重 ·
    「跨层不压平」的 `segments` 分支 · 称谓表的 `who` 也走章级表那一处。

    **不一致就是骨架抄错了，不是数据变了**——那几份产物一个字节都没动过。

    ⚠⚠ **两边必须先过同一个载体再比。** `build_writer_context` 交出来的是**组装形态**
    （可选键不给就是不在），盘上那份是 `store.write` 落的**落盘形态**
    （`model_dump` 把可选键显式写成 `null`）。直接拿裸 dict 比，
    场景载体的可选键会在**每一场上**各报一处「只在盘上有：None」——
    实测一本 530 处，而组装器一个字都没错。
    ⚠ 那种红最坏的地方是**它长得和真回归一模一样**：一列「只在盘上有」，
    看不出是形态差还是丢字段。⇒ 过一次 `WriterContext` 把两边归到同一形态，
    差异重新变成「真差异」。`_diffs` 那条「不做宽松相等」的纪律**不放松**，
    放松的是「拿两种形态直接比」这个前提错误。
    """
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid],
                                   knowledge_links=LINKS[sid], characters=CHARACTERS[sid])
        mine = WriterContext.model_validate({**asm.ctx, '_provenance': CTX[sid]['_provenance']}).model_dump(
            by_alias=True, mode='json')
        disk = WriterContext.model_validate(CTX[sid]).model_dump(
            by_alias=True, mode='json')
        assert mine == disk, f'{sid} 当前关联重组与盘上上下文不一致'


@needs_corpus
def test_the_rebuilt_context_passes_the_carrier():
    """重组出来的那份要**过得了新契约**——差异 0 只说明与盘上一样，不说明它合法。"""
    prov = {'code': '0' * 10, 'root_clean_sha1': '0' * 40,
            'inputs': {
                STAGE4_BLIND_CANDIDATE: {'file': '1' * 10, 'consumed': '2' * 10},
                STAGE4_EVIDENCE_BINDING: {'file': '3' * 10, 'consumed': '4' * 10},
            }}
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid],
                                   knowledge_links=LINKS[sid], characters=CHARACTERS[sid])
        WriterContext.model_validate({**asm.ctx, '_provenance': prov})


# ── 二、组装的副产物 ────────────────────────────────────────────────────

@needs_corpus
def test_the_side_ledger_is_returned_not_printed():
    """组装副产物必须以**结构化容器**返回（不是打印出去），且形状照 `Assembly` 的声明。

    ⚠⚠ **容器类型从 `Assembly` 的注解派生，不在这里手抄一份。**
    手抄那份漂过：本条曾断言 `asm.subs` 是 `list`，而 `Assembly.subs` 从声明起
    就是 `dict[str, str]`（`{原标签: 换成的}`，`rename_all` 按它做确定性替换）；
    `held_rules` 同样被抄成 `list` 而它是 `set`。两条都不是「组装器变了」，
    是**这张表在测试里有了第二个落点**——与 `target_band` 同一家族的跨模块漂移。
    ⇒ 派生之后，改 `Assembly` 的哪一格都会在这里出声，而不是安静地对不上。
    """
    hints = get_type_hints(Assembly)
    containers = {rows: get_origin(hints[rows]) for rows in
                  ('subs', 'collisions', 'chapter_collisions', 'withheld',
                   'held_rules')}
    assert set(containers.values()) <= {dict, list, set}, \
        f'副产物出现了非容器形状：{containers}'
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid],
                                   knowledge_links=LINKS[sid], characters=CHARACTERS[sid])
        for name, origin in containers.items():
            got = getattr(asm, name)
            assert isinstance(got, origin), \
                f'{sid} 的 {name} 是 {type(got).__name__}，而 `Assembly` 声明的是 {origin.__name__}'


@needs_corpus
def test_the_setting_gate_returns_structured_results():
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid],
                                   knowledge_links=LINKS[sid], characters=CHARACTERS[sid])
        assert all(isinstance(chapter['restricted_subject_refs'], list)
                   for chapter in asm.ctx['chapters'])


# ── 二·四、关系截止章的包含式边界（`R-2.8`）──────────────────────────────



# ── 二·五、secret 的下发时点（`R-2.18`）───────────────────────────────────


def test_writer_context_excludes_every_global_source_span_byte():
    """共享坐标/证据摘要只供审计，不得进入实际 WriterContext。"""
    import json
    from tests import plot_fixtures as fixture
    knowledge = deepcopy(fixture.GLOBAL)
    knowledge['evidence_anchors'] = {'ev_marker': {'chapter_ordinal': 1, 'start': 923456701,
        'end': 923456729, 'text_sha256': 'CONTEXT_EVIDENCE_ONLY'}}
    view = build_writer_context(fixture.plan(), knowledge, fixture.blueprint(), fixture.MAIN,
                                characters=fixture.REGISTRY).ctx
    wire = json.dumps(view)
    assert 'CONTEXT_EVIDENCE_ONLY' not in wire and '923456701' not in wire


# ── 三、名字表的三个方向 ─────────────────────────────────────────────────

def test_a_fabricated_canonical_is_replaced_by_an_attested_appellation():
    """⭐ `canonical` 在原文里出现 **0 次**时退回出现最多的那个有据叫法。

    实测某本 5 个角色的 canonical 原文 0 次（`苏父` `苏母` …），其中 3 个**已经写进了成品**。
    原文里从没有人管他叫「苏父」——那是 registry 的记账标签，不是这本书的语言。
    """
    chars = {'char_001': {'canonical': '苏父',
                          'aliases': [{'surface': '爹爹'}, {'surface': '老爷'}]}}
    book = '爹爹说了算。爹爹又说。老爷回来了。'
    disp, subs, unattested = display_names(chars, book)
    assert disp == {'char_001': '爹爹'} and subs == {'苏父': '爹爹'} and not unattested


def test_a_character_with_no_attested_appellation_is_a_terminal_state():
    from tests import plot_fixtures as fixture
    registry = deepcopy(fixture.REGISTRY)
    registry['characters']['char_001'].update(canonical='春江阁女子', aliases=[])
    with pytest.raises(RefusedByStructure, match='没有当前可辨认'):
        build_writer_context(fixture.plan(), fixture.GLOBAL, fixture.blueprint(), fixture.MAIN,
                             characters=registry)


def test_the_chapter_level_table_withholds_a_name_the_book_level_one_would_give():
    """⭐ **章级表是书级表的纯粹外推，而两者的结论必须不同。**

    书级表用全书正文判有据，于是一个到第 4 章才被指名的人，**在第 1 章就有了名字**。
    实测某本 10/12 人的 display_name 在 ch1 尚未出现。
    """
    chars = {'char_003': {'canonical': '岑亦', 'aliases': []}}
    whole = '第一章里没有名字。\n第四章：岑亦终于露面。'
    upto1 = '第一章里没有名字。'
    assert display_names(chars, whole)[0] == {'char_003': '岑亦'}
    assert display_names_at(chars, upto1) == {}, '本章还叫不出名字的人，键里就不该有他'


def test_a_collision_is_made_visible_not_resolved():
    """⚠ **不在这里替它选一个**：原文若本就管两个人都叫这个，改写照样叫才是保真的。

    `canonical 全书唯一` 守的是**注册键**，而写手拿到的是它的**派生值**——
    区分词恰恰是为消歧加的，派生时又把它剥回去，**而两边的检查各自都是绿的**。
    """
    chars = {'char_a': {'canonical': '李嬷嬷(慧贵妃宫)'}, 'char_b': {'canonical': '李嬷嬷(长公主府)'}}
    disp = {'char_a': '李嬷嬷', 'char_b': '李嬷嬷'}
    cols = name_collisions(disp, chars)
    assert len(cols) == 1 and cols[0][0] == '李嬷嬷' and sorted(cols[0][1]) == ['char_a', 'char_b']


def _compile_view(bps, main, registry):
    """现役 WriterContext 组装口：语义审阅退役后不再接收 KnowledgeLinks。"""
    from novel.stages.stage4.measure import identity_plan
    from tests.plot_fixtures import GLOBAL
    return build_writer_context(identity_plan(bps, main), GLOBAL, bps, main,
                                characters=registry).ctx


def test_registry_label_never_replaces_distinct_presentations_in_writer_text():
    """旧全树替换已退役；称谓归属必须由显式 ActorRef 保护，不能按字串改写别人的身份。"""
    from tests.test_plot_actor_resolution import presented
    bps, main, registry, old, new = presented()
    registry['characters']['char_001']['canonical'] = '内部登记标签'
    ctx = _compile_view(bps, main, registry)
    assert '内部登记标签' not in str(ctx)
    roster = ctx['chapters'][0]['characters']
    assert {'甲', '新客'} <= {row['display_name'] for row in roster}


def test_a_rule_without_any_version_text_is_a_terminal_state():
    from contracts.schema.stage2 import WorldRule
    # 当前正式 Global 载体在规则进入消费链之前就关闭缺版本的旧平级兜底。
    with pytest.raises(ValueError, match='versions'):
        WorldRule.model_validate({'rule_id': 'wr_01', 'kind': 'legal_rule',
                                  'versions': [], 'description': '旧平级兜底不可下发'})


# ── 五、冲突阶段的章覆盖 ──────────────────────────────────────────────────


# 叙述/视点是独立的实际人物位置，不能按 char_id 判等后丢掉不同呈现。

def narrated(narrator, focalizer):
    from tests.test_plot_actor_resolution import presented
    bps, main, registry, old, new = presented()
    bps['blueprints'][0]['narration'][0].update(narrator=narrator, focalizer=focalizer)
    return _compile_view(bps, main, registry)['chapters'][0]['narration'][0]


def test_same_presentation_narrator_and_focalizer_keep_same_public_reference():
    value = {'char_id': 'char_001', 'presentation_ref': 'ap01_01'}
    out = narrated(value, value)
    assert out['narrator'] == out['focalizer']
    assert out['narrator'].startswith('actor_')


def test_focalizer_keeps_distinct_presentation_even_for_same_internal_entity():
    out = narrated({'char_id': 'char_001', 'presentation_ref': 'ap01_01'},
                   {'char_id': 'char_001', 'presentation_ref': 'ap01_02'})
    assert out['narrator'] != out['focalizer']


def test_missing_narrator_is_refused_instead_of_silently_dropping_the_binding():
    with pytest.raises(RefusedByStructure, match='不存在的人物'):
        narrated({'char_id': 'char_999'}, {'char_id': 'char_001', 'presentation_ref': 'ap01_01'})


def test_unspecified_narrator_keeps_explicit_focalizer_without_fallback_identity():
    out = narrated(None, {'char_id': 'char_001', 'presentation_ref': 'ap01_02'})
    assert out['narrator'] is None and out['focalizer'].startswith('actor_')
    assert 'char_' not in str(out)


def test_stage2_free_segment_label_cannot_enter_current_narration_schema():
    from contracts.schema.plot import PlotNarration
    with pytest.raises(ValueError, match='segment'):
        PlotNarration.model_validate({'grammatical_person': 'first', 'narrator': None,
            'focalizer': None, 'pronoun_surfaces': ['我'], 'beat_refs': [], 'evidence_refs': ['e01_01'],
            'segment': '正文主体直至沉重的呼吸声'})


def test_writer_context_no_longer_requires_or_emits_literary_arc():
    from tests import plot_fixtures as fixture
    assert 'arc' not in fixture.GLOBAL
    assert all('arc' not in chapter for chapter in fixture.context()['chapters'])
