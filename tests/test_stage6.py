#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 当前文案契约、确定性重判、归一幂等与人工定稿。"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from contracts.invariants import slot_of
from contracts.rules import REGISTRY
from contracts.schema.stage6 import ApprovedHook, Hook
from novel.core.measure import hook_style_metrics, measure
from novel.run import RunContext
from novel.stages.stage6 import checks, hook, prompts
from novel.store import ArtifactStore
from novel.stages.stage6.measure import normalize_hook, quote_problems_of
from tests import corpus, factories, sandbox
from tests.markers import corpus_params

#: 一条 stage6 纵切跑起来要的五份。
STAGE6_INPUTS = ('stage1/hook_copy.json', 'stage2/characters.json',
                 'stage6/prepare.json', 'stage6/prepare_review.json', 'stage2/global_grounding.json',
                 'stage4/writer_context.json',
                 'stage6/hook.json')


def books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage6/hook.json'})


def _drafts_of(book: str) -> tuple[str, ...]:
    """本书当前正式成稿。"""
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.pattern == 'stage5/ch{nn}.json'
        and '_rounds/' not in a.rel))


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


@pytest.fixture
def current_drafts(box):
    """给**要读逐章成稿**的那些用例用：先声明「这本书的成稿相对上游是当前的」。

    ⚠⚠ **刻意不放进 `box` 自己**：本文件 16 条里只有 10 条真的去读成稿，
    另外 6 条（文案自身的判据、合成样本、闸门边界）在成稿过期的书上照样成立。
    把前提提到 `box` 上会把那 6 条一起变成跳过——**那是洗绿**，
    而跳过与「查过没事」在报告上长得一模一样。
    ⇒ 谁读成稿谁挂前提，用 `@pytest.mark.usefixtures('current_drafts')`。

    过期时的形状：`stage6.hook` 在读侧闸拿到
    `UpstreamStale: 上游 stage5/ch01.json 已过期` ⇒ 用例死在门口，
    它要验的判据行数/徽章/重判对账一次都没跑到。
    """
    corpus.requires_current(box.book, *_drafts_of(box.book),
                            produced_by='stage5.write', cost='付费')
    return box


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


def _approval_review_token(ctx: RunContext) -> str:
    """批准请求只搬运调用前真实看见的 opaque Stage6 token。"""
    return hook.approval_workpack(ArtifactStore(ctx)).review_token


# ── 报告的形状：由 `build()` 遍历注册表保证 ─────────────────────────────────

@pytest.mark.usefixtures('current_drafts')
def test_every_registered_rule_produced_a_row(box):
    """⭐ **每一条注册判据恰好一行，且没有 `skipped`。**（形态照抄 `test_stage3.py`）

    `skipped` 的意思是「注册了而实现没排它」——一条判据不可能因为「实现忘了调」
    而从报告里消失，因为报告不是由实现的输出生成的。
    """
    rep = _check(box)
    assert {r.rule for r in rep.rules} == {r.rule for r in REGISTRY.of_stage('stage6')}
    assert [r.rule for r in rep.rules if r.status == 'skipped'] == []
    assert len(rep.rules) == len(REGISTRY.of_stage('stage6'))


@pytest.mark.usefixtures('current_drafts')
def test_the_badge_can_never_be_pass_and_that_is_by_design(box):
    """⚠ **stage6 的徽章在结构上永远不可能是 `pass`。**

    unver 每次都输出（`builder._status_of` 强制），而 `badge_of` 见 `unver`
    就至少 amber。**这不是缺陷**，是本层「能查的都查了、查不了的写在脸上」的可见形态。

    判官退役（2026-09-15）后 unver 一族只剩四条：三条工作流/前提 + Prepare 的
    语义审阅前提（`prepare_semantic_review`，由独立人工接受收口）。
    """
    rep = _check(box)
    hard_hit = any(r.sev == 'hard' and r.status == 'hit' for r in rep.rules)
    assert rep.badge == ('fail' if hard_hit else 'amber')
    assert rep.passed is (not hard_hit)
    # 按两族分开数，让「哪一族变了」下次一眼看得出来。
    unver = {r.rule for r in rep.rules if r.sev == 'unver'}
    assert unver == {
        # 三条工作流/前提：判定的**前提**不成立时出声
        'hook_not_approved', 'hook_body_missing', 'hook_body_stale',
        # Prepare 的独立人工语义审阅前提
        'prepare_semantic_review',
    }
    semantic_lines = [r for r in rep.rules if r.check_kind == 'semantic']
    assert {r.rule for r in semantic_lines} == {'prepare_semantic_review'}


