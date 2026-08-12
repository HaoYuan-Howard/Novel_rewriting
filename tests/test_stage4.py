#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage4.py —— 重构计划与写作上下文的**节点级对账**。

形态照 `tests/test_stage7.py`（单文件、整份挂 skipif）。
纯函数那两族在 `tests/test_stage4_beats.py` 与 `tests/test_stage4_context.py`
——**它们不该因为语料不在就没人查**。

⚠⚠ **本层的对账天生是【两个数】**，而那不是缺陷：
`writer_context` **3/3** 本对得上，`plan_chapters` 只有 **2/3**——
`成年人不做选择` 那份计划**缺 `_provenance`，载体拒收**
（0 成本提升路把来源账整个丢了，`corpus.MISSING_PROVENANCE` 登记在案）。
⇒ 节点在那本上给**显式终态**而不是一份「PASS」，本文件专门配了一条测试盯着它。
**不许靠伪造一份来源账把它变绿。**

⚠ 另一条与前两层相反的结论也在这里钉着：**stage4 的徽章是真的 `pass`**。
本层判据表里**没有一条 `sev='unver'`**，而 stage6/stage7 各有 8/7 条常驻 `unver`、
结构上永远最好只有 amber。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from contracts.rules import REGISTRY
from contracts.schema.stage4 import PlanChapters, WriterContext
from novel.errors import ArtifactUnreadable
from novel.run import RunContext
from novel.stages.stage4 import plan, prompts
from tests import corpus, sandbox

#: 本层要的上游 + 它自己那两份。
STAGE4_INPUTS = ('stage1/main_text.json', 'stage2/global_grounding.json',
                 'stage2/chapter_blueprints.json',
                 'stage4/plan_chapters.json', 'stage4/writer_context.json')


def books() -> list[str]:
    if corpus.corpus_root() is None:
        return []
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage4/writer_context.json'})


pytestmark = pytest.mark.skipif(
    not books(), reason='盘上没有 stage4 产物 —— 本文件整份**没在查**，不是「查过没事」')


def _readable(book: str) -> bool:
    """这本书的计划**过得了载体**吗。

    ⚠ 不去 `try: model_validate`——那会把「载体拒收」与「文件不在」混成一件事。
    直接查那张登记表：**它是一条有名有姓的事实，不是一次运行时意外。**
    """
    sid = book.rsplit('_', 1)[-1]
    return (sid, 'stage4/plan_chapters.json') not in corpus.MISSING_PROVENANCE


def readable_books() -> list[str]:
    """⚠⚠ **要走节点那几条的参数化用它，不要用 `books()` 再 `pytest.skip`。**

    差别不是风格：`skip` 会让本仓那条「每一条 skip 都明说没在查」的纪律多出三条
    **本来就不该存在**的记录——那本书不是「没查」，是**它由另一条专门的测试查着**
    （`test_the_book_whose_plan_has_no_provenance_is_a_terminal_state`）。
    多一条假 skip，下一个人核对 skip 数时就得先排除它。
    """
    return [b for b in books() if _readable(b)]


def _ctx_for(box: sandbox.Sandbox, node: str, *, paid: bool = False) -> RunContext:
    return RunContext.open(box.root, box.sid, node, paid=paid)


# ── ⭐ 一、盘上三本：先重建上下文，再复检 ────────────────────────────────

@pytest.mark.parametrize('book', readable_books())
def test_rebuilding_the_context_then_checking_gives_a_green_badge(book):
    """⭐⭐ **stage4 是第一个徽章能真的是 `pass` 的已移植层。**

    两步：`stage4.context` 重建 → `stage4.plan --check-only`。
    ⚠ **顺序是承重的**：沙箱里的上游是按新契约现算的，字节与原型那次不同 ⇒
    盘上那份 ctx 记的边指向**另一版**计划，`context_link` 会**正确地**判 `stale`。
    重建之后它才对得上——那正是这条 HARD 存在的理由
    （代价付过一次：某轮 23 章成稿与盘上任何一版 ctx 都配不上，匹配 1/23）。
    """
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        plan.run_context(_ctx_for(box, 'stage4.context'))
        rep = plan.run_check_only(_ctx_for(box, 'stage4.plan'))

        want = {r.rule for r in REGISTRY.of_stage('stage4')}
        assert {ln.rule for ln in rep.rules} == want, '24 条判据一行不少'
        assert len(rep.rules) == 24
        assert not [ln.rule for ln in rep.rules if ln.status == 'skipped'], \
            '有判据注册了却没有任何代码执行过它'
        assert not [ln.rule for ln in rep.rules if ln.sev == 'hard' and ln.status == 'hit'], \
            'HARD 命中——盘上这三本一条都不该红'
        assert rep.badge == 'pass' and rep.passed, (
            f'badge={rep.badge}：本层没有一条常驻 `unver`，重建过上下文之后它就该是绿的。'
            f'unver 的有：{[ln.rule for ln in rep.rules if ln.status == "unver"]}')
    finally:
        box.close()


