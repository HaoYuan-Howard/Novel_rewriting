#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待人工六类聚合生产者（`novel/views/inbox.py`）——BE-04 第一条的落地判据。

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

pytestmark = pytest.mark.skipif(
    not _books(), reason='盘上没有 stage6 语料——这几条**没在查**，不是「查过没事」')


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


def test_human_only_collects_hits_whose_remediation_is_human(box):
    """`remediation=human` 的命中逐条入列；`unver` 与豁免不占清单。"""
    view = inbox_view(str(box.root), box.sid)
    human = [it for it in view.items if it.kind == 'human_only']
    if box.book == BOOK_WITH_HITS:
        assert [it.rule for it in human] == ['hook_ending_shape', 'hook_ending_shape']
        assert all(it.stage == 'stage6' for it in human)
        assert all('--apply-edit' in it.how or '--pick' in it.how or '跑 `' in it.how
                   for it in human)


def test_unver_rows_never_enter_the_inbox(box):
    """「查不了」是判据的边界不是待办——堆进清单就永远清不空。"""
    from contracts.rules import REGISTRY
    view = inbox_view(str(box.root), box.sid)
    unver_rules = {r.rule for r in REGISTRY.all() if r.sev == 'unver'}
    assert not [it for it in view.items if it.rule in unver_rules]


def test_counts_total_and_kind_partition_are_mutually_consistent(box):
    """六键全出（0 也出）、counts == items 分组 == total——载体后置条件与实产共同钉住。"""
    view = inbox_view(str(box.root), box.sid)
    assert set(view.counts) == {'invalid', 'rejected', 'stalled',
                                'human_only', 'upstream', 'approval'}
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
    monkeypatch.setattr(review, 'load_wontfix', lambda _s: {'3|invented_number|e': object()})

    items, wontfix = I._stage5_stalled_and_wontfix(object())
    assert wontfix == 1
    assert [(it.rule, it.chapter) for it in items] == [('band_drift', 3), ('book_length', None)]
    assert all(it.kind == 'stalled' and 'wontfix' in it.how for it in items)
