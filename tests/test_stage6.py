#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage6.py —— stage6 纵切与**盘上三本对账**。

⭐ 这一份里最值钱的是两条**等价测试**，它们对位 stage3 那次的「23 项逐值」：
  · `test_rejudging_reproduces_what_is_on_disk` —— 对 12 份文本重跑判据，
    产出的 rule 集合必须与产物里存的**逐份相等**。**不等就是判据抄错了。**
  · `test_normalization_is_idempotent` —— 存的是**归一之后**的字节 ⇒ 再归一必须一个字不动。
两条都 0 成本，而它们能抓到「逐字移植时漏了一个字符类」这种不会报错的错。

⚠ 盘上语料是**只读证据**：三本的判定结论一个字节都没动过，所以
「结论对不上」只可能是骨架抄错了，不可能是数据变了。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib

import pytest

from contracts.rules import REGISTRY
from novel.core.measure import measure
from novel.run import RunContext
from novel.stages.stage6 import checks, hook, prompts
from novel.stages.stage6.measure import normalize_hook, quote_problems_of
from tests import corpus, sandbox

#: 一条 stage6 纵切跑起来要的五份。
STAGE6_INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
                 'stage3/style_grounding.json', 'stage4/writer_context.json',
                 'stage6/hook.json')


def books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage6/hook.json'})


pytestmark = pytest.mark.skipif(
    not books(), reason='盘上没有 stage6 语料——这几条**没在查**，不是「查过没事」')


def _drafts_of(book: str) -> tuple[str, ...]:
    """本书的成稿（**不含 `_rounds/` 归档**——那不是现役正文）。"""
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))


def _box(book: str) -> sandbox.Sandbox:
    return sandbox.Sandbox(book, STAGE6_INPUTS + _drafts_of(book))


@pytest.fixture(params=books(), ids=lambda b: b[:10])
def box(request):
    s = _box(request.param)
    yield s
    s.close()


def _check(box: sandbox.Sandbox):
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    return hook.run_check_only(ctx)


# ── 报告的形状：由 `build()` 遍历注册表保证 ─────────────────────────────────

def test_every_registered_rule_produced_a_row(box):
    """⭐ **每一条注册判据恰好一行，且没有 `skipped`。**（形态照抄 `test_stage3.py`）

    `skipped` 的意思是「注册了而实现没排它」——一条判据不可能因为「实现忘了调」
    而从报告里消失，因为报告不是由实现的输出生成的。
    """
    rep = _check(box)
    assert {r.rule for r in rep.rules} == {r.rule for r in REGISTRY.of_stage('stage6')}
    assert [r.rule for r in rep.rules if r.status == 'skipped'] == []
    assert len(rep.rules) == 25


def test_the_badge_can_never_be_pass_and_that_is_by_design(box):
    """⚠ **stage6 的徽章在结构上永远不可能是 `pass`。**

    6 条 `sev=unver` 每次都输出（`builder._status_of` 强制），而 `badge_of` 见 `unver`
    就至少 amber。**这不是缺陷**，是本层「能查的都查了、查不了的写在脸上」的可见形态。

    ⚠ 下面那个 8 = **6 条 `sev=unver`＋2 条语义判据的 unver 状态**——盘上三本
    都没有 `stage6/semantic_<n>.json`，语义两条落「没审 ≠ 没问题」的 unver。
    这两族语义不同（一族是判据的边界，一族是「还没花钱审」），数值巧合相等；
    若语料书补跑了 `stage6.semantic`，此处应变成 6 并把语义两条另行断言。

    ⚠ 同时钉住 `passed=True`：**「有查不了的」不等于「不合格」**，退出码要是 0。
    """
    rep = _check(box)
    assert rep.badge == 'amber'
    assert rep.passed is True
    assert len([r for r in rep.rules if r.status == 'unver']) == 8
    semantic_lines = [r for r in rep.rules if r.check_kind == 'semantic']
    assert {r.rule for r in semantic_lines} == {'hook_semantic_spoiler',
                                                'hook_semantic_grounding'}
    assert all(r.status == 'unver' for r in semantic_lines), \
        '盘上没有语义审判产物，语义两条只能是 unver——绿了就是洗绿'