# ── ⭐ 两条等价测试 ─────────────────────────────────────────────────────────

def _texts_and_stored(box: sandbox.Sandbox):
    """盘上那份产物里全部要判的文本 + 它自己存的判定。"""
    data = box.read('stage6/hook.json')
    out = [(slot_of(index), c['text'], {f['rule'] for f in c['findings']})
           for index, c in enumerate(data['candidates'], 1)]
    if data.get('approved'):
        a = data['approved']
        # ⚠ 定稿记的是**位置身份**（`slot`），`approach` 已经不落盘了：
        #   同一种策略采两条样本时它答不出「从哪一条来」。`slot=None` = 人手写、无前身。
        out.append((f'定稿·{a["slot"] or "人手写"}', a['text'],
                    {f['rule'] for f in a['findings']}))
    return out


@pytest.mark.usefixtures('current_drafts')
def test_rejudging_reproduces_what_is_on_disk(box):
    """⭐⭐ **重判 == 存盘。** 对位 stage3 那次的「23 项逐值」。

    对 3 候选 + 1 定稿逐份重跑判据，产出的 rule 集合必须与产物里存的**相等**。
    **不等就是判据抄错了**——而逐字移植时漏一个字符类、少一个前瞻断言，
    不会报错，只会让某条判据在某类输入上静默地不再拦。

    排除两条不由逐文本判据产生的书级结论。
    """
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    up = hook._Upstreams(ctx, __import__('novel.store', fromlist=['x']).ArtifactStore(ctx))
    # `hook_report` 是书级汇总，不存进候选 findings；其余逐文本判据必须逐条相等。
    # 当前 schema v2 语料已经由含 `hook_source_distance` 的 producer 生成，不能再把
    # 这条无条件度量从两侧同时减掉，否则重判守卫会在最需要它时假绿。
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

@pytest.mark.usefixtures('current_drafts')
def test_identity_grid_excludes_semantic_rules(box):
    """恒等基线的格子只给**机械** hard+info——语义判据（现为
    `prepare_semantic_review`）的产地是独立人工审阅，0 成本路给它们
    占格就是永远 `0/1` 的假格子。"""
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    grid, _rep = hook.run_identity(ctx)
    semantic_rules = {r.rule for r in REGISTRY.of_stage('stage6')
                      if r.check_kind == 'semantic'}
    assert semantic_rules == {'prepare_semantic_review'}
    assert not semantic_rules & set(grid)


@pytest.mark.usefixtures('current_drafts')
def test_body_stale_uses_consumed_not_the_whole_file_sha1(box):
    """当前成稿未变时，消费投影应直接判 fresh。"""
    rep = _check(box)
    line = next(r for r in rep.rules if r.rule == 'hook_body_stale')
    assert line.status == 'unver'
    assert not line.findings, f'{box.book}: 当前成稿被误判为漂移：{line.findings}'


# ── 恒等基线 ────────────────────────────────────────────────────────────────

@pytest.mark.usefixtures('current_drafts')
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
            and r.rule not in {'hook_reuses_source', 'prepare_evidence_valid'}}
    assert set(grid) == want
    assert all(v in ('0/1', '1/1') for v in grid.values())
    assert len(rep.rules) == len(REGISTRY.of_stage('stage6'))


