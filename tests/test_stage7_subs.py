#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage7_subs.py —— 替换器与残留探针。**不吃语料，纯函数。**

【为什么单独一个文件】`tests/test_stage7.py` 整份挂着「盘上有没有这本书」的 skipif
（形态照 `tests/test_stage6.py`），而本文件这一族**在任何机器上都该跑**——
替换器是本层唯一动正文字节的东西，它不该因为语料不在就没人查。

⚠ 本文件盯的是那**三处方向各不相同的 count**（`subs.py` 模块头那张表）。
每一处弄错都不会报错，只会让某个数字静默地变成另一个意思。
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from contracts.schema import decode_artifact
from contracts.schema.stage7 import ExtraSub, NameMapEdit, Residual
from novel.errors import BlockingFindingsUnresolved, TerminalState
from novel.stages.stage7 import checks
from novel.stages.stage7 import rename
from novel.stages.stage7.subs import (
    apply_subs, mask_known, residual_scan, sub_table, surface_hits,
)

# 「阿禾」⊂「阿禾姊姊」· 「青禾」⊂「孟青禾」——嵌套是本层全部风险的来源。
CH1 = '阿禾姊姊回来了。孟青禾站在门口，青禾轻轻应了一声，阿禾没有回头。'


def test_edit_template_is_a_registry_encoded_strict_v1_artifact():
    raw = json.loads(rename.edit_template_json())
    assert raw['_schema_version'] == 1
    assert set(raw) == {
        '_schema_version', 'surname_map', 'characters',
        'character_patches', 'extra_subs',
    }
    model = NameMapEdit.model_validate(
        decode_artifact(rename.EDIT, raw).payload)
    assert model == NameMapEdit()


def test_generic_appellation_wrapper_may_keep_label_and_replace_embedded_name():
    """真实 E2E：`女主方衡→女主沈凌` 只改真名，不是把「女主」关系改掉。"""
    subs = {'女主方衡': '女主沈凌', '方衡': '沈凌'}
    assert not checks._generic_appellation_was_touched(
        '女主方衡', '女主沈凌', subs, {'方'}, set())
    assert checks._generic_appellation_was_touched(
        '姐姐', '妹妹', {'姐姐': '妹妹'}, set(), set())
    assert checks._generic_appellation_was_touched(
        '女主方衡', '女主沈凌', {'女主方衡': '女主沈凌'}, {'方'}, set()), \
        '没有内嵌真名映射作证时仍要保守拦截'


# ── ①b 「名」不是关系词：`rn_kinship_touched` 的第二条豁免 ─────────────────

#: 一真一诱饵。`周子白` 的名是 `子白`，而 `子` 在亲属字表里（儿子）；
#: `陈娘子` 去姓之后**正好**是 `娘子` —— 本条要守的东西。
_KINSHIP_CHARS = [
    {'char_id': 'char_001', 'old_canonical': '周子白', 'new_canonical': '顾清宴',
     'renamed': True, 'aliases': [{'old': '子白', 'new': '清宴'}]},
    {'char_id': 'char_002', 'old_canonical': '陈娘子', 'new_canonical': '林娘子',
     'renamed': True, 'aliases': []},
]
_KINSHIP_SURNAMES = {'周', '陈'}


def test_the_given_name_half_of_a_renamed_canonical_is_not_a_kinship_word():
    """⭐ `周子白→顾清宴` 时 `子白→清宴` 是**名**的那一半，不是关系词。

    姓氏豁免只看首字（`周` 在映射里、`子` 不在），够不着这一形。按本条去修数据，
    唯一走法是把 `子白` 登记成不改——**那会让原作的名活着进入「原创」成品**。
    """
    halves = checks.given_name_halves(_KINSHIP_CHARS, _KINSHIP_SURNAMES)
    subs = {'周子白': '顾清宴', '子白': '清宴', '陈娘子': '林娘子'}
    assert ('子白', '清宴') in halves
    assert not checks._generic_appellation_was_touched(
        '子白', '清宴', subs, _KINSHIP_SURNAMES, halves)