#: 盘上三本的命中集。**与原型的差别是一次有名有姓的裁决**：
#: 原型在有定稿时只报定稿那一条候选 ⇒ 三本命中集全空，而
#: 「合规的那个候选就在同一份产物里」这件事在报告上看不见——**人也就没有依据去挑**。
#: ⇒ 本仓报**全部四份**（3 候选 + 定稿），于是多出这 3 条 INFO。
EXPECTED_HITS = {
    '成年人不做选择，姐全都要_7581413687886353176': {},
    '我把夫君捧成头牌后，全家悔疯了_7615069877019300633': {'hook_ending_shape': 2},
    '我，三岁嘴强月老，拆姻缘一拆一个准_7627487175860556568': {'hook_shape': 1},
}


def test_hits_match_the_reconciliation(box):
    """⭐ 盘上三本的命中集**逐条对账**。不一致就是骨架抄错了，不是数据变了。"""
    rep = _check(box)
    got = {r.rule: len(r.findings) for r in rep.rules if r.status == 'hit'}
    assert got == EXPECTED_HITS[box.book], f'{box.book}: 命中集与对账不符'


def test_no_hard_rule_is_hit_on_disk(box):
    """5 条 HARD 盘上全 0。⚠ **要连基线一起读**：其中 4 条的失效模式一次都没发生过
    （一个会让它红的样本都没有），只有 `hook_para_sep` 是「绿得有理由」的那一条
    ——归一化真动过手（盘上 9 候选里 6 个 `paras_fixed=1`），而它 0 命中。"""
    rep = _check(box)
    hard = {r.rule for r in REGISTRY.of_stage('stage6') if r.sev == 'hard'}
    assert not [r.rule for r in rep.rules if r.rule in hard and r.status == 'hit']


# ── ⭐ 两条等价测试 ─────────────────────────────────────────────────────────

def _texts_and_stored(box: sandbox.Sandbox):
    """盘上那份产物里全部要判的文本 + 它自己存的判定。"""
    data = box.read('stage6/hook.json')
    out = [(c['approach'], c['text'], {f['rule'] for f in c['findings']})
           for c in data['candidates']]
    if data.get('approved'):
        a = data['approved']
        out.append((f'定稿·{a["approach"]}', a['text'], {f['rule'] for f in a['findings']}))
    return out


def test_rejudging_reproduces_what_is_on_disk(box):
    """⭐⭐ **重判 == 存盘。** 对位 stage3 那次的「23 项逐值」。

    对 3 候选 + 1 定稿逐份重跑判据，产出的 rule 集合必须与产物里存的**相等**。
    **不等就是判据抄错了**——而逐字移植时漏一个字符类、少一个前瞻断言，
    不会报错，只会让某条判据在某类输入上静默地不再拦。

    ⚠ 排除两条**不由文本判据产**的：`hook_report` 与 `hook_downsend_leak` 是书级的
    （原型也不把它们写进 `candidates[].findings`）。
    ⚠ 也排除 `hook_quote_unbalanced`：它晚于这三份产物（见下面那条 12 格的测试）。
    """
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    up = hook._Upstreams(ctx, __import__('novel.store', fromlist=['x']).ArtifactStore(ctx))
    book_level = {'hook_report', 'hook_downsend_leak', 'hook_quote_unbalanced'}
    for label, text, stored in _texts_and_stored(box):
        _t, _m, fs = hook._normalize_and_judge(text, up)
        got = {f.rule for f in fs} - book_level
        assert got == (stored - book_level), (
            f'{box.book} / {label}：重判 {sorted(got)} ≠ 存盘 {sorted(stored - book_level)}')


def test_normalization_is_idempotent(box):
    """⭐⭐ **归一幂等。** 存的是归一**之后**的字节 ⇒ 再归一必须一个字不动。

    它抓的是归一链本身抄错了：三步里任何一步的行为变了，
    盘上那些「已经归一过」的文本会被再改一次，而 `mech` 的三个计数器会非零。
    """
    src_hook = (sandbox.cut(box.book, 'stage1/hook_copy.json') or {}).get('text', '').strip()
    for label, text, _stored in _texts_and_stored(box):
        again, mech = normalize_hook(text, src_hook=src_hook)
        assert again == text, f'{box.book} / {label}：再归一改动了字节'
        assert (mech.punct_fixed, mech.quotes_fixed, mech.paras_fixed) == (0, 0, 0), \
            f'{box.book} / {label}：再归一记了改动 {mech}'
        assert quote_problems_of(text, src_hook=src_hook) == []


