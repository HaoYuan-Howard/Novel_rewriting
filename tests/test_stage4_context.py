#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`writer_context` 当前契约的确定性组装与结构守卫。"""
from __future__ import annotations

from functools import wraps
from typing import get_origin, get_type_hints

import pytest

from contracts.schema.stage4 import CtxBeat, WriterContext
from novel.errors import RefusedByStructure
from novel.stages.stage4.context import (
    Assembly, _gate_setting, build_writer_context, display_names, display_names_at,
    name_collisions, rename_all,
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
