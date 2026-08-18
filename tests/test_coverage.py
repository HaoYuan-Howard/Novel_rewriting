#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_coverage.py —— 需求 §6 立的**双向覆盖率**的执行落点。

没有这个文件，冻结后端契约的「谁在查」那一列就只是装饰——
而这个项目最贵的一类故障恰恰是「一条读起来和有守卫的约束一模一样、但没有任何代码在查」。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from contracts import requirements as reqs
from contracts.invariants import NODES, STAGE_SECTIONS, STAGES, WHO_CHECKS
from contracts.rules import REGISTRY

DOC = reqs.load()


# ───────────────────────── 文档自身的一致性 ─────────────────────────

#: 每个 R 节解析出的条目数（**含该节标题自身那一条**）。
#:
#: 【为什么是逐节精确快照，不是一个下限】这里原来写的是 `len(r) > 50`，
#: 而 R 实际有 280 条——那是 **230 条的缓冲**：某一节的表格少一列、标题格式被改，
#: 整节上百条解析不出来，这条断言照样绿。
#: 而"少解析"的后果不是报错，是**双向覆盖率对那些条目静默失效**：
#: 它们压根不在 `DOC` 里，于是「每条声明了 HARD 的需求必须有注册判据在管」
#: 这类检查根本轮不到它们头上——一整节需求就这样悄悄退出了验收。
#: ⇒ 改成逐节精确：加/删需求时**必须显式改这里的数字**（那正是它的用途），
#: 而一次预期外的漂移会精确报出「哪一节从 N 条变成 M 条」，不用去猜是哪里坏了。
_R_SECTION_SIZES = {
    'R-0': 16, 'R-1': 15, 'R-2': 20, 'R-3': 29, 'R-4': 18,
    'R-5': 21, 'R-6': 11, 'R-7': 33, 'R-8': 22, 'R-9': 24,
    'R-10': 32, 'R-11': 14, 'R-12': 11, 'R-13': 8, 'R-14': 7,
}


def test_doc_parses_and_has_all_three_families():
    q, b = DOC.of_kind('Q'), DOC.of_kind('B')
    assert len(q) == 12, f'Q 应有 12 条，实得 {len(q)}'
    assert len(b) == 22, f'B 应有 22 条，实得 {len(b)}'


def test_every_r_section_parses_to_exactly_the_expected_size():
    """逐节钉死条目数——解析面自己的守卫（判据 3：守卫的扫描面也需要守卫）。"""
    got = Counter(v.section for v in DOC.of_kind('R').values() if v.section)
    drift = {sec: (_R_SECTION_SIZES.get(sec), got.get(sec))
             for sec in sorted(set(_R_SECTION_SIZES) | set(got),
                               key=lambda s: int(s.split('-')[1]))
             if _R_SECTION_SIZES.get(sec) != got.get(sec)}
    assert not drift, (
        f'R 节的条目数与快照对不上，(快照, 实际) = {drift}\n'
        f'→ 若是有意增删需求，把 `_R_SECTION_SIZES` 里那一节的数字一并改掉；\n'
        f'  若不是，多半是那一节的表格列或标题格式被动过，解析器读不到了'
    )