@pytest.mark.parametrize('book', books())
def test_the_hit_set_matches_the_measured_one(book):
    """命中集逐本可复算。⚠ 钉的是**集合**，不是「有没有命中」。"""
    want = {14: {'flat_chapter', 'beat_size'},          # 成年人（此处只跑判据，不走节点）
            8: {'flat_chapter', 'beat_size'},           # 头牌
            9: {'beat_size', 'verbatim_starved'}}       # 三岁：flat_chapter 0 命中
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        from novel.stages.stage4.beats import beat_index
        from novel.stages.stage4.checks import check
        raw = {rel: box.read(rel) for rel in STAGE4_INPUTS}
        bidx = beat_index(raw['stage2/chapter_blueprints.json'], raw['stage1/main_text.json'])
        outs = check(raw['stage4/plan_chapters.json'],
                     blueprints=raw['stage2/chapter_blueprints.json'],
                     main_text=raw['stage1/main_text.json'],
                     global_g=raw['stage2/global_grounding.json'],
                     ctx=raw['stage4/writer_context.json'],
                     ctx_link=('ok', '对得上'), ctx_source='—', bidx=bidx)
        n_ch = len(raw['stage4/plan_chapters.json']['output_chapters'])
        hit = {o.rule for o in outs
               if o.findings and o.unver_why is None and REGISTRY.get(o.rule).sev != 'metric'}
        assert hit == want[n_ch], f'{book} 的命中集变了：{sorted(hit)}'
    finally:
        box.close()


def test_the_book_whose_plan_has_no_provenance_is_a_terminal_state():
    """⚠⚠ **它必须是显式终态，不是一份「PASS」。**

    `成年人不做选择` 的 `plan_chapters.json` 根本没有 `_provenance`——
    0 成本提升路把来源账整个丢了（落盘闸门的失败支用裸 `json.dump` 写 `.INVALID`、
    提升时又没传 `prov=`）。**那份产物留在盘上的意义就是提醒人这条路会丢账。**
    ⇒ 载体拒收，节点照常抛；**不许静默降级**。
    """
    bad = [b for b in books() if not _readable(b)]
    assert bad, '那本缺来源账的书不在语料里了？`corpus.MISSING_PROVENANCE` 该跟着改'
    for book in bad:
        box = sandbox.Sandbox(book, STAGE4_INPUTS)
        try:
            with pytest.raises(ArtifactUnreadable, match='_provenance'):
                plan.run_check_only(_ctx_for(box, 'stage4.plan'))
            with pytest.raises(ArtifactUnreadable, match='_provenance'):
                plan.run_context(_ctx_for(box, 'stage4.context'))
        finally:
            box.close()


@pytest.mark.parametrize('book', readable_books())
def test_the_rebuilt_context_lands_on_disk_and_carries_the_plan_edge(book):
    """⭐ **`stage4/plan_chapters.json` 这条边是全仓唯一一处「自己这一层的产物记成上游」**。

    它存在的全部理由就是 `context_link` 那个问题：
    「写手手里这份上下文，是不是照盘上这份计划组的」。
    ⇒ 组装那一条必须走 `store.read`（记边），不是 `read_own`（刻意不记边）。
    """
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        plan.run_context(_ctx_for(box, 'stage4.context'))
        art = WriterContext.model_validate(box.read('stage4/writer_context.json'))
        inputs = art.provenance.inputs or {}
        assert 'stage4/plan_chapters.json' in inputs, '那条边没记上——context_link 从此说不清'
        assert set(inputs) == {'stage1/main_text.json', 'stage2/chapter_blueprints.json',
                               'stage2/global_grounding.json', 'stage4/plan_chapters.json'}
        # 0 成本组装 ⇒ 来源账里**结构上没有** `model`
        assert not hasattr(art.provenance, 'model')
    finally:
        box.close()


@pytest.mark.parametrize('book', readable_books())
def test_context_link_says_stale_before_the_rebuild(book):
    """⚠ **它必须先红一次**，否则这条 HARD 在盘上就是零反例样本。

    沙箱里的上游按新契约现算 ⇒ 计划的字节与原型那次不同，
    而盘上那份 ctx 记的是**原型那一版**的指纹。这是一次**真的** `stale`。
    """
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        rep = plan.run_check_only(_ctx_for(box, 'stage4.plan'))
        line = next(ln for ln in rep.rules if ln.rule == 'context_link')
        assert line.status == 'hit' and rep.badge == 'fail'
        assert '另一版' in line.findings[0].detail
    finally:
        box.close()


# ── 二、prompt 逐字 ──────────────────────────────────────────────────────

_PROTO = Path(__file__).resolve().parent.parent.parent / 'Novel_refactoring' / \
    'Rapid_Prototyping' / 'Stage4' / 'n4_plan.py'