def test_the_exemption_is_a_pair_so_a_relationship_word_still_cannot_slip_through():
    """⭐⭐ **豁免按 `(旧, 新)` 配对，不按旧名单边。**

    只按旧名豁免的话，`陈娘子→林娘子` 会顺带把 `娘子` 放进白名单，
    于是任何一条 `娘子→X` 都不再被拦——而 `娘子` 正是本条要守的东西。
    """
    halves = checks.given_name_halves(_KINSHIP_CHARS, _KINSHIP_SURNAMES)
    subs = {'陈娘子': '林娘子', '娘子': '夫人'}
    assert ('娘子', '娘子') in halves, '配对里确实有它，但两边相同'
    assert checks._generic_appellation_was_touched(
        '娘子', '夫人', subs, _KINSHIP_SURNAMES, halves), \
        '把关系词换成另一个关系词仍然违规'
    assert checks._generic_appellation_was_touched(
        '大娘', '苏娘', subs, _KINSHIP_SURNAMES, halves), \
        '排行式称呼没有任何 canonical 供它派生 ⇒ 照旧被拦'


def test_a_character_that_is_not_renamed_contributes_no_exemption():
    """不改名的角色不该给任何映射发豁免——那正是幽灵映射的入口形状。"""
    chars = [{'char_id': 'char_001', 'old_canonical': '周子白', 'new_canonical': '周子白',
              'renamed': False, 'aliases': []}]
    assert checks.given_name_halves(chars, _KINSHIP_SURNAMES) == set()


# ── ①c 姓可以只由别名承载：`rn_surname_split` 的 orphan 那一路 ─────────────

def _split_detail(characters: list[dict], surname_map: dict, subs: dict) -> str | None:
    """只跑映射表自身那几条判据；替换器侧入参全给空。"""
    fs = checks.rename_findings(
        name_map={'surname_map': surname_map, 'characters': characters,
                  'extra_subs': [], 'subs': subs, 'kept': []},
        subs=subs, problems=[], applied={'chapters': {}}, residual=[], baseline={},
        registry={c['char_id']: {} for c in characters},
        pre_target='', source_text='', residual_keys=set(), stem_noise={},
        family_members={}, dup=[], missing=[], invalids=[], post_units=[],
        title_line='书名语义需人工确认')
    return next((f.detail for f in fs if f.rule == 'rn_surname_split'), None)


def test_a_surname_carried_only_by_an_alias_is_not_an_orphan_mapping():
    """⭐ `宝珠→林琬` 的 canonical 里没有姓，家姓只出现在别名 `陈娘子→林娘子` 上。

    只认 canonical 会把这条**真在用**的映射报成幽灵，而唯一的「修法」是去改一个
    正文里出现 0 次的登记标签（`陈母`/`陈父`）—— 那是判据错。
    """
    assert _split_detail(
        [{'char_id': 'char_001', 'old_canonical': '宝珠', 'new_canonical': '林琬',
          'renamed': True, 'aliases': [{'old': '陈娘子', 'new': '林娘子'},
                                       {'old': '小姐', 'new': '小姐'}]}],
        {'陈': '林'}, {'宝珠': '林琬', '陈娘子': '林娘子'}) is None


def test_a_mapping_no_one_uses_at_all_is_still_reported():
    """⭐⭐ 反例守卫：实测 `太后`/`太傅` 误组留下的 `太→宋`。

    两个角色都明确**不改名** ⇒ 既没有 canonical 也没有别名在用这个姓，
    这条幽灵映射此前能让全部 HARD 打绿。别名那一路不许把它放过去。
    """
    detail = _split_detail(
        [{'char_id': 'char_001', 'old_canonical': '太后', 'new_canonical': '太后',
          'renamed': False, 'aliases': [{'old': '太后', 'new': '太后'}]},
         {'char_id': 'char_002', 'old_canonical': '太傅', 'new_canonical': '太傅',
          'renamed': False, 'aliases': []}],
        {'太': '宋'}, {})
    assert detail is not None and '太→宋' in detail


def test_an_identity_alias_does_not_certify_a_surname_mapping():
    """别名两侧一字未改时不算「用过这个姓」——否则恒等条目会给任何映射背书。"""
    detail = _split_detail(
        [{'char_id': 'char_001', 'old_canonical': '宝珠', 'new_canonical': '林琬',
          'renamed': True, 'aliases': [{'old': '陈娘子', 'new': '陈娘子'}]}],
        {'陈': '陈'}, {'宝珠': '林琬'})
    assert detail is not None and '陈→陈' in detail


