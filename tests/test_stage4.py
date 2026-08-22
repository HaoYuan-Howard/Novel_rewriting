#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 当前契约的节点、prompt、闸门和免费维护路径。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from contracts.rules import REGISTRY
from contracts.schema.stage4 import WriterContext
from novel.errors import UpstreamMissing
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


def needs_corpus(test):
    test = pytest.mark.corpus(test)
    return pytest.mark.skipif(
        not books(), reason='盘上没有 stage4 语料——这条集成用例没在查')(test)


def _ctx_for(box: sandbox.Sandbox, node: str, *, paid: bool = False) -> RunContext:
    return RunContext.open(box.root, box.sid, node, paid=paid)


# ── ⭐ 一、盘上三本：先重建上下文，再复检 ────────────────────────────────

@pytest.mark.corpus
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
        assert {ln.rule for ln in rep.rules} == want, '31 条判据一行不少'
        assert len(rep.rules) == 31
        assert not [ln.rule for ln in rep.rules if ln.status == 'skipped'], \
            '有判据注册了却没有任何代码执行过它'
        assert not [ln.rule for ln in rep.rules if ln.sev == 'hard' and ln.status == 'hit'], \
            'HARD 命中——盘上这三本一条都不该红'

        # ⚠ **绿灯必须带扫描分母**（判据 31 / LESSONS 第 2 条）：`structure_units` 的分母是
        #   「源内容单元数」，它归零时这条 HARD 会**合法地**报绿——而那与「查过、干净」
        #   在报告上长得一模一样。上面那条 `HARD 命中 == 0` 因此单独成立不了。
        #   ⇒ 这一条是 `R-5.6` 里「源单元守恒」那半在语料上的唯一分母证据。
        units = next(ln for ln in rep.rules if ln.rule == 'structure_units')
        assert units.scanned and not units.scanned.startswith('0 '), (
            f'`structure_units` 的扫描分母是 {units.scanned!r}——这一层没东西可查，'
            f'它的绿不代表源内容单元真的守恒过')

        assert rep.badge == 'pass' and rep.passed, (
            f'badge={rep.badge}：本层没有一条常驻 `unver`，重建过上下文之后它就该是绿的。'
            f'unver 的有：{[ln.rule for ln in rep.rules if ln.status == "unver"]}')
    finally:
        box.close()


@pytest.mark.corpus
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


@pytest.mark.corpus
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


def test_the_reconstruction_threshold_reaches_the_prompt_from_its_single_home():
    """⭐ 「一章大到几个单元才要做结构决策」这个数，prompt 与判据**只能有一份**。

    它从前是 prompt 里一个手抄的 `4`，判据侧 `checks.py` 另有一份行内字面量——
    两份数字各自漂的下场 `Q-6` 已经写过一遍。⚠ 顺带钉住渲染没漏：`fill()` 会对
    缺值的占位符抛异常，但**多写一个 `$` 字面量它不管**，那会原样发给模型。
    """
    from contracts.invariants import PLAN_RECONSTRUCT_MIN_UNITS

    assert str(PLAN_RECONSTRUCT_MIN_UNITS) in prompts.PLAN_SYS
    assert '$' not in prompts.plan_system(), 'system prompt 里残留了未渲染的占位符'


def test_keeping_the_source_order_is_not_a_violation_when_something_else_changed():
    """⭐⭐ **换口径的验收断言：顺叙不再是违规。**

    2026-08-19 之前 `structure_changed` 判的是「源单元 ≥4 的章不得仍按源序展开」——
    保持源序 = 阻断落盘。于是够大的章，模型没有「本章顺序本来就对」这个选项，只能移位；
    实测代价是交付那本书 11/11 章 `cold_open` 倒叙起手、全书向前推进的 `continue` 只剩 1 处。

    新口径判的是「与恒等基线逐格相同」：这里保持源序、也没有合并，只是把其中一拍的承载
    压成 `summary`——**三根轴动了一根，就不该红**。
    """
    from tests.hard_cases import _MAIN4, _BPS4_PRESENT, _b4, _ch4, _plan4

    chapters = [_ch4(1, [_b4('b1_01'), _b4('b1_02'), _b4('b1_03'), _b4('b1_04')])]
    chapters[0]['segments'][2]['delivery'] = 'summary'
    hit = {f.rule for f in _plan4(chapters, blueprints=_BPS4_PRESENT,
                                  main_text=[_MAIN4[0]])}
    assert 'structure_changed' not in hit, \
        f'保持源序 + 一处 summary 仍被判成恒等改写——换口径没生效（命中 {sorted(hit)}）'


def test_the_reconstruction_gate_has_not_been_relaxed_into_always_green():
    """⭐ **反向**：三根轴一根都没动的那一份，仍然必须红。

    ⚠ 这条与上一条是**一对**。只留上一条，谁把判定式「补全」成三个松散的 `or`
    （合并 or summary or 入口移位）都能让它继续绿——而那样这条 HARD 就恒绿了：
    盘上实测 11/11 章有合并、9/11 章有 summary。靶子取自生产落点 `identity_plan()`。
    """
    from tests.hard_cases import _identity_structure4

    assert 'structure_changed' in {f.rule for f in _identity_structure4()}


# ─────────────────────────────────────────────────────────────────────────────
# `spine` 与 `structure_changed` 的换项读数（2026-08-22，批 3）
# ─────────────────────────────────────────────────────────────────────────────