def _proto_template() -> str:
    """从原型源码里取 `N4_SYS` 的**模板**（插值之前）。走 `ast`，不做字符串切片。"""
    tree = ast.parse(_PROTO.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == 'N4_SYS'):
            return node.value.args[0].value          # type: ignore[union-attr,attr-defined]
    raise AssertionError('原型里找不到 N4_SYS')


@pytest.mark.skipif(not _PROTO.exists(), reason='原型仓不在旁边——逐字对账无从谈起')
def test_the_rendered_system_prompt_is_byte_for_byte_identical_to_the_prototype():
    """⭐⭐ **钉的是【发出去的字节】，不是模板。**

    F1 那次的教训：`HOOK_SYS` 确实与原型逐字节相同，而从原型搬进 `invariants` 的
    **三句说明被转述了一遍**，渲染结果短了 48 字——丢掉的正是那一层唯一还禁的东西。
    **只钉模板的测试抓不到它**：占位符两侧一个字节都没变。
    """
    from contracts.invariants import (
        BEAT_MIN_CHARS, BEAT_NATURAL_MAX, LOCKED_OP, OP_BAND, STYLE_WORDS,
        VERBATIM_BUDGET_FACTOR,
    )
    rendered = (_proto_template()
                .replace('$BANDS', '{:g}~{:g}×'.format(*OP_BAND[LOCKED_OP]))
                .replace('$VERBATIM_FACTOR', '{:g}'.format(VERBATIM_BUDGET_FACTOR))
                .replace('$BEAT_NATURAL', f'{BEAT_MIN_CHARS}~{BEAT_NATURAL_MAX}')
                .replace('$BEAT_MIN', str(BEAT_MIN_CHARS))
                .replace('$STYLE_WORDS', '、'.join(f'「{w}」' for w in STYLE_WORDS)))
    assert prompts.PLAN_SYS == rendered, '渲染出来的 system 与原型对不上'
    assert len(prompts.PLAN_SYS) == 2192, '字数变了——先确认是有意改的 prompt 还是插值漂了'
    # 15 个写法词**整份**在里面：手抄一个子集正是原型漂掉的那次
    assert all(f'「{w}」' in prompts.PLAN_SYS for w in STYLE_WORDS)


@pytest.mark.parametrize('book', books())
def test_dry_run_builds_the_same_user_message_the_paid_path_would_send(book):
    """`--dry-run` 与真跑**共用同一个构造入口**（`R-11.8`）——

    否则「预览里看到的」与「真发出去的」是两件事，而那种分家没有任何东西会报错。
    ⚠ 顺带钉住五个块头：原型那一段**没有具名常量**，只钉 system 会漏掉整个下发面。
    """
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        system, user = plan.build_prompt(_ctx_for(box, 'stage4.plan'))
        assert system == prompts.PLAN_SYS
        for head in (prompts.BLOCK_INDEX, prompts.BLOCK_SKELETON,
                     prompts.BLOCK_SIZES, prompts.BLOCK_BODY):
            assert head in user, f'块头缺了：{head[:20]}'
        assert '"plan_beat":"p01_01"' in user, '下发的是拍不是源 beat'
        # ⚠⚠ **`beat_id` 不许进规划视野**：它一旦下发，模型就有了重新编组的抓手，
        #    而那正是这一层收走的自由度。
        assert '"beat_id"' not in user
        assert 'b01_01' not in user.split(prompts.BLOCK_BODY)[0], '骨架块里漏出了源 beat 编号'
    finally:
        box.close()


# ── 三、表间守卫（移植一层漏改一处就红）─────────────────────────────────

def test_stage4_has_exactly_two_nodes_and_only_one_of_them_checks():
    """一个 stage 两个节点，而 `--check-only` 只挂在其中一个上。"""
    from contracts.invariants import NODES
    nodes = {n for n, s in NODES.items() if s.stage == 'stage4'}
    assert nodes == {'stage4.plan', 'stage4.context'}
    assert [n for n in nodes if '--check-only' in NODES[n].flags] == ['stage4.plan']
    # `stage4.context` 的 `flags=()` 是**终态**：它本身就是那个动作
    assert NODES['stage4.context'].flags == ()


def test_the_registered_rules_all_produce_a_row_on_a_real_book():
    """⭐ 照抄 `tests/test_stage3.py` 的形态：**24 条一行不少 + 0 条 `skipped`**。

    `build()` 遍历判据注册表而不是遍历 findings ⇒ 一条判据不可能因为
    「实现忘了调」而从报告里消失，它会变成 `skipped`。本条盯的就是那个 0。
    """
    book = next(b for b in books() if _readable(b))
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        rep = plan.run_check_only(_ctx_for(box, 'stage4.plan'))
        assert len(rep.rules) == len(REGISTRY.of_stage('stage4')) == 24
        assert all(ln.status != 'skipped' for ln in rep.rules)
        assert all(ln.scanned for ln in rep.rules), '绿灯必须报出自己的分母（判据 31）'
        assert len(rep.retired) == 10, '退役表 10 条，随报告常驻列出'
    finally:
        box.close()
