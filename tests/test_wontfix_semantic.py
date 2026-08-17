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
    **不参与匹配**——`stage6.semantic` / `stage5.semantic` 都是付费节点、重跑是常态，
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
from contracts.schema.evaluation import (
    EvaluatorFingerprint, EvidenceRef, JudgeDecision, SemanticDecision,
)
from contracts.schema.stage6 import Hook, HookSemanticReview
from novel import prov
from novel.errors import TerminalState
from novel.run import RunContext
from novel.stages.stage6 import hook, semantic
from novel.store import ArtifactStore
from novel.views.inbox import inbox_view
from tests import corpus, sandbox
from tests.markers import corpus_params

STAGE6_INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
                 'stage3/style_grounding.json', 'stage4/writer_context.json',
                 'stage6/hook.json')


def _books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts() if a.rel == 'stage6/hook.json'})


#: 六维评分：本文件不测排序，给一组合法常数就够（总分由代码算，模型不自报）。
_QUALITY = {'read_on_desire': 7.0, 'body_voice_match': 7.0,
            'concrete_scene_and_specificity': 7.0, 'curiosity_and_payoff_promise': 7.0,
            'hook_craft': 7.0, 'synopsis_penalty': 2.0}


@pytest.fixture(params=corpus_params(
    _books()[:1], reason='盘上没有 stage6 语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
def box(request):
    book = request.param
    drafts = tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))
    s = sandbox.Sandbox(book, STAGE6_INPUTS + drafts)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _current_fixture_calibration(monkeypatch):
    """本文件测 wontfix；profile 漂移的正反例在 test_stage6_semantic。"""
    monkeypatch.setattr(semantic, '_calibration_status', lambda _artifact: (True, ''))


def _ctx(box, node: str, *, paid: bool = False) -> RunContext:
    return RunContext.open(str(box.root), box.sid, node, paid=paid, write_ledger=False)