def _flat_detail_chapter() -> list:
    """一单元一源单元、按源序、无 `summary`——三根轴一根没动 ⇒ 现役口径**红**。

    但章内详略极不均（拍级 `预算/源` 相对本章均值 0.5~2.5，冲出 `PLAN_BUDGET_BAND`）
    ⇒ 候选的第四根轴 `axis_reshape` **命中**。
    ⚠ 预算刻意配成全章合计 400 = 全章源字数 ⇒ `budget_band` / `plan_book_band` 都绿：
    靶子要证明的是「换项会改变结论」，顺带红两条别的 HARD 会让人以为那是设计的一部分。
    """
    from tests.hard_cases import _MAIN4, _BPS4_PRESENT, _b4, _bidx4_present, _ch4, _plan4

    return _plan4([_ch4(1, [_b4('b1_01', budget=50), _b4('b1_02', budget=50),
                            _b4('b1_03', budget=50), _b4('b1_04', budget=250)])],
                  blueprints=_BPS4_PRESENT, main_text=[_MAIN4[0]], bidx=_bidx4_present())


def _regrouped_flat_budget_chapter() -> list:
    """反过来：分组重画（4 源单元 → 3 写作单元）但章内详略**平坦**（rel 恒 1.00）。

    ⇒ 现役口径绿（`axis_group` 成立），换项之后会红。与上面那一份是一对。
    """
    from tests.hard_cases import _MAIN4, _BPS4_PRESENT, _b4, _bidx4_present, _ch4, _plan4

    return _plan4([_ch4(1, [_b4('b1_01', 'b1_02', budget=200), _b4('b1_03', budget=100),
                            _b4('b1_04', budget=100)])],
                  blueprints=_BPS4_PRESENT, main_text=[_MAIN4[0]], bidx=_bidx4_present())


def test_the_reconstruction_gate_still_judges_grouping_not_pacing():
    """⭐⭐ **换项还没有发生。** 这一对断言把「读数」与「判定式」之间那道线钉死。

    批 3 只加了读数（`structure_reshaped`），`structure_changed` 的判定式**一行没动**。
    换项那天这条测试会红——**那是它的用途**：换项是一次产品语义变更（详略从"没人判"
    变成"够大的章不做就退回"），必须有人重新裁决一次，而不是顺手让绿灯继续绿。
    """
    detail_only = {f.rule for f in _flat_detail_chapter()}
    group_only = {f.rule for f in _regrouped_flat_budget_chapter()}
    assert 'structure_changed' in detail_only, (
        '只重画了详略、没重画分组的章现在**不该**过关——`axis_reshape` 进判定式了？'
        '若确实要换，先读 `structure_reshaped` 的分布，并把裁决写进 `R-5.6` 的换口径记录')
    assert 'structure_changed' not in group_only, (
        '只重画了分组、详略平坦的章现在**该**过关——`axis_group` 被换掉或被收紧了')


def test_the_swap_readout_reports_the_counterfactual_not_just_a_hit_count():
    """⭐ `structure_reshaped` 要能直接回答「换了会怎样」，而不是只报一个命中数。

    ⚠ 只报「`axis_reshape` 命中 N 章」是回答不了换项问题的：真正要知道的是
    **换项之后那条 HARD 会红哪几章**，而那是命中数与 `axis_delivery` 的联合结果。
    人拿到一个命中数还得自己去每章解谜——那正是 `plan_book_band` 那次
    「全书 1.14× 一句话」的同一个教训。
    """
    detail = next(f.detail for f in _flat_detail_chapter() if f.rule == 'structure_reshaped')
    assert '章内详略重画 1/1' in detail and '现役的分组重画 0/1' in detail
    assert '会红 []' in detail and '现役口径红 [1]' in detail, (
        f'反事实两侧没有同时报出来：{detail}')

    swapped = next(f.detail for f in _regrouped_flat_budget_chapter()
                   if f.rule == 'structure_reshaped')
    assert '章内详略重画 0/1' in swapped and '现役的分组重画 1/1' in swapped
    assert '会红 [1]' in swapped and '现役口径红 []' in swapped, (
        f'这一份换项后会红、现在绿——两个结论必须都摆出来：{swapped}')


def test_a_chapter_whose_pacing_cannot_be_measured_lands_in_neither_bucket():
    """⭐⭐ 判不动的章**单列**，不并进任何一边（判据 21）。

    压成「未命中」会让反事实凭空多红几章、压成「命中」则凭空少红几章——
    两个方向都是替一份量不出来的东西担保。
    """
    from tests.hard_cases import _MAIN4, _BPS4_PRESENT, _b4, _ch4, _plan4

    # 不传 `bidx` ⇒ `_BPS4_PRESENT` 的 beat 没有 span ⇒ 拍级详略整章判不动
    findings = _plan4([_ch4(1, [_b4('b1_01'), _b4('b1_02'), _b4('b1_03'), _b4('b1_04')])],
                      blueprints=_BPS4_PRESENT, main_text=[_MAIN4[0]])
    detail = next(f.detail for f in findings if f.rule == 'structure_reshaped')
    assert '判不动 1' in detail, f'量不出来的那一章被算进了某一边：{detail}'
    assert '说不清的 [1] 章' in detail, f'说不清的章没有被点名：{detail}'