def test_invented_number_compares_values_and_ignores_success_false_positive():
    """真实 E2E 曾把「好感度一百成功」报成不存在的「一百成」。"""
    mat = prompts.Material(blocks=('人物已经三十岁，做这件事有八成胜算。',))
    text = ('好感度一百成功。这位人物已经30岁，做这件事仍有八成胜算。'
            '所有事实都来自下发材料，这一句只是把文案补到正常长度。')
    fs = checks.hook_findings(
        text, mat=mat, secrets=[], arc=None, characters={}, src_hook=None,
        style_hook=None, quote_problems=[])
    assert not [f for f in fs if f.rule == 'hook_invented_number']


def test_chars_are_recomputed_with_the_same_ruler(box):
    """`candidates[].chars` 已被新契约裁掉 ⇒ 现算。

    ⚠ **必须走 `measure()` 那把尺子**：同一条候选 `measure()['chars']` 与 `len(text)`
    实测差 ~9.5%。读产物或改用 `len()`，报告里的字数就与 `hook_length` 判据
    用的字数**不是同一个数**——同一页上人会看到两个字数。
    """
    for _label, text, _stored in _texts_and_stored(box):
        m = measure(text)
        assert m is not None and m.chars != len(text), \
            '两个口径一样了？那说明 measure 的定义变了，判据要跟着重量'


# ── ⚠ 三条陷阱 ──────────────────────────────────────────────────────────────

def test_the_identity_baseline_is_exactly_twelve_cells_on_disk(box):
    """⚠⚠ **盘上恰好 12 格，而契约要 13 格**——载体是 `dict[str, str]`，**少一格不报错**。

    缺的正是 `hook_quote_unbalanced`（那条判据晚于这三份产物）。
    **不许拿占位符补上**：断言「恰好等于 12 且缺的正好是它」，
    才能让「这份产物比那条判据老」这件事**留在盘上而不是被抹平**。
    ⚠ 语义两条**不占格**：恒等基线是 0 成本路，不跑付费审判——集合要与
    `hook._judgeable_rules` 同口径，否则这条测试在语义判据进表那一刻就会假红。
    """
    stored = sandbox.cut(box.book, 'stage6/hook.json')['baselines_identity']
    want = {r.rule for r in REGISTRY.of_stage('stage6')
            if r.sev in ('hard', 'info') and r.check_kind != 'semantic'
            and r.rule != 'hook_reuses_source'}
    assert len(want) == 13, '恒等基线该占 13 格（机械 hard+info 减去 hook_reuses_source）'
    assert len(stored) == 12
    assert want - set(stored) == {'hook_quote_unbalanced'}


def test_identity_grid_excludes_semantic_rules(box):
    """恒等基线的格子只给**机械** hard+info——语义判据的产地是付费审判，
    0 成本路给它们占格就是两个永远 `0/1` 的假格子。"""
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    grid, _rep = hook.run_identity(ctx)
    semantic_rules = {r.rule for r in REGISTRY.of_stage('stage6')
                      if r.check_kind == 'semantic'}
    assert semantic_rules == {'hook_semantic_spoiler', 'hook_semantic_grounding'}
    assert not semantic_rules & set(grid)


def test_body_stale_uses_consumed_not_the_whole_file_sha1(box):
    """⚠⚠ **口径与原型不同，而这是被迁移逼出来的**（`R-9.17`）。

    `judged[].sha1` 是**整文件**指纹，而新契约裁过 stage5 产物的字段 ⇒ 迁移之后它
    **必然**对不上；照原型退回整文件 `sha1` 判过期，盘上会有书被**误报成红**。
    ⇒ `consumed`（字段投影）判过期、`sha1` 只作审计；**没有 `consumed` ⇒ 说不清**。

    这条钉住的是：无论哪本，`hook_body_stale` 都**不是** `hit`。
    """
    rep = _check(box)
    line = next(r for r in rep.rules if r.rule == 'hook_body_stale')
    assert line.status == 'unver', (
        f'{box.book}: hook_body_stale = {line.status}。'
        f'⚠ 若它变成 hit，多半是有人把口径改回了整文件 sha1——那会让每一本都误报')


