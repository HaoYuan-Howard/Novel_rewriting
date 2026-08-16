#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待人工八类聚合生产者（`novel/views/inbox.py`）——BE-04 第一条的落地判据。

两条裁决在这里各有一条锚：
  · **UG-11**（计划轻重告警没有处置回环）→ 处置=归人判断：那两条 INFO 的
    remediation 恒为 `human`，而任何 `human` 命中都会被生产者收进清单；
  · **UG-12**（定向重生成被驳回后没有恢复路径）→ `rejected` 类入列，
    `how` 里带 `_rounds/` 归档指针与 `--revalidate` 出路。
"""
from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from novel.views.inbox import inbox_view
from tests import corpus, sandbox

STAGE6_INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
                 'stage3/style_grounding.json', 'stage4/writer_context.json',
                 'stage6/hook.json')

#: 盘上有 `hook_ending_shape` 命中（×2）的那本——对账表 `test_stage6.EXPECTED_HITS` 同源。
BOOK_WITH_HITS = '我把夫君捧成头牌后，全家悔疯了_7615069877019300633'


def _books() -> set[str]:
    return {a.book_dir for a in corpus.iter_artifacts() if a.rel == 'stage6/hook.json'}

pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(
        not _books(), reason='盘上没有 stage6 语料——这几条**没在查**，不是「查过没事」'),
]


def _drafts_of(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))


@pytest.fixture
def box():
    book = BOOK_WITH_HITS if BOOK_WITH_HITS in _books() else sorted(_books())[0]
    s = sandbox.Sandbox(book, STAGE6_INPUTS + _drafts_of(book))
    yield s
    s.close()


def test_human_only_collects_hits_whose_remediation_is_human():
    """用受控报告钉语义；不能把某本现役语料碰巧有两条命中当成契约。"""
    from novel.views import inbox as I

    finding = SimpleNamespace(
        wontfix=None, unresolved_rounds=0, owner='stage6', detail='结尾形态需人工裁决')
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='hook_ending_shape', findings=[finding])
    human = I._report_items(SimpleNamespace(rules=[line]), 'stage6')
    assert len(human) == 1
    assert human[0].kind == 'human_only'
    assert human[0].stage == 'stage6' and human[0].rule == 'hook_ending_shape'
    assert '--apply-edit' in human[0].how or '--pick' in human[0].how


def test_unver_rows_never_enter_the_inbox(box):
    """「查不了」是判据的边界不是待办——堆进清单就永远清不空。"""
    from contracts.rules import REGISTRY
    view = inbox_view(str(box.root), box.sid)
    unver_rules = {r.rule for r in REGISTRY.all() if r.sev == 'unver'}
    assert not [it for it in view.items if it.rule in unver_rules]


def test_counts_total_and_kind_partition_are_mutually_consistent(box):
    """八键全出（0 也出）、counts == items 分组 == total——载体后置条件与实产共同钉住。"""
    view = inbox_view(str(box.root), box.sid)
    assert set(view.counts) == {'invalid', 'rejected', 'stalled',
                                'repairable', 'human_only', 'verification',
                                'upstream', 'approval'}
    assert view.total == len(view.items) == sum(view.counts.values())


def test_an_invalid_without_formal_is_invalid_and_with_formal_is_rejected(box):
    """同一个 `.INVALID`，正式稿在不在决定它是「在等人改」还是「钱花了稿丢了」。"""
    formal = box.dir / 'stage6' / 'hook.json'
    shutil.copyfile(formal, box.dir / 'stage6' / 'hook.INVALID.json')

    view = inbox_view(str(box.root), box.sid)
    rejected = [it for it in view.items if it.kind == 'rejected' and it.stage == 'stage6']
    assert len(rejected) == 1
    assert rejected[0].rule == 'stage6.hook'
    # UG-12 的锚：被驳回之后的下一条恢复路径写在 how 里——归档指针 + 0 成本出路。
    assert '_rounds' in rejected[0].how and '--revalidate' in rejected[0].how

    formal.unlink()
    view2 = inbox_view(str(box.root), box.sid)
    invalid = [it for it in view2.items if it.kind == 'invalid' and it.stage == 'stage6']
    assert len(invalid) == 1
    assert '--revalidate' in invalid[0].how
    assert not [it for it in view2.items if it.kind == 'rejected' and it.stage == 'stage6']


def test_approval_lists_the_unapproved_hook_gate(box):
    """定稿闸门没盖章 ⇒ `approval` 入列；它不是故障，但确实该在清单上。"""
    rel = box.dir / 'stage6' / 'hook.json'
    data = json.loads(rel.read_text(encoding='utf-8'))
    baseline = inbox_view(str(box.root), box.sid)
    if data.get('approved') is None:
        assert [it for it in baseline.items if it.kind == 'approval']
        return
    data['approved'] = None
    rel.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    view = inbox_view(str(box.root), box.sid)
    gates = [it for it in view.items if it.kind == 'approval']
    assert [it.rule for it in gates] == ['stage6.approve']
    assert '--pick' in gates[0].how


def test_plan_weight_warnings_route_to_human_and_thus_into_the_inbox():
    """UG-11 的锚：轻重告警的处置是「归人判断」——remediation 恒 `human`，
    而 `_report_items` 把任何 `human` 命中收进清单（上一条已验）。两半合起来即回环。"""
    from contracts.rules import REGISTRY
    from novel.report.builder import remediation_of
    for rule_id in ('flat_chapter', 'beat_size'):
        rule = REGISTRY.get(rule_id)          # 改名会在这里当场说话
        assert rule.sev == 'info'
        assert remediation_of(rule) == 'human'


def test_stalled_items_come_from_unresolved_rounds_at_the_stop_threshold(monkeypatch):
    """`unresolved_rounds >= STOP_REWRITE_AFTER` 且未豁免 ⇒ stalled；豁免只计数不入列。"""
    from novel.stages.stage5 import review
    from novel.views import inbox as I

    def finding(rule, rounds, wontfix=None):
        return SimpleNamespace(rule=rule, detail=f'{rule} 连续未消', evidence_key='e',
                               unresolved_rounds=rounds, wontfix=wontfix)

    rev = SimpleNamespace(
        chapters=[SimpleNamespace(output_ordinal=3, findings=[
            finding('band_drift', 2),
            finding('dialogue_ratio_drift', 1),
            finding('invented_number', 2, wontfix=object()),
        ])],
        book=[finding('book_length', 2)])
    monkeypatch.setattr(review, 'latest_review', lambda _s: (4, rev))

    # ⚠ 豁免计数**不再由这个函数交**：那张账逐层各一份（`<stage>/wontfix.json`），
    #   由 `inbox_view` 跨层求和。顺手在这里数会把 stage5 那一份数两次。
    items = I._stage5_stalled(object())
    assert [(it.rule, it.chapter) for it in items] == [('band_drift', 3), ('book_length', None)]
    assert all(it.kind == 'stalled' and 'wontfix' in it.how for it in items)
    assert all('stage5.review --wontfix' in it.how for it in items),         'stalled 那条路要指名节点——`--wontfix` 现在两层都有'


def test_the_fallback_how_never_names_a_flag_the_stage_does_not_have():
    """⭐ **兜底文案不许承诺一条这一层敲不出来的命令。**

    ⚠⚠ 这里原来是一个常量，逐字写着「`--apply-edit` 改内容 / `--wontfix` 记「不修」的裁决」，
    而 `--wontfix` **全仓只有 `stage5.review` 有**：stage6 的
    `hook_candidate_contradiction`（`sev=info` · `fix=None` ⇒ `remediation='human'`）
    一命中，清单就建议人去跑一个这一层根本没有的 flag ⇒ `build_argv` 当场拒收。
    与 `contracts/rules.py::_validate` ⑧ 拦的是同一种病，区别只在于
    **`Rule.fix` 那一侧有三道校验，兜底文案这一侧一道都没有**——本条就是那道。

    ⚠ 它逐层比对，不写死名单：新入口一上线这句话自动跟上，
    退役一个入口也会当场说话。
    """
    import re as _re

    from contracts.invariants import NODES, STAGES
    from novel.views.inbox import _human_how

    for stage in STAGES:
        have = {flag for spec in NODES.values() if spec.stage == stage for flag in spec.flags}
        text = _human_how(stage)
        named = set(_re.findall(r'`(--[a-z-]+)`', text))
        assert named <= have, (
            f'{stage} 的兜底文案点名了它没有的 flag：{sorted(named - have)}；'
            f'这一层实有 {sorted(have)}')
        if not ({'--apply-edit', '--wontfix'} & have):
            assert '--' not in text, f'{stage} 一条 0 成本人工路都没有，却给出了一条命令：{text}'


def test_every_human_only_row_offers_something_the_operator_can_actually_run():
    """⭐ 端到端：清单上每一条 `human_only` 的 `how` 都必须是**跑得动的**。

    `Rule.fix` 那条路由由注册期三道校验守着（节点存在 · flag 在该节点的 `flags` 里 ·
    不指向别的层），**兜底那条此前一道都没有**——而 stage6 的语义提示恰好走的是兜底。
    """
    import re as _re

    from contracts.invariants import NODES
    from novel.views import inbox as I

    # 语料此刻没有 human_only 是合法盘面，不应把偶然有行当作契约。
    # 用可控报告直接验证生产路径。
    finding = SimpleNamespace(
        wontfix=None, unresolved_rounds=0, owner='stage6', detail='候选需人工判断')
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='hook_candidate_contradiction', findings=[finding])
    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage6')
    assert len(rows) == 1 and rows[0].kind == 'human_only'
    for item in rows:
        for node, flag in _re.findall(r'`([a-z0-9]+\.[a-z-]+) (--[a-z-]+)`', item.how):
            spec = NODES.get(node)
            assert spec is not None, f'{item.rule} 指向不存在的节点 {node}'
            assert flag in spec.flags, f'{item.rule} 指的 {node} {flag} 不在该节点的 flags 里'
        bare = set(_re.findall(r'`(--[a-z-]+)`', item.how))
        have = {f for spec in NODES.values() if spec.stage == item.stage for f in spec.flags}
        assert bare <= have, (
            f'{item.stage}/{item.rule} 的 how 点名了这一层没有的 flag：{sorted(bare - have)}')


def test_a_hard_local_hit_with_a_fix_route_is_repairable_not_human_only():
    from novel.views import inbox as I

    fix = SimpleNamespace(
        node='stage2.global', flags=['--apply-edit', 'stage2/global_edit.json'],
        paid=False, available=True, unavailable_reason=None)
    finding = SimpleNamespace(
        wontfix=None, unresolved_rounds=0, owner='stage2', detail='设定字段可确定性修复')
    line = SimpleNamespace(
        status='hit', sev='hard', remediation='deterministic', fix=fix,
        rule='global_field_repair', findings=[finding])
    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage2')
    assert len(rows) == 1 and rows[0].kind == 'repairable'
    assert '`stage2.global --apply-edit stage2/global_edit.json`' in rows[0].how


def test_global_review_pending_fail_and_unver_are_verification_rows(box):
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW
    from contracts.schema.evaluation import GlobalReview, GlobalReviewItem
    from novel import prov
    from novel.orchestrator.state import readonly_ctx
    from novel.store import ArtifactStore

    digest = 'a' * 64
    review = GlobalReview(source_sha256='b' * 64, items=[
        GlobalReviewItem(
            item_id='secret:one', kind='secret', subject_path='canon.secrets.s1.fact',
            subject_sha256=digest, state='pending', evidence=[]),
        GlobalReviewItem(
            item_id='reveal:two', kind='reveal',
            subject_path='canon.secrets.s1.reader_reveal_events.0',
            subject_sha256='c' * 64, state='UNVER', evidence=[], actor='reviewer',
            decided_at='2026-08-16T00:00:00+00:00', reason='证据不足'),
        GlobalReviewItem(
            item_id='known:three', kind='known_by',
            subject_path='canon.secrets.s1.character_known_by',
            subject_sha256='d' * 64, state='FAIL', evidence=[], actor='reviewer',
            decided_at='2026-08-16T00:00:00+00:00', reason='与原文冲突'),
        GlobalReviewItem(
            item_id='known:four', kind='known_by',
            subject_path='canon.secrets.s2.character_known_by',
            subject_sha256='e' * 64, state='PASS', evidence=[], actor='reviewer',
            decided_at='2026-08-16T00:00:00+00:00', reason='已核实'),
    ])
    store = ArtifactStore(readonly_ctx(
        box.dir, 'stage2.global', prov.code_version()))
    store.write(STAGE2_GLOBAL_REVIEW, review)

    rows = [row for row in inbox_view(str(box.root), box.sid).items
            if row.kind == 'verification' and row.stage == 'stage2']
    assert {row.rule for row in rows} == {'secret:one', 'reveal:two', 'known:three'}
    assert all('--review-decisions' in row.how and 'subject hash' in row.how for row in rows)


def test_unadopted_trim_without_semantic_evidence_is_a_verification_row(box):
    from contracts.artifacts import artifact_rel
    from contracts.schema.evaluation import TrimAudit
    from novel import prov
    from novel.orchestrator.state import readonly_ctx
    from novel.store import ArtifactStore

    audit = TrimAudit(
        output_ordinal=3, mode='deep', before_sha256='a' * 64, after_sha256='b' * 64,
        before_claims=[], after_claims=[], deterministic_passed=True,
        semantic=None, adopted=False,
        reason='未激活经 ground-truth 校准的 evaluator')
    store = ArtifactStore(readonly_ctx(box.dir, 'stage5.write', prov.code_version()))
    store.write(artifact_rel('stage5.trim_audit', nn=3), audit)

    rows = [row for row in inbox_view(str(box.root), box.sid).items
            if row.kind == 'verification' and row.stage == 'stage5']
    assert [(row.rule, row.chapter) for row in rows] == [('trim_audit_ch03', 3)]
    assert 'pretrim' in rows[0].how and '未采用收缩候选' in rows[0].how