def test_the_plan_prompt_tells_the_planner_what_a_spine_is_and_what_it_is_not():
    """⭐⭐ `spine` 在下发面上必须同时说清两件事：**抄哪个编号**，以及它不是顺序。

    ⚠⚠ 「不许数数」是这一条现在的承重点，而它是实付买回来的：从前 prompt 教的是
    「把各源单元的 events 接起来连续编号」，实测 60 个写作单元错 10 个、9 个是
    「只有 1 件事却填 2/3」。⇒ 下发面必须**要一个编号**（`bNN_NN`），
    并明说不要填数字；形状提示里那一格同样得是编号样子的字符串，
    印一个 `1` 就等于把已经被证伪的走法重新示范一遍。
    ⚠ 「不是顺序」同样承重：`stage5/checks.py` 那条拍内顺序臂在批 3 一行没动，
    模型若把「主干」读成「写在最前面」，成稿会落 `event_landing`。
    """
    hint = prompts.PLAN_HINT['output_chapters'][0]['segments'][0]
    assert 'spine' in hint, '形状提示里没有 spine——模型看不到这一格，必填字段会整轮拒收'
    assert re.fullmatch(r'b\d{2}_\d{2}', str(hint['spine'])), \
        f'形状提示把 spine 印成了 {hint["spine"]!r}：模型会照着填一个数字'
    body = prompts.PLAN_SYS
    assert '本单元的主干是哪一件事' in body
    assert '不要数「第几件」，也不要填数字' in body, '没堵住数数这条路 ⇒ 模型会去数'
    assert '逐字抄下它的编号' in body, '没说清编号从哪里来 ⇒ 模型会编一个'
    assert 'spine 只说主次，不说先后' in body
    assert '仍然是它们在骨架里的先后' in body


def test_the_plan_prompt_gives_both_bands_and_says_which_one_is_per_chapter():
    """⭐⭐ **`PLAN_SYS` 改口的验收断言。**

    改之前这一段写的是「**每一章都是这个倍率，没有例外**：重构不改变篇幅」——
    那是「逐句改写」在计划层的字面命令，也是「成稿只改了措辞」这条链的第一环。
    改之后：总量归全书，单章的倍率是**表达章与章之间轻重的唯一工具**。
    ⚠ 断言两件事：两个数都在（漏一个的后果见 `test_prompts.py` 那一条），
    以及那句旧命令**真的不在了**——只加新句不删旧句，模型会照更严的那句执行。
    """
    from contracts.invariants import BOOK_BAND, PLAN_BUDGET_BAND

    sysp = prompts.plan_system()
    assert '{:g}~{:g}×'.format(*PLAN_BUDGET_BAND) in sysp, '单章带没发下去'
    assert '{:g}~{:g}×'.format(*BOOK_BAND) in sysp, '全书带没发下去'
    assert '每一章都是这个倍率，没有例外' not in sysp, \
        '旧那句「每一章都是这个倍率」还在——放宽了带却仍然命令模型章章同倍率'


# ─────────── 响应侧枚举前缀化（2026-08-22，一次 6 连拒的实付买来的）───────────

def test_no_artifact_side_enum_value_reaches_the_planner_prompt():
    """⭐⭐ **前缀化买的就是这一条：下发面上不许出现任何一个产物侧取值。**

    出现了模型就会照抄，而抄来的值**在另一张表里是合法的** ⇒ schema 闸只能在整份响应
    收完之后才发现，一次全书规划当场作废。实测代价（2026-08-22 批 2 第二次实付）：
    `intent='turn'`×6 章 + `narrative_role='recall'`（从骨架下发面抄的），
    整条 `LADDER` 走到底、**6 次 wire 全被内容闸拒、completion 86 150 全白花、0 产物**。

    ⚠ **两个刻意的例外，各有理由**：
      · `TIME_LAYERS` 的三个值出现在骨架下发面上（那是**输入**，不是模型要填的字段）；
      · 那条「别把骨架里读到的 `recall` 填进 narrative_role」的反面判例——
        点名禁忌值是 stage2 prompt 一直在用的 ✗ 判例写法，删掉它等于收回这次的教训。
    """
    import re

    from contracts.invariants import (
        DELIVERY_MODES, GOAL_INTENTS, STRUCTURAL_ROLES, TEMPORAL_LINKS,
    )

    sysp = prompts.plan_system()
    # 只看带反引号那一类：`scene` / `turn` 这种。裸词（"open"、"return"）在中文散文里
    # 是普通英文单词，扫它们只会制造假红。
    quoted = set(re.findall(r'`([A-Za-z_]+)`', sysp))
    leaked = quoted & set(STRUCTURAL_ROLES + DELIVERY_MODES + TEMPORAL_LINKS + GOAL_INTENTS)
    assert not leaked, (
        f'下发面上还印着产物侧取值 {sorted(leaked)}——模型会照抄，'
        f'而 `PlanBeatResponse` / `PlanGoalResponse` 只收带前缀的那一张表')


def test_the_response_enums_round_trip_through_the_single_mapping_landing_point():
    """⭐ `decode` 与 `encode` 必须是同一张表的两个方向，且**已是产物值的原样过**。

    第三个断言是承重的：`--apply-edit` 的人工稿与 `--revalidate` 的存量产物走同一批字段、
    从来没有前缀。在 decode 里对不认识的值 `raise`，等于把两条路合并成一条——
    人工稿会当场炸，而它本来是这一层唯一的 0 LLM 出路。
    """
    from novel.stages.stage4.measure import decode_plan_response, encode_plan_baseline
    from tests.hard_cases import _b4, _ch4

    artifact = _ch4(1, [_b4('b1_01'), _b4('b1_02')])
    artifact['segments'][1]['delivery'] = 'summary'
    artifact['segments'][1]['transition'] = 'earlier'
    # ⚠ 两级 `weight`（批 4）走的是同一条映射，而它们是**两张不同的表**——
    #   章级那一张漏掉的话，定向修复的基线块会在那一格印产物值，模型照抄就被 schema 拒。
    artifact['weight'] = 'major'
    artifact['segments'][0]['weight'] = 'peak'
    artifact['segments'][1]['weight'] = 'brush'
    before = json.loads(json.dumps(artifact))

    wire = encode_plan_baseline(json.loads(json.dumps(artifact)))
    assert wire['segments'][1]['delivery'] == 'D_SUMMARY'
    assert wire['segments'][1]['transition'] == 'X_EARLIER'
    assert wire['chapter_goal']['intent'] == 'G_ADVANCE'
    assert wire['weight'] == 'CH_MAJOR'
    assert [b['weight'] for b in wire['segments']] == ['W_PEAK', 'W_BRUSH']

    plan = {'output_chapters': [wire]}
    assert decode_plan_response(plan) > 0
    assert plan['output_chapters'][0] == before, 'encode → decode 不是恒等'

    # 已是产物值 ⇒ 一格都不该被动，也不该抛
    passthrough = {'output_chapters': [json.loads(json.dumps(artifact))]}
    assert decode_plan_response(passthrough) == 0
    assert passthrough['output_chapters'][0] == before


