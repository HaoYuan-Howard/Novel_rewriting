#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`writer_context` 当前契约的确定性组装与结构守卫。"""
from __future__ import annotations

from functools import wraps
from typing import get_origin, get_type_hints

import pytest

from contracts.schema.stage4 import CtxScene, WriterContext
from novel.errors import RefusedByStructure
from novel.stages.stage4.context import (
    Assembly, _name_narration, _resolve_scenes,
    build_writer_context, rename_all,
)
from novel.core.naming import display_names, display_names_at, name_collisions
from tests import corpus
from tests.test_stage2_grounding import character_preparation, pair  # noqa: F401


def _by(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_current(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN: dict[str, object] = {}
BPS: dict[str, object] = {}
GG: dict[str, object] = {}
PLANS: dict[str, object] = {}
CTX: dict[str, object] = {}
LINKS: dict[str, object] = {}
BOOKS: list[str] = []
_CORPUS_LOADED = False


def _load_corpus() -> None:
    global MAIN, BPS, GG, PLANS, CTX, LINKS, BOOKS, _CORPUS_LOADED
    if _CORPUS_LOADED:
        return
    MAIN = _by('stage1/main_text.json')
    BPS = _by('stage2/chapter_blueprints.json')
    from contracts.schema.characters import CharacterRegistry
    from contracts.schema.knowledge import KnowledgeGrounding
    from novel.stages.stage2.grounding import compose
    knowledge = _by('stage2/global_grounding.json')
    characters = _by('stage2/characters.json')
    LINKS = _by('stage2/knowledge_links.json')
    assert set(knowledge) <= set(characters), '当前语料缺少独立人物登记，不能伪装组合成功'
    GG = {sid: compose(CharacterRegistry.model_validate(characters[sid]),
                       KnowledgeGrounding.model_validate(raw)).model_dump(by_alias=True)
          for sid, raw in knowledge.items()}
    PLANS = _by('stage4/plan_chapters.json')
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
    base = {
        'slot': 's01', 'purpose': '让拒绝当场爆发', 'role': 'setup',
        'delivery': 'scene', 'transition': 'start', 'budget_chars': 120,
        'required_facts': ['三位住客拒绝接受主角'], 'context_facts': [],
        'turn_fact': '三位住客拒绝接受主角', 'entry_state': [], 'exit_state': [],
        'stages': [{'time_layer': 'present', 'place': '公寓', 'present': ['主角']}],
        'knowledge': [],
    }
    return {**base, **overrides}


def test_ctx_scene_preserves_each_selected_stage_instead_of_flattening_them():
    scene = CtxScene.model_validate(_ctx_scene(stages=[
        {'time_layer': 'present', 'place': '公寓', 'present': []},
        {'time_layer': 'recall', 'place': '车站', 'present': ['主角']},
    ]))
    dumped = scene.model_dump(mode='json')
    assert dumped['stages'][0]['present'] == []
    assert dumped['stages'][1]['time_layer'] == 'recall'


def test_ctx_scene_rejects_every_retired_writing_skeleton_key():
    for key, value in (
        ('events', ['旧复合事件']), ('from_beats', ['b01_01']), ('end_beat', 'b01_01'),
        ('source_chars', 100), ('participants', ['主角']),
    ):
        with pytest.raises(ValueError, match=key):
            CtxScene.model_validate({**_ctx_scene(), key: value})


def test_ctx_scene_turn_must_be_one_of_its_required_facts():
    with pytest.raises(ValueError, match='turn_fact'):
        CtxScene.model_validate(_ctx_scene(turn_fact='本场没有承载的事实'))


def test_scene_resolution_uses_atom_claims_and_keeps_stage_boundaries():
    scenes, dropped, withheld = _resolve_scenes(
        {'scenes': [{
            'slot': 's01', 'purpose': '先给结果再追原因', 'role': 'setup',
            'delivery': 'scene', 'transition': 'start', 'budget_chars': 120,
            'land_fact_refs': ['f01_002'], 'context_fact_refs': ['f01_001'],
            'material_refs': [], 'turn_fact_ref': 'f01_002',
            'entry_state_refs': [], 'exit_state_refs': [],
            'stage_refs': ['b01_02', 'b01_01'],
        }]},
        {
            'b01_01': {'source_chars': 30, 'time_layer': 'present', 'place': '门口',
                       'present_char_ids': ['char_001'], 'hinges': []},
            'b01_02': {'source_chars': 50, 'time_layer': 'recall', 'place': '车站',
                       'present_char_ids': ['char_001'], 'hinges': []},
        },
        {
            'f01_001': {'claim': '主角此前收到邀请'},
            'f01_002': {'claim': '住客当面拒绝主角'},
        },
        {'char_001': '阿青'},
    )
    assert dropped == [] and withheld == []
    assert scenes[0]['required_facts'] == ['住客当面拒绝主角']
    assert scenes[0]['context_facts'] == ['主角此前收到邀请']
    assert [stage['place'] for stage in scenes[0]['stages']] == ['车站', '门口']
    assert 'source_chars' not in scenes[0]
    assert 'participants' not in scenes[0]


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
                                   knowledge_links=LINKS[sid])
        mine = WriterContext.model_validate({**asm.ctx, '_provenance': CTX[sid]['_provenance']}).model_dump(
            by_alias=True, mode='json')
        disk = WriterContext.model_validate(CTX[sid]).model_dump(
            by_alias=True, mode='json')
        assert mine == disk, f'{sid} 当前关联重组与盘上上下文不一致'


@needs_corpus
def test_the_rebuilt_context_passes_the_carrier():
    """重组出来的那份要**过得了新契约**——差异 0 只说明与盘上一样，不说明它合法。"""
    prov = {'code': '0' * 10, 'root_clean_sha1': '0' * 40,
            'inputs': {'stage4/plan_chapters.json': {'file': '1' * 10, 'consumed': '2' * 10}}}
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid],
                                   knowledge_links=LINKS[sid])
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
                   'dropped_hinges', 'held_rules', 'withheld_setting')}
    assert set(containers.values()) <= {dict, list, set}, \
        f'副产物出现了非容器形状：{containers}'
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid],
                                   knowledge_links=LINKS[sid])
        for name, origin in containers.items():
            got = getattr(asm, name)
            assert isinstance(got, origin), \
                f'{sid} 的 {name} 是 {type(got).__name__}，而 `Assembly` 声明的是 {origin.__name__}'


