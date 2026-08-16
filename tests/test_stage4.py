#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 当前契约的节点、prompt、闸门和免费维护路径。"""
from __future__ import annotations

import pytest

from contracts.rules import REGISTRY
from contracts.schema.stage4 import WriterContext
from novel.run import RunContext
from novel.stages.stage4 import plan, prompts
from tests import corpus, sandbox

#: 本层要的上游 + 它自己那两份。全链根由 `sandbox.Sandbox` 恒带，不列在这里。
STAGE4_INPUTS = ('stage1/main_text.json', 'stage2/global_grounding.json',
                 'stage2/chapter_blueprints.json',
                 'stage4/plan_chapters.json', 'stage4/writer_context.json')


def books() -> list[str]:
    if corpus.corpus_root() is None:
        return []
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage4/writer_context.json'})


pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(
        not books(), reason='盘上没有 stage4 产物 —— 本文件整份**没在查**，不是「查过没事」'),
]


def _ctx_for(box: sandbox.Sandbox, node: str, *, paid: bool = False) -> RunContext:
    return RunContext.open(box.root, box.sid, node, paid=paid)


# ── ⭐ 一、盘上三本：先重建上下文，再复检 ────────────────────────────────

@pytest.mark.parametrize('book', books())
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
        assert {ln.rule for ln in rep.rules} == want, '26 条判据一行不少'
        assert len(rep.rules) == 26
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


@pytest.mark.parametrize('book', books())
def test_the_context_is_fresh_even_when_the_paid_node_rebuilds_it_in_the_same_batch(book):
    """⭐⭐ **付费那一趟顺手重建的上下文，必须当场就是 `fresh`。**

    `stage4.plan` 写完计划会同批调 `run_context`，而 `consumed` 投影是按
    **(节点, 上游)** 登记的：`stage4.plan` 与 `stage4.context` 对
    `stage2/chapter_blueprints.json` 读的不是同一批字段，`stage4/plan_chapters.json`
    更只在后者名下登记过。借调用方的 ctx 写这一份 ⇒ 写盘按 `stage4.plan` 记账、
    freshness 按产地 `stage4.context` 复算，两侧永远对不上，
    一份刚组好的上下文当场判 `consumed_changed`。

    ⚠ 代价是主链断在下一层：`stage5.write` 读它会被 `_refuse_stale_upstream` 拒绝，
    而报错长得像数据问题。⇒ 这里同时钉住两件事：写它的节点身份，与复算后的结论。
    """
    from novel.consumption import producer_node

    assert producer_node(plan.CTX_REL) == plan.CTX_NODE, \
        '产地节点与写它的节点分家了——consumed 投影两侧必然各算一份'

    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        # 付费节点的 ctx：同批重建走的就是这一条
        plan.run_context(_ctx_for(box, 'stage4.plan'))
        store = plan.ArtifactStore(_ctx_for(box, 'stage4.context'))
        fr = store.freshness(plan.CTX_REL)
        assert fr.state == 'fresh', (
            f'刚组好的上下文判 {fr.state}（{fr.reason}）：'
            f'{[(e.key, e.reason) for e in fr.edges if e.state != "fresh"]}')
        art = WriterContext.model_validate(box.read('stage4/writer_context.json'))
        assert all(edge.consumed for edge in (art.provenance.inputs or {}).values()), \
            '有边的 consumed 是 null ⇒ 那条边的投影没在本节点名下登记过'
    finally:
        box.close()


# ── 二、当前 prompt 契约 ─────────────────────────────────────────────────

def test_system_prompt_contains_every_current_style_word():
    from contracts.invariants import STYLE_WORDS

    assert prompts.PLAN_SYS.strip()
    assert all(f'「{word}」' in prompts.PLAN_SYS for word in STYLE_WORDS)