def test_the_prompt_is_a_byte_for_byte_port():
    """⭐ **`HOOK_SYS` 与原型 `N8_SYS` 逐字节相同。**

    这份 prompt 压着 120+ 条真机 findings 的学费，而**改写不会报错**——
    它只会让某一类失效重新出现，且要花一次真钱才看得见。

    ⚠ 原型仓是**只读**的，找不到时 skip 并明说「没在查」。
    """
    p = pathlib.Path(__file__).resolve().parents[2] / \
        'Novel_refactoring' / 'Rapid_Prototyping' / 'Stage6' / 'n8_hook.py'
    if not p.exists():
        pytest.skip(f'原型仓不在 {p}——本条**没在查**，不是「查过一致」')
    tree = ast.parse(p.read_text(encoding='utf-8'))
    want = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}['N8_SYS']
    assert prompts.HOOK_SYS == want, (
        'HOOK_SYS 与原型 N8_SYS 分家了。'
        f'原型 sha1={hashlib.sha1(want.encode()).hexdigest()[:10]} '
        f'本仓 sha1={hashlib.sha1(prompts.HOOK_SYS.encode()).hexdigest()[:10]}')
    # 守卫的守卫：模板本身也钉一个数，免得两边被同时改坏。
    assert len(prompts.HOOK_SYS) == 893


def test_the_rendered_prompt_is_byte_for_byte_too():
    """⭐⭐ **模板逐字还不够——渲染出来的那一段也要逐字。**

    ⚠ 这条是被一次真实的失误逼出来的：模板确实与原型逐字节相同，而
    `HOOK_APPROACH_NOTES`（从原型 `APPROACHES` 搬过来的三句说明）被**转述**了一遍，
    于是渲染结果短了 48 字——**丢掉的正是 `result_flashback` 那句里的硬约束
    「不得取后期高潮或结局」**，而那是本层唯一还禁的东西（`R-9.9`）。
    模板那条测试**抓不到它**：占位符两侧一个字节都没变。

    ⇒ 真正要钉的是**发出去的字节**，不是模板的字节。
    """
    p = pathlib.Path(__file__).resolve().parents[2] / \
        'Novel_refactoring' / 'Rapid_Prototyping' / 'Stage6' / 'n8_hook.py'
    if not p.exists():
        pytest.skip(f'原型仓不在 {p}——本条**没在查**，不是「查过一致」')
    tree = ast.parse(p.read_text(encoding='utf-8'))
    lits = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}
    sys_tpl = lits['N8_SYS'].value
    approaches = ast.literal_eval(lits['APPROACHES'])
    from string import Template
    want = Template(sys_tpl).substitute(
        APPROACHES='\n'.join(f'  {i}. {k} —— {v}'
                             for i, (k, v) in enumerate(approaches.items(), 1)))
    assert prompts.hook_system() == want, (
        '渲染出来的 system 与原型分家了。模板可能一字未改，'
        '而插值进去的那几句被转述了——**那会静默删掉里面的硬约束**')


def test_the_interpolated_prompt_names_every_approach():
    """三种写法**每一种都要真的出现在渲染出来的 system 里**。

    ⚠ 这正是「10 个词只提了 2 个」那种失效的形状：枚举表加了一项而 prompt 没跟上，
    渲染出来不会报错，只是少提一种。
    """
    from contracts.invariants import HOOK_APPROACHES, HOOK_APPROACH_NOTES
    rendered = prompts.hook_system()
    for k in HOOK_APPROACHES:
        assert k in rendered and HOOK_APPROACH_NOTES[k] in rendered
    assert '$' not in rendered, '渲染后还有残留占位符'


# ── 恒等基线 ────────────────────────────────────────────────────────────────