@needs_corpus
def test_the_setting_gate_returns_structured_results():
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid],
                                   knowledge_links=LINKS[sid])
        assert isinstance(asm.withheld_setting, list)


# ── 二·四、关系截止章的包含式边界（`R-2.8`）──────────────────────────────



# ── 二·五、secret 的下发时点（`R-2.18`）───────────────────────────────────


def test_writer_context_excludes_every_global_source_span_byte(pair):
    """正式证据只用于关联和审计；付费 writer 材料不能读取原文片段或坐标。"""
    import json
    from novel.core.evidence import iter_global_evidence_slots
    from tests.test_stage4_disclosure import _inputs, _build
    inputs = _inputs(pair)
    markers, coordinates = [], []
    for index, (_claim_path, owner, field) in enumerate(iter_global_evidence_slots(inputs[1]), 1):
        marker = f'CONTEXT_EVIDENCE_ONLY_{index}'
        start = 923_456_700 + index * 100
        owner[field] = {'chapter_ordinal': 1,
                        'source': {'text': marker, 'start': start, 'end': start + len(marker)}}
        markers.append(marker)
        coordinates.extend((start, start + len(marker)))
    paid_context = json.dumps(_build(inputs), ensure_ascii=False, sort_keys=True)
    assert markers and all(marker not in paid_context for marker in markers)
    assert all(str(coordinate) not in paid_context for coordinate in coordinates)