# ── 定稿 ────────────────────────────────────────────────────────────────────

@pytest.mark.usefixtures('current_drafts')
def test_approve_copies_the_candidate_and_leaves_provenance_alone(box):
    """`stage6.approve --pick N`：定稿 = **候选那一份的拷贝** + `origin='human'`。

    ⚠⚠ **`_provenance` 一个字都不动**：不走 `llm_provenance`（0 成本的跑不许现造
    一份声称模型产了它的账），**也不走 `rewrite_provenance`**——`--pick` 没有改任何
    LLM 产的字节，标 `partial_rewrite` 会说假话。
    """
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.approve', paid=False)
    pick = _mechanically_clean_pick(box)
    before = box.read('stage6/hook.json')['_provenance']
    chosen = box.read('stage6/hook.json')['candidates'][pick - 1]
    new, rep = hook.run_approve(
        ctx, pick, actor='test-editor', reason='已核验',
        review_token=_approval_review_token(ctx))
    after = box.read('stage6/hook.json')
    assert after['_provenance'] == before, '定稿改动了来源账'
    assert new.approved is not None
    assert new.approved.origin == 'human'
    assert new.approved.resolution.basis == 'human_selection'
    assert new.approved.slot == slot_of(pick), (
        '定稿绑的是**位置**——`--pick N` 从一开始就是位置寻址；'
        '`approach` 已经不落盘了')
    assert new.approved.text == chosen['text']
    # 返回的是整份三候选报告；未选候选的红行仍会如实保留，
    # 所以「批准成功」不蕴含 `rep.passed is True`。
    assert rep.rules
    # 归档：换掉的是「谁被选中」这个决定，而那正是下次调 prompt 时唯一的证据。
    assert list((box.dir / 'stage6' / '_rounds').glob('hook.r*.json'))


def test_approve_checks_quality_before_archiving_or_writing(box, monkeypatch):
    """定稿的人工绑定闸必须在任何状态变化之前执行，拒绝后 hook.json
    与归档目录都不动（判官退役后这里是字节绑定/凭据校验的拒绝面）。"""
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
            review_token=_approval_review_token(ctx))

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
            review_token='s6r4_' + '0' * 64)


# ── 落盘闸门与提升路：**盘上零样本，只能合成** ──────────────────────────────
#
# ⚠ 本层的闸门条件与 stage3 不同：stage3 是「任何 hard ⇒ `.INVALID`」，
#   而本层一次产**三份**，一条候选红不该废掉另两条 ⇒ 拦的是**一条干净候选都没有**。
#   两条路各配一个样本：部分红（照常落盘）与全红（拦下）。

def _fake_client(ctx, texts: list[str]):
    """一个**不发任何请求**的 client；脏候选定向重试时回同文案（因此不采纳）。"""
    import json as _json

    from contracts.invariants import HOOK_RECIPE, HOOK_SLOTS
    from novel.llm.client import LlmClient
    from novel.llm.transport import ChatResponse, FakeTransport
    body = _json.dumps({
        'candidates': [{'approach': a, 'text': t}
                       for a, t in zip(HOOK_RECIPE, texts)],
        'rationale': '三条各赌一个角度。',
    }, ensure_ascii=False)
    # ⚠ 按**位置**回原文，不按策略：配方里 `scene_punchline` 占两槽，
    #   按策略取会让两条修复稿指向同一句 ⇒ 第二条被判 `duplicate`，
    #   而这个假 client 想造的是「重试没改善」（`hard_not_reduced`）。
    by_slot = dict(zip(HOOK_SLOTS, texts))

    def respond(request, _index):
        content = body
        marker = '── 单候选定向修复 ──\n'
        if marker in request.user:
            repair = _json.loads(request.user.split(marker, 1)[1])
            content = _json.dumps({
                'approach': repair['target_approach'],
                'text': by_slot[repair['target_slot']],
                'rationale': '三条仍各赌一个角度；本次修复没有改善目标候选。',
            }, ensure_ascii=False)
        return ChatResponse(content=content, finish_reason='stop',
                            usage={'prompt_tokens': 1, 'completion_tokens': 1},
                            model='deepseek-v4-pro')

    return LlmClient(ctx, FakeTransport(respond), sleep=lambda _s: None)


