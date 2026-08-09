#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_coverage.py —— 需求 §6 立的**双向覆盖率**的执行落点。

没有这个文件，`REQUIREMENTS.md` 的「谁在查」那一列就只是装饰——
而这个项目最贵的一类故障恰恰是「一条读起来和有守卫的约束一模一样、但没有任何代码在查」。
"""
from __future__ import annotations

import pytest

from contracts import requirements as reqs
from contracts.rules import PORTED_STAGES, REGISTRY

DOC = reqs.load()


# ───────────────────────── 文档自身的一致性 ─────────────────────────

def test_doc_parses_and_has_all_three_families():
    r, q, b = DOC.of_kind('R'), DOC.of_kind('Q'), DOC.of_kind('B')
    assert len(r) > 50, f'只解析出 {len(r)} 条 R——多半是表格格式变了'
    assert len(q) == 12, f'Q 应有 12 条，实得 {len(q)}'
    assert len(b) == 22, f'B 应有 22 条，实得 {len(b)}'


def test_every_r_item_declares_who_checks():
    """「谁在查」是必填列。留空的那条就是这份文档的漏洞。"""
    blank = [k for k, v in DOC.of_kind('R').items()
             if v.who is not None and not v.who.raw]
    assert not blank, f'这些需求没填「谁在查」：{blank}'


def test_unguarded_items_point_at_an_existing_b():
    """字面写了「无守卫」的，必须指向一条存在的 B，且不许同时声称有守卫。"""
    b_ids = DOC.b_ids
    problems = []
    for rid, r in DOC.of_kind('R').items():
        w = r.who
        if w is None or not w.unguarded:
            continue
        if not w.b_refs:
            problems.append(f'{rid}: 写了「无守卫」却没指向任何 B-xx')
        for b in w.b_refs:
            if b not in b_ids:
                problems.append(f'{rid}: 指向不存在的 {b}')
        if w.guards:
            problems.append(f'{rid}: 同时写了「无守卫」和 {w.guards}——到底查没查？')
    assert not problems, '\n'.join(problems)


def test_every_b_is_referenced_by_some_requirement():
    """B 清单是施工清单。一条没有任何需求指向的 B，要么需求漏写，要么这条 B 该退役。

    ⚠ 例外：B-19~B-22 是「按设计就没有判据」那一类，它们由 `人` 那一列指向，
    形态是「**`人`**，见 `B-20`」——同样算被指向。
    """
    referenced = {b for r in DOC.of_kind('R').values()
                  if r.who for b in r.who.b_refs}
    orphan = sorted(DOC.b_ids - referenced)
    assert not orphan, (
        f'这些 B 没有任何需求指向它：{orphan}。'
        f'要么是需求漏写了，要么这条 B 该退役——退役也要标注，不能只是删掉'
    )


def test_all_b_ids_are_contiguous():
    """B-01..B-22 不许有洞。有洞说明删过一条而没有标注，而退役的东西必须带标注。"""
    nums = sorted(int(b.split('-')[1]) for b in DOC.b_ids)
    assert nums == list(range(1, len(nums) + 1)), f'B 编号不连续：{nums}'


# ───────────────────────── 反向：判据 → 需求 ─────────────────────────

def test_every_rule_points_at_an_existing_requirement():
    """反向覆盖率。指向不存在的编号 = 需求改过而判据没跟上。"""
    bad = [(r.rule, r.requirement) for r in REGISTRY.all()
           if r.requirement not in DOC.r_ids]
    assert not bad, f'这些判据指向不存在的需求：{bad}'


def test_no_rule_claims_a_requirement_marked_unguarded():
    """补了守卫就要同步改需求。

    一条需求写着「无守卫 → B-xx」而实际上已经有判据在查它——文档开始说假话，
    而说假话的文档比没有文档更坏（它会以现役身份被引用）。
    """
    liars = []
    for rule in REGISTRY.all():
        req = DOC.requirements.get(rule.requirement)
        if req and req.who and req.who.unguarded:
            liars.append(f'{rule.rule} 在查 {rule.requirement}，但需求那边写着「无守卫」')
    assert not liars, '\n'.join(liars) + '\n→ 补了守卫，需求表那一列要跟着改'


# ───────────────────────── 正向：需求 → 判据 ─────────────────────────

@pytest.mark.parametrize('stage', sorted(PORTED_STAGES) or ['<还没有已移植的 stage>'])
def test_ported_stages_have_rules_for_every_guarded_requirement(stage):
    """正向覆盖率。**只对已移植的 stage 生效**——

    否则第一天就是满屏红，而一条从一开始就红的测试等于没有测试
    （「没报违规」与「根本没在查」必须能区分，这条测试自己也不例外）。
    """
    if not PORTED_STAGES:
        pytest.skip('PORTED_STAGES 是空的：还没有任何 stage 移植完，正向覆盖率无从谈起')

    section = 'R-' + stage.removeprefix('stage')
    want = {rid for rid, r in DOC.of_kind('R').items()
            if r.section == section and r.who and r.who.claims_guard}
    have = REGISTRY.requirements_covered()
    missing = sorted(want - have)
    assert not missing, (
        f'{stage} 声称有守卫、但没有任何判据指着它的需求：{missing}'
    )


def test_ported_stages_are_real_stages():
    known = {f'stage{i}' for i in range(1, 8)}
    assert PORTED_STAGES <= known, f'PORTED_STAGES 里有不存在的 stage：{PORTED_STAGES - known}'