def _seed_semantic(box, *, dirty: int = 0, uncertain: tuple[int, ...] = ()) -> str:
    """手写一份**当前形态**的质量审判，让 `dirty` 那条候选带一处实质矛盾。

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
    """
    ctx = _ctx(box, 'stage6.semantic', paid=True)
    store = ArtifactStore(ctx)
    art, _raw = store.read_own(Hook, hook.REL)
    hook._Upstreams(ctx, store)                      # 读齐上游 ⇒ 来源账与真跑同源

    texts = semantic._unique_texts(art)
    reviews = []
    decisions = []
    for index, (label, text) in enumerate(texts):
        bad = index == dirty
        unsure = index in uncertain
        reviews.append({
            'label': label,
            'coverage': 'uncertain' if unsure else 'complete',
            'uncertainty_reason': '开篇几章还没写全，核对不完整' if unsure else None,
            'contradictions': ([{
                'assertion': '这条候选把人物关系写反了',
                'evidence': text.strip()[:12],
                'reason': '与全书事实里的关系相反',
            }] if bad else []),
            'quality': dict(_QUALITY),
            'concise_reason': '这一条的判定说明',
            'text_sha1': semantic.text_sha1(text),
        })
        verdict = 'UNVER' if unsure else 'FAIL' if bad else 'PASS'
        ref = EvidenceRef(
            artifact=f'{hook.REL}#candidate/{label}',
            subject_sha256=semantic.text_sha256(text), start=0, end=len(text))
        judges = [JudgeDecision(
            judge=name, verdict=verdict, evidence=[ref], reason='这一条的判定说明')
            for name in ('A', 'B')]
        decisions.append(SemanticDecision(
            ability='reference_boundary', subject_key=semantic.text_sha256(text),
            verdict=verdict, automated=verdict != 'UNVER', evidence=[ref],
            reason='这一条的合并判定说明', profile_id='fixture-profile',
            judges=judges))
    fingerprint = EvaluatorFingerprint(
        dataset_sha256='1' * 64, model='test-model', prompt_sha256='2' * 64,
        schema_sha256='3' * 64, decoder_sha256='4' * 64)
    artifact = HookSemanticReview(
        judged_texts={label: semantic.text_sha1(text) for label, text in texts},
        hook_file_sha1=prov.fingerprint_bytes(store.path(hook.REL).read_bytes()),
        reviews=reviews,
        ranking=[label for label, _t in texts],
        judge_rankings={
            'A': [label for label, _t in texts],
            'B': [label for label, _t in texts],
        },
        decisions=decisions,
        evaluator_fingerprint=fingerprint,
        _provenance=ctx.llm_provenance(
            prompts={
                'HOOK_SEMANTIC_SYS_A': semantic.SEMANTIC_SYS + semantic.JUDGE_ROLE_SUFFIX['A'],
                'HOOK_SEMANTIC_SYS_B': semantic.SEMANTIC_SYS + semantic.JUDGE_ROLE_SUFFIX['B'],
            }, model='test-model'),
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


def test_a_rule_outside_this_stage_is_refused(box):
    """判据必须是**这一层**的。stage5 的判据写进 stage6 的账里，
    那条豁免永远套不上任何东西，而报告上看不出来。"""
    _seed_semantic(box)
    with pytest.raises(TerminalState, match='不是 stage6 的判据'):
        hook.run_wontfix(_ctx(box, 'stage6.hook'),
                         'book|semantic_secret_leak|ch1:xx=层写错了')


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

def test_the_exemption_never_opens_the_approval_gate(box):
    """⭐ **豁免不开闸门。** `hook_semantic_contradiction` 被豁免之后，
    批准路照旧拒绝那条有矛盾的候选——「有人看过并决定不修」不等于
    「这条不再是问题」。放松这一条，一条实质矛盾就会被一句理由送进成书。
    """
    from contracts.schema.stage6 import Hook as _Hook

    _seed_semantic(box, dirty=0)
    store = ArtifactStore(_ctx(box, 'stage6.hook'))
    art, _raw = store.read_own(_Hook, hook.REL)
    dirty_text = art.candidates[0].text

    report = _report(box)
    line = _line(report, 'hook_semantic_contradiction')
    if line.status == 'hit':
        _key, spec = _spec_of(report, 'hook_semantic_contradiction', '我认了')
        hook.run_wontfix(_ctx(box, 'stage6.hook'), spec)

    with pytest.raises(TerminalState, match='不能定稿'):
        semantic.require_selectable(store, art, dirty_text, '候选 1')


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


# ─────────────────────────────────────────────────────────────────────────────
# 四、stage5 那一半：`semantic_secret_leak` 从「结构上够不着」变成可裁
# ─────────────────────────────────────────────────────────────────────────────

def _stage5_books() -> list[str]:
    """有完整 stage5 上游与正式成稿的书。"""
    out = []
    for book in sorted({a.book_dir for a in corpus.iter_artifacts()}):
        rels = {a.rel for a in corpus.iter_artifacts() if a.book_dir == book}
        if ({'stage1/main_text.json', 'stage2/global_grounding.json',
             'stage3/style_grounding.json', 'stage4/writer_context.json'} <= rels
                and any(r.startswith('stage5/') and '/ch' in r for r in rels)):
            out.append(book)
    return out


@pytest.fixture
def box5(tmp_path):
    """一份能跑 `stage5.review` 的沙箱（不含既有 review / semantic）。"""
    books = _stage5_books()
    if not books:
        pytest.skip('当前语料没有完整 stage5 上游与正式成稿')
    book = books[0]
    root = tmp_path / 'out'
    target = root / book
    for a in corpus.iter_artifacts():
        if a.book_dir != book or '_rounds' in a.rel:
            continue
        if a.rel.startswith(('stage0/', 'stage6/', 'stage7/')) or 'review' in a.rel \
                or 'semantic' in a.rel or 'wontfix' in a.rel:
            continue
        p = target / a.rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(encode_artifact(a.rel, corpus.load_current(a)), ensure_ascii=False),
            encoding='utf-8',
        )
    # ⚠ 上面刻意跳过整个 `stage0/`（本条不测剔除），但**全链根那一份字节必须在**：
    #   `stage5.review` 铸造来源账要拿它算 `root_clean_sha1`，缺了它当场
    #   `ProvenanceRefused`，而报错长得像本条自己坏了。
    root_txt = corpus.corpus_root() / book / 'stage0/clean_text.txt'
    (target / 'stage0').mkdir(parents=True, exist_ok=True)
    (target / 'stage0/clean_text.txt').write_bytes(root_txt.read_bytes())
    yield root, book, book.rsplit('_', 1)[-1]