def test_the_repair_baseline_speaks_the_same_dialect_as_the_repair_system_prompt():
    """⭐ 不回编码，同一份 user 里就会**自相矛盾**：system 要 `D_SCENE`、基线印着 `scene`。

    模型照基线抄 ⇒ schema 当场拒 ⇒ 那一拒要**花一次钱**才看得见。
    （stage2 `compile_v2` 回写 `--dry-run` 基线时踩的是同一格。）
    """
    from novel.stages.stage4.plan import _response_baseline
    from tests.hard_cases import _b4, _ch4

    baseline = _response_baseline(_ch4(1, [_b4('b1_01'), _b4('b1_02')]),
                                  {1: [['b1_01'], ['b1_02']]}, 1)
    got = {baseline['segments'][0][k] for k in ('narrative_role', 'delivery', 'transition')}
    assert got == {'R_SETUP', 'D_SCENE', 'X_START'}, f'基线没有回到响应侧写法：{got}'
    assert baseline['chapter_goal']['intent'].startswith('G_')


def test_the_model_response_only_ever_enters_through_one_door():
    """⭐ **守卫的守卫**：解前缀与展开编号必须成对发生，所以生产侧只许走 `adopt_plan_response`。

    ⚠ 分开写在两个调用点，迟早会有一处只做一半——而只展开不解码的那一半**不会报错**：
    判据读到 `'D_SCENE'` 当成一个陌生的 `delivery` 值，`structure_changed` 的
    `axis_delivery`（`be.get('delivery') == 'summary'`）会**静静地恒假**。
    """
    src = (Path(__file__).resolve().parents[1]
           / 'novel' / 'stages' / 'stage4' / 'plan.py').read_text(encoding='utf-8')
    assert 'expand_plan_beats(' not in src, \
        'plan.py 直接调了 `expand_plan_beats` ⇒ 那一路的前缀不会被解开，而判据不会为此出声'
    assert src.count('adopt_plan_response(') == 3, \
        ('模型响应有两条入口（整跑 / 定向修复）加一道内容闸（`_expansion_validator`），'
         '三处都必须走同一个门')


def test_the_length_section_carries_the_three_lines_the_first_paid_run_bought():
    """⭐⭐ **三句话，三条实测落红买回来的**（2026-08-22 批 2 第一次全书付费）。

    放宽逐章带 + 改口篇幅段之后跑了一次全书规划（1 次实付，flash/high，一次过），
    章级倍率分布确实变宽了（极差 0.168 → 0.741），但同一份计划落了三条 HARD：

    · `budget_band`：ch18 被压到 **0.442×**（应在 886~1329 字，只给了 490）。
      模型不是贴着下界，是**冲出去**——它把「压缩」理解成了把那个数字调小。
      ⇒ 补「两端都是硬的，再要压就合进 `summary`，不是改这个数字」。
    · `structure_changed`：ch6/19/22 与恒等基线逐格相同。写作单元 **57 → 85**
      （几乎一拍一单元）、「分组重画」这根轴 **13/13 → 4/13**。主嫌是
      `delivery` 那句新说明的后半截「不想压掉的事实不要放进 `summary`」——
      它把合并说成了一件有代价的事，于是模型不敢合了。
      ⇒ 那半句改成中性，并明说「合并本身就是一次结构决策」。
    · `goal_landing`：8 章的 `end_hook_goal` 落在倒数第二三个单元上。
      与上一条同根：末单元被切碎了。⇒ 补一句「末尾那个单元不要切得太碎」。

    ⚠ 这三句都**不是新发明的约束**，是把三条既有 HARD 的后果提前说出来——
    prompt 不说，模型就只能靠一次付费重跑才知道。

    ⚠⚠ **2026-08-22（批 4）起第一句换了责任方，而教训本身没有作废。**
    `budget_chars` 由 `allocate_budgets` 算 ⇒ 「把那个数字调小到冲出下界」这件事
    **结构上不可能**，靠 prompt 叮嘱换成了靠代码钳带。所以这里断言的不再是那句话，
    而是**那件事**：见 `test_no_weighting_the_model_can_pick_pushes_a_chapter_out_of_band`。
    另外两句仍然只有 prompt 能说，一个字都不许丢。
    """
    sysp = prompts.plan_system()
    assert '切分本身就是一次结构决策' in sysp, \
        '「一拍一单元 = 没做过分组决策」这句丢了 ⇒ structure_changed 会再红一次'
    assert '不想压掉的事实不要放进' not in sysp, \
        '那句劝退还在——它实测把写作单元从 57 推到 85、分组重画从 13 掉到 4'
    assert '末尾那个写作单元不要切得太碎' in sysp, 'goal_landing 那 8 章的教训丢了'
    # ⚠ ch18 那次的根因是「模型把压缩理解成把数字调小」。数字已经不归它管了 ⇒
    #   下发面里不许再留一句请它自己去配平总量的话。
    assert '必须落在' not in sysp, \
        '篇幅段还在要模型自己配平一个它已经不产的数——那是一道不存在的闸'