def test_the_section_sizes_cover_every_parsed_r_item():
    """快照求和必须等于 R 总数——防止有条目落在任何已知节之外而无人察觉。"""
    assert sum(_R_SECTION_SIZES.values()) == len(DOC.of_kind('R')), (
        f'快照合计 {sum(_R_SECTION_SIZES.values())} ≠ 解析出的 R 总数 '
        f'{len(DOC.of_kind("R"))}——有条目不属于任何登记在册的 R 节'
    )


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
    # 13 →(F1-6) 15 →(F2-6) 30 →(F3-0) **32**：stage7 那七行 `结构` 从原型裸符号
    # （`save_name_map` / `apply_subs` / `sub_table` / `merge_mapping_edit`…）
    # 改成 `路径::名字` 之后，本条的扫描面**一次翻了一倍**；F3-0 把 `R-5.10` 从
    # `待建` 兑现成 `结构` 又多了两个（`store.py::write_invalid` / `prov.py::FreshnessReport`
    # ——**那两段代码本来就在，欠的只是那一格没说出它们**）。
    # ⚠ 顺带说明这个数为什么该往上走而不是往下：`结构` 那一列的全部价值是
    # 「说得出是哪段代码在守」，而旧形态**对本条不可见**——移植一层就该多几行。
    # 32 →(TODO-16) **76**：最后 5 行裸符号（`R-3.4` 的散文指代 · `R-4.2` 的
    # `measure()` · `R-4.3b` 的 `SYS`/`bands()` · `R-12.1c` 的 `extract.` 简写 ·
    # `R-13.7` 的 `jobs.start`）全部改成 `仓内路径::名字`
    # ——**那几段代码本来就在，欠的只是那一格没说出它们**。
    assert checked >= 76, (
        f'只扫到 {checked} 个可解析落点——本条快要变成一条什么都没查的测试了')


def test_every_structural_row_names_a_machine_locatable_site():
    """⭐ **TODO-16 的兑现条件**：`结构` 行不许只写裸符号或散文。

    上面那条只查「写出来的落点存不存在」，对**根本没写可解析落点**的行是盲的
    ——那正是 `R-4.2` 的 `` `measure()` ``、`R-13.7` 的 `` `jobs.start` `` 这一族
    长期躲过存在性检查的方式：两条正则都要求落点里至少有一个 `/`，
    没有 `/` 就整行不可见，而 `test_structural_rows_name_their_code_site`
    只要求「格子里有个反引号」，它们照样过。
    ⇒ 本条把那个洞堵上：每一行 `结构` 至少要有一个**机器定位得到**的落点。

    ⚠ 真定位不到时的正确做法是**改判成更诚实的类别**（`人` / `待建`），
    不是在这里开豁免——一个说不出落点的 `结构` 读起来像有守卫而其实没有。
    """
    blind = []
    for rid, r in DOC.of_kind('R').items():
        if not (r.who and 'STRUCTURAL' in r.who.markers):
            continue
        if not (_CODE_SITE.findall(r.who.raw) or _BARE_PATH.findall(r.who.raw)):
            blind.append(f'{rid}: {r.who.raw[:70]}…')
    assert not blind, (
        '这些 `结构` 行没有任何机器定位得到的落点（存在性检查看不见它们）：\n'
        + '\n'.join(f'  {b}' for b in blind)
        + '\n→ 改成 `仓内路径::名字`；真定位不到就改判成 `人` 或 `待建`')


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

@pytest.mark.parametrize('stage', sorted(STAGES))
def test_every_stage_has_rules_for_every_guarded_requirement(stage):
    """当前八层的正向覆盖率。"""
    # ⚠ **不许写成 `'R-' + stage.removeprefix('stage')`。** 那个式子只在 stage0/stage1 上对：
    # R 节的编排轴是**节点**，不是 stage（R-2/R-3 是 stage2 的两个节点、R-5/R-6 是 stage4 的、
    # R-7/R-8 是 stage5 的两相），于是 `stage3` 会拿到 **stage2 逐章抽取**的需求。
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


# ─────────────── §五 反向覆盖率：手抄的那张表也要有人对账 ───────────────
#
# 【为什么这三条必须存在】§五 是 `contracts/rules.py` 注册表的**手抄副本**，
# 而它此前没有任何消费方——于是它成了本仓 `Q-6`（单一落点）最干净的一次自我违反。
#
# 实测（2026-08-17）：下线 `stage5.semantic` 那一次，**被解析的 §二** 里
# `R-7.18b` / `R-7.18c` 被逐条改成了转授形态（不改，
# `test_every_stage_has_rules_for_every_guarded_requirement` 当场红），
# 而**没被解析的 §五** 至今写着「48 条现役……含 `R-7.18b` / `R-7.18c`」。
# ⇒ 同一份文档、同一次改动：**有守卫的那一半没漂，没守卫的这一半漂了。**
# 一并抓到的还有两处：stage6 少记 1 条，以及**整个 `stage0` 层（13 条判据）
# 从建层起就没进过这张表**——少一整层不会让任何别的测试变色，
# 而「反向覆盖率」自己漏层时，它宣称的那个验收方向是空的。
#
# 手抄不是问题，**手抄而无人对账**才是。下面三条把它变成会红的那一半。
_REVERSE_ROW = re.compile(r'`(stage\d)`[^|]*?\*\*(\d+) 条现役\*\*')