@pytest.mark.parametrize('book', books())
def test_dry_run_builds_the_same_user_message_the_paid_path_would_send(book):
    """`--dry-run` 与真跑**共用同一个构造入口**（`R-11.8`）——

    否则「预览里看到的」与「真发出去的」是两件事，而那种分家没有任何东西会报错。
    ⚠ 顺带钉住五个块头：原型那一段**没有具名常量**，只钉 system 会漏掉整个下发面。
    """
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        system, user = plan.build_prompt(_ctx_for(box, 'stage4.plan'))
        # ⚠ 比的是**渲染后**那一份（`plan_system()` = `PLAN_SYS` + JSON 形状块），
        #   不是裸常量：`_common.md` §八 明写来源账记的是插值之后的指纹。
        #   本条原来断言 `system == prompts.PLAN_SYS`，于是 `with_json_shape` 一进来就恒红，
        #   而它报的「预览与真跑分家」是假的——两边当时都已经渲染过了。
        assert system == prompts.plan_system()
        assert prompts.PLAN_SYS in system, '渲染把正文吃掉了——形状块不该替换 system 本体'
        for head in (prompts.BLOCK_INDEX, prompts.BLOCK_SKELETON,
                     prompts.BLOCK_SIZES, prompts.BLOCK_BODY):
            assert head in user, f'块头缺了：{head[:20]}'
        assert '"plan_beat":"p01_01"' in user, '下发的是源内容单元，不是 Stage2 beat'
        # ⚠⚠ **`beat_id` 不许进规划视野**：它一旦下发，模型就有了重新编组的抓手，
        #    而那正是这一层收走的自由度。
        assert '"beat_id"' not in user
        # ⚠ 只量**骨架块**。断言原来切的是 `BLOCK_BODY` 之前的**全部**下发面，
        #   而 `BLOCK_FACT_REFS` 里的 `events{beat_id: 章号}` 是**契约要求**下发的
        #   （`fact_refs.catalog` 产它、`validate_plan_refs` 拿它验 `kind=='event'` 的引用）
        #   ⇒ 旧口径把一条合法下发读成「漏出」，而它想守的是骨架那一块。
        skeleton = user.split(prompts.BLOCK_SKELETON)[1]
        for nxt in (prompts.BLOCK_SIZES, prompts.BLOCK_BODY):
            skeleton = skeleton.split(nxt)[0]
        assert 'b01_01' not in skeleton, '骨架块里漏出了源 beat 编号'
    finally:
        box.close()


# ── 三、落盘闸门与提升路（合成样本 —— 盘上这三本一条 HARD 都不红）───────────

def _synthetic_response(box: sandbox.Sandbox, *, poison: bool) -> dict:
    """拿盘上那份计划反推一份**模型响应**（带 `plan_beats`、不带 `from_beats`）。

    `poison=True` ⇒ 往一个 goal 里塞一个写法词。**盘上三本 `style_words` 0 命中**，
    要看闸门真的拦得住就只能合成——那 16 条 HARD 在盘上是零反例样本。
    """
    from novel.stages.stage4.beats import plan_beat_id
    src = box.read('stage4/plan_chapters.json')
    out = {'reconstruction_strategy': src['reconstruction_strategy'], 'output_chapters': []}
    for ch in src['output_chapters']:
        c = {k: ch[k] for k in ('output_ordinal', 'from_source_ordinals',
                                'chapter_goal', 'open_hook_goal', 'end_hook_goal')}
        rows = [{'plan_beats': [plan_beat_id(ch['output_ordinal'], i)],
                 'budget_chars': be['budget_chars'],
                 'narrative_role': 'setup', 'delivery': 'scene',
                 'transition': 'start' if i == 1 else 'continue'}
                for i, be in enumerate(ch['segments'], 1)]
        if len(rows) >= 4:
            rows = [rows[-1], *rows[:-1]]
            rows[0].update(narrative_role='cold_open', transition='start')
            rows[1]['transition'] = 'earlier'
            for row in rows[2:]:
                row['transition'] = 'continue'
        first_i = int(rows[0]['plan_beats'][0].rsplit('_', 1)[1]) - 1
        last_i = int(rows[-1]['plan_beats'][-1].rsplit('_', 1)[1]) - 1
        c['open_hook_goal']['fact_refs'] = [{
            'kind': 'event', 'ref': ch['segments'][first_i]['from_beats'][0],
            'chapter': ch['output_ordinal']}]
        c['end_hook_goal']['fact_refs'] = [{
            'kind': 'event', 'ref': ch['segments'][last_i]['from_beats'][-1],
            'chapter': ch['output_ordinal']}]
        c['segments'] = rows
        out['output_chapters'].append(c)
    if poison:
        # ⚠ `rationale` 是可空键：某些书的计划里它就是 `null`，`+=` 会当场 TypeError
        #   ——那是**测试自己炸了**，而它想验的闸门一次都没跑到。
        goal = out['output_chapters'][0]['chapter_goal']
        goal['rationale'] = (goal.get('rationale') or '') + '，要有张力'
    return out


def _provenance(ctx: RunContext, up) -> object:
    from contracts.schema.stage4 import PlanProvenance
    return PlanProvenance(code='0' * 10, root_clean_sha1=ctx.root_clean_sha1, inputs=ctx.edges,
                          prompts={'N4_SYS': '0' * 10}, model='deepseek-v4-pro',
                          params=plan._params(up))


