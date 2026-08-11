#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_coverage.py —— 需求 §6 立的**双向覆盖率**的执行落点。

没有这个文件，`REQUIREMENTS.md` 的「谁在查」那一列就只是装饰——
而这个项目最贵的一类故障恰恰是「一条读起来和有守卫的约束一模一样、但没有任何代码在查」。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from contracts import requirements as reqs
from contracts.invariants import NODES, STAGE_SECTIONS, STAGES, WHO_CHECKS
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


def test_every_r_item_declares_something():
    """**填了字**与**声明了什么**是两回事。

    上一条只查「这一格是不是空的」，而真实的故障是另一种：格子里写着字，
    解析器一个记号也没认出来——于是这一行**什么也没声明**，却在所有测试里绿着。
    实测：本文档一度有 **10 行**落在这里（六行「见 `R-0.x`」· 一行「只统计」·
    两行「—」· 一行 `度量`），而那正是这一列存在的理由要防的事。
    """
    mute = [(k, v.who.raw) for k, v in DOC.of_kind('R').items()
            if v.who is not None and v.who.raw and not v.who.declares_something]
    assert not mute, (
        '这些「谁在查」解析器读不懂，等于什么也没声明：\n'
        + '\n'.join(f'  {k:10} {raw!r}' for k, raw in mute)
        + f'\n→ 取值面是 {WHO_CHECKS}（外加 `人` / `待建` / `无守卫`）。'
        f'要加新取值就同时改 `invariants.WHO_CHECKS` 与 `requirements._MARKER_TOKENS`'
    )


def test_every_who_checks_value_is_actually_used():
    """取值面不许有**没人用**的取值——那就是抢跑造出来的一条声明（判据 32）。

    ⚠ 它同时是「每个新取值都要有一个会让它红的样本」的一半：
    加了一个取值却没有任何一行用它，这条测试当场红。
    """
    used = {m for r in DOC.of_kind('R').values() if r.who for m in r.who.markers}
    if any(r.who and r.who.delegate for r in DOC.of_kind('R').values()):
        used.add('DELEGATED')
    unused = sorted(set(WHO_CHECKS) - used)
    assert not unused, (
        f'这些取值一行都没用上：{unused}。'
        f'一个没人用的取值与一条没人查的约束是同一个形状——要么用它，要么别加'
    )


def test_structural_rows_name_their_code_site():
    """`结构` 的**准入条件**：那一格必须写出代码落点。

    没有这一条，「代码结构在守」就会变成「无守卫」的化妆品——
    它读起来像有守卫，而谁也说不出是哪一段代码在守。
    判准很低但够用：格子里得有一个反引号引用（函数名 / 调用点）。
    """
    bad = [(k, v.who.raw) for k, v in DOC.of_kind('R').items()
           if v.who and 'STRUCTURAL' in v.who.markers and '`' not in v.who.raw.replace('`结构`', '')]
    assert not bad, (
        '这些标了 `结构` 却没写出代码落点：\n'
        + '\n'.join(f'  {k:10} {raw!r}' for k, raw in bad)
        + '\n→ 写成 ``结构``（`模块.函数`）——说不出是哪段代码在守，就不算守'
    )


ROOT = Path(__file__).resolve().parent.parent

#: 「代码落点」的可解析形态：`<仓内相对路径>::<名字>`。
#: D/E 期立的惯例（`R-11.13` · `R-13.x` · `R-14.x` 全是它）。
_CODE_SITE = re.compile(r'`((?:[\w.\-]+/)+[\w.\-]+\.\w+)::(\w+)`')
#: 只写路径不写名字的那种（`R-14.3` 的 `novel/llm/ledger.py`、`R-14.6` 的那份文档）。
_BARE_PATH = re.compile(r'`((?:[\w.\-]+/)+[\w.\-]+\.(?:py|md|json|toml|yml))`')