def test_only_the_full_run_gets_a_book_total_line_under_the_sizes_table():
    """⭐ 表底那一行「全书合计」在**定向修复**那一路会是假的：它只发目标章那一格。

    ⚠ 这一对断言是**一对**：只断言整跑有，谁把 `book_total=True` 顺手加到修复那一路
    也不会有任何东西出声，而模型会照着一个只覆盖一章的「全书合计」去配平全书。
    """
    from novel.stages.stage4.beats import beat_index, group_beats
    from novel.stages.stage4.measure import source_chars_table
    from tests.hard_cases import _BPS4, _MAIN4

    bidx = beat_index(_BPS4, _MAIN4)
    groups = group_beats(_BPS4, _MAIN4, bidx)
    full = source_chars_table(groups, bidx, book_total=True)
    one = source_chars_table({1: groups.get(1) or []}, bidx)
    assert '全书合计' in full, f'整跑那一路没给全书合计：{full}'
    assert '全书合计' not in one, f'定向修复那一路印了一行假的全书合计：{one}'
    # ⚠ 批 4：这一行说的是「盘子有多大」，**不再是**一道要模型对齐的硬约束
    #   （它根本不产那个整数了）。留一句已经不成立的硬约束比不说更糟——
    #   模型会为一个不存在的闸去凑数。
    assert '硬约束' not in full, f'表底还在宣布一道模型够不着的硬约束：{full}'
    assert '由代码定在' in full, f'表底没说清这个数是谁定的：{full}'


def test_a_book_that_overshoots_while_every_chapter_stays_in_band_is_caught():
    """⭐⭐ **`plan_book_band` 存在的全部理由：逐章带一章都不红，全书却超了。**

    2026-08-22 之前逐章带是 ±10%，于是「章章在带内 ⇒ 全书必在带内」是一条**蕴含**，
    总量不需要自己的门。带放宽到 ±20% 之后那条蕴含断了——这一条钉死断了以后有人接。
    ⚠ 断言必须是**这一对**：`budget_band` 不红 **且** `plan_book_band` 红。
    只断言后者的话，一个「某章离谱」的样本也能让它绿，而那证明不了任何新东西。
    """
    from tests.hard_cases import _book_band_over4

    hit = {f.rule for f in _book_band_over4()}
    assert 'budget_band' not in hit, \
        f'样本里有章越了逐章带 ⇒ 它证明不了书级带拦住了逐章带拦不住的东西（命中 {sorted(hit)}）'
    assert 'plan_book_band' in hit, f'全书 660/560=1.179× 没被拦下（命中 {sorted(hit)}）'


def test_the_book_band_finding_names_every_chapter_so_a_human_can_rebalance():
    """⭐ **逐章明细是这条 HARD 的一半，不是装饰。**

    本层的修法是「手改那几行 + `--revalidate`」这条 **0 LLM 出路**（`_HUMAN_FIX`）。
    人拿到「全书 1.179×」这一句话，只能自己去二十多章里解谜该从哪几章匀——
    那条 0 成本恢复路就名存实亡了。
    """
    from tests.hard_cases import _book_band_over4

    d = next(f.detail for f in _book_band_over4() if f.rule == 'plan_book_band')
    assert 'ch1 330/280' in d and 'ch2 330/280' in d, f'逐章倍率明细没打出来：{d}'
    assert '超出' in d and '字' in d, f'没说清超了多少、该匀掉多少：{d}'


def test_the_book_band_refuses_to_judge_on_a_shrunken_basis():
    """⭐ **缺一章就不判**——它的 `unver` 臂比 `budget_band` 严，而这是有理由的。

    逐章带可以「判得动的那些照样给结论」；书级带做不到同样的事：少一章，
    分子分母**同时**缺一块，得出的倍率既不能说合格也不能说不合格。
    ⚠ 而「跑了、零 findings」与「压根判不动」在报告上必须长得不一样——
    `dialogue_halved` 那次假绿就是这两者同形买来的。
    """
    from novel.report.findings import Outcome
    from novel.stages.stage4 import checks
    from tests.hard_cases import _BPS4, _MAIN4, _b4, _ch4

    # 第 2 章一个整数预算都没有 ⇒ 它进不了全书基准。
    ch2 = _ch4(2, [_b4('b2_01')])
    ch2['segments'][0]['budget_chars'] = None
    outs: list[Outcome] = checks.check(
        {'reconstruction_strategy': '把故事原样讲完。',
         'output_chapters': [_ch4(1, [_b4('b1_01'), _b4('b1_02'), _b4('b1_03')]), ch2]},
        blueprints=_BPS4, main_text=_MAIN4)
    o = next(o for o in outs if o.rule == 'plan_book_band')
    assert o.unver_why, f'基准缺一章却给了结论：findings={o.findings}'
    assert not o.findings, f'走了 unver 臂却还产出了结论：{o.findings}'
    assert 'out2' in o.unver_why, f'没说清是哪一章把基准打缺了：{o.unver_why}'


def test_structure_distance_reports_the_new_pair_of_detail_readings():
    """⭐ 线 A 的验收出口：移位数与源相邻保留数答的是**顺序**，一个字碰不到**详略**。

    放宽逐章带 + 改口 `PLAN_SYS` 之后要回答的问题是「模型有没有把自由度用起来」，
    而在这两个读数之前，报告上**没有任何一处**能回答它。
    ⚠ 比的是相对本章均值不是相对 1.0：章级倍率本来就允许在带内漂。
    """
    from tests.hard_cases import (
        _MAIN4, _BPS4_PRESENT, _b4, _bidx4_present, _ch4, _plan4,
    )

    # 四拍各 100 源字；预算 200/40/80/80 = 400 ⇒ 章级 1.00×（**两条带都不红**，
    # 这一条量的是章内分配，不该被越带的噪声溅到）。r̄=1.00 ⇒ 相对均值 0.40~2.00。
    chapters = [_ch4(1, [_b4('b1_01', budget=200), _b4('b1_02', budget=40),
                         _b4('b1_03', budget=80), _b4('b1_04', budget=80)])]
    chapters[0]['segments'][1]['delivery'] = 'summary'
    hits = _plan4(chapters, blueprints=_BPS4_PRESENT, main_text=[_MAIN4[0]],
                  bidx=_bidx4_present())
    assert not {f.rule for f in hits} & {'budget_band', 'plan_book_band'}, \
        f'样本自己越了带，读数会被噪声盖住：{sorted({f.rule for f in hits})}'
    d = next(f.detail for f in hits if f.rule == 'structure_distance')
    assert '拍级 预算/源 相对本章均值 0.40~2.00' in d, f'详略离散度不对：{d}'
    assert 'summary 承载源字数 25%' in d, f'summary 承载比不对：{d}'