def test_rename_semantic_marker_finding_catches_rank_change():
    kwargs = dict(
        name_map={'characters': [], 'surname_map': {}},
        problems=[], applied={}, residual=[], baseline={}, registry={},
        pre_target='', source_text='', residual_keys=set(), stem_noise={},
        family_members={}, dup=[], missing=[], invalids=[],
        post_units=[], title_line='书名语义需人工确认',
    )
    changed = checks.rename_findings(subs={'七郎': '六郎'}, **kwargs)
    kept = checks.rename_findings(subs={'阿九': '玄九'}, **kwargs)
    assert 'rn_semantic_marker_changed' in {finding.rule for finding in changed}
    assert 'rn_semantic_marker_changed' not in {finding.rule for finding in kept}


# ⚠ `test_custom_edit_path_is_resolved_inside_the_novel_directory` 曾在这里，
# 直接测 `rename._edit_rel`（全仓唯一一条），只覆盖「书内绝对路径 / 目录外绝对路径」两类。
# 四份 `_edit_rel` 收敛成 `novel/paths.py::edit_rel` 之后，它的覆盖面并进
# `tests/test_paths.py` §五「四层 × 五类路径」——**只增不减**：
# 新增了相对路径按书目录解释（旧实现按 cwd，那正是 FN-016 记着的那条 bug）与 `..` 穿越两类，
# 且四层各跑一遍而不只是 stage7。


# ── ① 最长优先与恒等屏蔽 ───────────────────────────────────────────────────

def test_longest_first_is_by_sort_not_by_regex():
    """Python 的 alternation 是 **leftmost-first**，不是最长匹配。

    短的排前面会切出半截残串——`sub_table` 靠 `sorted(key=len, reverse=True)` 兜这件事。
    """
    pat, table = sub_table({'青禾': '晚棠', '孟青禾': '苏晚棠'})
    assert pat.findall('孟青禾') == ['孟青禾'], '短项抢走了长项的字节'
    assert table['孟青禾'] == '苏晚棠'


def test_kept_surface_shields_the_shorter_mapped_one():
    """⭐ **第一道防线是结构性的**：保留项进 alternation 当恒等条目。

    「阿禾」要换、「阿禾姊姊」要留 —— 不把后者编进正则，单次扫描会把它切成「阿舒姊姊」。
    """
    out, counts, _ = apply_subs(CH1, {'阿禾': '阿舒'}, kept=['阿禾姊姊'])
    assert '阿禾姊姊' in out, '保留项被更短的映射项切开了'
    assert out.count('阿舒') == 1 and counts['阿禾'] == 1


def test_no_chaining_a_to_b_to_c():
    """`A→B` 与 `B→C` 一起跑，A **不许**变成 C（`R-10.2`：级联在结构上不可表示）。"""
    out, _counts, _ = apply_subs('甲乙', {'甲': '乙', '乙': '丙'})
    assert out == '乙丙', '游标回头了 —— 这就是链式 replace 的形状'


def test_empty_subs_is_identity():
    assert apply_subs('原样', {}) == ('原样', {}, [])
    assert apply_subs('', {'甲': '乙'}) == ('', {}, [])


def test_claimed_covers_identity_entries_too():
    """⭐ `claimed` **必须含恒等条目认领的那一半**。

    原型最初只记「被替换的」那一半，**被测试当场抓住**：保留串里的子串
    会被残留判据算成残留，而那不是残留、是那条保留决定的直接后果。
    """
    out, _counts, claimed = apply_subs('阿禾姊姊', {'阿禾': '阿舒'}, kept=['阿禾姊姊'])
    assert out == '阿禾姊姊'
    assert claimed == [(0, 4)], '恒等条目没有认领它自己那一段'
    # 区间是**输出**坐标系的：换成更长的新名之后，后一段要相应右移
    _out2, _c2, claimed2 = apply_subs('阿禾和阿禾', {'阿禾': '苏阿舒'})
    assert claimed2 == [(0, 3), (4, 7)]


# ── ② 下发面的计数：绝不 `str.count` ──────────────────────────────────────

