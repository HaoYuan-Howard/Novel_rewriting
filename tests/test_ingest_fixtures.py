#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ingest_fixtures.py —— 摄入链（stage0 + stage1）合成样本的两半。

上半检查样本资产自身是否自洽、覆盖面有没有退化；下半把同一批字节真正送进
stage0 + stage1，检查终态、告警、剔除、比例、尾段与 HARD 判据。F6/F7 落地后
这里不再有 importorskip：模块存在就必须把 18 个分支全部跑完。
"""
from __future__ import annotations

import codecs
import re

import pytest

from contracts.invariants import (
    BODY_RATIO_MIN, DROP_RATIO_MAX, NONBODY_HEAD_MAX_CP, NONBODY_KINDS,
    NONBODY_MARKER_WARN_CP, STAGE0_WARNINGS, STAGE1_WARNINGS,
)
from tests.fixtures_ingest import (
    CASES, COVERS, WARNINGS_BY_OWNER, Case, marker_of_length, write_all,
)

#: 解码候选集（BOM 探测 + 无 BOM 回退），`R-0.11`。样本要把它们各覆盖一次。
_ENCODING_CANDIDATES = frozenset({'utf-8-sig', 'utf-32', 'utf-16', 'utf-8', 'gb18030'})

#: 已有**真实语料样本**的 `kind`，附实测条数。它们不需要合成样本。
#: ⚠ 这张表是「已经见过」的登记，不是「已验证」的登记——两者的区别见 `docs/schema/stage1.md`。
_CORPUS_COVERED_KINDS = {'番外': 31, '后记': 2, '尾声': 1}

_REQ_RE = re.compile(r'^R-\d+\.\d+[a-z]?$')

#: **这条摄入链新加的 HARD 判据**。每加一条就往这里加一行，然后去补一份会让它红的样本。
#: 只列新加的：既有判据的样本覆盖是另一回事，不该被这条测试一起绑死。
_HARD_JUDGES_ADDED_HERE = frozenset({'R-1.14'})


# ═════════════════════════════ 上半：样本集自洽 ═════════════════════════════

def test_names_are_unique():
    names = [c.name for c in CASES]
    assert len(names) == len(set(names)), '样本重名会让「哪一条红了」说不清'


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_every_case_states_why_it_exists(case: Case):
    """没有理由的样本会在第一次判据变动时被顺手删掉，而它守的分支跟着一起消失。"""
    assert len(case.why) >= 30, f'{case.name}: `why` 太短，说不清它守的是哪条分支'


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_bytes_round_trip(case: Case):
    """样本落成字节后能不能按声明的编码读回来。

    解码失败那两支例外——它们**故意**读不回来，那正是它们要守的分支。
    """
    raw = case.to_bytes()
    if case.terminal == 'R-0.11':
        with pytest.raises(UnicodeDecodeError):
            raw.decode('utf-16' if raw.startswith(codecs.BOM_UTF16_LE) else 'utf-8')
        return
    assert raw.decode(case.encoding) == case.text


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_terminal_cases_declare_nothing_else(case: Case):
    """入了终态就没有产物，再声称「章数是几」「有哪几条告警」是自相矛盾的。

    ⚠ `terminal` 与 `fails` 是**两件事**：前者一份产物都不写，后者产物照写、校验器判红。
    混成一个字段，「红在花钱之前」和「拦在写盘之前」就分不开了。
    """
    if case.terminal is None:
        return
    assert not case.fails, f'{case.name}: 终态就没有产物，谈不上「校验器判 FAIL」'
    assert case.num_chapters is None and not case.warnings and not case.removed_kinds, (
        f'{case.name}: 声明了 terminal={case.terminal}，就不该再声明产物字段——'
        f'终态意味着一份产物都不写'
    )


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_declared_failures_point_at_real_requirements(case: Case):
    ids = set(case.fails) | ({case.terminal} if case.terminal else set())
    bad = [r for r in ids if not _REQ_RE.match(r)]
    assert not bad, f'{case.name}: {bad} 不像需求编号（形如 R-1.14）'


def test_every_hard_judge_this_layer_added_has_a_violating_sample():
    """**本层新加的 HARD 判据，每条都要有一份会让它红的样本。**

    `R-1.14` 在全语料 163 本上 163/163 通过。一条只见过「通过」的判据，
    与一条根本没在跑的判据，在报告上长得一模一样——而它接管的那条 warning
    （完结标记之后还有内容）在语料上同样是 0 命中。
    ⇒ 这个形态**只可能靠合成样本触发**，没有样本它就是新造的一条零样本分支。
    """
    violated = {r for c in CASES for r in c.fails}
    missing = sorted(_HARD_JUDGES_ADDED_HERE - violated)
    assert not missing, (
        f'这些新加的 HARD 判据没有任何样本会让它红：{missing}。'
        f'「163/163 通过」不是证据，「见过它红一次」才是'
    )


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_declared_warnings_belong_to_the_owning_stage(case: Case):
    """告警 id 要落在**它自己那一层**的取值域里。

    判据换层时（2026-08-10 就搬了三条到 stage0）样本必须跟着换 `owner`——
    **写错层不会有任何自然后果**，覆盖率表会照常宣称覆盖了一个已经不在那儿的分支。
    """
    domain = WARNINGS_BY_OWNER[case.owner]
    unknown = set(case.warnings) - set(domain)
    assert not unknown, (
        f'{case.name}(owner={case.owner}): {unknown} 不在 {domain} 里。'
        f'要么改 owner，要么改告警 id'
    )


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_declared_kinds_are_in_the_closed_enum(case: Case):
    unknown = set(case.removed_kinds) - set(NONBODY_KINDS)
    assert not unknown, f'{case.name}: {unknown} 不在闭合枚举 {NONBODY_KINDS} 里'


# ── 覆盖率：这才是这个文件存在的理由 ──────────────────────────────────────

@pytest.mark.parametrize('owner,domain', sorted(WARNINGS_BY_OWNER.items()))
def test_every_warning_id_has_a_sample(owner, domain):
    """**两层各自的**可疑项，每类至少一份样本。

    改判据时如果某一类没有样本，它就退回「零样本分支」——
    而这个文件的全部意义就是不让那件事悄悄发生。
    ⚠ 按层分别检查：把两层的告警混在一个集合里算覆盖率，
    某一层被清空时另一层的样本会替它「凑数」。
    """
    covered = {w for c in CASES if c.owner == owner for w in c.warnings}
    missing = sorted(set(domain) - covered)
    assert not missing, f'{owner} 的这些告警没有任何样本会触发它：{missing}'


def test_the_two_warning_domains_do_not_overlap():
    """一个告警 id 只能属于一层。两层都认，「这条是谁报的」就说不清了。"""
    both = set(STAGE0_WARNINGS) & set(STAGE1_WARNINGS)
    assert not both, f'{both} 同时在两层的取值域里'


def test_every_nonbody_kind_is_accounted_for():
    """四类 `kind` 要么有合成样本，要么有真实语料样本——**不许两头都没有**。"""
    synthetic = {k for c in CASES for k in c.removed_kinds}
    accounted = synthetic | set(_CORPUS_COVERED_KINDS)
    missing = sorted(set(NONBODY_KINDS) - accounted)
    assert not missing, f'这些 kind 既没有语料样本也没有合成样本：{missing}'


def test_every_encoding_candidate_has_a_sample():
    """五个编码候选。`utf-8` 有 163 本真实语料，另外四个只能靠合成样本。"""
    covered = {c.encoding for c in CASES} | {'utf-8'}
    # utf-16-le 是造「BOM 说谎」和「无 BOM 的 UTF-16」用的，归到 utf-16 名下
    covered = {'utf-16' if e.startswith('utf-16') else e for e in covered}
    missing = sorted(_ENCODING_CANDIDATES - covered)
    assert not missing, f'这些编码分支没有样本：{missing}'


def test_cover_map_lists_every_case():
    listed = {n for names in COVERS.values() for n in names}
    assert listed == {c.name for c in CASES}, 'COVERS 与 CASES 对不上，覆盖率表在说谎'


# ── 带状结构：三个阈值之间的关系本身是需求 ────────────────────────────────

def test_marker_band_has_no_silent_gap():
    """`core` 长度的四段带状结构，中间不许再有静默区。

    ≤17 真标记区（静默切）· 18~20 切但告警 · >20 不切。
    **告警线必须严格小于命中上限**——否则 `(WARN, HEAD_MAX]` 那一段又变成切了不出声。
    """
    assert NONBODY_MARKER_WARN_CP < NONBODY_HEAD_MAX_CP, (
        f'告警线 {NONBODY_MARKER_WARN_CP} 不小于命中上限 {NONBODY_HEAD_MAX_CP}：'
        f'告警永远不可能触发，而误切照旧'
    )
    assert NONBODY_MARKER_WARN_CP >= 17, (
        f'告警线降到 {NONBODY_MARKER_WARN_CP} 会让实测真标记（最长 17 码点）开始告警——'
        f'那是噪声，不是发现'
    )


@pytest.mark.parametrize('cp', [2, 17, 18, 20, 21, 30])
def test_marker_of_length_is_exact(cp: int):
    """样本生成器必须精确——band 测试全部建立在「这一行恰好 N 码点」之上。"""
    line = marker_of_length(cp)
    assert len(line) == cp, f'要 {cp} 码点，实际 {len(line)}：{line!r}'
    assert line.startswith('番外')
    assert line == line.strip(), '不许带前后空白，否则 core 与整行长度就不相等了'


def test_the_two_ratio_thresholds_leave_a_real_window():
    """`drop_ratio` 独立起作用的窗口。

    `body + removed ≈ total`，所以 `removed > DROP_RATIO_MAX` 基本等价于
    `body < 1 - DROP_RATIO_MAX`。两条判据的**独立窗口**就是
    `[BODY_RATIO_MIN, 1 - DROP_RATIO_MAX)`；窗口塌成空的时候，
    `drop_ratio` 就只是 `early_cut` 的复读，该合并成一条。
    """
    lo, hi = BODY_RATIO_MIN, 1 - DROP_RATIO_MAX
    assert lo < hi, (
        f'两条比例判据的独立窗口是空的（[{lo}, {hi})）——'
        f'drop_ratio 不再提供任何 early_cut 之外的信息，应当合并成一条'
    )


def test_write_all_produces_one_file_per_case():
    """落盘那一步也要验——样本再对，写不出去也是零覆盖。

    ⚠ 这里用 `tempfile.mkdtemp` 而不是 pytest 的 `tmp_path`：本机
    `%TEMP%\\pytest-of-<user>` 目录的 ACL 坏了（`scandir` 直接 WinError 5），
    `tmp_path` 一用就炸。那是环境问题不是代码问题，但没必要让这条测试绑在它上面。
    """
    import shutil
    import tempfile

    d = tempfile.mkdtemp(prefix='ingest-fixtures-')
    try:
        paths = write_all(d)
        assert set(paths) == {c.name for c in CASES}
        assert all(p.exists() and p.stat().st_size > 0 for p in paths.values())
        assert len({p.read_bytes() for p in paths.values()}) == len(paths), \
            '有两份样本字节完全相同——其中一份没在守任何独立的分支'
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ═════════════════════ 下半：等 stage0 / stage1 落地 ══════════════════════

#: 每一层的实现模块。**两层各自 skip**——stage0 先落地时，
#: 它那 13 份样本立刻转成真断言，不必等 stage1。
_IMPL_MODULE = {
    'stage0': 'novel.stages.stage0.removal',
    'stage1': 'novel.stages.stage1.partition',
}


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_case_behaves_as_declared(case: Case):
    """把样本喂进它那一层，断言它按声明的分支走。

    ⚠ 断言的是 `terminal` / `num_chapters` / `warnings` 的 **id** / `removed_kinds` /
    `body_ratio` 的**区间** / `tail`——**不断言任何措辞**。
    两层的报告都不在逐字节验收范围内，措辞随时可改。
    """
    from contracts.rules import REGISTRY
    from novel.core.ingest import decode_source_bytes
    from novel.errors import IngestTerminal
    from novel.stages.stage0 import checks as checks0
    from novel.stages.stage0.removal import remove_bytes
    from novel.stages.stage1 import checks as checks1
    from novel.stages.stage1.partition import partition_text

    def exercise():
        removed = remove_bytes(
            case.to_bytes(),
            original_path=f'{case.name}.txt',
            code='test',
        )
        partitioned = partition_text(
            removed.clean_text, source_sha1=removed.report.source_sha1)
        return removed, partitioned

    if case.terminal is not None:
        with pytest.raises(IngestTerminal) as exc:
            exercise()
        assert exc.value.requirement == case.terminal
        return

    removed, partitioned = exercise()
    warnings = (
        tuple(w.kind for w in removed.report.warnings)
        if case.owner == 'stage0'
        else tuple(w.kind for w in partitioned.report.warnings)
    )
    assert len(partitioned.main_text.root) == case.num_chapters
    assert warnings == case.warnings
    if case.owner == 'stage0':
        assert tuple(segment.kind for segment in removed.report.removed) == case.removed_kinds
    if case.tail is not None:
        assert partitioned.tail == case.tail

    ratio = removed.body_ratio
    if case.body_ratio_below is not None:
        assert ratio < case.body_ratio_below
    if case.body_ratio_band is not None:
        lo, hi = case.body_ratio_band
        assert lo <= ratio < hi

    if case.owner == 'stage0':
        outcomes = checks0.check(
            removed.report,
            removed.clean_text,
            source=decode_source_bytes(case.to_bytes()),
        )
    else:
        outcomes = checks1.check(
            partitioned.report,
            partitioned.main_text,
            partitioned.hook_copy,
            clean_text=removed.clean_text,
            stage0=removed.report,
        )
    failed = {
        REGISTRY.get(outcome.rule).requirement
        for outcome in outcomes
        if outcome.findings and REGISTRY.get(outcome.rule).sev == 'hard'
    }
    assert set(case.fails) <= failed