def test_synthetic_stage4_response_satisfies_every_reference_hard_rule():
    """A legal response must be constructible from the current prompt ID spaces.

    This is stronger than checking ``needs_id_spaces ⊆ provides_id_spaces``:
    it builds a schema-valid model response, expands the ``end_beat`` cut points
    through the production join, validates all typed fact refs, and runs the
    complete judge.
    """
    from contracts.schema.stage4 import PlanResponse
    from novel.stages.stage4 import checks
    from novel.stages.stage4.beats import beat_index, group_beats
    from novel.stages.stage4.fact_refs import catalog, validate_plan_refs
    from novel.stages.stage4.measure import adopt_plan_response
    from tests.test_stage2 import blueprints, global_grounding, main_text

    body = main_text()
    bps = blueprints()
    global_g = global_grounding()
    bidx = beat_index(bps, body)
    groups = group_beats(bps, body, bidx)
    refs = catalog(global_g, bps, body, plan_units=groups)
    skeleton = prompts.project_blueprints_for_planner(bps, groups)
    surface_units = {
        row['chapter_ordinal']: [segment['plan_beat'] for segment in row['segments']]
        for row in skeleton['blueprints']
    }
    events_by_unit: dict[str, list[str]] = {}
    for event_id, unit_id in refs['event_units'].items():
        events_by_unit.setdefault(unit_id, []).append(event_id)

    chapters = []
    for ordinal in sorted(surface_units):
        source_units = surface_units[ordinal]
        order = ([source_units[-1], *source_units[:-1]]
                 if len(source_units) >= 4 else source_units)
        segments = []
        for output_index, unit_id in enumerate(order):
            moved = len(source_units) >= 4
            segments.append({
                # ⚠ 一个写作单元 = 一个源内容单元 ⇒ 它的切点就是它自己。
                #   `order` 把最后一个提到最前面，而**切点集合不变**——
                #   变的只有数组序，也就是成稿序。这正是批 4 编码的形状。
                'end_beat': unit_id,
                'weight': 'W_MID',
                # ⚠ 这里写的是**响应侧带前缀的取值**：合成的是一份「模型响应」，
                #   而响应侧与产物侧从 2026-08-22 起是两张表
                #   （`contracts/invariants.py::RESPONSE_STRUCTURAL_ROLES`）。
                'narrative_role': ('R_COLD_OPEN' if moved and output_index == 0
                                   else 'R_SETUP'),
                'delivery': 'D_SCENE',
                'transition': ('X_START' if output_index == 0
                               else 'X_EARLIER' if moved and output_index == 1
                               else 'X_CONTINUE'),
                # ⚠ `spine` 是**编号**不是下标：这里只引一个源单元 ⇒ 从它自己的
                #   events 里挑第一条抄编号（合成的是模型响应，模型也只能这么填）。
                'spine': sorted(events_by_unit[unit_id])[0],
            })
        first_event = sorted(events_by_unit[order[0]])[0]
        last_event = sorted(events_by_unit[order[-1]])[-1]
        chapters.append({
            'output_ordinal': ordinal,
            'from_source_ordinals': [ordinal],
            'weight': 'CH_EVEN',
            'chapter_goal': {
                'intent': 'G_ADVANCE',
                'fact_refs': [{
                    'kind': 'chapter_boundary',
                    'ref': f'chapter:{ordinal}',
                    'chapter': ordinal,
                }],
            },
            'open_hook_goal': {
                'intent': 'G_OPEN',
                'fact_refs': [{
                    'kind': 'event', 'ref': first_event, 'chapter': ordinal,
                }],
            },
            'end_hook_goal': {
                'intent': 'G_CLOSE',
                'fact_refs': [{
                    'kind': 'event', 'ref': last_event, 'chapter': ordinal,
                }],
            },
            'segments': segments,
        })
        assert refs['event_units'][first_event] == order[0]
        assert refs['event_units'][last_event] == order[-1]

    response = PlanResponse.model_validate({
        'reconstruction_strategy': '从末尾事件切入，再回到起因并顺序收束。',
        'output_chapters': chapters,
    })
    expanded = response.model_dump(by_alias=True, mode='json')
    # ⚠⚠ 走**生产那一个入口**（`adopt_plan_response` = 解前缀 + 展开编号），不是只展开编号：
    #   判据全都读产物侧取值，只 `expand_plan_beats` 的话下面整批断言打的是带前缀的字符串，
    #   而它们会安静地全绿——那正是这条测试要挡的那种「守卫重拼了生产的实参」。
    assert adopt_plan_response(expanded, groups, bidx) == sum(map(len, groups.values()))
    assert {be['delivery'] for ch in expanded['output_chapters']
            for be in ch['segments']} == {'scene'}, '前缀没有被解回产物取值'
    validate_plan_refs(expanded, refs)
    outcomes = checks.check(
        expanded,
        blueprints=bps,
        main_text=body,
        global_g=global_g,
        ctx=None,
        ctx_link=None,
        ctx_source='合成响应不落盘',
        bidx=bidx,
    )
    by_rule = {outcome.rule: outcome for outcome in outcomes}
    reference_hard = {
        spec.rule for spec in REGISTRY.of_stage('stage4')
        if spec.sev == 'hard' and spec.needs_id_spaces
    }
    assert 'goal_landing' in reference_hard
    assert set(refs['event_units']) == set(refs['events'])
    hit = {rule for rule in reference_hard if by_rule[rule].findings}
    assert hit == set(), f'合法引用响应仍命中 HARD：{sorted(hit)}'