def _seed_stage5_semantic(root, sid):
    """手写一份**当前形态**的书级语义审判，让第一条 secret 判成 `leak`。

    ⚠ 与 stage6 那半同一条纪律：**来源账由 `ctx.llm_provenance()` 从真读到的上游边铸出来**，
    `_current_artifact` 那三道闸（对得上机械验收序号 · 判的是同一批成稿 · 新鲜）
    走的都是真代码。伪造的只有模型那一次答了什么。
    """
    from contracts.schema.stage5 import SemanticReview
    from novel.stages.stage5 import measure as M
    from novel.stages.stage5 import review as R
    from novel.stages.stage5 import semantic as S

    ctx = RunContext.open(str(root), sid, 'stage5.semantic', paid=True, write_ledger=False)
    store = ArtifactStore(ctx)
    book = R.load_book(ctx, store)
    if not book.secrets:
        pytest.skip('这本书没有 secret——`semantic_secret_leak` 无从命中')
    seq, _rev = R.latest_review(store)
    _by_ord, _invalids, judged = M.load_drafts(store)

    ordinal = min(book.chapters)
    drafts, _judged2 = S._current_drafts(store, book)
    excerpt = (drafts[ordinal].get('text') or '').strip()[:14]
    secret = book.secrets[0]
    reveal = S._reveal_chapter(book, secret)
    if reveal <= ordinal:
        pytest.skip('第一条 secret 的揭示章不在第 1 章之后，构造不出「提前泄露」')

    evidence = [{
        'artifact': f'stage5/ch{ordinal:02d}',
        'subject_sha256': hashlib.sha256(excerpt.encode()).hexdigest(),
        'start': 0, 'end': len(excerpt),
    }]
    decisions = []
    for index, item in enumerate(book.secrets):
        verdict = 'FAIL' if index == 0 else 'PASS'
        sid_key = item.get('secret_id')
        decisions.append({
            'ability': 'secret_timeline', 'subject_key': sid_key,
            'verdict': verdict, 'automated': True, 'evidence': evidence,
            'reason': '测试双盲一致', 'profile_id': 'test-profile',
            'judges': [
                {'judge': role, 'verdict': verdict, 'evidence': evidence,
                 'reason': '测试双盲一致'} for role in ('A', 'B')],
        })

    artifact = SemanticReview(
        mechanical_review_seq=seq,
        judged=judged,
        chapters=[{'output_ordinal': o, 'coverage': 'complete', 'uncertainty_reason': None,
                   'claims': ([{'claim_id': 'c1', 'assertion': '这一章已经点破了那件事',
                                'evidence': excerpt}] if o == ordinal else [])}
                  for o in sorted(book.chapters)],
        secrets=[{'secret_id': secret.get('secret_id'), 'reveal_chapter': reveal,
                  'status': 'leak',
                  'evidence_refs': [{'output_ordinal': ordinal, 'claim_id': 'c1'}],
                  'earliest_chapter': ordinal, 'reason': '第 1 章已经让读者确定了这件事'}]
        + [{'secret_id': s.get('secret_id'), 'reveal_chapter': S._reveal_chapter(book, s),
            'status': 'clear', 'evidence_refs': [], 'reason': '没有提前表达'}
           for s in book.secrets[1:]],
        decisions=decisions,
        _provenance=ctx.llm_provenance(
            prompts={'CLAIMS_SYS': S.CLAIMS_SYSTEM, 'ALIGN_SYS': S.ALIGN_SYSTEM},
            model='test-model'),
    )
    seqs = S.semantic_seqs(store)
    rel = S.SEMANTIC_TPL.format(n=(seqs[-1] + 1) if seqs else 1)
    store.write(rel, artifact)
    return excerpt, ordinal


@pytest.mark.corpus
def test_a_semantic_secret_leak_can_finally_be_marked_and_stops_occupying_the_inbox(box5):
    """⭐⭐ **本轮之前这条路结构上不可能走通。**

    `semantic_secret_leak` 在报告渲染时由 `stage5/semantic.py::outcomes` 现算，
    **一份产物都不进** ⇒ 旧的 `mark_wontfix` 只从 `review_<n>.json` 反查，
    对它永远回一句「不给不存在的结论发豁免」——而那条结论明明就在报告上。
    ⇒ 反查池现在是「验收里的机械结论 ∪ 现算的语义结论」。

    ⚠ 语义结论**一律 book 级寻址**：章信息在证据键里（`ch<n>:<正文原字节>`），
    再按章分一次是同一个信息的第二个落点。
    """
    from novel.stages.stage5 import review as R

    root, book, sid = box5
    ctx = RunContext.open(str(root), sid, 'stage5.review', paid=False, write_ledger=False)
    R.run_review(ctx)
    excerpt, ordinal = _seed_stage5_semantic(root, sid)

    ctx2 = RunContext.open(str(root), sid, 'stage5.review', paid=False, write_ledger=False)
    report = R.run_check_only(ctx2)
    line = next(r for r in report.rules if r.rule == 'semantic_secret_leak')
    assert line.status == 'hit' and line.findings, '没造出一条真语义命中——本条没在查'
    key = line.findings[0].evidence_key
    assert key.startswith(f'ch{ordinal}:'), f'语义证据键的形态变了：{key!r}'

    ctx3 = RunContext.open(str(root), sid, 'stage5.review', paid=False, write_ledger=False)
    R.mark_wontfix(ctx3, f'book|semantic_secret_leak|{key}=这一章本来就该让读者知道')

    rel = 'stage5/wontfix.json'
    raw = json.loads((root / book / rel).read_text(encoding='utf-8'))
    ledger = decode_artifact(rel, raw).payload
    entry = ledger[f'book|semantic_secret_leak|{key}']
    assert entry['why'] == '这一章本来就该让读者知道'
    assert entry['at'].startswith('semantic_') and 'review_' in entry['at'], \
        '语义结论的审计账要同时说出「哪一版审判」与「哪一版机械验收」'

    ctx4 = RunContext.open(str(root), sid, 'stage5.review', paid=False, write_ledger=False)
    after = next(r for r in R.run_check_only(ctx4).rules if r.rule == 'semantic_secret_leak')
    assert after.status == 'hit', '豁免把结论删掉了'
    marked = next(f for f in after.findings if f.evidence_key == key)
    assert marked.wontfix is not None and marked.actionable is False

    view = inbox_view(str(root), sid)
    assert view.wontfix >= 1
    assert not [it for it in view.items
                if it.rule == 'semantic_secret_leak' and key in (it.what or '')], \
        '豁免过的语义结论还占着待人工清单'