def _reverse_coverage_claims() -> dict[str, int]:
    """§五 逐行扫出的「这一层宣称有几条判据」。**逐行**扫是刻意的：
    整篇 findall 时 `[^|]` 会放过跨行匹配，而表格行首尾都有 `|`，逐行才锁得住列。"""
    claims: dict[str, int] = {}
    for line in Path(reqs.DOC_PATH).read_text(encoding='utf-8').splitlines():
        for stage, n in _REVERSE_ROW.findall(line):
            if stage in claims:
                raise AssertionError(
                    f'§五 里 {stage} 有两行都带条数登记——一层只该有一行是权威')
            claims[stage] = int(n)
    return claims


def test_reverse_coverage_counts_match_the_live_registry():
    """§五 手抄的「N 条现役」必须逐层等于注册表实际条数。"""
    claims = _reverse_coverage_claims()
    drift = {s: (n, len(REGISTRY.of_stage(s)))
             for s, n in claims.items() if n != len(REGISTRY.of_stage(s))}
    assert not drift, (
        f'§五 的条数与注册表对不上，(宣称, 实际) = {drift}\n'
        f'→ 判据表改了而反向覆盖率那一节没跟上。真值恒在 `contracts/rules.py` 的注册表，'
        f'文档里那个数字只是副本；改判据时两边一起改'
    )


def test_every_stage_with_rules_appears_in_the_reverse_coverage_table():
    """反过来：注册表里有判据的层，一层都不许在 §五 缺席。"""
    claims = _reverse_coverage_claims()
    with_rules = {r.stage for r in REGISTRY.all()}
    missing = sorted(with_rules - set(claims))
    assert not missing, (
        f'这些层有注册判据却没进 §五 反向覆盖率表：{missing}\n'
        f'→ 给每层补一行「（`stageN`，**M 条现役**）」，M 取注册表实际条数'
    )
    stray = sorted(set(claims) - with_rules)
    assert not stray, f'§五 登记了没有任何注册判据的层：{stray}'


def test_the_reverse_coverage_scan_actually_finds_something():
    """守卫的守卫：正则失配时上面两条会全绿，而那正是最坏的情况。"""
    claims = _reverse_coverage_claims()
    assert len(claims) >= 8, (
        f'只从 §五 扫到 {len(claims)} 层的条数登记——正则多半失配了。'
        f'当前 stage0~stage7 每层都有判据，八层都该被扫到'
    )


# ─────────────────── `docs/schema/*.md` 的条数声明与 id 覆盖 ───────────────────
#
# 【为什么单开一组】上面那三条只扫 `docs/BACKEND_CONTRACT.md` §五。而 `docs/schema/*.md`
# 里同样逐层写着「当前注册表共 N 条」，**那些数字此前无人对账** —— 实测漂到过
# stage5 写 48（实为 46）、stage6 写 21 条 / 6-8-4-3（实为 22 条 / 5-8-5-4），
# 而 stage0 / stage1 / stage3 压根没写。
# ⚠ 只查「写了的对不对」会放过「压根没写」，所以下面第二条是**必需的另一半**。

#: `当前注册表共 **N 条**：**a hard、b info、c metric、d unver**。`（零值桶省略）
_SCHEMA_COUNT_ROW = re.compile(
    r'当前注册表共 \*\*(\d+) 条\*\*：\*\*([^*]+)\*\*')
_SCHEMA_SEV_CELL = re.compile(r'(\d+)\s*([a-z]+)')
_SCHEMA_DOC_DIR = Path(__file__).resolve().parent.parent / 'docs' / 'schema'