def test_surface_hits_are_scan_hits_not_str_count():
    """⭐⭐ **这个数下发给模型**，虚高会让它按错的轻重去分配注意力。

    实测某本「阿禾」`count`=4 而独立命中只有 1（另外 3 次在「阿禾姊姊」里）。
    """
    universe = {'阿禾姊姊', '阿禾', '孟青禾', '青禾'}
    hits = surface_hits(universe, [CH1])
    assert CH1.count('阿禾') == 2 and hits['阿禾'] == 1
    assert CH1.count('青禾') == 2 and hits['青禾'] == 1
    assert hits['阿禾姊姊'] == 1 and hits['孟青禾'] == 1
    assert surface_hits(set(), [CH1]) == {}


# ── ③ 残留探针：分母【故意】含嵌套 ────────────────────────────────────────

def test_residual_total_is_measured_before_and_keeps_nesting():
    """⭐ 分母量在 **pre**，而且**故意**用 `str.count`（单字姓字，嵌套本来就该算）。

    量在 post 上，改名之后旧姓字所剩无几，覆盖率会恒等于 0 —— 这个比率就不携带信息。
    """
    pre = [('ch1', '孟青禾和孟青兰在孟家。')]
    post = [('ch1', '苏晚棠和苏晚兰在孟家。')]
    residual, base = residual_scan(pre, post, {'孟'}, {'苏晚棠', '苏晚兰'})
    assert base['孟']['total'] == 3, '三处「孟」，含被替换掉的那两处'
    assert base['孟']['residual'] == 1 and base['孟']['covered'] == 2
    assert base['孟']['ratio'] == 0.67
    assert [r['hit'] for r in residual] == ['孟家。']
    assert residual[0]['confidence'] == 'high', '`<姓>家` 是结构信号'
    assert residual[0]['unit'] == '孟家' and residual[0]['suggest'] is None


def test_residual_high_uses_longest_appellation_suffix_and_split_canonical():
    residual, _base = residual_scan(
        [('ch1', '温兄温公子与汤临')],
        [('ch1', '温兄、温公子见到汤、临')],
        {'温', '汤'},
        set(),
        {'温': '顾', '汤': '陆'},
        {'汤临': '陆景'},
    )
    by_unit = {row['unit']: row for row in residual}
    assert by_unit['温兄']['confidence'] == 'high'
    assert by_unit['温兄']['suggest'] == '顾兄'
    assert by_unit['温公子']['confidence'] == 'high'
    assert by_unit['温公子']['suggest'] == '顾公子'
    assert by_unit['汤、临']['confidence'] == 'high'
    assert by_unit['汤、临']['suggest'] == '陆、景'


def test_residual_split_canonical_accepts_any_gap_in_a_three_character_name():
    residual, _base = residual_scan(
        [('ch1', '温夏白温夏白温夏白')],
        [('ch1', '温夏、白，温、夏白，温、夏 白')],
        {'温'},
        set(),
        {'温': '顾'},
        {'温夏白': '顾清禾'},
    )
    by_unit = {row['unit']: row for row in residual}
    assert by_unit['温夏、白']['suggest'] == '顾清、禾'
    assert by_unit['温、夏白']['suggest'] == '顾、清禾'
    assert by_unit['温、夏 白']['suggest'] == '顾、清 禾'
    assert all(row['confidence'] == 'high' for row in by_unit.values())


def test_residual_common_words_stay_low():
    residual, _base = residual_scan(
        [('ch1', '温柔温度常常周围')],
        [('ch1', '温柔温度常常周围')],
        {'温', '常', '周'},
        set(),
        {'温': '顾', '常': '林', '周': '陆'},
    )
    assert residual
    assert all(row['confidence'] == 'low' for row in residual)


class _AdoptionStore:
    def __init__(self, art, *, fail_write=False):
        self.art = art
        self.writes = []
        self.archives = []
        self.events = []
        self.fail_write = fail_write

    def exists(self, rel):
        return rel == rename.REL

    def read_own(self, _model, rel):
        assert rel == rename.REL
        return self.art, b'original-map'

    def history_transaction(self, *_rels):
        return nullcontext()

    def archive_round(self, rel, *, keep):
        self.events.append('archive')
        self.archives.append((rel, keep))

    def write(self, rel, art):
        self.events.append('write')
        if self.fail_write:
            raise RuntimeError('injected write failure')
        self.writes.append((rel, art))

    def write_round(self, rel, art):
        self.archive_round(rel, keep=True)
        self.write(rel, art)