def test_a_single_hard_hit_sends_the_whole_plan_to_invalid():
    """⭐ **落盘闸门照 stage3 不照 stage6**：本层一次产**一份** ⇒ 任何一条 hard 就 `.INVALID`。

    ⚠ 而且这条闸**必须配一条 0 成本提升路**：闸门是我们自己加的，
    不给出路，一次付费产出就被永久锁死。下一条测试跑的就是那条路。
    """
    from novel.errors import GateBlocked
    book = books()[0]
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        ctx = _ctx_for(box, 'stage4.plan')
        store = plan.ArtifactStore(ctx)
        up = plan._Upstreams(ctx, store)
        before = box.read('stage4/plan_chapters.json')
        with pytest.raises(GateBlocked, match='style_words'):
            plan.write_plan(ctx, store, _synthetic_response(box, poison=True), up,
                            provenance=_provenance(ctx, up))
        assert (box.dir / 'stage4/plan_chapters.INVALID.json').exists(), '问题稿没存下来'
        assert box.read('stage4/plan_chapters.json') == before, \
            '**正式产物被覆盖了**——先校验后落盘那条就白写了'
    finally:
        box.close()


def test_revalidate_promotes_the_fixed_plan_and_rebuilds_the_context():
    """⭐ 0 成本提升路：手改那一行 → `--revalidate` → 提升 **并重建上下文**。

    ⚠ **重建是必须的**：计划换了一版而上下文没跟上，`context_link` 会立刻红。
    """
    import json

    from novel.errors import GateBlocked
    book = books()[0]
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        ctx = _ctx_for(box, 'stage4.plan')
        store = plan.ArtifactStore(ctx)
        up = plan._Upstreams(ctx, store)
        with pytest.raises(GateBlocked):
            plan.write_plan(ctx, store, _synthetic_response(box, poison=True), up,
                            provenance=_provenance(ctx, up))
        # 人手改掉那个写法词（0 成本），再提升
        # ⚠ `rationale` 的取值面是「非空串或 null」：原来无条件 `.replace()` 之后，
        #   本来就是 `null` 的那些书会得到一个**空串**——载体当场拒收，而这条路
        #   报出来的是「产物读不回来」，不是它要验的「提升成功没有」。
        inv = box.dir / 'stage4/plan_chapters.INVALID.json'
        fixed = json.loads(inv.read_text(encoding='utf-8'))
        goal = fixed['output_chapters'][0]['chapter_goal']
        goal['rationale'] = (goal['rationale'] or '').replace('，要有张力', '') or None
        inv.write_text(json.dumps(fixed, ensure_ascii=False), encoding='utf-8')

        rep = plan.run_revalidate(_ctx_for(box, 'stage4.plan'))
        assert rep.passed and rep.badge == 'pass', f'提升之后该是绿的，实得 {rep.badge}'
        assert not inv.exists(), '.INVALID 提升之后该消失'
        ctx_art = WriterContext.model_validate(box.read('stage4/writer_context.json'))
        assert 'stage4/plan_chapters.json' in (ctx_art.provenance.inputs or {})
        line = next(ln for ln in rep.rules if ln.rule == 'context_link')
        assert line.status == 'ok', '提升之后上下文没跟着重建——context_link 该红而没红'
    finally:
        box.close()


# ── 四、两条 0 成本路 ────────────────────────────────────────────────────

@pytest.mark.parametrize('book', books())
def test_the_identity_baseline_lands_on_disk_as_an_artifact(book):
    """⭐ **恒等基线是一份产物，不是一次 `print`。**

    原型这条路整个输出是 `print` + 返回码 ⇒「跑过且写手手里有这些」与
    「从没跑过」**在盘上长得一模一样**（`Q-2`）。
    ⚠ 它**不吃计划** ⇒ 那本载体拒收的书照样跑得动，所以这里参数化的是 `books()`。
    """
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        n_beats, rep = plan.run_identity(_ctx_for(box, 'stage4.plan'))
        art = WriterContext.model_validate(box.read('stage4/writer_context.identity.json'))
        assert n_beats == sum(len(c.segments) for c in art.chapters)
        assert all(c.chapter_goal.facts == [f'第 {c.output_ordinal} 章整体范围']
                   for c in art.chapters), \
            '恒等计划的边界引用必须解析成可读事实，不能把内部 id 或审计理由发给写手'
        # ⚠ 它写的是**另一份文件**，正式那份一个字节都不许动
        assert box.read('stage4/writer_context.json') != box.read(
            'stage4/writer_context.identity.json')
        # `context_link` 这一趟按设计不排 ⇒ `unver`，徽章 amber。**这不是缺陷。**
        assert next(ln for ln in rep.rules if ln.rule == 'context_link').status == 'unver'
    finally:
        box.close()