def test_identity_baseline_runs_and_excludes_the_constructed_one(box):
    """`--identity`：同一把尺子量**原作文案段**。

    ⚠ `hook_reuses_source` **单独剔除**：原作 vs 原作必然 100%，那是**构造使然**，
    不是判据坏了——不剔除的话每一轮都会看到一条假的红线。
    """
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    grid, rep = hook.run_identity(ctx)
    assert 'hook_reuses_source' not in grid
    assert len(grid) == 13
    assert all(v in ('0/1', '1/1') for v in grid.values())
    assert len(rep.rules) == 25


# ── 定稿 ────────────────────────────────────────────────────────────────────

def test_approve_copies_the_candidate_and_leaves_provenance_alone(box):
    """`stage6.approve --pick N`：定稿 = **候选那一份的拷贝** + `origin='human'`。

    ⚠⚠ **`_provenance` 一个字都不动**：不走 `llm_provenance`（0 成本的跑不许现造
    一份声称模型产了它的账），**也不走 `rewrite_provenance`**——`--pick` 没有改任何
    LLM 产的字节，标 `partial_rewrite` 会说假话。
    """
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.approve', paid=False)
    before = box.read('stage6/hook.json')['_provenance']
    new, rep = hook.run_approve(ctx, 2)
    after = box.read('stage6/hook.json')
    assert after['_provenance'] == before, '定稿改动了来源账'
    assert new.approved is not None
    assert new.approved.origin == 'human'
    assert new.approved.approach == new.candidates[1].approach
    assert new.approved.text == new.candidates[1].text
    assert rep.passed is True
    # 归档：换掉的是「谁被选中」这个决定，而那正是下次调 prompt 时唯一的证据。
    assert list((box.dir / 'stage6' / '_rounds').glob('hook.r*.json'))


@pytest.mark.parametrize('pick', [0, 4, -1])
def test_approve_refuses_an_out_of_range_pick(box, pick):
    """`--pick 0` / 越界 ⇒ 显式终态，**并报出有几个候选**。"""
    from novel.errors import TerminalState
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.approve', paid=False)
    with pytest.raises(TerminalState, match='候选'):
        hook.run_approve(ctx, pick)


# ── 落盘闸门与提升路：**盘上零样本，只能合成** ──────────────────────────────
#
# ⚠ 本层的闸门条件与 stage3 不同：stage3 是「任何 hard ⇒ `.INVALID`」，
#   而本层一次产**三份**，一条候选红不该废掉另两条 ⇒ 拦的是**一条干净候选都没有**。
#   两条路各配一个样本：部分红（照常落盘）与全红（拦下）。

def _fake_client(ctx, texts: list[str]):
    """一个**不发任何请求**的 client，按剧本回三条候选。"""
    import json as _json

    from contracts.invariants import HOOK_APPROACHES
    from novel.llm.client import LlmClient
    from novel.llm.transport import ChatResponse, FakeTransport
    body = _json.dumps({
        'candidates': [{'approach': a, 'text': t}
                       for a, t in zip(HOOK_APPROACHES, texts)],
        'rationale': '三条各赌一个角度。',
    }, ensure_ascii=False)
    resp = ChatResponse(content=body, finish_reason='stop',
                        usage={'prompt_tokens': 1, 'completion_tokens': 1},
                        model='deepseek-v4-pro')
    return LlmClient(ctx, FakeTransport.always(resp), sleep=lambda _s: None)


def _sample(box: sandbox.Sandbox, *, truncated: bool) -> str:
    """造一条合成文案：只在 `hook_truncated` 这一条上可控地红或不红。

    ⚠ **段间分隔必须照本书自己的档位造**，否则 `hook_para_sep`（另一条 HARD）
    会恒亮，于是「部分红」那个样本变成「全红」——第一版就踩了这个：
    写了一段单行文本，而三本原作文案段都是**空行分段**，
    单段文本根本没有分隔符可归一 ⇒ 那条 HARD 修不掉。
    〔顺带记一条观察：`hook_para_sep` 的 detail 写着「归一化应该已经修掉它了」，
    而**单段文案是归一化修不掉的**——那句措辞对这一类输入是错的诊断。
    判定本身不冤（本书文案是多段，这条是一整块），留给 F 期后续层一起看。〕
    """
    from novel.core.bands import sep_band_of_text
    src = (sandbox.cut(box.book, 'stage1/hook_copy.json') or {}).get('text', '').strip()
    sep = '\n\n' if sep_band_of_text(src) == '空行分段' else '\n'
    body = sep.join(['她站在门口，手里攥着那张纸', '不知道该不该推门进去，风把头发吹乱了'])
    return body if truncated else body + '。'


