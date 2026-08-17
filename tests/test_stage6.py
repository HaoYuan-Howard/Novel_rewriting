#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 当前文案契约、确定性重判、归一幂等与人工定稿。"""
from __future__ import annotations

import pytest

from contracts.rules import REGISTRY
from novel.core.measure import measure
from novel.run import RunContext
from novel.stages.stage6 import checks, hook, prompts
from novel.stages.stage6.measure import normalize_hook, quote_problems_of
from tests import corpus, sandbox
from tests.markers import corpus_params

#: 一条 stage6 纵切跑起来要的五份。
STAGE6_INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
                 'stage3/style_grounding.json', 'stage4/writer_context.json',
                 'stage6/hook.json')


def books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage6/hook.json'})


def _drafts_of(book: str) -> tuple[str, ...]:
    """本书当前正式成稿。"""
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.pattern == 'stage5/{mode}/ch{nn}.json'
        and '_rounds' not in a.rel))


def _box(book: str) -> sandbox.Sandbox:
    # 机械复检不依赖付费语义历史。旧 semantic v0 的形状不可无损迁移；把它复制进
    # 沙箱只会让免费路径被一份它不需要的旧制品卡住。
    return sandbox.Sandbox(book, STAGE6_INPUTS + _drafts_of(book))


@pytest.fixture(params=corpus_params(
    books(), reason='盘上没有 stage6 语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
def box(request):
    s = _box(request.param)
    yield s
    s.close()


def _check(box: sandbox.Sandbox):
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    return hook.run_check_only(ctx)


def _mechanically_clean_pick(box: sandbox.Sandbox) -> int:
    """选一条当前语料里机械 HARD 为空的候选，不把「第 N 条恰好干净」当契约。"""
    data = box.read('stage6/hook.json')
    for index, candidate in enumerate(data['candidates'], 1):
        if not any(REGISTRY.get(row['rule']).sev == 'hard'
                   for row in candidate.get('findings') or []):
            return index
    raise AssertionError('现役 hook 产物没有可定稿候选，语料本身违反落盘闸契约')


# ── 报告的形状：由 `build()` 遍历注册表保证 ─────────────────────────────────

def test_every_registered_rule_produced_a_row(box):
    """⭐ **每一条注册判据恰好一行，且没有 `skipped`。**（形态照抄 `test_stage3.py`）

    `skipped` 的意思是「注册了而实现没排它」——一条判据不可能因为「实现忘了调」
    而从报告里消失，因为报告不是由实现的输出生成的。
    """
    rep = _check(box)
    assert {r.rule for r in rep.rules} == {r.rule for r in REGISTRY.of_stage('stage6')}
    assert [r.rule for r in rep.rules if r.status == 'skipped'] == []
    assert len(rep.rules) == len(REGISTRY.of_stage('stage6'))


def test_the_badge_can_never_be_pass_and_that_is_by_design(box):
    """⚠ **stage6 的徽章在结构上永远不可能是 `pass`。**

    3 条 `sev=unver` 每次都输出（`builder._status_of` 强制），而 `badge_of` 见 `unver`
    就至少 amber。**这不是缺陷**，是本层「能查的都查了、查不了的写在脸上」的可见形态。

    3 条工作流/前提 `sev=unver` 常驻；质量语义三条是否 `unver` 取决于当前审判产物。
    """
    rep = _check(box)
    hard_hit = any(r.sev == 'hard' and r.status == 'hit' for r in rep.rules)
    assert rep.badge == ('fail' if hard_hit else 'amber')
    assert rep.passed is (not hard_hit)
    assert len([r for r in rep.rules if r.sev == 'unver']) == 3
    semantic_lines = [r for r in rep.rules if r.check_kind == 'semantic']
    assert {r.rule for r in semantic_lines} == {
        'hook_semantic_contradiction', 'hook_candidate_contradiction', 'hook_quality_rank'}


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

    排除两条不由逐文本判据产生的书级结论。
    """
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    up = hook._Upstreams(ctx, __import__('novel.store', fromlist=['x']).ArtifactStore(ctx))
    book_level = {'hook_report'}
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

def test_identity_grid_excludes_semantic_rules(box):
    """恒等基线的格子只给**机械** hard+info——语义判据的产地是付费审判，
    0 成本路给它们占格就是两个永远 `0/1` 的假格子。"""
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    grid, _rep = hook.run_identity(ctx)
    semantic_rules = {r.rule for r in REGISTRY.of_stage('stage6')
                      if r.check_kind == 'semantic'}
    assert semantic_rules == {
        'hook_semantic_contradiction', 'hook_candidate_contradiction', 'hook_quality_rank'}
    assert not semantic_rules & set(grid)


def test_body_stale_uses_consumed_not_the_whole_file_sha1(box):
    """当前成稿未变时，消费投影应直接判 fresh。"""
    rep = _check(box)
    line = next(r for r in rep.rules if r.rule == 'hook_body_stale')
    assert line.status == 'unver'
    assert not line.findings, f'{box.book}: 当前成稿被误判为漂移：{line.findings}'


# ── 恒等基线 ────────────────────────────────────────────────────────────────

def test_identity_baseline_runs_and_excludes_the_constructed_one(box):
    """`--identity`：同一把尺子量**原作文案段**。

    ⚠ `hook_reuses_source` **单独剔除**：原作 vs 原作必然 100%，那是**构造使然**，
    不是判据坏了——不剔除的话每一轮都会看到一条假的红线。
    """
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    grid, rep = hook.run_identity(ctx)
    assert 'hook_reuses_source' not in grid
    want = {r.rule for r in REGISTRY.of_stage('stage6')
            if r.sev in ('hard', 'info') and r.check_kind != 'semantic'
            and r.rule != 'hook_reuses_source'}
    assert set(grid) == want
    assert all(v in ('0/1', '1/1') for v in grid.values())
    assert len(rep.rules) == len(REGISTRY.of_stage('stage6'))


# ── 定稿 ────────────────────────────────────────────────────────────────────

def test_approve_copies_the_candidate_and_leaves_provenance_alone(box, monkeypatch):
    """`stage6.approve --pick N`：定稿 = **候选那一份的拷贝** + `origin='human'`。

    ⚠⚠ **`_provenance` 一个字都不动**：不走 `llm_provenance`（0 成本的跑不许现造
    一份声称模型产了它的账），**也不走 `rewrite_provenance`**——`--pick` 没有改任何
    LLM 产的字节，标 `partial_rewrite` 会说假话。
    """
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.approve', paid=False)
    from novel.stages.stage6 import semantic
    pick = _mechanically_clean_pick(box)
    before = box.read('stage6/hook.json')['_provenance']
    chosen = box.read('stage6/hook.json')['candidates'][pick - 1]
    monkeypatch.setattr(
        semantic, 'require_human_resolution',
        lambda *_args, **_kw: (
            __import__('types').SimpleNamespace(semantic_review_seq=None),
            'human_verification'))
    new, rep = hook.run_approve(
        ctx, pick, actor='test-editor', reason='已核验',
        subject_sha256=semantic.text_sha256(chosen['text']))
    after = box.read('stage6/hook.json')
    assert after['_provenance'] == before, '定稿改动了来源账'
    assert new.approved is not None
    assert new.approved.origin == 'human'
    assert new.approved.approach == chosen['approach']
    assert new.approved.text == chosen['text']
    # 返回的是整份三候选报告；未选候选的红行仍会如实保留，
    # 所以「批准成功」不蕴含 `rep.passed is True`。
    assert rep.rules
    # 归档：换掉的是「谁被选中」这个决定，而那正是下次调 prompt 时唯一的证据。
    assert list((box.dir / 'stage6' / '_rounds').glob('hook.r*.json'))


def test_approve_checks_quality_before_archiving_or_writing(box, monkeypatch):
    """矛盾闸必须在任何状态变化之前执行，拒绝后 hook.json 与归档目录都不动。"""
    from novel.errors import TerminalState
    from novel.stages.stage6 import semantic

    before = box.read('stage6/hook.json')
    rounds_before = sorted((box.dir / 'stage6' / '_rounds').glob('hook.r*.json'))

    def reject(*_args, **_kwargs):
        raise TerminalState('候选有实质矛盾，不能定稿')

    monkeypatch.setattr(semantic, 'require_human_resolution', reject)
    pick = _mechanically_clean_pick(box)
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.approve', paid=False)
    with pytest.raises(TerminalState, match='实质矛盾'):
        hook.run_approve(
            ctx, pick, actor='test-editor', reason='已核验',
            subject_sha256=semantic.text_sha256(
                box.read('stage6/hook.json')['candidates'][pick - 1]['text']))

    assert box.read('stage6/hook.json') == before
    assert sorted((box.dir / 'stage6' / '_rounds').glob('hook.r*.json')) == rounds_before


@pytest.mark.parametrize('pick', [0, 4, -1])
def test_approve_refuses_an_out_of_range_pick(box, pick):
    """`--pick 0` / 越界 ⇒ 显式终态，**并报出有几个候选**。"""
    from novel.errors import TerminalState
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.approve', paid=False)
    with pytest.raises(TerminalState, match='候选'):
        hook.run_approve(
            ctx, pick, actor='test-editor', reason='已核验',
            subject_sha256='0' * 64)


# ── 落盘闸门与提升路：**盘上零样本，只能合成** ──────────────────────────────
#
# ⚠ 本层的闸门条件与 stage3 不同：stage3 是「任何 hard ⇒ `.INVALID`」，
#   而本层一次产**三份**，一条候选红不该废掉另两条 ⇒ 拦的是**一条干净候选都没有**。
#   两条路各配一个样本：部分红（照常落盘）与全红（拦下）。

def _fake_client(ctx, texts: list[str]):
    """一个**不发任何请求**的 client；脏候选定向重试时回同文案（因此不采纳）。"""
    import json as _json

    from contracts.invariants import HOOK_APPROACHES
    from novel.llm.client import LlmClient
    from novel.llm.transport import ChatResponse, FakeTransport
    body = _json.dumps({
        'candidates': [{'approach': a, 'text': t}
                       for a, t in zip(HOOK_APPROACHES, texts)],
        'rationale': '三条各赌一个角度。',
    }, ensure_ascii=False)
    by_approach = dict(zip(HOOK_APPROACHES, texts))

    def respond(request, _index):
        content = body
        marker = '── 单候选定向修复 ──\n'
        if marker in request.user:
            repair = _json.loads(request.user.split(marker, 1)[1])
            approach = repair['target_approach']
            content = _json.dumps({
                'approach': approach,
                'text': by_approach[approach],
                'rationale': '三条仍各赌一个角度；本次修复没有改善目标候选。',
            }, ensure_ascii=False)
        return ChatResponse(content=content, finish_reason='stop',
                            usage={'prompt_tokens': 1, 'completion_tokens': 1},
                            model='deepseek-v4-pro')

    return LlmClient(ctx, FakeTransport(respond), sleep=lambda _s: None)


def test_synthetic_stage6_gate_blocks_only_when_every_candidate_is_dirty(
        tmp_path, monkeypatch):
    """Three-candidate gating is a default-suite contract, not corpus data."""
    from types import SimpleNamespace

    from contracts.schema.report import finding
    from contracts.schema.stage6 import Mech
    from novel.errors import GateBlocked
    from tests.test_stage2_generation import _ctx

    written = []

    class Store:
        def __init__(self, ctx):
            self.ctx = ctx

        def write(self, rel, artifact):
            written.append((rel, artifact))

        def write_invalid(self, rel, artifact, *, gates_skipped):
            invalid = rel.replace('.json', '.INVALID.json')
            written.append((invalid, artifact))
            return self.ctx.novel_dir / invalid

    upstreams = SimpleNamespace(
        mat=prompts.Material(blocks=('合成完整创作包。',), upto=1),
        upto=1,
        judged={},
    )
    monkeypatch.setattr(hook, 'ArtifactStore', Store)
    monkeypatch.setattr(hook, '_Upstreams', lambda _ctx, _store: upstreams)

    def judge(text, _up):
        findings = ([finding(
            'hook_truncated', detail=f'候选未完整：{text}', evidence=[text])]
            if text.startswith('坏候选') else [])
        return text, Mech(punct_fixed=0, quotes_fixed=0, paras_fixed=0), findings

    monkeypatch.setattr(hook, '_normalize_and_judge', judge)
    book = tmp_path / '合成开篇闸门_611'
    ctx = _ctx(book, 'stage6.hook')
    partial = hook.run_hook(ctx, _fake_client(
        ctx, ['干净候选甲。', '干净候选乙。', '坏候选丙']))
    assert sum(bool(candidate.findings) for candidate in partial.candidates) == 1
    assert written[-1][0] == hook.REL

    ctx2 = _ctx(book, 'stage6.hook')
    with pytest.raises(GateBlocked, match='每一条'):
        hook.run_hook(ctx2, _fake_client(
            ctx2, ['坏候选甲', '坏候选乙', '坏候选丙']))
    assert written[-1][0] == 'stage6/hook.INVALID.json'


#: 三条候选各换一处措辞。⚠ **只换形容词，不碰段间分隔也不碰末字**——
#: 变体存在的唯一理由是让三条文本互不相同，它不许顺手改变任何一条判据的判定。
_VARIANTS = ('风把头发吹乱了', '雨把头发打湿了', '雪把头发压塌了')


def _sample(box: sandbox.Sandbox, *, truncated: bool, variant: int = 0) -> str:
    """造一条合成文案：只在 `hook_truncated` 这一条上可控地红或不红。

    ⚠ **段间分隔必须照本书自己的档位造**，否则 `hook_para_sep`（另一条 HARD）
    会恒亮，于是「部分红」那个样本变成「全红」——第一版就踩了这个：
    写了一段单行文本，而三本原作文案段都是**空行分段**，
    单段文本根本没有分隔符可归一 ⇒ 那条 HARD 修不掉。
    〔那句「归一化应该已经修掉它了」对单段输入是**错的诊断**，已修（TODO-10b）：
    判据现在按「成稿是不是一整段」分两种病因，反例在
    `tests/test_hard_counterexamples.py` 那两条。本 fixture 的构造理由不变。〕

    ⚠⚠ **`variant` 是被载体逼出来的**：`HookResponse` 现在有一条后置条件
    「三个候选文本不得重复」（`contracts/schema/stage6.py:273`），而本 fixture 原来
    对三条候选喂**同一个字符串** ⇒ 4 档阶梯逐档拒收、以 `LlmFailed` 收场。
    那次红说的不是闸门坏了，是 fixture 比载体老。
    ⇒ 三条各换一处措辞；换的位置刻意避开段间分隔与末字，
    **它不许顺手改变任何一条判据的判定**。
    """
    from novel.core.bands import sep_band_of_text
    src = (sandbox.cut(box.book, 'stage1/hook_copy.json') or {}).get('text', '').strip()
    sep = '\n\n' if sep_band_of_text(src) == '空行分段' else '\n'
    body = sep.join(['她站在门口，手里攥着那张纸',
                     f'不知道该不该推门进去，{_VARIANTS[variant % len(_VARIANTS)]}'])
    return body if truncated else body + '。'


def _samples(box: sandbox.Sandbox, *, truncated: list[bool]) -> list[str]:
    """三条互不相同的候选；`truncated` 逐条说这一条该不该红。"""
    return [_sample(box, truncated=cut, variant=i) for i, cut in enumerate(truncated)]


def test_the_gate_blocks_only_when_no_candidate_is_clean(box):
    """⭐ **闸门拦的是「一条干净候选都没有」，不是「有一条红」。**

    一条候选红就废掉整次付费产出，会让另外两条本来可用的候选一起陪葬——
    而本层的现役闸门是**人定稿**，人手里有三条可挑。
    """
    from novel.errors import GateBlocked
    from novel.store import ArtifactStore

    # ① 部分红 ⇒ 照常落盘，报告把红的那条摆出来
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=True)
    art = hook.run_hook(ctx, _fake_client(ctx, _samples(box, truncated=[False, False, True])))
    assert ArtifactStore(ctx).exists('stage6/hook.json')
    red = {c.approach for c in art.candidates
           for f in c.findings if f.rule == 'hook_truncated'}
    assert len(red) == 1, '只有第三条该红'

    # ② 全红 ⇒ 拦下，问题稿进 .INVALID
    ctx2 = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=True)
    with pytest.raises(GateBlocked, match='每一条') as blocked:
        hook.run_hook(ctx2, _fake_client(ctx2, _samples(box, truncated=[True, True, True])))
    assert ('stage6.hook --revalidate --actor <id> --reason <why>'
            in str(blocked.value))
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

    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=True)
    with pytest.raises(GateBlocked):
        hook.run_hook(ctx, _fake_client(ctx, _samples(box, truncated=[True, True, True])))
    inv = box.dir / 'stage6' / 'hook.INVALID.json'
    before = _json.loads(inv.read_text(encoding='utf-8'))['_provenance']

    ctx2 = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    promoted, _rep = hook.run_revalidate(
        ctx2, actor='stage6-test', reason='verify the unchanged draft remains blocked')
    assert promoted is False, '还没改就提升了？那道闸门形同虚设'

    # 人手改一个字（0 成本自救），再提升
    data = _json.loads(inv.read_text(encoding='utf-8'))
    for i, c in enumerate(data['candidates']):
        c['text'] = _sample(box, truncated=False, variant=i)
    inv.write_text(_json.dumps(data, ensure_ascii=False), encoding='utf-8')

    ctx3 = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    promoted, rep = hook.run_revalidate(
        ctx3, actor='stage6-test', reason='repair all truncated hook candidates')
    assert promoted is True
    after = box.read('stage6/hook.json')['_provenance']
    assert after['prompts'] == before['prompts'], '提升重盖了 prompt 指纹 —— 那是伪造'
    assert after['model'] == before['model']
    assert after.get('revalidated') is True
    assert not ArtifactStore(ctx3).exists('stage6/hook.INVALID.json'), \
        '.INVALID 该被归档进 _rounds/，不是留在原地'
    assert rep.passed is True


# ── 完整创作包 ──────────────────────────────────────────────────────────────

def _packet_of(user: str) -> dict:
    """从实际要发出去的 user 里把创作包**解回来**。

    ⚠⚠ **别拿裸子串去查多行内容。** 下发面是 `json.dumps(packet, indent=2)`，
    原文里的换行在里面是 `\\n` 两个字符 ⇒ `source['text'] in user` 对**任何**
    带空行分段的文案都必然为假，而它报出来的样子是「原始文案没有下发」——
    一句与事实相反的话（文案就在里面，只是被 JSON 转义了）。
    ⇒ 解回来再逐字段比：既准确，也不会在下次改编码方式时假红。
    """
    import json as _json
    return _json.loads(user[user.index('{'):])


def test_prompt_carries_source_full_story_and_body_execution_style(box):
    """质量升级的三项信息必须真的进入实际发送的 user，而不只写在设计文档里。"""
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    _sys, user, mat = hook.build_prompt(ctx)
    packet = _packet_of(user)
    source = sandbox.cut(box.book, 'stage1/hook_copy.json')
    gg = sandbox.cut(box.book, 'stage2/global_grounding.json')
    style = sandbox.cut(box.book, 'stage3/style_grounding.json')
    assert packet['source_hook'] == source['text'].strip()
    assert mat.source_hook == source['text'].strip()
    assert gg['arc'] == mat.story_truth['arc'] == packet['story_truth']['arc']
    for value in (gg['arc'].get('climax'), gg['arc'].get('ending')):
        if value:
            assert value in user, '全书兑现没有真正下发，payoff_preview 仍会闭卷写作'
    assert mat.style_packet['book_surface_habits'] == style.get('surface_habits')
    assert packet['style'] == mat.style_packet, '下发的风格包与 Material 记的那份分家了'
    assert packet['whole_story_map'] == list(mat.story_map) and len(mat.story_map) >= 1