# ── 三、名字表的三个方向 ─────────────────────────────────────────────────

def test_a_fabricated_canonical_is_replaced_by_an_attested_appellation():
    """⭐ `canonical` 在原文里出现 **0 次**时退回出现最多的那个有据叫法。

    实测某本 5 个角色的 canonical 原文 0 次（`苏父` `苏母` …），其中 3 个**已经写进了成品**。
    原文里从没有人管他叫「苏父」——那是 registry 的记账标签，不是这本书的语言。
    """
    chars = {'char_001': {'canonical': '苏父',
                          'address': [{'surface': '爹爹'}, {'surface': '老爷'}]}}
    book = '爹爹说了算。爹爹又说。老爷回来了。'
    disp, subs, unattested = display_names(chars, book)
    assert disp == {'char_001': '爹爹'} and subs == {'苏父': '爹爹'} and not unattested


def test_a_character_with_no_attested_appellation_is_a_terminal_state():
    """一个有据称呼都没有 ⇒ **显式终态**，不许静默下发一个自造标签。"""
    chars = {'char_001': {'canonical': '春江阁女子', 'address': []}}
    with pytest.raises(RefusedByStructure, match='没有任何【原文里有据】的称呼'):
        build_writer_context({'output_chapters': []},
                             {'entities': {'characters': chars}}, {'blueprints': []},
                             [{'chapter_ordinal': 1, 'text': '街上人来人往。'}], knowledge_links={})


def test_the_chapter_level_table_withholds_a_name_the_book_level_one_would_give():
    """⭐ **章级表是书级表的纯粹外推，而两者的结论必须不同。**

    书级表用全书正文判有据，于是一个到第 4 章才被指名的人，**在第 1 章就有了名字**。
    实测某本 10/12 人的 display_name 在 ch1 尚未出现。
    """
    chars = {'char_003': {'canonical': '岑亦', 'address': []}}
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


def test_rename_all_replaces_values_never_keys_and_longest_first():
    """⚠ **只换值不换键**（键是 char_id 这类内部引用，动了就断链）· **长的先换**。"""
    tree = {'char_001': {'role': '赵家小厮(车夫)兼跑腿', 'note': ['赵家小厮来了']}}
    subs = {'赵家小厮(车夫)': '小厮', '赵家小厮': '小厮'}
    out = rename_all(tree, subs)
    assert list(out) == ['char_001'], '键被改了——内部引用会断'
    assert out['char_001']['role'] == '小厮兼跑腿', '短的先换会切出半截残串'
    assert out['char_001']['note'] == ['小厮来了']


# ── 四、两条剩下的显式终态 ───────────────────────────────────────────────

def _minimal(canon: dict) -> tuple[dict, dict, list]:
    chars = {'char_001': {'canonical': '阿禾', 'address': [{'surface': '阿禾'}]}}
    gg = {'entities': {'characters': chars}, 'canon': canon, }
    return gg, {'blueprints': []}, [{'chapter_ordinal': 1, 'text': '阿禾走了进来。'}]


def test_a_rule_without_any_version_text_is_a_terminal_state():
    """⚠⚠ **兜底才是漏洞本身。**

    旧契约把 `versions` 写成「仅会改版的规则才给」，于是绝大多数规则的正文只存在于
    平级的 `description` 里，而这里曾用 `or description` 兜底——某本 5/5 都恰好有
    versions，看起来没事；而另外 3 本一条 versions 都没有，**同一行兜底让带人名的
    description 直接成为下发给写手的文本**。
    """
    gg, bps, mt = _minimal({'world_rules': [{'rule_id': 'wr_01', 'versions': []}]})
    with pytest.raises(RefusedByStructure, match='没有任何带 desc 的 version'):
        build_writer_context({'output_chapters': []}, gg, bps, mt, knowledge_links={})