@pytest.mark.corpus
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


@pytest.mark.corpus
@pytest.mark.parametrize('book', books())
def test_the_planner_can_actually_satisfy_goal_landing_from_what_it_is_given(book):
    """⭐ **一条 HARD 的可满足性，必须由下发面本身保证。**

    `goal_landing` 要求边界目标引用**首/尾写作单元里**的一条 event，而：
      · 骨架块按设计不下发 `beat_id`（上一条测试正钉着这一点）；
      · 事实目录的 `events` 只有 `bNN_MM → 章号`，没有正文也没有所属单元。
    两块拼起来，模型手里是一堆没有含义的 id 加一堆没有 id 的正文——
    中间那道 join 谁也没给它 ⇒ 那条 HARD 只能靠序号碰巧对上。
    实测（常春杜若，2026-08-17 首跑）50 个边界目标错 20 个，
    而 20 条的 `rationale` 全都正确描述了目标单元里的那一拍。

    ⇒ 本条守的不是「模型会不会照做」，而是**照做所需的信息在不在下发面里**。
    """
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        _system, user = plan.build_prompt(_ctx_for(box, 'stage4.plan'))
        refs = json.loads(
            user.split(prompts.BLOCK_FACT_REFS)[1].split(prompts.BLOCK_SKELETON)[0].strip())
        units = refs['event_units']
        assert set(units) == set(refs['events']), \
            '每一条可引用 event 都必须查得到它属于哪个源内容单元，一条都不能少'
        # 反查得到 ⇒ 「首个写作单元里有哪些 event 可引用」是个可答的问题。
        by_unit: dict[str, list[str]] = {}
        for event_id, unit in units.items():
            by_unit.setdefault(unit, []).append(event_id)
        assert all(by_unit.values()), '存在一个查不出任何 event 的源内容单元'
        # 而单元编号必须与骨架块用的是同一套，否则这张表反查不到任何东西。
        skeleton = user.split(prompts.BLOCK_SKELETON)[1]
        assert f'"plan_beat":"{sorted(by_unit)[0]}"' in skeleton, \
            'event_units 的单元编号与骨架块下发的 plan_beat 不是同一套'
    finally:
        box.close()


@pytest.mark.corpus
@pytest.mark.parametrize('book', books())
def test_the_planner_can_actually_tell_which_beats_are_present_layer(book):
    """⭐ **`transition_direction` 的可满足性，同样必须由下发面本身保证。**

    那条 HARD 只在接缝两端都是**现场层**时判，而 prompt 明说了这个口径
    （「接缝两端都是第一种时，编号大的那一头就是故事时间更晚的」）。
    ⇒ 模型必须能从下发面上看出每个源内容单元是哪一层。看不出来，
    它就只能猜——而那正是 `goal_landing` 那次「50 个边界目标错 20 个」的形状：
    **HARD 要求的信息不在模型手里，判据就退化成运气。**
    """
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        _system, user = plan.build_prompt(_ctx_for(box, 'stage4.plan'))
        skeleton = json.loads(user.split(prompts.BLOCK_SKELETON)[1]
                              .split(prompts.BLOCK_SIZES)[0].strip())
        segments = [s for row in skeleton['blueprints'] for s in row['segments']]
        assert segments, '骨架块一个源内容单元都没下发'
        assert all(s.get('time_layer') for s in segments), \
            '有源内容单元没带 time_layer ⇒ 模型看不出接缝两端是不是现场层'
    finally:
        box.close()


# ── 三、落盘闸门与提升路（合成样本 —— 盘上这三本一条 HARD 都不红）───────────

def _synthetic_response(box: sandbox.Sandbox, *, poison: bool) -> dict:
    """拿盘上那份计划反推一份**模型响应**（带 `plan_beats`、不带 `from_beats`）。

    `poison=True` ⇒ 往一个 goal 里塞一个写法词。**盘上三本 `style_words` 0 命中**，
    要看闸门真的拦得住就只能合成——那 16 条 HARD 在盘上是零反例样本。

    ⚠⚠ **`plan_beats` 必须由生产落点反查，不许按段序现编。** 这里曾经是
    `plan_beat_id(oo, i) for i, _ in enumerate(segments, 1)`，即假定
    **「写作单元数 == 源内容单元数」**——而 `R-5.5` 恰恰废除了这个恒等：
    规划器可以把相邻源单元并进同一个写作单元。盘上实测三本 51/55 章都并过
    （书[0] 20 章里 19 章有并，缺 41 个编号），于是合成响应漏掉尾部那些 `pNN_MM`，
    `expand_plan_beats` 报「这一拍没有给预算」**41 处**——
    **测试自己造了一份违约响应**，而它想验的那道闸（`style_words`）一次都没跑到。
    ⇒ 从 `group_map(group_beats(…))` 反查每个源 beat 属于哪一拍。
    这是 `docs/LESSONS.md` 第 21 条第一形状（守卫重拼了生产的实参）。

    ⚠ **其余各格忠实镜像盘上那一份**（段序、`narrative_role`、`delivery`、`transition`、
    两个 hook goal 的落点都不动）。本函数的定义就是「反推一份**会产出这份计划**的响应」，
    而盘上那份是过了全部结构闸才落的 ⇒ 镜像它，结构类 HARD 按构造必然全过，
    只剩被投毒的那一条会红。曾经这里另外造了一次重排（`len(rows) >= 4` 就把末段提到最前），
    那是上一版「编号按段序」留下的补丁：编号恒等于源序 ⇒ 必然撞 `structure_changed`，
    于是拿人为重排去躲。现在编号是反查出来的，那个补丁不再需要，
    留着反而会把 `structure_transition` 的衔接标记覆盖掉。
    """
    from novel.stages.stage4.beats import group_beats, group_map

    unit_of = {bid: pid
               for pid, bids in group_map(group_beats(
                   box.read('stage2/chapter_blueprints.json'),
                   box.read('stage1/main_text.json'))).items()
               for bid in bids}

    src = box.read('stage4/plan_chapters.json')
    out = {'reconstruction_strategy': src['reconstruction_strategy'], 'output_chapters': []}
    for ch in src['output_chapters']:
        c = {k: ch[k] for k in ('output_ordinal', 'from_source_ordinals',
                                'chapter_goal', 'open_hook_goal', 'end_hook_goal')}
        rows = []
        for be in ch['segments']:
            pids: list[str] = []
            for bid in (be.get('from_beats') or []):
                pid = unit_of.get(bid)
                if pid is not None and pid not in pids:
                    pids.append(pid)
            rows.append({'plan_beats': pids,
                         'budget_chars': be['budget_chars'],
                         'narrative_role': be['narrative_role'],
                         'delivery': be['delivery'],
                         'transition': be['transition']})
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
                          prompts={'PLAN_SYS': '0' * 10}, model='deepseek-v4-pro',
                          params=plan._params(up))