def test_approved_slot_is_nullable_but_required():
    """缺失不是人手写；只有显式 `null` 才表示没有候选前身。"""
    assert ApprovedHook.model_fields['slot'].is_required()


def test_synthetic_stage6_gate_blocks_only_when_every_candidate_is_dirty(
        tmp_path, monkeypatch):
    """Three-candidate gating is a default-suite contract, not corpus data."""
    from types import SimpleNamespace

    from contracts.schema.report import finding
    from contracts.schema.stage6 import Mech
    from novel.errors import GateBlocked
    from tests.test_stage2_generation import _ctx

    written = []
    state = {}

    class Store:
        def __init__(self, ctx):
            self.ctx = ctx

        def write(self, rel, artifact):
            state[rel] = artifact
            state.pop(hook._INVALID, None)
            written.append((rel, artifact))

        def write_invalid(self, rel, artifact, *, gates_skipped):
            invalid = rel.replace('.json', '.INVALID.json')
            state[invalid] = artifact
            written.append((invalid, artifact))
            return self.ctx.novel_dir / invalid

        def exists(self, rel):
            return rel in state

        def read_own(self, _model, rel, **_kwargs):
            return state[rel], {}

        def prospective_sha256(self, _rel, artifact):
            return hook._canonical_sha256(artifact.model_dump(by_alias=True, mode='json'))

        def full_sha256_or_none(self, rel):
            return self.prospective_sha256(rel, state[rel]) if rel in state else None

        def freshness(self, _rel):
            return SimpleNamespace(state='fresh', reason='match')

        @contextmanager
        def history_transaction(self, *_rels):
            yield

    from tests.test_stage6_core_consumption import _accepted_prepare_outcomes
    upstreams = SimpleNamespace(
        prepare_outcomes=_accepted_prepare_outcomes(),
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

    ctx2 = _ctx(tmp_path / '合成全脏闸门_612', 'stage6.hook')
    with pytest.raises(GateBlocked, match='每一条'):
        hook.run_hook(ctx2, _fake_client(
            ctx2, ['坏候选甲', '坏候选乙', '坏候选丙']))
    assert written[-1][0] == 'stage6/hook.INVALID.json'

    def normalize_full_width(text, _up):
        return (text.replace('?', '？'),
                Mech(punct_fixed=0, quotes_fixed=0, paras_fixed=0), [])

    monkeypatch.setattr(hook, '_normalize_and_judge', normalize_full_width)
    ctx3 = _ctx(tmp_path / '合成归一闸门_613', 'stage6.hook')
    with pytest.raises(GateBlocked, match='归一后出现重复'):
        hook.run_hook(ctx3, _fake_client(
            ctx3, ['干净候选甲。', '同一句候选?', '同一句候选？']))
    assert written[-1][0] == 'stage6/hook.INVALID.json'

    invalid_artifact = written[-1][1]

    class RecoveryStore:
        invalid = invalid_artifact
        promoted = None

        def __init__(self, ctx):
            self.ctx = ctx

        def exists(self, rel):
            return rel == 'stage6/hook.INVALID.json' or (
                rel == hook.REL and self.promoted is not None)

        def read_own(self, _model, rel):
            artifact = self.promoted if rel == hook.REL else self.invalid
            return artifact, {}

        def promote_invalid(self, _rel, _model, *, actor, reason, artifact_override):
            assert actor and reason
            self.promoted = artifact_override

    monkeypatch.setattr(hook, 'ArtifactStore', RecoveryStore)
    monkeypatch.setattr(hook, '_judge_artifact', lambda _art, _up, _store: [])
    monkeypatch.setattr(
        hook, '_report',
        lambda _ctx, _store, _outcomes, _art: SimpleNamespace(passed=True))

    recovery_ctx = _ctx(book, 'stage6.hook')
    with pytest.raises(GateBlocked, match='重复槽位'):
        hook.run_revalidate(
            recovery_ctx, actor='stage6-test', reason='still duplicated')

    RecoveryStore.invalid = invalid_artifact.model_copy(update={
        'candidates': [
            candidate.model_copy(update={'text': f'人工改开的候选 {index}。'})
            for index, candidate in enumerate(invalid_artifact.candidates, 1)
        ],
    })
    promoted, report = hook.run_revalidate(
        recovery_ctx, actor='stage6-test', reason='three normalized texts are unique')
    assert promoted is True and report.passed is True


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


@pytest.mark.usefixtures('current_drafts')
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
    assert ('stage6.hook --revalidate --actor system:invalid-revalidate --reason <audit>'
            in str(blocked.value))
    assert ArtifactStore(ctx2).exists('stage6/hook.INVALID.json')


@pytest.mark.usefixtures('current_drafts')
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

    # 人先把三条都改干净，但两槽写成同文：仍不得提升，且问题稿继续留在原位。
    data = _json.loads(inv.read_text(encoding='utf-8'))
    data['candidates'][0]['text'] = _sample(box, truncated=False, variant=0)
    data['candidates'][1]['text'] = _sample(box, truncated=False, variant=0)
    data['candidates'][2]['text'] = _sample(box, truncated=False, variant=2)
    inv.write_text(_json.dumps(data, ensure_ascii=False), encoding='utf-8')
    ctx3 = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    with pytest.raises(GateBlocked, match='重复槽位'):
        hook.run_revalidate(
            ctx3, actor='stage6-test', reason='duplicate normalized candidates stay blocked')
    assert inv.exists()

    # 人把重复那条也改开（0 成本自救），再提升。
    data = _json.loads(inv.read_text(encoding='utf-8'))
    for i, c in enumerate(data['candidates']):
        c['text'] = _sample(box, truncated=False, variant=i)
    inv.write_text(_json.dumps(data, ensure_ascii=False), encoding='utf-8')

    ctx4 = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    promoted, rep = hook.run_revalidate(
        ctx4, actor='stage6-test', reason='repair all truncated hook candidates')
    assert promoted is True
    after = box.read('stage6/hook.json')['_provenance']
    assert after['prompts'] == before['prompts'], '提升重盖了 prompt 指纹 —— 那是伪造'
    assert after['model'] == before['model']
    assert after.get('revalidated') is True
    assert not ArtifactStore(ctx4).exists('stage6/hook.INVALID.json'), \
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


@pytest.mark.usefixtures('current_drafts')
def test_prompt_carries_source_full_story_and_minimal_hook_mechanical(box):
    """质量材料与五叶机械基线必须真的进入 user，定性画像不得混入。"""
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    _sys, user, mat = hook.build_prompt(ctx)
    packet = _packet_of(user)
    source = sandbox.cut(box.book, 'stage1/hook_copy.json')
    gg = sandbox.cut(box.book, 'stage2/global_grounding.json')
    assert packet['source_hook'] == source['text'].strip()
    assert mat.source_hook == source['text'].strip()
    assert mat.story_truth['prepare'] == packet['story_truth']['prepare']
    assert 'arc' not in packet['story_truth']
    expected = hook_style_metrics(measure(source['text'].strip()))
    assert packet['source_hook_mechanical'] == mat.hook_mechanical == expected
    assert 'style' not in packet and 'measurable' not in packet
    assert packet['whole_story_map'] == list(mat.story_map) and len(mat.story_map) >= 1


def test_paid_hook_prompt_excludes_global_source_spans(monkeypatch):
    """Stage6 可看全书 claim，但不可看 Stage2 SourceSpan 原文与坐标。"""
    import copy
    import json
    from types import SimpleNamespace

    from novel.stages.stage2.knowledge import grounding as stage2_grounding
    from contracts.schema.knowledge import KnowledgeGrounding
    from novel.stages.stage5 import review as stage5_review
    from tests.plot_fixtures import REGISTRY, GLOBAL
    from novel.consumption import prepare_projection

    seed = factories.stage5_book()
    registry = copy.deepcopy(REGISTRY)
    grounding = copy.deepcopy(GLOBAL)
    markers = ['STAGE6_EVIDENCE_ONLY_registry', 'STAGE6_EVIDENCE_ONLY_global']
    coordinates = [945678901, 945678999]
    for data, marker in zip((registry, grounding), markers):
        data['evidence_anchors'] = {'ev_fake': {
            'chapter_ordinal': 1, 'start': coordinates[0], 'end': coordinates[1],
            'text_sha256': marker}}
    preparation = {'source_blurb': {
        'facts': [{'kind': 'supported', 'claim_id': 'hook_001', 'claim': '主角离家。',
                   'evidence': {'source': {'text': markers[0], 'start': coordinates[0], 'end': coordinates[1]}}}],
        'opening': {'kind': 'proposed', 'through_chapter': 1, 'rationale': '开篇',
                    'boundary_evidence': {'source': {'text': markers[1]}}}}}

    class Dumped:
        def __init__(self, value):
            self.value = value

        def model_dump(self, **_kwargs):
            return copy.deepcopy(self.value)

    from contracts.artifacts import STAGE2_CHARACTERS
    stored = {
        hook.GLOBAL_GROUNDING: Dumped(grounding),
        STAGE2_CHARACTERS: Dumped(registry),
        hook.WRITER_CONTEXT: Dumped(seed.ctx),
        hook.HOOK_COPY: SimpleNamespace(text='原作开篇文案。'),
    }
    monkeypatch.setattr(stage2_grounding, 'read',
                        lambda store: store.read(KnowledgeGrounding, hook.GLOBAL_GROUNDING))
    monkeypatch.setattr(hook.prepare, 'require_accepted', lambda _store: Dumped(preparation))

    class Store:
        @staticmethod
        def full_sha256_or_none(rel):
            return 'a' * 64 if rel in stored else None

        @staticmethod
        def exists(rel):
            return rel in stored

        @staticmethod
        def read(_model, rel):
            return stored[rel]

    monkeypatch.setattr(hook, 'ArtifactStore', lambda _ctx: Store())
    # 本例只验证已获准主体的付费 prompt 投影；accepted Review 边界另有专门回归。
    monkeypatch.setattr(stage5_review, 'require_accepted_review', lambda *_args: None)
    monkeypatch.setattr(
        hook, 'lead_drafts',
        lambda _store, _upto: (
            [{'output_ordinal': 1, 'text': '重写后的开篇正文。'}], [], {}),
    )
    _system, user, material = hook.build_prompt(SimpleNamespace(title=seed.title))

    paid_material = user + '\n' + material.given
    truth = json.dumps(material.story_truth, ensure_ascii=False, sort_keys=True)
    assert markers and all(marker not in paid_material for marker in markers)
    assert all(str(coordinate) not in paid_material for coordinate in coordinates), \
        'SourceSpan 的 start/end 坐标进了 Stage6 付费 prompt'
    assert all(key not in truth for key in ('"source":', '"start":', '"end":'))


#: 合成原作文案段：一句一段、空行分隔、末行是一句宣言。
_SRC_HOOK = """系统让我当反派的踏板。

我把他养成了乖顺的模样。

十年过去。

结婚第七年，他忽然换了个人。

一个是后来的枭雄。

一个是十七岁的愣头青。

嚷着退婚，重夺基业。

绝不低头。

"""

#: **贴着原作结构写**：信息出场顺序逐段对位、落在与原作**同一句**收尾上，
#: 但每一句都换了措辞 ⇒ 连续内容字符数仍远低于 `hook_reuses_source` 的 20 字闸线。
_NEAR = """系统挑我当反派的垫脚。

我却把他养得服服帖帖。

整整十年。

第七年头上，他里头忽然多出两个人。

一个是日后的枭雄。

一个是十七岁的莽小子。

叫着退婚，重夺基业。

绝不低头。

"""

#: **另起一个场面**：人物动作与对白，与原作的信息顺序和收尾都不相干。
_FAR = """他睁眼就骂人。

「谁准你碰我的？」

我没说话，扇了他一巴掌。

他捂着脸，眼睛红了。

我再抬手时，他抓住了我的腕子。

指尖贴着我的掌心，抖得厉害。

他张嘴，一个字也没骂出来。

"""


def _distance_reading(text: str, src_hook: str | None) -> dict:
    """跑生产那一个落点取读数。**不在守卫里重拼一份计算。**"""
    fs = checks.hook_findings(
        text, mat=prompts.Material(blocks=('合成创作包。',), upto=1),
        secrets=[],  characters=None, src_hook=src_hook,
        hook_mechanical=None, quote_problems=[])
    return {f.rule: f for f in fs}


def test_source_distance_reads_what_the_20_char_gate_cannot_see():
    """⭐⭐ 结构距离读数必须在 `hook_reuses_source` **失明的那一带**分得开候选。

    那道 HARD 只查「连续 20 个内容字符相同」，而 `R-9.11` 的尾注**明许**学结构、
    节拍与最后一击的功能。两条合起来留了一个看不见的地带：逐句改写措辞、却完整
    复用原作的信息出场顺序并落在同一句收尾上——**闸报 0 违规**。盘上实测三条候选
    最长逐字重合 10 / 6 / 3 字（闸线 20）、4 字窗口重合率 19.5% / 8.2% / 0.0%，
    贴合度差 20 倍，而那道闸对三条一律沉默。

    ⚠ 本条**不断言任何阈值**：`R-9.23` 说的就是「度量不是门」。它只断言
    「读数分得开」——分不开的读数等于没有读数。
    """
    near = _distance_reading(_NEAR, _SRC_HOOK)
    far = _distance_reading(_FAR, _SRC_HOOK)

    # ① 那道闸对两条**都**沉默——这正是本条读数存在的前提。前提不成立就该重造样本。
    assert 'hook_reuses_source' not in near and 'hook_reuses_source' not in far, (
        '样本已经越过 20 字闸线 ⇒ 这条守卫此刻查的不是「闸看不见的那一带」')

    # ② 读数**无条件报出**，两条都有。
    assert 'hook_source_distance' in near and 'hook_source_distance' in far

    def numbers(row):
        longest, tail = row.evidence[0].split('/')
        return int(longest), int(tail)

    near_longest, near_tail = numbers(near['hook_source_distance'])
    far_longest, far_tail = numbers(far['hook_source_distance'])

    # ③ ⭐ 分得开：贴着写的那条，最长逐字重合与末行重合都严格更高。
    assert near_longest > far_longest, (
        '贴着原作结构写与另起场面读出同一个数 ⇒ 这条读数没有判别力')
    assert near_tail > 0 and far_tail == 0, (
        '末行重合是「最后一击照抄」的直接信号，不该被并进总的最长重合里')
    assert '20 字 0.0%' in near['hook_source_distance'].detail, (
        '20 字那一档必须与闸线同口径并排出现，否则读的人对不上那道闸')


def test_missing_source_hook_reads_as_unmeasurable_not_as_zero():
    """没有原作文案段时是**量不了**，不是「距离为 0」——两者不许压成一个值。"""
    row = _distance_reading(_FAR, None)['hook_source_distance']
    assert '量不了' in row.detail and not row.evidence


def test_short_hook_still_reports_source_distance():
    """不足 20 字会命中 `hook_empty`，但无条件度量不能被提前返回吞掉。"""
    rows = _distance_reading('太短。', _SRC_HOOK)
    assert 'hook_empty' in rows
    assert 'hook_source_distance' in rows


# ── 候选互异：四条写盘路一条都不许绕过 ────────────────────────────────────

def _write_hook_edit(box, *, candidate: str, text: str) -> None:
    """`--apply-edit` 的决策集。⚠ **走 `encode_artifact`**：它是注册产物，
    手写一个裸 dict 会被读盘口按「缺版本标记」拒掉，用例就测不到真正那道闸。"""
    import json as _json

    from contracts.schema import encode_artifact

    (box.dir / 'stage6' / 'hook_edit.json').write_text(
        _json.dumps(encode_artifact(
            'stage6/hook_edit.json',
            {'candidate': candidate, 'text': text}), ensure_ascii=False),
        encoding='utf-8')


def _land_three_clean_candidates(box):
    """一份三条互异候选的正式 `hook.json`。"""
    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=True)
    art = hook.run_hook(
        ctx, _fake_client(ctx, _samples(box, truncated=[False, False, False])))
    assert len(art.candidates) == 3
    return art


