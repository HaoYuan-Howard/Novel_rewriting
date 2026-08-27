#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wontfix_semantic.py —— 语义结论的人工 wontfix 闭环（TODO-04）。

【它补的是什么】豁免机制此前只活在 `novel/stages/stage5/review.py` 里，而**语义结论
够不着它**——两层的语义判据都在**报告渲染时**现算、不落任何产物，而反查只看
`review_<n>.json`。更坏的是待人工清单在建议人去跑 `--wontfix`，而 **stage6 根本没有
这个 flag**：一句结构上兑现不了的承诺，挂在一条会真命中的 INFO
（`hook_candidate_contradiction`）后面。

【2026-08-15 的两条裁决，本文件逐条钉住】
  · **持久身份 = `(层, 章|book, 判据, 证据键)`**，层由**文件路径**承载
    （`<stage>/wontfix.json`），不进收敛键；审判产物版本只进审计账 `at`，
    **不参与匹配**——`stage6.semantic` 是付费节点、重跑是常态，
    把版本塞进匹配键会让一次不改变任何结论的重审把整本账清空；
  · **失效由证据自身保证**：语义证据是候选文案 / 正文的连续原字节，文本一改键就失配。

⚠ 语义审判是**付费**产物，本文件把「模型那一次答了什么」**当成 fixture 手写**，
随后走的全部是真代码：
真判据、真报告、真清单、真定稿闸门。这与 `tests/test_stage6.py::_fake_client`
是同一条纪律——伪造的只有模型的回答，一条判定都不伪造。
"""
from __future__ import annotations

import json
import hashlib

import pytest

from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.stage6 import Hook, HookSemanticResponse, HookSemanticReview
from novel import prov
from novel.errors import TerminalState
from novel.run import RunContext
from novel.stages.stage6 import hook, semantic
from novel.store import ArtifactStore
from novel.views.inbox import inbox_view
from tests import corpus, sandbox
from tests.markers import corpus_params

STAGE6_INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
                 'stage4/writer_context.json',
                 'stage6/hook.json')


def _books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts() if a.rel == 'stage6/hook.json'})


#: 六维评分：本文件不测排序，给一组合法常数就够（总分由代码算，模型不自报）。
_QUALITY = {'read_on_desire': 7.0, 'body_voice_match': 7.0,
            'concrete_scene_and_specificity': 7.0, 'curiosity_and_payoff_promise': 7.0,
            'hook_craft': 7.0, 'synopsis_penalty': 2.0}


def _drafts_of(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.pattern == 'stage5/ch{nn}.json'
        and '_rounds/' not in a.rel))


def _one_book() -> list[str]:
    """只取一本——**取成稿链条相对上游是当前的那一本**，不是排序第一的那一本。

    本文件 11 条集成用例全部要经 `stage6.semantic` 读逐章成稿；成稿一旦相对
    重生成过的 `stage4/writer_context.json` 过期，读侧闸会 `UpstreamStale`
    ⇒ **11 条一条不剩地红在同一句话上**，而它们要验的 wontfix 三条约束一次都没跑到。
    实测三本里两本过期、一本干净，换到干净那本上 11 条全绿。
    """
    books = _books()
    if not books:
        return []
    return [next((book for book in books
                  if (rels := _drafts_of(book)) and corpus.is_current(book, *rels)),
                 books[0])]


@pytest.fixture(params=corpus_params(
    _one_book(), reason='盘上没有 stage6 语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
def box(request):
    book = request.param
    # ⚠ 前提是**语料的版本**不是被测的性质：本文件测 wontfix 的理由/证据键/闸门边界，
    #   而它们都得先让 `stage6.semantic` 读得进成稿。过期时用例死在门口（实测
    #   11/11 都是 `UpstreamStale`），拿不到自己要断言的输入。
    drafts = _drafts_of(book)
    corpus.requires_current(book, *drafts,
                            produced_by='stage5.write', cost='付费')
    s = sandbox.Sandbox(book, STAGE6_INPUTS + drafts)
    yield s
    s.close()


# ⚠ 这里曾有一个 autouse fixture，猴补 `semantic._calibration_status` 返回 `(True, '')`，
#   好让 fixture 造出来的语义产物被当成「已校准」。自动放行门整批下线后不再有 calibrated
#   这个状态，也就不需要假装它成立——本文件测的 wontfix 闭环与它本来就无关。


def _ctx(box, node: str, *, paid: bool = False) -> RunContext:
    return RunContext.open(str(box.root), box.sid, node, paid=paid, write_ledger=False)


def _seed_semantic(box, *, dirty: int = 0, uncertain: tuple[int, ...] = ()) -> str:
    """手写两名判官**那一次答了什么**，其余一律交给生产的合并路。

    ⚠ **来源账不是编的**：它由 `ctx.llm_provenance()` 从本次**真读到的上游边**铸出来，
    所以 `store.freshness(semantic_<n>.json)` 会如实判 `fresh`，
    `semantic._current_artifact` 那三道闸（有 reviews · 新鲜 · 覆盖全部当前文本）
    走的都是真代码。伪造的只有模型那一次答了什么。

    ⚠ 矛盾的 `evidence` 取候选文案**开头 12 个字**——它必须是候选自己的连续原字节，
    因为整条闭环的失效判准就是「文本一改证据就变」。

    ⚠⚠ **按去重后的文本编号，不按 `_texts_of` 的四个位置编号**，理由是真代码的形状：
    定稿常常就是某条候选的拷贝，而 `current_assessments` 是**按文本 sha1 索引**的
    ⇒ 给「定稿」单独写一份 review 会把同 sha 那条候选的结论覆盖掉。
    真跑那一侧同样只审去重文本（`semantic._unique_texts`），本 fixture 照它来。

    ⚠⚠⚠ 这里曾**手工拼** `SemanticDecision` / `EvaluatorFingerprint` / 逐判官
    `JudgeDecision`，于是 `automated` / `profile_id` / `dataset_sha256` 三格随自动
    放行门删除之后，这份 fixture 在 `extra='forbid'` 上当场 `ValidationError`
    ——而本文件整批挂在 corpus 上，默认套件一次都看不见。⇒ 现在只造
    `HookSemanticResponse`（模型真正回答的那一层），合并判定、逐判官 verdict、
    排序与运行时指纹全部走 `materialize_double_blind` 与 `_runtime_contract`：
    **合并契约再变一次，这份 fixture 自动跟着变，不会再落后。**
    `dirty` ⇒ 那条候选两名判官都报矛盾（judge verdict FAIL）；
    `uncertain` ⇒ 那几条报 `coverage=uncertain`（judge verdict UNVER）。
    合并结论恒 `UNVER`（本仓没有自动放行门），差别落在人读的 `reason` 上。
    """
    ctx = _ctx(box, 'stage6.semantic', paid=True)
    store = ArtifactStore(ctx)
    art, _raw = store.read_own(Hook, hook.REL)
    up = hook._Upstreams(ctx, store)                 # 读齐上游 ⇒ 来源账与真跑同源

    # 匿名标签照 `build_prompt` 那一条现算，别在测试里另起一套编号。
    aliases = [
        (f'T{index}', real, text)
        for index, (real, text) in enumerate(semantic._unique_texts(art), 1)
    ]
    response = HookSemanticResponse.model_validate({
        'reviews': [{
            'label': neutral,
            'coverage': 'uncertain' if index in uncertain else 'complete',
            'uncertainty_reason': ('开篇几章还没写全，核对不完整'
                                   if index in uncertain else None),
            'contradictions': ([{
                'assertion': '这条候选把人物关系写反了',
                'evidence': text.strip()[:12],
                'reason': '与全书事实里的关系相反',
            }] if index == dirty else []),
            'quality': dict(_QUALITY),
            'concise_reason': '这一条的判定说明',
        } for index, (neutral, _real, text) in enumerate(aliases)],
        'ranking': [neutral for neutral, _real, _text in aliases],
    })
    # 两名判官同答：本文件测的是 wontfix 闭环，不测 A/B 分歧那条 reason 分支。
    reviews, decisions, ranking, judge_rankings = semantic.materialize_double_blind(
        response, response, aliases=aliases)
    artifact = HookSemanticReview(
        judged_texts={real: semantic.text_sha1(text) for _n, real, text in aliases},
        judged=semantic._prompt_judged(up),
        hook_file_sha1=prov.fingerprint_bytes(store.path(hook.REL).read_bytes()),
        reviews=reviews,
        ranking=ranking,
        judge_rankings=judge_rankings,
        decisions=decisions,
        evaluator_fingerprint=semantic._runtime_contract('test-model'),
        # ⚠ 走生产那一个产地，**不在这里手拼第二份**：手拼的那份原来写着
        #   `HOOK_SEMANTIC_SYS_A/B`，而生产改了键名之后它照样绿——一份不会红的 fixture
        #   就是在断言一件不再发生的事。
        _provenance=ctx.llm_provenance(
            prompts=semantic.judge_prompts(semantic.SEMANTIC_SYS),
            model='test-model', adopted_models=['test-model']),
    )
    seq = (semantic.semantic_seqs(store)[-1] + 1) if semantic.semantic_seqs(store) else 1
    rel = semantic.SEMANTIC_TPL.format(n=seq)
    store.write(rel, artifact)
    return rel


def _report(box):
    return hook.run_check_only(_ctx(box, 'stage6.hook'))


def _line(report, rule: str):
    return next(r for r in report.rules if r.rule == rule)


def _spec_of(report, rule: str, why: str) -> tuple[str, str]:
    """从**报告自己**取那条结论的证据键 —— 人在界面上做的就是这件事。"""
    line = _line(report, rule)
    assert line.findings, f'{rule} 这一次一条结论都没产出——本条没在查'
    key = line.findings[0].evidence_key
    return key, f'book|{rule}|{key}={why}'


def test_synthetic_semantic_finding_can_be_marked_without_current_corpus():
    """The semantic-wontfix closure is deterministic and belongs in defaults."""
    from contracts.schema.report import finding
    from novel import wontfix

    conclusion = finding(
        'hook_candidate_contradiction',
        detail='候选把人物关系写反了',
        evidence=['悬念前置:她推门看见空屋'],
    )
    saved = {}

    class Store:
        def exists(self, _rel):
            return False

        def write(self, rel, artifact):
            saved[rel] = artifact

    entry = wontfix.mark(
        Store(),
        'stage6',
        f'book|{conclusion.rule}|{conclusion.evidence_key}=体裁允许保留这处反差',
        pool_of=lambda ordinal: [conclusion] if ordinal is None else [],
        at='semantic_1:synthetic',
    )
    ledger = saved['stage6/wontfix.json'].root
    assert entry.why == '体裁允许保留这处反差'
    marked = wontfix.applied([conclusion], ledger)[0]
    assert marked.rule == conclusion.rule and marked.detail == conclusion.detail
    assert marked.actionable is False and marked.wontfix is not None


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐ 主链：真实语义命中 → 标记 → 重渲染 → 进计数 → 正文变化后失效
# ─────────────────────────────────────────────────────────────────────────────

def test_a_real_semantic_hit_can_be_marked_and_the_conclusion_survives(box):
    """⭐⭐ TODO-04 的验收样本前半段。

    ① 真的有一条 `hook_candidate_contradiction` 命中（判据是真跑的）；
    ② `--wontfix` 收得下它 —— **这一步在本轮之前结构上不可能**：
       反查只看 `review_<n>.json`，而语义结论一份产物都不进；
    ③ 重渲染后那条结论**还在**，只是带上了 `wontfix` 且 `actionable=False`
       ——**豁免不删结论**，「有人看过并决定不修」与「没人看过」必须分得开；
    ④ 账落在 `stage6/wontfix.json`，键里**不含层名**（层由文件路径承载）。
    """
    _seed_semantic(box)
    before = _report(box)
    key, spec = _spec_of(before, 'hook_candidate_contradiction', '文案体裁允许这么写')
    assert _line(before, 'hook_candidate_contradiction').status == 'hit'

    hook.run_wontfix(_ctx(box, 'stage6.hook'), spec)

    rel = 'stage6/wontfix.json'
    raw = json.loads((box.dir / rel).read_text(encoding='utf-8'))
    ledger = decode_artifact(rel, raw).payload
    assert list(ledger) == [f'book|hook_candidate_contradiction|{key}'], \
        '键里不该出现层名——层由文件路径承载'
    entry = ledger[list(ledger)[0]]
    assert entry['why'] == '文案体裁允许这么写'
    assert entry['at'].startswith('semantic_'), \
        '审计账要说出人当时看的是哪一版审判（它不参与匹配，但必须记得下来）'

    after = _report(box)
    line = _line(after, 'hook_candidate_contradiction')
    assert line.status == 'hit', '豁免把结论删掉了——那正是这个机制不许做的事'
    marked = next(f for f in line.findings if f.evidence_key == key)
    assert marked.wontfix is not None and marked.wontfix.why == '文案体裁允许这么写'
    assert marked.actionable is False
    assert marked.sev == 'info', '豁免不许改 sev'


def test_the_marked_hit_leaves_the_inbox_and_enters_the_count(box):
    """⭐ 验收样本中段：**豁免不占清单，只计数**。"""
    _seed_semantic(box)
    rule = 'hook_candidate_contradiction'
    _key, spec = _spec_of(_report(box), rule, '文案体裁允许这么写')

    before = inbox_view(str(box.root), box.sid)
    assert any(it.rule == rule for it in before.items), \
        f'{rule} 没进待人工清单——本条没在查'
    assert before.wontfix == 0

    hook.run_wontfix(_ctx(box, 'stage6.hook'), spec)

    after = inbox_view(str(box.root), box.sid)
    assert after.wontfix == 1, 'stage6 那份账没被跨层计数收进来'
    assert len([it for it in after.items if it.rule == rule]) \
        == len([it for it in before.items if it.rule == rule]) - 1


def test_the_exemption_expires_when_the_candidate_text_changes(box):
    """⭐⭐ 验收样本后半段：**正文一变，豁免自动失效**。

    这是「豁免不能成为永久洗绿」的**机制**，而它不是靠版本号，是靠证据本身：
    收敛键里的证据是候选文案的连续原字节 ⇒ 人手改掉那条候选，键就失配。
    ⚠ 顺带钉住反面：改的是**另一条**候选时，这条豁免**不该**被牵连失效。
    """
    _seed_semantic(box)
    rule = 'hook_candidate_contradiction'
    report = _report(box)
    key, spec = _spec_of(report, rule, '文案体裁允许这么写')
    hook.run_wontfix(_ctx(box, 'stage6.hook'), spec)
    assert next(f for f in _line(_report(box), rule).findings
                if f.evidence_key == key).wontfix is not None

    # 人工改掉那条被豁免的候选（0 成本路），随后重新审判
    art, _raw = ArtifactStore(_ctx(box, 'stage6.hook')).read_own(Hook, hook.REL)
    target = key.split(':', 1)[0]
    box.write('stage6/hook_edit.json', {
        'candidate': target,
        'text': art.candidates[0].text.replace(
            art.candidates[0].text.strip()[:12], '她推开门，屋里空无一人', 1)
        if target == art.candidates[0].approach else '她推开门，屋里空无一人。'})
    hook.run_apply_edit(_ctx(box, 'stage6.hook'), 'stage6/hook_edit.json')
    _seed_semantic(box)

    after = _line(_report(box), rule)
    survived = [f for f in after.findings if f.evidence_key == key and f.wontfix is not None]
    assert not survived, (
        '候选文案改过了，旧豁免还挂着——那正是「永久洗绿」：'
        '人当初裁的是另一段字节')
    assert inbox_view(str(box.root), box.sid).wontfix == 1, \
        '账本身要留着（它是审计记录），失效的是「套不套得上」这件事'


# ─────────────────────────────────────────────────────────────────────────────
# 二、拒收：不存在的、越界的、说不清的
# ─────────────────────────────────────────────────────────────────────────────

def test_a_finding_that_does_not_exist_is_refused(box):
    """**不给不存在的结论发豁免**——缺省是「不知道」，不许猜。"""
    _seed_semantic(box)
    ctx = _ctx(box, 'stage6.hook')
    with pytest.raises(TerminalState, match='不给不存在的结论发豁免'):
        hook.run_wontfix(ctx, 'book|hook_candidate_contradiction|查无此键=随便写点')
    assert not (box.dir / 'stage6' / 'wontfix.json').exists(), \
        '拒收路不许留下半份账'


def test_a_wrong_rule_id_gets_two_different_refusals():
    """⭐ **非 corpus**：跨层保护的最小守卫，一份语料都不需要。

    ⚠⚠ 为什么单独有这一条：下面那条同名用例挂在 corpus 上，而语料一旦腐坏它就整批
    ERROR（实测：`trim_audit` 的版本破坏让本文件 11 条全死在 sandbox 构造期，
    一条断言都没跑到）。「正例全挂 corpus」正是跨层守卫当初能静默退化整整一轮而
    没人发现的原因。⇒ 把 `mark()` 的两句拒绝钉进默认套件。

    `store` 在这三个分支触发之前一次都没被读，所以传 `None` 就够——
    这不是偷懒，是**这三道闸本来就不该需要盘上有东西**。
    """
    from novel import wontfix as W

    # ① 根本没注册：这个 id 拼错了，或者判据的行还没写。
    with pytest.raises(TerminalState, match='不在判据表里'):
        W.mark(None, 'stage6', 'book|no_such_rule_anywhere|k=拼错了',
               pool_of=lambda _o: [], at='semantic_1')
    # ② 注册了，但属于别层——这一条才是「跨层」。两句合并就分不出①和②。
    with pytest.raises(TerminalState, match='是 stage5 的判据，不是 stage6 的'):
        W.mark(None, 'stage6', 'book|secret_leak|k=层写错了',
               pool_of=lambda _o: [], at='semantic_1')
    # ③ 反向：同一条判据记进**它自己那一层**不该被上面两道拦住，
    #    要一直走到结论池那一步才因「池里没有」被拒。
    with pytest.raises(TerminalState, match='不给不存在的结论发豁免'):
        W.mark(None, 'stage5', 'book|secret_leak|k=本层',
               pool_of=lambda _o: [], at='review_1')


def test_a_rule_outside_this_stage_is_refused(box):
    """判据必须是**这一层**的。stage5 的判据写进 stage6 的账里，
    那条豁免永远套不上任何东西，而报告上看不出来。

    ⚠⚠ 这里曾用 `semantic_secret_leak` 当「stage5 的判据」。它随 `stage5.semantic`
    整节点下线后**不属于任何层**，于是这条用例照样绿——但它测的已经退化成
    「未知 id 被拒」，跨层保护静默失效。⇒ 换成一条**当前真属于 stage5** 的判据
    （`secret_leak`），并把两句拒绝**各钉一条**：只要 `mark()` 哪天又把它们合成
    一句，下面第二条就会连着第一条一起绿，那正是当初漏掉的形状。
    """
    _seed_semantic(box)
    ctx = _ctx(box, 'stage6.hook')
    with pytest.raises(TerminalState, match='是 stage5 的判据，不是 stage6 的'):
        hook.run_wontfix(ctx, 'book|secret_leak|ch1:xx=层写错了')
    with pytest.raises(TerminalState, match='不在判据表里'):
        hook.run_wontfix(ctx, 'book|no_such_rule_anywhere|ch1:xx=这个 id 拼错了')


def test_a_chapter_number_is_refused_on_stage6(box):
    """本层结论一律书级：候选身份在证据键里，不在章这一维。"""
    _seed_semantic(box)
    with pytest.raises(TerminalState, match='只能是 `book`'):
        hook.run_wontfix(_ctx(box, 'stage6.hook'), '3|hook_candidate_contradiction|x=写错了')


def test_a_reason_is_mandatory(box):
    """写不出理由的豁免，与「没看过」在盘上没有区别。"""
    _seed_semantic(box)
    with pytest.raises(TerminalState, match='缺理由'):
        hook.run_wontfix(_ctx(box, 'stage6.hook'), 'book|hook_candidate_contradiction|x')


def test_two_hits_on_one_rule_need_the_evidence_key(box):
    """同一条判据有多条结论时**要人说清豁免的是哪一条**——
    一并豁免不是人做的决定。"""
    _seed_semantic(box, dirty=0, uncertain=(1,))
    report = _report(box)
    assert len(_line(report, 'hook_candidate_contradiction').findings) >= 2
    with pytest.raises(TerminalState, match='要说清豁免的是哪一条'):
        hook.run_wontfix(_ctx(box, 'stage6.hook'),
                         'book|hook_candidate_contradiction=两条一起免了')


def test_each_uncertain_candidate_gets_its_own_key(box):
    """⭐ **`coverage=uncertain` 那一族必须逐条可寻址。**

    ⚠⚠ 这一格原来是 `evidence=[]` ⇒ 收敛键恒是空串 ⇒ 三条候选的「核对不完整」
    **共用同一个键**，一次豁免会把三条一并豁免掉。
    ⇒ 现在是 `<候选>:<文本指纹>`：既逐条可寻址，也在文本变化后自动失效
    （用候选名会失效不了——那是位置不是内容）。
    """
    _seed_semantic(box, dirty=-1, uncertain=(0, 1))
    findings = _line(_report(box), 'hook_candidate_contradiction').findings
    keys = [f.evidence_key for f in findings]
    assert len(keys) == len(set(keys)) >= 2, f'两条「核对不完整」同键：{keys}'
    assert all(k and ':' in k for k in keys), f'空证据键又回来了：{keys}'


# ─────────────────────────────────────────────────────────────────────────────
# 三、边界：豁免不是通行证
# ─────────────────────────────────────────────────────────────────────────────

def test_an_exemption_does_not_soften_the_approval_binding(box):
    """⭐ **豁免不是通行证。** 它只降 `actionable` 并打标，批准路那把尺子一格不松。

    ⚠⚠ 这条曾断言「豁免之后批准路**照旧拒绝**那条有矛盾的候选」，走的是
    `semantic.require_selectable`。那个函数已整份删除，而它守的产品语义也被裁决掉了：
    自动 FAIL 不再可产 ⇒ 没有任何机制会替人否决一个候选
    （`novel/stages/stage6/semantic.py::require_human_resolution` 的 docstring）。
    ⇒ 保留的是仍然承重的那一半：**豁免不改变批准闸的任何判定输入**——
    字节绑定照拒、basis 照旧只能是人工核验、那条结论也照旧留在报告里。
    删掉这条用例等于把「豁免不开闸」这条纪律的最后一个守卫也删了。
    """
    from contracts.schema.stage6 import Hook as _Hook

    _seed_semantic(box, dirty=0)
    store = ArtifactStore(_ctx(box, 'stage6.hook'))
    art, _raw = store.read_own(_Hook, hook.REL)
    dirty_text = art.candidates[0].text

    report = _report(box)
    line = _line(report, 'hook_candidate_contradiction')
    assert line.findings, 'dirty 候选没产出矛盾结论——本条没在查'
    before_sev = line.sev
    _key, spec = _spec_of(report, 'hook_candidate_contradiction', '我认了')
    hook.run_wontfix(_ctx(box, 'stage6.hook'), spec)

    # ① 结论仍在、严重度未变——豁免打标，不删账。
    after = _line(_report(box), 'hook_candidate_contradiction')
    assert after.findings, '豁免把结论本身抹掉了——那是删账不是打标'
    assert after.sev == before_sev, '豁免改了判据的严重度'

    # ② 字节绑定照拒：拿一个不是当前候选字节的 sha256 定稿，仍是显式终态。
    with pytest.raises(TerminalState, match='已变化，不能用旧页面定稿'):
        semantic.require_human_resolution(
            store, art, dirty_text, '候选 1',
            actor='wontfix-test', reason='豁免过了就想直接定稿',
            expected_subject_sha256='0' * 64)

    # ③ 绑对了也只拿得到人工核验这一种依据——豁免不会换来一条自动 basis。
    _assessment, basis = semantic.require_human_resolution(
        store, art, dirty_text, '候选 1',
        actor='wontfix-test', reason='逐项核对了判官证据后仍决定用这一条',
        expected_subject_sha256=semantic.text_sha256(dirty_text))
    assert basis == 'human_verification'


def test_the_two_ledgers_never_share_a_file(box):
    """两层各写各的账。⚠ 合成一份的后果是**键会撞**：
    `book|<判据>|<证据键>` 里不含层名，而两层的判据 id 空间是分开注册的。
    """
    from contracts.schema import ARTIFACTS
    from novel import wontfix as W

    assert W.rel_of('stage5') != W.rel_of('stage6')
    assert ARTIFACTS[W.rel_of('stage5')] is ARTIFACTS[W.rel_of('stage6')], \
        '两份账共用一个模型——同一件事的第 N 个落点必然分家'

    _seed_semantic(box)
    _key, spec = _spec_of(_report(box), 'hook_candidate_contradiction', '就这样')
    hook.run_wontfix(_ctx(box, 'stage6.hook'), spec)
    assert (box.dir / 'stage6' / 'wontfix.json').exists()
    assert not (box.dir / 'stage5' / 'wontfix.json').exists()