@needs_corpus
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
        with pytest.raises(GateBlocked, match='style_words') as blocked:
            plan.write_plan(ctx, store, _synthetic_response(box, poison=True), up,
                            provenance=_provenance(ctx, up))
        assert ('stage4.plan --revalidate --actor <id> --reason <why>'
                in str(blocked.value))
        assert (box.dir / 'stage4/plan_chapters.INVALID.json').exists(), '问题稿没存下来'
        assert box.read('stage4/plan_chapters.json') == before, \
            '**正式产物被覆盖了**——先校验后落盘那条就白写了'
    finally:
        box.close()


@needs_corpus
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

        rep = plan.run_revalidate(
            _ctx_for(box, 'stage4.plan'),
            actor='stage4-test',
            reason='remove the injected style-word violation',
        )
        assert rep.passed and rep.badge == 'pass', f'提升之后该是绿的，实得 {rep.badge}'
        assert not inv.exists(), '.INVALID 提升之后该消失'
        ctx_art = WriterContext.model_validate(box.read('stage4/writer_context.json'))
        assert 'stage4/plan_chapters.json' in (ctx_art.provenance.inputs or {})
        line = next(ln for ln in rep.rules if ln.rule == 'context_link')
        assert line.status == 'ok', '提升之后上下文没跟着重建——context_link 该红而没红'
    finally:
        box.close()


# ── 四、两条 0 成本路 ────────────────────────────────────────────────────

@pytest.mark.corpus
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


@pytest.mark.corpus
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


@needs_corpus
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


@needs_corpus
def test_check_only_reads_the_blocked_draft_when_no_formal_plan_exists():
    """⭐ 首跑被落盘闸拦下时，`.INVALID` 是唯一可审阅稿，复检必须能读它。

    此前这里抛 `UpstreamMissing`，而本层的 `.INVALID` **不带 findings**（schema 无此字段）
    ⇒ 终态只给了 rule id，全链没有任何只读入口能拿到「错在哪几条」，
    人只能回去读 `checks.py` 自己复算判据。形态照 stage3 的成例。
    """
    book = books()[0]
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        src = box.dir / 'stage4' / 'plan_chapters.json'
        (box.dir / 'stage4' / 'plan_chapters.INVALID.json').write_bytes(src.read_bytes())
        src.unlink()

        rep = plan.run_check_only(_ctx_for(box, 'stage4.plan'))
        assert rep.provenance.invalid is True, '报告必须自报判的是 .INVALID 稿'
        assert len(rep.rules) == len(REGISTRY.of_stage('stage4')) == 31
        assert all(ln.status != 'skipped' for ln in rep.rules), \
            '判据要真的跑在问题稿上，不能整排落成「没跑成」'
    finally:
        box.close()


@needs_corpus
def test_check_only_still_refuses_when_neither_draft_is_on_disk():
    """两份都不在盘上才是真的「先跑那一层」。**显式终态，不是「查过没事」。**"""
    book = books()[0]
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        (box.dir / 'stage4' / 'plan_chapters.json').unlink()
        with pytest.raises(UpstreamMissing, match='都不在盘上'):
            plan.run_check_only(_ctx_for(box, 'stage4.plan'))
    finally:
        box.close()


@needs_corpus
def test_the_registered_rules_all_produce_a_row_on_a_real_book():
    """⭐ 照抄 `tests/test_stage3.py` 的形态：**30 条一行不少 + 0 条 `skipped`**。

    `build()` 遍历判据注册表而不是遍历 findings ⇒ 一条判据不可能因为
    「实现忘了调」而从报告里消失，它会变成 `skipped`。本条盯的就是那个 0。
    """
    book = books()[0]
    box = sandbox.Sandbox(book, STAGE4_INPUTS)
    try:
        rep = plan.run_check_only(_ctx_for(box, 'stage4.plan'))
        assert len(rep.rules) == len(REGISTRY.of_stage('stage4')) == 31
        assert all(ln.status != 'skipped' for ln in rep.rules)
        assert all(ln.scanned for ln in rep.rules), '绿灯必须报出自己的分母（判据 31）'
    finally:
        box.close()