@pytest.mark.parametrize('book', books())
def test_names_only_reads_the_roster_and_writes_nothing(book):
    """⚠ **它一个字节都不写**，而本表默认按会写算 ⇒ 不登记的方向是**反的**。"""
    from contracts.invariants import WRITE_EFFECTS
    assert WRITE_EFFECTS['--names-only'] == '', \
        '不登记 ⇒ `/api/enums` 会把它派生成「写 plan_chapters.json」——那是在说假话'
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        before = sorted(p.name for p in (box.dir / 'stage4').iterdir())
        rows = plan.run_names_only(_ctx_for(box, 'stage4.plan'))
        assert sorted(p.name for p in (box.dir / 'stage4').iterdir()) == before, '它写盘了'
        assert len(rows) == len(box.read('stage1/main_text.json'))
        # ⚠⚠ **只有 `char_id` 的集合单调，名字的集合不单调**——这不是 bug：
        #    章级表退回的是「到本章为止出现最多的有据称呼」，读到更多原文之后
        #    同一个人的叫法**会变**（某人 ch2 叫「书童」、后面才有本名）。
        ids = [set(a) for _o, a, _h in rows]
        assert all(ids[i] <= ids[i + 1] for i in range(len(ids) - 1)), \
            '章级名字表的 char_id 集合不是单调的——那说明「到本章为止」的累积算错了'
    finally:
        box.close()


def test_names_only_returns_a_mapping_for_every_chapter():
    """称呼可能随章变化，因此接口必须保留 `char_id -> 称呼` 映射。"""
    for book in books():
        box = sandbox.Sandbox(book, STAGE4_INPUTS)
        try:
            rows = plan.run_names_only(_ctx_for(box, 'stage4.plan'))
            assert rows and all(isinstance(names, dict) for _o, names, _held in rows)
        finally:
            box.close()


def test_identity_action_reports_the_file_it_really_writes():
    """`--identity` 说出基线专用文件，不得冒充正式计划。

    只按 flag 登记会把跨节点差异压平；这一格因此必须走
    `(node, flag)` 覆盖，并且与 `run_identity` 的真实落点同源受守卫。
    """
    from contracts.invariants import NODE_WRITE_EFFECTS, WRITE_EFFECTS
    from novel.api.enums import payload
    assert '--identity' not in WRITE_EFFECTS
    assert NODE_WRITE_EFFECTS[('stage4.plan', '--identity')] == plan.IDENTITY_CTX_REL
    act = next(a for a in payload()['actions']
               if a['kind'] == 'stage4.plan' and a['extra'] == ['--identity'])
    assert act['writes_disk'] is True
    assert act['writes'] == plan.IDENTITY_CTX_REL


# ── 五、表间守卫（移植一层漏改一处就红）─────────────────────────────────

def test_stage4_has_exactly_two_nodes_and_only_one_of_them_checks():
    """一个 stage 两个节点，而 `--check-only` 只挂在其中一个上。"""
    from contracts.invariants import NODES
    nodes = {n for n, s in NODES.items() if s.stage == 'stage4'}
    assert nodes == {'stage4.plan', 'stage4.context'}
    assert [n for n in nodes if '--check-only' in NODES[n].flags] == ['stage4.plan']
    # `stage4.context` 的 `flags=()` 是**终态**：它本身就是那个动作
    assert NODES['stage4.context'].flags == ()


def test_the_registered_rules_all_produce_a_row_on_a_real_book():
    """⭐ 照抄 `tests/test_stage3.py` 的形态：**26 条一行不少 + 0 条 `skipped`**。

    `build()` 遍历判据注册表而不是遍历 findings ⇒ 一条判据不可能因为
    「实现忘了调」而从报告里消失，它会变成 `skipped`。本条盯的就是那个 0。
    """
    book = books()[0]
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        rep = plan.run_check_only(_ctx_for(box, 'stage4.plan'))
        assert len(rep.rules) == len(REGISTRY.of_stage('stage4')) == 26
        assert all(ln.status != 'skipped' for ln in rep.rules)
        assert all(ln.scanned for ln in rep.rules), '绿灯必须报出自己的分母（判据 31）'
    finally:
        box.close()