def test_structural_rows_that_name_a_repo_path_actually_point_at_it():
    """上一条查「有没有反引号」，本条查**反引号里那个东西在不在**。

    上一条的判准低到可以被一句原型仓的文件名满足：`R-9.1` 写着
    `` `n8_hook.main` ``——那是**原型仓**的文件，本仓根本没有这个模块，
    而它照样过了「写出代码落点」这道准入。**说不出是哪段代码在守，
    与说了一段不存在的代码，对读的人是同一件事。**

    ⇒ 本条只认可解析的那种形态 `路径::名字`：路径要在仓里，
    名字要在那个文件里真的定义得出来。裸路径（`novel/llm/ledger.py`）只查存在。

    ⚠ **旧形态对本条不可见**：实测 26 行 `结构` 里 **13 行**仍是裸符号名或原型文件名
    （`R-4.2` 的 `measure()` · `R-9.1` 的 `n8_hook.main` · `R-10.1` 的 `save_name_map` …），
    本条只扫得到另外 13 行的 16 个落点。**那正是这一格的漏洞，不是本条的疏忽**：
    逐层移植时各自改成可解析形态，**改一行本条就多查一行**。
    下面那条「守卫的守卫」保证它不会悄悄退化成一条什么都没扫到的测试。
    """
    unresolved: list[str] = []
    checked = 0
    for rid, r in DOC.of_kind('R').items():
        if not (r.who and 'STRUCTURAL' in r.who.markers):
            continue
        sites = _CODE_SITE.findall(r.who.raw)
        for rel, name in sites:
            checked += 1
            p = ROOT / rel
            if not p.exists():
                unresolved.append(f'{rid}: `{rel}::{name}` —— 文件不在仓里')
                continue
            if not re.search(rf'^\s*(?:def|class)\s+{name}\b|^{name}\s*[:=]',
                             p.read_text(encoding='utf-8'), re.M):
                unresolved.append(f'{rid}: `{rel}::{name}` —— {rel} 里定义不出 {name}')
        with_name = {rel for rel, _ in sites}
        for rel in _BARE_PATH.findall(r.who.raw):
            if rel in with_name:              # 已被上面那一轮算过
                continue
            checked += 1
            if not (ROOT / rel).exists():
                unresolved.append(f'{rid}: `{rel}` —— 不在仓里')

    assert not unresolved, (
        '这些 `结构` 行点名了一个不存在的落点：\n'
        + '\n'.join(f'  {u}' for u in unresolved)
        + '\n→ 落点写的是**本仓**的代码。指向原型仓、或改名之后没跟上，'
          '读的人会以为有一段代码在守而其实没有'
    )
    # 守卫的守卫（形态照抄 `test_docs_references.py::test_the_scan_actually_finds_something`）：
    # 把可解析形态全改回裸符号名，上面那个循环一条都不跑而测试照样绿。
    assert checked >= 15, (
        f'只扫到 {checked} 个可解析落点——本条快要变成一条什么都没查的测试了')


def test_carrier_rows_name_their_field():
    """`载体` 的准入条件同理：必须点名是哪个字段被类型收走了取值面。"""
    bad = [(k, v.who.raw) for k, v in DOC.of_kind('R').items()
           if v.who and 'CARRIER' in v.who.markers and '`' not in v.who.raw.replace('`载体`', '')]
    assert not bad, (
        '这些标了 `载体` 却没点名字段：\n'
        + '\n'.join(f'  {k:10} {raw!r}' for k, raw in bad)
    )


def test_delegated_rows_point_at_an_existing_requirement():
    """「见 `R-0.7`」转授的目标必须存在。指向一个不存在的编号 = 职责掉在地上。"""
    bad = [(k, v.who.delegate) for k, v in DOC.of_kind('R').items()
           if v.who and v.who.delegate and v.who.delegate not in DOC.requirements]
    assert not bad, f'这些「见 X」指向不存在的编号：{bad}'


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


def test_no_rule_claims_a_non_judge_requirement():
    """`载体` / `结构` / `见另一条` / `不适用` 四个取值，**不许有判据指着**。

    有判据就说明它其实是判据在守，那一格标错了——而标错的方向恰恰是**低估**：
    读的人会以为「这条没有判据也没关系，类型/结构兜着」，于是判据坏掉时没人会去看它。
    """
    NOT_A_JUDGE = ('CARRIER', 'STRUCTURAL', 'N_A')
    liars = []
    for rule in REGISTRY.all():
        req = DOC.requirements.get(rule.requirement)
        if not (req and req.who):
            continue
        wrong = [m for m in req.who.markers if m in NOT_A_JUDGE]
        if wrong:
            liars.append(f'{rule.rule} 在查 {rule.requirement}，而那一格写着 {wrong}')
        if req.who.delegate:
            liars.append(f'{rule.rule} 在查 {rule.requirement}，而那一格写着「见 {req.who.delegate}」')
    assert not liars, '\n'.join(liars) + '\n→ 有判据就该写 HARD/INFO/GATE/METRIC'


# ───────────────────────── 正向：需求 → 判据 ─────────────────────────