class _RebuiltMap(SimpleNamespace):
    def model_copy(self, *, update):
        values = vars(self) | update
        return _RebuiltMap(**values)


def _residual(*, unit, suggest, confidence='high'):
    return Residual(
        stem='温', where='ch1', hit='温兄。', context='温兄站在门外',
        confidence=confidence, unit=unit, suggest=suggest,
    )


def _adoption_harness(monkeypatch, residual, *, fail_write=False):
    art = SimpleNamespace(
        residual=residual,
        extra_subs=[ExtraSub(old='沈家', new='林家', origin='human')],
        subs={'周兄': '陆兄'}, surname_map={'温': '顾'}, characters=[],
        provenance='old-provenance', approved='old-approval',
        book_txt='old-delivery', identity='identity-baseline',
    )
    store = _AdoptionStore(art, fail_write=fail_write)
    captured = {}
    retired = []

    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(rename, '_Upstreams', lambda _ctx, _store: object())
    def retire(_store):
        store.events.append('retire')
        retired.append(True)

    monkeypatch.setattr(rename, '_retire_book_txt', retire)
    monkeypatch.setattr(rename, '_judge', lambda *_args: [])
    monkeypatch.setattr(rename, '_report', lambda *_args: 'report')

    def fake_build(_ctx, _up, model_out, extra, provenance):
        captured.update(model_out=model_out, extra=extra, provenance=provenance)
        return _RebuiltMap(
            approved='stale', book_txt='stale', identity=None,
        ), [], None

    monkeypatch.setattr(rename, '_build', fake_build)
    ctx = SimpleNamespace(
        rewrite_provenance=lambda provenance, *, by: (provenance, by),
    )
    return ctx, store, captured, retired


def test_adopt_residual_only_commits_unambiguous_uncovered_high_suggestions(monkeypatch):
    ctx, store, captured, retired = _adoption_harness(monkeypatch, [
        _residual(unit='温兄', suggest='顾兄'),
        _residual(unit='温柔', suggest='顾柔', confidence='low'),
        _residual(unit=None, suggest=None),
        _residual(unit='沈家', suggest='赵家'),       # already in extra_subs
        _residual(unit='周兄', suggest='赵兄'),       # already in derived subs
        _residual(unit='汤兄', suggest='陆兄'),       # conflicting suggestions
        _residual(unit='汤兄', suggest='赵兄'),
    ])

    rebuilt, report = rename.run_adopt_residual(ctx, 'high')

    assert report == 'report'
    assert captured['provenance'] == ('old-provenance', '--adopt-residual')
    assert captured['extra'] == [
        {'old': '沈家', 'new': '林家', 'origin': 'human'},
        {'old': '温兄', 'new': '顾兄', 'origin': 'human'},
    ]
    assert rebuilt.approved is None and rebuilt.book_txt is None
    assert rebuilt.identity == 'identity-baseline'
    assert retired == [True]
    assert store.events == ['archive', 'write', 'retire']
    assert store.archives == [(rename.REL, True)]
    assert store.writes == [(rename.REL, rebuilt)]


def test_adopt_residual_with_no_safe_candidate_is_a_no_write_terminal_state(monkeypatch):
    ctx, store, _captured, retired = _adoption_harness(monkeypatch, [
        _residual(unit='温柔', suggest='顾柔', confidence='low'),
        _residual(unit='沈家', suggest='赵家'),
        _residual(unit='汤兄', suggest='陆兄'),
        _residual(unit='汤兄', suggest='赵兄'),
    ])

    with pytest.raises(TerminalState, match='没有可安全采纳'):
        rename.run_adopt_residual(ctx, 'high')

    assert store.archives == [] and store.writes == [] and retired == []