def test_the_gate_blocks_only_when_no_candidate_is_clean(box):
    """⭐ **闸门拦的是「一条干净候选都没有」，不是「有一条红」。**

    一条候选红就废掉整次付费产出，会让另外两条本来可用的候选一起陪葬——
    而本层的现役闸门是**人定稿**，人手里有三条可挑。
    """
    from novel.errors import GateBlocked
    from novel.store import ArtifactStore

    # ① 部分红 ⇒ 照常落盘，报告把红的那条摆出来
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=True)
    clean, dirty = _sample(box, truncated=False), _sample(box, truncated=True)
    art = hook.run_hook(ctx, _fake_client(ctx, [clean, clean, dirty]))
    assert ArtifactStore(ctx).exists('stage6/hook.json')
    red = {c.approach for c in art.candidates
           for f in c.findings if f.rule == 'hook_truncated'}
    assert len(red) == 1, '只有第三条该红'

    # ② 全红 ⇒ 拦下，问题稿进 .INVALID
    ctx2 = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=True)
    with pytest.raises(GateBlocked, match='每一条'):
        hook.run_hook(ctx2, _fake_client(ctx2, [dirty, dirty, dirty]))
    assert ArtifactStore(ctx2).exists('stage6/hook.INVALID.json')


def test_revalidate_promotes_only_after_the_problem_is_fixed(box):
    """⚠ **提升路是那道闸门的必然配套**——闸门是我们自己加的，
    不给一条 0 成本的提升路，一次付费产出就被永久锁死。

    两步：仍然全红 ⇒ 不提升；把字改对 ⇒ 提升，且 `_provenance` 不许被重盖
    （`promote_invalid` 的签名里**没有** `prompts`/`model`/`inputs`）。
    """
    import json as _json

    from novel.errors import GateBlocked
    from novel.store import ArtifactStore

    dirty = _sample(box, truncated=True)
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=True)
    with pytest.raises(GateBlocked):
        hook.run_hook(ctx, _fake_client(ctx, [dirty, dirty, dirty]))
    inv = box.dir / 'stage6' / 'hook.INVALID.json'
    before = _json.loads(inv.read_text(encoding='utf-8'))['_provenance']

    ctx2 = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    promoted, _rep = hook.run_revalidate(ctx2)
    assert promoted is False, '还没改就提升了？那道闸门形同虚设'

    # 人手改一个字（0 成本自救），再提升
    data = _json.loads(inv.read_text(encoding='utf-8'))
    for c in data['candidates']:
        c['text'] = _sample(box, truncated=False)
    inv.write_text(_json.dumps(data, ensure_ascii=False), encoding='utf-8')

    ctx3 = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    promoted, rep = hook.run_revalidate(ctx3)
    assert promoted is True
    after = box.read('stage6/hook.json')['_provenance']
    assert after['prompts'] == before['prompts'], '提升重盖了 prompt 指纹 —— 那是伪造'
    assert after['model'] == before['model']
    assert after.get('revalidated') is True
    assert not ArtifactStore(ctx3).exists('stage6/hook.INVALID.json'), \
        '.INVALID 该被归档进 _rounds/，不是留在原地'
    assert rep.passed is True


# ── 下发面 ──────────────────────────────────────────────────────────────────

def test_the_downsend_face_carries_no_ending(box):
    """⚠ **护栏自己也可能是泄漏源。** 盘上 0/3 本命中。"""
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    _sys, user, mat = hook.build_prompt(ctx)
    gg = sandbox.cut(box.book, 'stage2/global_grounding.json')
    assert checks.downsend_leak(mat.given, gg['arc']) == []
    assert 'mode_variation' not in user, \
        '文案不需要情境切换；书级聚合和历史产物的 mode_variation 不该下发'