@pytest.mark.usefixtures('current_drafts')
def test_apply_edit_cannot_collapse_two_candidate_slots(box):
    """⭐ **`--apply-edit --candidate` 能把两个槽写成同文，直接落正式产物。**

    候选的身份是**位置**（配方按 slot 记：`payoff_preview` ×1 + `scene_punchline` ×2）。
    两个 slot 同字之后，判官对哪一条给的结论就再也分不开了。
    生成路与 `--revalidate` 早有这道闸，`--apply-edit` 没有。
    """
    import json as _json

    from novel.errors import TerminalState

    art = _land_three_clean_candidates(box)
    _write_hook_edit(box, candidate='c2', text=art.candidates[0].text)

    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    with pytest.raises(TerminalState, match='重复槽位'):
        hook.run_apply_edit(ctx)

    after = ArtifactStore(
        RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False),
    ).read_own(Hook, 'stage6/hook.json')[0]
    assert [c.text for c in after.candidates] == [c.text for c in art.candidates],         '正式产物被一次会折叠槽位的编辑覆盖了'


@pytest.mark.usefixtures('current_drafts')
def test_apply_edit_still_accepts_a_genuinely_different_text(box):
    """闸只拦折叠，不拦正常改字——收多了是另一个方向的错。"""
    import json as _json

    art = _land_three_clean_candidates(box)
    _write_hook_edit(
        box, candidate='c2',
        text=_sample(box, truncated=False, variant=1) + '换一句。')

    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    updated, _report = hook.run_apply_edit(ctx)
    assert updated.candidates[1].text != art.candidates[1].text
    assert len({c.text for c in updated.candidates}) == 3


@pytest.mark.usefixtures('current_drafts')
def test_recheck_refuses_to_write_back_candidates_that_normalise_together(box):
    """⭐ `--recheck` 0 成本但**写盘**：归一后坍缩成同文时不许静默写回。

    归一规则改过之后，两条本来不同的候选可能在归一后变成同一串字节。
    `--recheck` 原来照写不误 —— 那一刻位置身份就没了。
    """
    import json as _json

    from novel.errors import TerminalState

    _land_three_clean_candidates(box)
    formal = box.dir / 'stage6' / 'hook.json'
    data = _json.loads(formal.read_text(encoding='utf-8'))
    payload = data['payload'] if 'payload' in data else data
    # 只差一个会被归一吃掉的全角/半角差异 ⇒ 归一后两槽同文。
    payload['candidates'][1]['text'] = payload['candidates'][0]['text']
    formal.write_text(_json.dumps(data, ensure_ascii=False), encoding='utf-8')

    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False)
    with pytest.raises(TerminalState, match='重复槽位'):
        hook.run_recheck(ctx)