def test_adopt_residual_blocked_by_hard_findings_is_a_terminal_state_not_a_crash(
        monkeypatch):
    """⭐ 采纳后判出 hard ⇒ **退出码 2 的预期内终态**，不是崩。

    这条路此前抛 `GateBlocked` **却漏了它必填的 `invalid_path`** ⇒ 真走到这里得到的是
    `TypeError`，退出码 70「崩了，结论未知」——编排层会据此把「这是我们的 bug」
    报给人，而实际发生的只是「这份采纳结果不合格，什么都没动」。
    ⚠ 分类也错：什么都没落盘，盘上根本没有 `.INVALID` 可指 ⇒ 不是 `GateBlocked`。
    """
    ctx, store, _captured, retired = _adoption_harness(
        monkeypatch, [_residual(unit='温兄', suggest='顾兄')])
    monkeypatch.setattr(
        rename, '_judge',
        lambda *_args: [SimpleNamespace(rule='rn_ambiguous', findings=['阿禾'])])

    with pytest.raises(BlockingFindingsUnresolved) as blocked:
        rename.run_adopt_residual(ctx, 'high')

    assert blocked.value.rules == ('rn_ambiguous',)
    assert store.archives == [] and store.writes == [] and retired == []
    assert store.art.book_txt == 'old-delivery'


def test_adopt_residual_write_failure_keeps_live_delivery(monkeypatch):
    ctx, store, _captured, retired = _adoption_harness(
        monkeypatch,
        [_residual(unit='温兄', suggest='顾兄')],
        fail_write=True,
    )

    with pytest.raises(RuntimeError, match='injected write failure'):
        rename.run_adopt_residual(ctx, 'high')

    assert store.events == ['archive', 'write']
    assert store.writes == [] and retired == []
    assert store.art.book_txt == 'old-delivery'


class _Dumpable(SimpleNamespace):
    def model_dump(self, **_kwargs):
        return dict(vars(self))


class _AtomicStore:
    def __init__(self, art, *, rels, edit=None, fail_write=False, fail_archive=False):
        self.art = art
        self.rels = set(rels)
        self.edit = edit or _Dumpable()
        self.fail_write = fail_write
        self.fail_archive = fail_archive
        self.events = []
        self.writes = []
        self.archives = []

    def exists(self, rel):
        return rel in self.rels

    def read_own(self, _model, rel):
        assert rel in (rename.REL, rename._INVALID)
        return self.art, b'old-map'

    def read(self, _model, rel):
        assert rel == rename.EDIT
        return self.edit

    def history_transaction(self, *_rels):
        return nullcontext()

    def archive_round(self, rel, *, keep):
        self.events.append('archive')
        if self.fail_archive:
            raise RuntimeError('injected archive failure')
        self.archives.append((rel, keep))

    def write(self, rel, art):
        self.events.append('write')
        if self.fail_write:
            raise RuntimeError('injected write failure')
        self.writes.append((rel, art))

    def write_round(self, rel, art):
        self.archive_round(rel, keep=True)
        self.write(rel, art)


def _atomic_harness(
        monkeypatch, *, rels, fail_write=False, fail_archive=False,
        blocking=False):
    subject = 'a' * 64
    art = _Dumpable(
        surname_map={'温': '顾'}, characters=[], extra_subs=[],
        provenance='old-provenance',
        approved=SimpleNamespace(subject_sha256=subject),
        book_txt='old-delivery', identity='identity-baseline',
    )
    store = _AtomicStore(
        art, rels=rels, edit=_Dumpable(character_patches=[]),
        fail_write=fail_write, fail_archive=fail_archive,
    )
    retired = []
    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(rename, '_Upstreams', lambda _ctx, _store: object())
    monkeypatch.setattr(rename, 'approval_subject_sha256', lambda _art: subject)
    monkeypatch.setattr(rename.paths, 'edit_rel', lambda *_args: rename.EDIT)
    monkeypatch.setattr(rename, 'merge_mapping_edit', lambda *_args: ({}, []))
    monkeypatch.setattr(rename, '_judge', lambda *_args: ['hard'] if blocking else [])
    monkeypatch.setattr(rename, '_blocking', lambda outcomes: bool(outcomes))
    monkeypatch.setattr(rename, '_report', lambda *_args: 'report')

    def fake_build(_ctx, _up, _model_out, _extra, _provenance):
        return _RebuiltMap(
            approved='stale', book_txt='stale', identity=None,
        ), [], None

    monkeypatch.setattr(rename, '_build', fake_build)

    def retire(_store):
        store.events.append('retire')
        retired.append(True)

    monkeypatch.setattr(rename, '_retire_book_txt', retire)
    ctx = SimpleNamespace(
        novel_dir='novel-dir',
        rewrite_provenance=lambda provenance, *, by: (provenance, by),
    )
    return ctx, store, retired