def _schema_doc(stage: str) -> Path:
    return _SCHEMA_DOC_DIR / f'{stage}.md'


def _schema_count_claims() -> dict[str, tuple[int, dict[str, int]]]:
    """逐层扫出 `docs/schema/<stage>.md` 宣称的条数与 sev 分布。"""
    claims: dict[str, tuple[int, dict[str, int]]] = {}
    for stage in sorted({r.stage for r in REGISTRY.all()}):
        doc = _schema_doc(stage)
        if not doc.exists():
            continue
        for line in doc.read_text(encoding='utf-8').splitlines():
            hit = _SCHEMA_COUNT_ROW.search(line)
            if hit is None:
                continue
            if stage in claims:
                raise AssertionError(
                    f'{doc.name} 里有两行「当前注册表共」——一层只该有一行是权威')
            dist = {sev: int(n) for n, sev in _SCHEMA_SEV_CELL.findall(hit.group(2))}
            claims[stage] = (int(hit.group(1)), dist)
    return claims


def test_schema_docs_declare_the_live_rule_counts():
    """`docs/schema/*.md` 宣称的条数与 sev 分布逐层等于注册表。"""
    claims = _schema_count_claims()
    assert claims, '一层都没扫到——正则多半失配了，而那时下面的断言会全绿'
    drift: dict[str, str] = {}
    for stage, (total, dist) in claims.items():
        rules = REGISTRY.of_stage(stage)
        live = Counter(r.sev for r in rules)
        if total != len(rules):
            drift[stage] = f'条数 宣称 {total} / 实际 {len(rules)}'
        elif dist != {sev: n for sev, n in live.items() if n}:
            drift[stage] = f'分布 宣称 {dist} / 实际 {dict(live)}'
    assert not drift, (
        f'docs/schema 的条数声明与注册表对不上：{drift}\n'
        f'→ 真值恒在 `contracts/judges/stageN.py` 的注册表，文档里那行只是副本；'
        f'改判据时两边一起改'
    )


def test_every_stage_with_rules_declares_its_count_in_the_schema_doc():
    """**漏网的另一半**：有判据的层，一层都不许在 `docs/schema/` 里不写条数。

    ⚠ 只查「写了的对不对」会放过「压根没写」——stage0 / stage1 / stage3 正是这样
    长期没有任何条数声明，而上面那条对它们一句话都说不出来。
    """
    claims = _schema_count_claims()
    with_rules = {r.stage for r in REGISTRY.all() if _schema_doc(r.stage).exists()}
    missing = sorted(with_rules - set(claims))
    assert not missing, (
        f'这些层有注册判据、也有 schema 文档，却没写条数声明：{missing}\n'
        f'→ 补一行「当前注册表共 **N 条**：**a hard、b info、c metric、d unver**。」'
        f'（零值桶省略）'
    )


def test_schema_docs_name_every_live_rule():
    """每条现役判据的 id 都要在本层 schema 文档里出现过。

    ⚠ 判据表本身也会漂：stage6 曾整整少列一条 `hook_fact_attribution`，而条数那一行
    当时写的是 21（正好等于列出来的行数）⇒ **两个错互相圆上了**，谁也发现不了。
    ⇒ 条数与 id 集合**两条都要查**：前者防漏改数字，后者防漏列行。
    """
    missing: dict[str, list[str]] = {}
    for stage in sorted({r.stage for r in REGISTRY.all()}):
        doc = _schema_doc(stage)
        if not doc.exists():
            continue
        tokens = set(re.findall(r'`([a-z0-9_]+)`', doc.read_text(encoding='utf-8')))
        absent = sorted({r.rule for r in REGISTRY.of_stage(stage)} - tokens)
        if absent:
            missing[stage] = absent
    assert not missing, (
        f'这些判据一次都没在本层 schema 文档里出现：{missing}\n'
        f'→ 把它们补进该层的判据表；一条查不到名字的判据，读文档的人无从知道它存在'
    )