@pytest.mark.parametrize('stage', sorted(PORTED_STAGES) or ['<还没有已移植的 stage>'])
def test_ported_stages_have_rules_for_every_guarded_requirement(stage):
    """正向覆盖率。**只对已移植的 stage 生效**——

    否则第一天就是满屏红，而一条从一开始就红的测试等于没有测试
    （「没报违规」与「根本没在查」必须能区分，这条测试自己也不例外）。
    """
    if not PORTED_STAGES:
        pytest.skip('PORTED_STAGES 是空的：还没有任何 stage 移植完，正向覆盖率无从谈起')

    # ⚠ **不许写成 `'R-' + stage.removeprefix('stage')`。** 那个式子只在 stage0/stage1 上对：
    # R 节的编排轴是**节点**，不是 stage（R-2/R-3 是 stage2 的两个节点、R-5/R-6 是 stage4 的、
    # R-7/R-8 是 stage5 的两相），于是 `stage3` 会拿到 **stage2 逐章抽取**的需求。
    # 它曾经就是那么写的，而 `PORTED_STAGES` 为空让这条 bug 整个藏在 `skip` 后面。
    sections = STAGE_SECTIONS[stage]
    # 用 `expects_rule` 而不是 `claims_guard`：**度量也必须有一条注册判据**。
    # 度量不是门，但「这条度量无条件报出」与「根本没人算它」必须分得开（判据 21）。
    want = {rid for rid, r in DOC.of_kind('R').items()
            if r.section in sections and r.who and r.who.expects_rule}
    have = REGISTRY.requirements_covered()
    missing = sorted(want - have)
    assert not missing, (
        f'{stage} 声称有守卫、但没有任何判据指着它的需求：{missing}'
    )


def test_ported_stages_are_real_stages():
    """**从 0 起**：2026-08-10 加了 stage0（非正文剔除 + 人工审阅闸门，需求 `R-0`）。

    ⚠ 这个范围要跟着 `invariants.STAGES` 走，别靠记忆，也别在这里重写一遍 `range(0, 8)`。
    """
    assert PORTED_STAGES <= set(STAGES), \
        f'PORTED_STAGES 里有不存在的 stage：{PORTED_STAGES - set(STAGES)}'


# ───────────────────── 阶段 ↔ 节点 ↔ 需求节的映射（F-A 的守卫） ─────────────────────

def test_every_node_points_at_an_existing_requirement_section():
    """节点表里写的需求节必须真的存在。

    指向一个不存在的节 = 那个节点的正向覆盖率**静默零覆盖**：`want` 是空集，
    于是「一条判据都没有」与「判据全齐」在报告上长得一模一样。
    """
    sections = {r.section for r in DOC.of_kind('R').values() if r.section}
    bad = [(n, spec.section) for n, spec in NODES.items() if spec.section not in sections]
    assert not bad, f'这些节点指向不存在的需求节：{bad}'


def test_every_pipeline_requirement_section_is_claimed_by_a_node():
    """反过来：处理链的每一节都必须有节点认领它。

    没人认领的一节 = 那一节里所有「谁在查」写着 HARD 的需求**永远不会被正向覆盖率查到**。
    ⚠ `R-11`（人工验收界面）/ `R-12`（编排）/ `R-13`/`R-14`（新增能力）不是处理链节点，
    按设计不在这张表里——它们的守卫在别处，不该靠这条测试。
    """
    claimed = {spec.section for spec in NODES.values()}
    pipeline = {f'R-{i}' for i in range(0, 11)}
    assert pipeline <= claimed, f'这些需求节没有任何节点认领：{sorted(pipeline - claimed)}'


def test_stage_sections_is_not_the_naive_stage_number():
    """⚠ **这条测试就是那个 bug 本身。**

    `STAGE_SECTIONS` 曾经是算出来的：`'R-' + stage.removeprefix('stage')`。
    那个式子只在 stage0/stage1 上对——R 节的编排轴是**节点**不是 stage。
    下面这几个字面值是分歧点，写死在这里，改坏了当场红：
    """
    assert STAGE_SECTIONS['stage2'] == ('R-2', 'R-3'), 'stage2 有两个节点（全书 / 逐章）'
    assert STAGE_SECTIONS['stage3'] == ('R-4',), 'stage3 是 R-4，不是 R-3——R-3 是 stage2 逐章抽取'
    assert STAGE_SECTIONS['stage4'] == ('R-5', 'R-6'), 'stage4 有两份产物（计划 / 上下文组装）'
    assert STAGE_SECTIONS['stage5'] == ('R-7', 'R-8'), 'stage5 有两相（写作 / 全书验收）'
    assert STAGE_SECTIONS['stage7'] == ('R-10',)

    naive = {s for s in STAGES if STAGE_SECTIONS[s] == ('R-' + s.removeprefix('stage'),)}
    assert naive == {'stage0', 'stage1'}, (
        f'恰好只有 stage0/stage1 该与 `R-N` 同号，实得 {sorted(naive)}——'
        f'这条断言在盯「有人把 STAGE_SECTIONS 改回那个式子」'
    )


def test_every_stage_has_at_least_one_node():
    """八个 stage 一个都不许没有节点。

    ⚠ stage0 在原型里**一个 action 都没有**，而它是唯一那道人工闸门（`R-0.3a`）——
    「这一层没有作业」与「这一层不需要作业」在界面上长得一样。
    """
    empty = [s for s in STAGES if not STAGE_SECTIONS[s]]
    assert not empty, f'这些 stage 一个节点都没有：{empty}'