def test_recheck_write_failure_keeps_live_delivery(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL}, fail_write=True,
    )

    with pytest.raises(RuntimeError, match='injected write failure'):
        rename.run_recheck(ctx)

    assert store.events == ['archive', 'write']
    assert store.writes == [] and retired == []
    assert store.rels == {rename.REL} and store.art.book_txt == 'old-delivery'


def test_recheck_archive_failure_keeps_map_and_live_delivery(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL}, fail_archive=True,
    )

    with pytest.raises(RuntimeError, match='injected archive failure'):
        rename.run_recheck(ctx)

    assert store.events == ['archive']
    assert store.writes == [] and store.archives == [] and retired == []
    assert store.rels == {rename.REL} and store.art.book_txt == 'old-delivery'


def test_apply_edit_write_failure_keeps_live_delivery(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL, rename.EDIT}, fail_write=True,
    )

    with pytest.raises(RuntimeError, match='injected write failure'):
        rename.run_apply_edit(ctx)

    assert store.events == ['archive', 'write']
    assert store.writes == [] and retired == []
    assert store.rels == {rename.REL, rename.EDIT}
    assert store.art.book_txt == 'old-delivery'


def test_apply_edit_invalid_hard_keeps_live_delivery_and_invalid_map(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename._INVALID, rename.EDIT}, blocking=True,
    )

    rebuilt, report = rename.run_apply_edit(ctx)

    assert rebuilt.approved is None and rebuilt.book_txt is None
    assert report == 'report'
    assert store.events == []
    assert store.writes == [] and store.archives == [] and retired == []
    assert store.rels == {rename._INVALID, rename.EDIT}
    assert store.art.book_txt == 'old-delivery'


def test_residual_reports_its_baseline_even_with_nothing_left():
    """判据 31：**残留为 0 时也要输出这四个数。**

    没有分母，「residual 只有 4 条」读不出是探针管用还是探针瞎了。
    """
    residual, base = residual_scan([('ch1', '孟青禾')], [('ch1', '苏晚棠')],
                                   {'孟'}, {'苏晚棠'})
    assert residual == []
    assert base == {'孟': {'total': 1, 'covered': 1, 'residual': 0, 'ratio': 1.0}}


def test_a_stem_that_is_not_in_the_source_gets_a_null_ratio_not_zero():
    """⭐ `total == 0` ⇒ `ratio` 是 `null` **不是 `0.0`**（`Q-2` 的三态）。

    压成 0.0 会被读成「覆盖率 0%」——在这张**按覆盖率排序**的表上，
    一个原文里压根不存在的姓会被摆到「灾难性漏网」那一端。
    """
    _residual, base = residual_scan([('ch1', '苏晚棠')], [('ch1', '苏晚棠')],
                                    {'霍'}, {'苏晚棠'})
    assert base['霍'] == {'total': 0, 'covered': 0, 'residual': 0, 'ratio': None}


def test_residual_order_is_load_bearing():
    """顺序 = `(confidence != 'high', -ratio, stem, where)`，**是契约不是装饰**。

    设计口径是「人从上往下读到覆盖率掉下去为止」。
    """
    pre = [('ch1', '沈家沈家沈家沈佐'), ('ch2', '许久许久不允许')]
    post = [('ch1', '沈家沈家沈家林佐'), ('ch2', '许久许久不允许')]
    residual, base = residual_scan(pre, post, {'沈', '许'}, {'林佐'},
                                   surname_map={'沈': '林'})
    assert base['沈']['ratio'] == 0.25 and base['许']['ratio'] == 0.0
    # `沈` 覆盖率**更低**，而它排在最前——因为 `confidence` 是结构信号，压过比率
    assert [r['stem'] for r in residual][:3] == ['沈', '沈', '沈']
    assert residual[0]['confidence'] == 'high' and residual[0]['suggest'] == '林家'
    assert all(r['confidence'] == 'low' for r in residual if r['stem'] == '许')


def test_mask_known_is_length_preserving():
    """等长替换是为了让报出来的位置**还对得上原文**。"""
    masked = mask_known('孟青禾走了', {'孟青禾'})
    assert len(masked) == len('孟青禾走了') and '孟' not in masked
    assert mask_known('原样', set()) == '原样'