# ── 五、冲突阶段的章覆盖 ──────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# `_name_narration` —— 那道判等闸。⚠ 2026-08-23 之前**两个分支都没有测试覆盖**
# ─────────────────────────────────────────────────────────────────────────────
#
# 这道闸是 `CtxNarration.focalizer` 盘上恒空的**唯一成因**：stage2 那一侧
# `focalizer_name` 填充率 27/31，但它填的值恒等于 `narrator_name`
# （两格在 `novel/stages/stage2/prompts.py` 里是**同一个占位符字符串**，
# 且从头到尾没有定义什么是「视点人物」、没说「只在与叙述者不同时才填」），
# 于是这里判等一律为真、键一律不写。
# ⇒ **那不是死值，是一道从未成立过的闸**（`docs/LESSONS.md` 第 8 条：
#   删字段前审计语义消费，不只搜字段名）。本批只补覆盖，不动 prompt、不删字段。

_DISP = {'c1': '沈青', 'c2': '顾堇'}


def _seg(**kw):
    base = {'grammatical_person': 'first', 'segment': '正文主体（直至"……沉重的呼吸声"）'}
    base.update(kw)
    return base


def test_focalizer_is_dropped_when_it_is_the_same_person_as_the_narrator():
    """⭐ 盘上 100% 走的就是这一支。"""
    out = _name_narration(
        _seg(narrator_char_id='c1', focalizer_char_id='c1'), _DISP, 0, 1)
    assert out['narrator'] == '沈青' and 'focalizer' not in out


def test_focalizer_is_kept_when_it_really_is_a_different_person():
    """⭐ 另一支——盘上零样本，只有这条测试见过它。"""
    out = _name_narration(
        _seg(narrator_char_id='c1', focalizer_char_id='c2'), _DISP, 0, 1)
    assert out['narrator'] == '沈青' and out['focalizer'] == '顾堇'


def test_an_unresolvable_narrator_does_not_suppress_a_resolvable_focalizer():
    """⚠ **代码上可达的倒置路径**：叙述者叫不出名字、视点人物叫得出
    ⇒ 只发 `focalizer` 不发 `narrator`。本语料从没走到过，但它不是死路。"""
    out = _name_narration(
        _seg(narrator_char_id='c9', focalizer_char_id='c2'), _DISP, 0, 1)
    assert 'narrator' not in out and out['focalizer'] == '顾堇'


def test_a_nameless_narrator_writes_neither_key_and_never_falls_back_to_char_id():
    """第一人称书的第 1 章正是这样：主角通篇写「我」，名字要到后面才出现。
    ⚠ **不许兜底成 `char_002`**——写作侧不该看见内部编号。"""
    out = _name_narration(
        _seg(narrator_char_id='c9', focalizer_char_id='c9'), _DISP, 0, 1)
    assert 'narrator' not in out and 'focalizer' not in out
    assert out['grammatical_person'] == 'first'


def test_the_stage2_segment_label_never_reaches_the_writer():
    """⚠⚠ **反原文泄漏**：stage2 的 `segment` 常原样引用原文当分界，
    而泄的往往正是本章末句。写手要的只是「分几段、什么顺序」。"""
    one = _name_narration(_seg(narrator_char_id='c1'), _DISP, 0, 1)
    many = _name_narration(_seg(narrator_char_id='c1'), _DISP, 1, 3)
    assert one['segment'] == '全章'
    assert many['segment'] == '第2段（共3段，按顺序写）'
    assert '沉重的呼吸声' not in str(one) and '沉重的呼吸声' not in str(many)


def test_writer_context_no_longer_requires_or_emits_literary_arc(pair):
    from tests.test_stage4_disclosure import _inputs, _build
    inputs = _inputs(pair)
    assert 'arc' not in inputs[1]
    assert all('arc' not in chapter['facts'] for chapter in _build(inputs))
