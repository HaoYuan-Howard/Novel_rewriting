#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`writer_context` 当前契约的确定性组装与结构守卫。"""
from __future__ import annotations

from functools import wraps
from typing import get_origin, get_type_hints

import pytest

from contracts.schema.stage4 import CtxBeat, WriterContext
from novel.core.facts import heuristic_relation_gates
from novel.errors import RefusedByStructure
from novel.stages.stage4.context import (
    Assembly, _gate_setting, _name_narration, _project_facts, _resolve_beats,
    build_writer_context,
    display_names, display_names_at, name_collisions, rename_all,
)
from tests import corpus


def _by(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_current(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN: dict[str, object] = {}
BPS: dict[str, object] = {}
GG: dict[str, object] = {}
PLANS: dict[str, object] = {}
CTX: dict[str, object] = {}
BOOKS: list[str] = []
_CORPUS_LOADED = False


def _load_corpus() -> None:
    global MAIN, BPS, GG, PLANS, CTX, BOOKS, _CORPUS_LOADED
    if _CORPUS_LOADED:
        return
    MAIN = _by('stage1/main_text.json')
    BPS = _by('stage2/chapter_blueprints.json')
    GG = _by('stage2/global_grounding.json')
    PLANS = _by('stage4/plan_chapters.json')
    CTX = _by('stage4/writer_context.json')
    BOOKS = sorted(set(MAIN) & set(BPS) & set(GG) & set(PLANS) & set(CTX))
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


def test_flat_and_segmented_present_have_distinct_serialized_meanings():
    flat = CtxBeat.model_validate({
        'slot': 's01',
        'events': ['空院里没有别人。'], 'budget_chars': 80, 'source_chars': 20,
        'time_layer': 'present', 'place': '空院', 'present': [],
    })
    assert flat.present == []
    assert flat.model_dump(mode='json')['present'] == []

    segmented = CtxBeat.model_validate({
        'slot': 's01',
        'events': ['先在空院。', '后转入回忆。'], 'budget_chars': 100, 'source_chars': 30,
        'segments': [
            {'time_layer': 'present', 'place': '空院', 'present': []},
            {'time_layer': 'recall', 'place': None, 'present': ['阿青']},
        ],
    })
    dumped = segmented.model_dump(mode='json')
    assert dumped['present'] is None and dumped['segments'][0]['present'] == []

    with pytest.raises(ValueError, match='present'):
        CtxBeat.model_validate({
            'slot': 's01',
            'events': ['空院里没有别人。'], 'budget_chars': 80, 'source_chars': 20,
            'time_layer': 'present', 'place': '空院', 'present': None,
        })

    with pytest.raises(ValueError, match='只在 segments'):
        CtxBeat.model_validate({
            'slot': 's01',
            'events': ['跨场。'], 'budget_chars': 80, 'source_chars': 20,
            'present': [],
            'segments': [{'time_layer': 'present', 'place': None, 'present': []}],
        })


def _diffs(mine: object, disk: object, path: str = '', out: list[str] | None = None) -> list[str]:
    """逐字段比对。⚠ **不做「宽松相等」**：多一个键、少一个键、值差一个字都要报出来。"""
    if out is None:
        out = []
    if isinstance(mine, dict) and isinstance(disk, dict):
        for k in sorted(set(mine) | set(disk)):
            if path == '' and k in _GENERATED_TOP:
                continue
            if k not in mine:
                out.append(f'{path}.{k} 只在盘上有：{str(disk[k])[:60]}')
            elif k not in disk:
                out.append(f'{path}.{k} 只在算出来的里有：{str(mine[k])[:60]}')
            else:
                _diffs(mine[k], disk[k], f'{path}.{k}', out)
    elif isinstance(mine, list) and isinstance(disk, list):
        if len(mine) != len(disk):
            out.append(f'{path} 长度 {len(mine)} vs {len(disk)}')
        for i, (x, y) in enumerate(zip(mine, disk)):
            _diffs(x, y, f'{path}[{i}]', out)
    elif mine != disk:
        out.append(f'{path}: 算={str(mine)[:70]!r} 盘={str(disk)[:70]!r}')
    return out


def _through_the_carrier(ctx: dict[str, object], provenance: object) -> dict[str, object]:
    """组装形态 → **落盘形态**，走的是 `store.write` 用的同一个载体。

    盘上那份的来源账由写盘口生成，纯组装器产不出它 ⇒ 借盘上那份把模型填满，
    随后 `_GENERATED_TOP` 会在比对时把它排除；这里给它只是为了过必填校验。
    """
    return WriterContext.model_validate(
        {**ctx, '_provenance': provenance}).model_dump(by_alias=True, mode='json')


# ── ⭐ 一、ctx 重组 == 存盘 ───────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# `spine`：拍内主次（2026-08-22，批 3）
# ─────────────────────────────────────────────────────────────────────────────

def _bidx_spine() -> dict[str, dict[str, object]]:
    return {f'b01_0{i}': {
        'ord': 1, 'event': f'第{i}件事。', 'source_chars': 50, 'hinges': [],
        'time_layer': 'present', 'place': '茶馆', 'present_char_ids': [],
    } for i in (1, 2, 3)}


@pytest.mark.parametrize('given', [2, None])
def test_the_spine_reaches_the_context_exactly_as_the_plan_left_it(given):
    """⭐ **原样投影，两个方向都不许加工。** 有值就带值，`None` 就留 `None`。

    ⚠ 旁边那三个（`narrative_role`/`delivery`/`transition`）带 `or` 默认值，是给存量载体读的；
    `spine` **不能**照抄那个写法——补一个 1 等于替计划做了它没做过的决策，
    而下发面会照着演（`stage5/prompts.py::_unit_rule`）。
    """
    beats, _dropped, _withheld = _resolve_beats(
        {'segments': [{'slot': 's01', 'from_beats': ['b01_01', 'b01_02'],
                       'budget_chars': 120, 'spine': given}]},
        _bidx_spine(), {})
    assert beats[0]['spine'] == given
    assert 'spine' in beats[0], '键必须恒在——「没做过这个决策」与「这一格丢了」不能同形'


def test_a_spine_that_points_past_the_last_event_dies_on_the_carrier():
    """⭐⭐ **全链只有 `CtxBeat` 能把 `spine ≤ len(events)` 写成类型**——它同时持有两者。

    ⚠ 这不是把判据搬上载体：上游那两侧各缺一半（响应侧 `plan_beats[]` 还没展开、
    产物侧 `from_beats[]` 是 id 不是内容），这条不等式在这一层**第一次**可表达。
    ⚠ 它也是 `--apply-edit` 那一路**唯一**的守卫：人工稿不过 `expand_plan_beats`。
    """
    base = {'slot': 's03', 'events': ['甲。', '乙。'], 'budget_chars': 80,
            'source_chars': 40, 'time_layer': 'present', 'place': '空院', 'present': []}
    assert CtxBeat.model_validate({**base, 'spine': 2}).spine == 2
    with pytest.raises(ValueError, match='只有 2 件'):
        CtxBeat.model_validate({**base, 'spine': 3})


def test_an_empty_event_string_dies_on_the_carrier_because_it_shifts_the_spine():
    """⭐ 空 event 会让下发面的 ①②③ 与 `spine` **差一格**，所以它在载体上就死。

    ⚠ 下发面渲染时**过滤**空串（`stage5/prompts.py::chapter_facts` 的 `evs`），
    而 `spine` 数的是 `events[]` 的下标——留一个空串在里面，主干就指向另一件事。
    `_resolve_beats` 那句 `bidx[b].get('event') or ''` 产出的正是这个必然被拒的值：
    它不是兜底，是把上游违约原样端上来让这道闸拒掉。
    """
    with pytest.raises(ValueError, match='空条目'):
        CtxBeat.model_validate({
            'slot': 's01', 'events': ['甲。', ''], 'budget_chars': 80, 'source_chars': 40,
            'time_layer': 'present', 'place': '空院', 'present': []})


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
    `CtxBeat` 的六个可选键会在**每一拍上**各报一处「只在盘上有：None」——
    实测一本 530 处，而组装器一个字都没错。
    ⚠ 那种红最坏的地方是**它长得和真回归一模一样**：一列「只在盘上有」，
    看不出是形态差还是丢字段。⇒ 过一次 `WriterContext` 把两边归到同一形态，
    差异重新变成「真差异」。`_diffs` 那条「不做宽松相等」的纪律**不放松**，
    放松的是「拿两种形态直接比」这个前提错误。
    """
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid])
        mine = _through_the_carrier(asm.ctx, CTX[sid]['_provenance'])
        disk = WriterContext.model_validate(CTX[sid]).model_dump(
            by_alias=True, mode='json')
        d = _diffs(mine, disk)
        assert not d, f'{sid} 重组与盘上不一致（{len(d)} 处）：\n  ' + '\n  '.join(d[:8])


@needs_corpus
def test_the_rebuilt_context_passes_the_carrier():
    """重组出来的那份要**过得了新契约**——差异 0 只说明与盘上一样，不说明它合法。"""
    prov = {'code': '0' * 10, 'root_clean_sha1': '0' * 40,
            'inputs': {'stage4/plan_chapters.json': {'file': '1' * 10, 'consumed': '2' * 10}}}
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid])
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
                   'dropped_hinges', 'held_rules', 'setting_cut', 'setting_emptied')}
    assert set(containers.values()) <= {dict, list, set}, \
        f'副产物出现了非容器形状：{containers}'
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid])
        for name, origin in containers.items():
            got = getattr(asm, name)
            assert isinstance(got, origin), \
                f'{sid} 的 {name} 是 {type(got).__name__}，而 `Assembly` 声明的是 {origin.__name__}'


@needs_corpus
def test_the_setting_gate_returns_structured_results():
    for sid in BOOKS:
        asm = build_writer_context(PLANS[sid], GG[sid], BPS[sid], MAIN[sid])
        assert isinstance(asm.setting_cut, list)
        assert isinstance(asm.setting_emptied, list)


def test_the_setting_gate_uses_the_same_number_ruler_as_secret_keywords():
    """原样转抄含「一直 / 三」时也必须被从写手的 setting 中剪掉。"""
    fact = '她其实一直都知道他是三皇子'
    setting = {'暗线': fact + '。这里另有一条无关规则。'}
    out, cut = _gate_setting(
        setting, [{'secret_id': 'secret_x', 'fact': fact}], ['secret_x'])
    assert out['暗线'] == '这里另有一条无关规则。'
    assert cut == [('暗线', fact + '。')]


# ── 二·四、关系截止章的包含式边界（`R-2.8`）──────────────────────────────

def test_relation_valid_through_chapter_includes_the_named_chapter_only():
    grounding = {
        'entities': {
            'characters': {},
            'relations': [{
                'relation_id': 'rel_01',
                'subject_id': 'char_001',
                'predicate': '雇佣',
                'object_id': 'char_002',
                'valid_from_chapter': 1,
                'valid_through_chapter': 2,
                'visibility': {'kind': 'public', 'from_chapter': 1},
            }],
        },
        'canon': {},
        'arc': {'conflict_stages': [{
            'stage': '共事阶段',
            'chapters': [1, 2, 3],
        }]},
    }
    display = {'char_001': '沈青', 'char_002': '周衡'}

    at_boundary = _project_facts(grounding, 2, False, display)[0]
    after_boundary = _project_facts(grounding, 3, False, display)[0]

    assert at_boundary['relations'] == [{
        'subject': '沈青', 'predicate': '雇佣', 'object': '周衡'}]
    assert after_boundary['relations'] == []


# ── 二·五、secret 的下发时点（`R-2.18`）───────────────────────────────────

def _split_secrets_grounding() -> dict:
    """一个复合真相拆成两条、`group_id` 归组的合成底座。

    这正是盘上那本书 `secret_01` 的形状：穿女装的行为在 ch4 点破，而「为什么」
    要到 ch7 才点破。v1 只能把两件事写进一条 `fact`，于是 ch4 的写手整份都拿到。
    """
    return {
        'entities': {'characters': {
            'char_001': {'canonical': '江尽夏', 'role': '练习生',
                         'address': [{'surface': '江尽夏', 'by': 'narration',
                                      'context': 'reference', 'from_chapter': 1}]},
        }, 'relations': []},
        'canon': {
            'setting': {'时代背景': '当代', '世界观': '偶像公寓'},
            'world_rules': [],
            'secrets': [
                {'secret_id': 'secret_01', 'group_id': 'sgrp_01',
                 'fact': '江尽夏私下会穿女装打扮自己',
                 'knowledge_events': [{
                     'subject': {'kind': 'character', 'char_id': 'char_001'},
                     'known_from_chapter': 1,
                     'evidence': {'chapter_ordinal': 1, 'source': None},
                 }],
                 'setup': {'kind': 'foreshadowed', 'chapters': [2]},
                 'reader_reveal_events': [{'chapter': 4, 'kind': 'first'},
                                          {'chapter': 9, 'kind': 'confirm'}]},
                {'secret_id': 'secret_02', 'group_id': 'sgrp_01',
                 'fact': '他这么做是因为中学时穿裙子被舅舅剃了头',
                 'knowledge_events': [{
                     'subject': {'kind': 'character', 'char_id': 'char_001'},
                     'known_from_chapter': 1,
                     'evidence': {'chapter_ordinal': 1, 'source': None},
                 }],
                 'setup': {'kind': 'cold'},
                 'reader_reveal_events': [{'chapter': 7, 'kind': 'first'}]},
            ],
        },
        # ⚠ 阶段必须逐章覆盖全书（`R-2.14`）：`_project_facts` 对定不出章级阶段的
        #   情形是显式终态，不猜也不省略 ⇒ 这里给满 1~9 章，否则测的就成了那条闸。
        'arc': {'conflict_stages': [{'stage': '藏', 'chapters': [1, 2, 3, 4, 5]},
                                    {'stage': '认', 'chapters': [6, 7, 8, 9]}],
                'climax': '摊牌', 'ending': '和解', 'arc_tracks': []},
    }


def _explicit_visibility_grounding() -> dict:
    """显式 relation visibility 与 typed knowledge event 的合成底座。"""
    grounding = _split_secrets_grounding()
    grounding['entities']['characters']['char_002'] = {
        'canonical': '周衡',
        'role': '室友',
        'address': [{'surface': '周衡', 'by': 'narration',
                     'context': 'reference', 'from_chapter': 1}],
    }
    # 两端名字都在 fact 且关系在隐瞒窗口里，旧启发式会把 rel_public 推到 ch4；
    # 显式 public 却声明 ch1 可见。实际 gate 必须取 max(valid_from=2, public=1) = 2。
    grounding['canon']['secrets'][0]['fact'] = '江尽夏与周衡其实是恋人'
    grounding['entities']['relations'] = [
        {
            'relation_id': 'rel_public',
            'subject_id': 'char_001',
            'predicate': '室友',
            'object_id': 'char_002',
            'valid_from_chapter': 2,
            'valid_through_chapter': 5,
            'visibility': {'kind': 'public', 'from_chapter': 1},
        },
        {
            'relation_id': 'rel_via_secret',
            'subject_id': 'char_002',
            'predicate': '恋人',
            'object_id': 'char_001',
            'valid_from_chapter': 3,
            'valid_through_chapter': 5,
            'visibility': {'kind': 'via_secret', 'secret_id': 'secret_01'},
        },
    ]
    grounding['canon']['secrets'].append({
        'secret_id': 'secret_03',
        'fact': '公寓里的人会轮流替江尽夏保管钥匙',
        'setup': {'kind': 'cold'},
        'reader_reveal_events': [{'chapter': 1, 'kind': 'first'}],
        'knowledge_events': [
            {
                'subject': {'kind': 'character', 'char_id': 'char_001'},
                'known_from_chapter': 4,
                'evidence': {'chapter': 4, 'locator': '江尽夏收下钥匙'},
            },
            {
                'subject': {'kind': 'group', 'label': '公寓众人'},
                'known_from_chapter': 4,
                'evidence': {'chapter': 4, 'locator': '众人都点了头'},
            },
        ],
    })
    return grounding


def test_explicit_public_and_via_secret_gates_override_the_old_heuristic():
    """显式声明是生产权威；旧启发式仍能复算，但只供 INFO 对账。"""
    grounding = _explicit_visibility_grounding()
    display = {'char_001': '江尽夏', 'char_002': '周衡'}

    inferred, vanished = heuristic_relation_gates(
        grounding['entities'], grounding['canon']['secrets'])
    assert inferred['rel_public'] == 4, '合成样本必须制造显式值与旧启发式的真实分歧'
    assert inferred['rel_via_secret'] == 4
    assert vanished == []

    chapter2 = _project_facts(grounding, 2, False, display)[0]['relations']
    chapter3 = _project_facts(grounding, 3, False, display)[0]['relations']
    chapter4 = _project_facts(grounding, 4, False, display)[0]['relations']

    assert [row['predicate'] for row in chapter2] == ['室友'], \
        'public gate=max(valid_from=2, from=1)，不能被旧启发式推到 ch4'
    assert [row['predicate'] for row in chapter3] == ['室友']
    assert [row['predicate'] for row in chapter4] == ['室友', '恋人'], \
        'via_secret gate=max(valid_from=3, secret 首次揭示=4)'


def test_explicit_relation_valid_through_boundary_is_inclusive_for_both_arms():
    grounding = _explicit_visibility_grounding()
    display = {'char_001': '江尽夏', 'char_002': '周衡'}

    at_boundary = _project_facts(grounding, 5, False, display)[0]['relations']
    after_boundary = _project_facts(grounding, 6, False, display)[0]['relations']

    assert [row['predicate'] for row in at_boundary] == ['室友', '恋人']
    assert after_boundary == []


def test_typed_knowledge_events_open_on_the_named_chapter_for_character_and_group():
    grounding = _explicit_visibility_grounding()
    display = {'char_001': '江尽夏', 'char_002': '周衡'}

    before = _project_facts(grounding, 3, False, display)[0]['secrets']
    on_chapter = _project_facts(grounding, 4, False, display)[0]['secrets']
    before_row = next(row for row in before if row['secret_id'] == 'secret_03')
    on_row = next(row for row in on_chapter if row['secret_id'] == 'secret_03')

    assert before_row['known_by'] == []
    assert on_row['known_by'] == ['江尽夏', '公寓众人']
    assert 'evidence' not in on_row, '上游回取证据不能进入写手上下文'


def test_writer_context_excludes_every_global_source_span_byte():
    """writer_context 是 Stage5/6 付费材料，正式证据须在组装入口剥掉。"""
    import json

    from novel.core.evidence import iter_global_evidence_slots
    from tests.test_stage2 import blueprints, global_grounding, main_text

    global_g = global_grounding()
    markers: list[str] = []
    coordinates: list[int] = []
    for index, (_claim_path, owner, field) in enumerate(
        iter_global_evidence_slots(global_g), 1,
    ):
        marker = f'CONTEXT_EVIDENCE_ONLY_{index}'
        start = 923_456_700 + index * 100
        owner[field] = {
            'chapter_ordinal': 1,
            'source': {
                'text': marker,
                'start': start,
                'end': start + len(marker),
                'normalized': f'CONTEXT_NORMALIZED_ONLY_{index}',
                'recovery': 'normalized',
            },
        }
        markers.extend((marker, f'CONTEXT_NORMALIZED_ONLY_{index}'))
        coordinates.extend((start, start + len(marker)))

    goal = {'intent': 'advance', 'fact_refs': [{
        'kind': 'chapter_boundary', 'ref': 'chapter:3', 'chapter': 3,
    }]}
    plan_raw = {'output_chapters': [{
        'output_ordinal': 3,
        'from_source_ordinals': [3],
        'chapter_goal': goal,
        'open_hook_goal': goal,
        'end_hook_goal': goal,
        'segments': [{
            'slot': 's01',
            'from_beats': [f'b03_{index:02d}' for index in range(1, 7)],
            'budget_chars': 900,
            'narrative_role': 'setup',
            'delivery': 'scene',
            'transition': 'start',
            'spine': 1,
        }],
    }]}
    assembled = build_writer_context(
        plan_raw, global_g, blueprints(), main_text()).ctx
    paid_context = json.dumps(assembled, ensure_ascii=False, sort_keys=True)

    assert markers and all(marker not in paid_context for marker in markers)
    assert all(str(coordinate) not in paid_context for coordinate in coordinates), \
        'SourceSpan 的 start/end 坐标进了 writer_context'


@pytest.mark.parametrize(('ordinal', 'visible', 'hidden'), [
    # ch2 是 secret_01 的埋点章 ⇒ 事实要摆出来（但 reader_knows_by_now=False）
    (2, ['secret_01'], ['secret_02']),
    (4, ['secret_01'], ['secret_02']),
    (6, ['secret_01'], ['secret_02']),
    (7, ['secret_01', 'secret_02'], []),
])
def test_a_split_secret_group_is_released_per_member_not_per_group(
    ordinal: int, visible: list[str], hidden: list[str],
):
    """⭐ `R-2.18`：拆开的两条各按**自己的** `first` 章下发，不按 `group_id` 拉平。

    这是 v2 那次改动真正修掉的东西。v1 里这两件事只能写进一条 `fact`，
    而闸门按「任意一次揭示 ≤ 本章」判 ⇒ 写 ch4 的写手连 ch7 才点破的成因一起拿到。
    ⚠ 如果哪天有人「顺手」按 group 下发（同组一条揭示就全放），本条会红——
    那正是把刚拆开的两件事又粘回去。
    """
    facts, must_not_reveal, _held, _cut = _project_facts(
        _split_secrets_grounding(), ordinal, False, {'char_001': '江尽夏'})
    got = [row['secret_id'] for row in facts['secrets']]
    assert [sid for sid in got if sid in visible] == visible
    assert sorted(must_not_reveal) == sorted(hidden)
    # 已下发的那些必须同时说清「读者到本章知不知道」——ch2 是埋点章的那一条例外。
    for row in facts['secrets']:
        assert isinstance(row['reader_knows_by_now'], bool)


def test_a_setup_chapter_releases_the_fact_but_marks_the_reader_as_unaware():
    """埋点章要把事实摆出来，但 `reader_knows_by_now` 必须是 `False`。

    没有这一格，写 ch2 那件事时不知道它后面会被回收，ch4 的点破就没有铺垫。
    """
    facts, must_not_reveal, _held, _cut = _project_facts(
        _split_secrets_grounding(), 2, False, {'char_001': '江尽夏'})
    row = next(r for r in facts['secrets'] if r['secret_id'] == 'secret_01')
    assert row['reader_knows_by_now'] is False, '埋点章不是「读者已经知道」'
    assert 'secret_02' in must_not_reveal, '另一条既没埋点也没揭示 ⇒ 只能留 id'


def test_a_confirm_event_alone_never_opens_the_gate():
    """⚠ `confirm` 不参与「读者知道了没」。

    把 `first` 那条去掉、只留 `confirm`，闸门必须**关着**——旧式子
    `any(chapter <= o)` 在这里会开闸，而那正是被换掉的判定。
    ⚠ 这个形状载体是拒收的（恰一个 `first`）；这里直接喂 `_project_facts` 的
    dict，测的是**闸门自己**在拿到坏形状时倒向安全的一边，不是测载体。
    """
    grounding = _split_secrets_grounding()
    grounding['canon']['secrets'] = [{
        'secret_id': 'secret_01', 'group_id': None, 'fact': '只有后续确认的坏形状',
        'character_known_by': ['char_001'], 'setup': {'kind': 'cold'},
        'reader_reveal_events': [{'chapter': 4, 'kind': 'confirm'}],
    }]
    facts, must_not_reveal, _held, _cut = _project_facts(
        grounding, 9, False, {'char_001': '江尽夏'})
    assert facts['secrets'] == []
    assert must_not_reveal == ['secret_01']


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
                             [{'chapter_ordinal': 1, 'text': '街上人来人往。'}])


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

def _minimal(canon: dict, arc: dict | None = None) -> tuple[dict, dict, list]:
    chars = {'char_001': {'canonical': '阿禾', 'address': [{'surface': '阿禾'}]}}
    gg = {'entities': {'characters': chars}, 'canon': canon, 'arc': arc or {}}
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
        build_writer_context({'output_chapters': []}, gg, bps, mt)


def test_an_arc_subject_that_is_not_human_readable_is_a_terminal_state():
    """实测某本 ch23 有 **7 处**内部编号原样发给写手：`char_001`×3 / `rel_05` / `rel_09`…"""
    gg, bps, mt = _minimal({}, {'arc_tracks': [{'subject': 'rel_99'}]})
    with pytest.raises(RefusedByStructure, match='解析不出人话'):
        build_writer_context({'output_chapters': []}, gg, bps, mt)


# ── 五、冲突阶段的章覆盖 ──────────────────────────────────────────────────

def _elided_plan(ordinal: int = 4) -> dict:
    """一个只够走通组装的最小输出章：三个 goal 都用可零成本解析的 chapter_boundary。"""
    goal = {'intent': 'advance', 'fact_refs': [
        {'kind': 'chapter_boundary', 'ref': f'ch{ordinal}:start', 'chapter': ordinal}]}
    return {'output_chapters': [{
        'output_ordinal': ordinal, 'from_source_ordinals': [ordinal],
        'chapter_goal': goal, 'open_hook_goal': goal, 'end_hook_goal': goal,
        'segments': [],
    }]}


def _elided_inputs(ordinal: int = 4) -> tuple[dict, dict, list]:
    chars = {'char_001': {'canonical': '阿禾', 'address': [{'surface': '阿禾'}]}}
    gg = {'entities': {'characters': chars}, 'canon': {}, 'arc': {}}
    mt = [{'chapter_ordinal': ordinal, 'text': '阿禾走了进来。'}]
    return gg, {'blueprints': []}, mt


def test_a_chapter_inside_an_elided_stage_span_still_gets_its_current_stage():
    """⭐ 阶段章列表省写中间章（实测 flash 产出 [3,5]/[8,10]/[13,15]）时按区间解释。

    旧代码只认精确成员，段内空隙的那章组出**空的 arc**——`CtxArc.current_stage`
    是必填字段，`WriterContext.model_validate` 在落盘事务里裸抛 exit 70（结论未知）。
    区间包含唯一可解 = 读出写段的本意，不发明归属；`chapters` 按记录原样透传，不补写。
    """
    gg, bps, mt = _elided_inputs()
    gg['arc'] = {'conflict_stages': [
        {'stage': '前段', 'chapters': [1, 2]}, {'stage': '中段', 'chapters': [3, 5]}]}
    asm = build_writer_context(_elided_plan(), gg, bps, mt)
    arc = asm.ctx['chapters'][0]['facts']['arc']
    assert arc['current_stage'] == {'stage': '中段', 'chapters': [3, 5]}, \
        '第 4 章落在 [3,5] 的段内空隙，应按区间归入该段；跨度按记录原样透传'


def test_a_chapter_in_a_true_stage_gap_or_overlap_is_a_terminal_state():
    """跨阶段的真空隙（0 个包含）与双包含（≥2 个）都定不出唯一归属 ⇒ 显式终态。

    current_stage 是写手必拿的章级事实：省略它与猜一个都是静默造假；
    修复面在产地（stage2 的阶段覆盖），本层只负责不说谎。
    """
    gg, bps, mt = _elided_inputs()
    gg['arc'] = {'conflict_stages': [{'stage': '前段', 'chapters': [1, 2]},
                                     {'stage': '后段', 'chapters': [6, 7]}]}
    with pytest.raises(RefusedByStructure, match='唯一归属'):
        build_writer_context(_elided_plan(), gg, bps, mt)

    gg['arc'] = {'conflict_stages': [{'stage': '段甲', 'chapters': [3, 5]},
                                     {'stage': '段乙', 'chapters': [2, 6]}]}
    with pytest.raises(RefusedByStructure, match='唯一归属'):
        build_writer_context(_elided_plan(), gg, bps, mt)


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
