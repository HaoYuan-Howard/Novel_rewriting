#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_a3_conflicts —— R10a a3 判据面的定向红绿例（源证据冲突回派）。

【验证边界】§二.2 三分支的工程入口：Preserve 报告 evidence_conflicts →
按锚点句柄回派 Stage2 语义 owner（拍句柄→逐章 owner chNN／item=chapter:N，
书级句柄→book）；checkpoint 不发布、回答由 ask 路径耐久；同指纹重现走
无进展停（「Stage2 维持原解释后 Preserve 再报同一冲突」不二次回派）。
fake transport 回派测试证明的是**归因、修订、恢复与停止的机械正确性**
（PLAN §五），不证明「Preserve 面对真实错误会主动报告冲突」。
"""
from __future__ import annotations

import pytest

from novel.stages.owner_revision import (
    OwnerRevisionRequired, OwnerRevisionStop, read_owner_issue_file,
)
from novel.stages.chain import _backfill_source_evidence_conflicts

CONFLICTS = [{'anchor': 'b01_01.e1', 'segment': 'e01_01',
              'note': '引文写夫君藏身床底，效应却写离家未归'}]


def _repo_with_observations(tmp_path, name='conflict'):
    """最小盘面：完整观察链（book/ch01/ch02）在盘——回派账的 against 摘要读字节。"""
    from tests.str06_faults import looped_full_chain_wires
    from tests.test_str06_body import _make_session

    wires = looped_full_chain_wires()
    repo, _session, _transport = _make_session(tmp_path, wires, name=name)
    from tests.str06_facilities import make_context
    from novel.llm.ledger import CostLedger
    repo.attach_context(make_context(repo.root, ledger=CostLedger(path=None)))
    from contracts.schema.observation import BookObservation, ChapterObservation
    from tests.str06_facilities import synthetic_ref
    repo.write('stage2/observations/book.json',
               BookObservation.model_validate(wires['book']), owners=('book',),
               refs=(synthetic_ref('book', '已购输入'),))
    repo.write('stage2/observations/ch01.json',
               ChapterObservation.model_validate(wires['ch01']), owners=('ch01',),
               refs=(synthetic_ref('ch01', '已购输入'),))
    repo.write('stage2/observations/ch02.json',
               ChapterObservation.model_validate(wires['ch02']), owners=('ch02',),
               refs=(synthetic_ref('ch02', '已购输入'),))
    return repo, wires


# ───────────────────────── 归因：锚点句柄 → owner/item ─────────────────────────

def test_conflict_routes_to_chapter_owner_and_writes_issue_record(tmp_path):
    """拍句柄 b01_01.e1 → str06.stage2.chapters／owner=ch01／item=chapter:1；
    issue 带 anchor/segment/note 的 typed context；checkpoint 相关产物不动。"""
    repo, _wires = _repo_with_observations(tmp_path)
    with pytest.raises(OwnerRevisionRequired) as excinfo:
        _backfill_source_evidence_conflicts(repo, CONFLICTS)
    assert excinfo.value.node == 'str06.stage2.chapters'
    assert excinfo.value.owner == 'ch01'
    assert excinfo.value.issues[0].item == 'chapter:1'
    assert excinfo.value.issues[0].code == 'source_evidence_conflict'
    record = read_owner_issue_file(repo, 'ch01')
    assert record.status == 'open' and record.raised_by == 'str06.stage4.preserve'
    assert record.issues[0].context['anchor'] == 'b01_01.e1'
    assert record.issues[0].context['segment'] == 'e01_01'
    assert not repo.path('runtime/preserve_checkpoint.json').exists(), \
        '冲突未闭合前 checkpoint 不发布'


def test_book_level_anchor_routes_to_book_owner(tmp_path):
    """书级句柄（s0_*/wc_*）→ str06.stage2.book／owner=book／item=锚点本体。"""
    repo, _wires = _repo_with_observations(tmp_path)
    conflicts = [{'anchor': 'b01_01.e1', 'segment': 'e01_01', 'note': 'x'},
                 {'anchor': 's0_01', 'segment': 'e01_01', 'note': '书级初态冲突'}]
    # 书级锚点路由到 book owner（这里因 s0_01 不在观察闭集而要求先有初态；
    # 归因面只看路由：把书级条目单独喂）
    with pytest.raises(OwnerRevisionRequired) as excinfo:
        _backfill_source_evidence_conflicts(repo, [conflicts[1]])
    assert excinfo.value.node == 'str06.stage2.book'
    assert excinfo.value.owner == 'book'


def test_unresolvable_anchor_stops_without_guessing(tmp_path):
    """锚点不可解析 → 显式 ValueError（机械路由不猜，E-8）。"""
    repo, _wires = _repo_with_observations(tmp_path)
    with pytest.raises(ValueError, match='无法路由'):
        _backfill_source_evidence_conflicts(
            repo, [{'anchor': 'zzz', 'segment': 'e01_01', 'note': 'x'}])


# ───────────────────────── 回跳闭环与无进展停（stage，fake transport） ─────────────────────────

class _ConflictPreserveTransport:
    """preserve 首答带 evidence_conflicts；非粘性时第二轮回干净 wire
    （「Stage2 修订后 Preserve 复验通过」），粘性时每轮都带（无进展停）。"""

    def __init__(self, wires, conflicts, sticky=False):
        from tests.test_str06_body import _RoutingTransport
        self._inner = _RoutingTransport(wires)
        self._conflicts = conflicts
        self._sticky = sticky
        self._served = False
        self.calls: list[str] = []

    def send(self, request):
        import json as _json
        from novel.llm.transport import ChatResponse
        owner = self._inner._owner(request)
        self.calls.append(owner)
        if owner == 'preserve' and (self._sticky or not self._served):
            self._served = True
            wire = dict(self._inner.wires['preserve'])
            wire['evidence_conflicts'] = self._conflicts
            return ChatResponse(
                content=_json.dumps(wire, ensure_ascii=False),
                finish_reason='stop',
                usage={'prompt_tokens': 100, 'completion_tokens': 40},
                model='fake')
        return self._inner.send(request)


def _run_to_preserve(tmp_path, transport_factory, monkeypatch):
    from contracts.schema.hook import HookConstraints
    from novel.stages.chain import run_workflow
    from tests.str06_facilities import json_ask, text_ask, stub_body_runner
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_faults import looped_full_chain_wires

    wires = looped_full_chain_wires()
    repo, session, transport = _make_session(
        tmp_path, wires, name='heal', transport=transport_factory(wires))
    stub_body_runner(monkeypatch)

    def runner():
        return run_workflow(
            repository=repo, chapters=_chapters(), ask=json_ask(session),
            ask_text=text_ask(session),
            ask_body=lambda o, p: (_ for _ in ()).throw(AssertionError('stub')),
            budget=2000, through='stage4',
            node_filter=['str06.stage2.book', 'str06.stage2.chapters',
                         'str06.stage2.names', 'str06.stage2.associate',
                         'str06.stage2.compile', 'str06.stage4.preserve'])
    return runner, repo, transport


def test_conflict_full_chain_heals_via_revision(tmp_path, monkeypatch):
    """闭环：preserve 首答带冲突 → 回派 ch01 → 章观察修订轮（prompt 带修订
    要求）→ 重编译 → preserve 复验干净 → checkpoint 与 brief 发布。"""
    def factory(wires):
        return _ConflictPreserveTransport(wires, CONFLICTS)

    runner, repo, transport = _run_to_preserve(tmp_path, factory, monkeypatch)
    report = runner()
    assert 'obligations' in report, report
    assert repo.path('runtime/preserve_checkpoint.json').is_file()
    assert repo.path('stage4/grounding/reconstruction_brief.json').is_file()
    record = read_owner_issue_file(repo, 'ch01')
    assert record.status == 'resolved' and record.rounds_total == 1, \
        '复验通过按 owner 清账（轮数留账不复位）'
    # ch01 恰两轮（原轮＋修订轮）；preserve 恰两轮（冲突轮＋复验轮）
    assert transport.calls.count('ch01') == 2
    assert transport.calls.count('preserve') == 2


def test_sticky_conflict_stops_after_revision_without_progress(tmp_path,
                                                               monkeypatch):
    """粘性冲突：Stage2 修订后 Preserve 再报**同一**冲突 → 同指纹无进展停
    （OwnerRevisionStop），不二次回派、不重复起轮。"""
    def factory(wires):
        return _ConflictPreserveTransport(wires, CONFLICTS, sticky=True)

    runner, repo, transport = _run_to_preserve(tmp_path, factory, monkeypatch)
    with pytest.raises(OwnerRevisionStop, match='没有进展'):
        runner()
    record = read_owner_issue_file(repo, 'ch01')
    assert record.status == 'open' and record.rounds_total == 1
    assert record.no_progress is True
    # 回派只发生一次：ch01 两轮（原轮＋修订轮），preserve 两轮后停
    assert transport.calls.count('ch01') == 2
    assert transport.calls.count('preserve') == 2
    assert not repo.path('runtime/preserve_checkpoint.json').exists()


def test_revision_prompt_embeds_previous_without_storage_envelope(tmp_path):
    """R10c c1（2026-10-02）：修订 prompt 嵌入的上一版必须剥存储信封——
    `_provenance` 是工程元数据，喂给模型会被原样回声，wire 校验
    extra_forbidden 拒收烧轮（ch05 第 4 轮实证）。"""
    from novel.stages.owner_revision import (
        OwnerIssueV1, discover_owner_issues, revision_prompt_for_owner,
    )
    repo, _wires = _repo_with_observations(tmp_path, name='envelope')
    try:
        discover_owner_issues(repo, raised_by='str06.stage4.compile',
                              owner='ch01', node='str06.stage2.chapters',
                              issues=[OwnerIssueV1(
                                  code='material_name_leak', item='chapter:1',
                                  message='测试归因：请修订 ch01 观察',
                                  context={})])
    except Exception:
        pass  # discover 必然抛回派；open 记录已落账。
    prompt = revision_prompt_for_owner(
        repo, 'ch01', 'BASE', 'stage2/observations/ch01.json')
    assert 'BASE' in prompt
    assert '_provenance' not in prompt, '存储信封不得进修订 prompt'
    assert '_schema_version' not in prompt


def test_cascade_rewind_limit_stops_before_spiraling(tmp_path):
    """R10c 硬化：跨节点回派每根累计上界——多 owner 互相打断对方收敛的
    级联身份 churn 在超界时停给人（不起轮、不付费）；上界内正常回派，
    计数持久在 runtime/cascade_rewinds.json、重启不重置。"""
    from contracts.invariants import CASCADE_REWIND_LIMIT
    from novel.stages.owner_revision import (
        OwnerIssueV1, OwnerRevisionRequired, OwnerRevisionStop,
        discover_owner_issues, _bump_cascade_rewinds,
    )
    repo, _wires = _repo_with_observations(tmp_path, name='rewindcap')
    # 预置到上界前一刻（等价于历史上已发生过 LIMIT-1 次回派）。
    for _ in range(CASCADE_REWIND_LIMIT - 1):
        _bump_cascade_rewinds(repo)
    # 上界内最后一次：正常起轮。
    try:
        discover_owner_issues(
            repo, raised_by='str06.stage4.compile', owner='book',
            node='str06.stage2.book',
            issues=[OwnerIssueV1(code='material_name_leak', item='chapter:1',
                                 message='上界内问题', context={})])
        raise AssertionError('discover 必然抛错')
    except OwnerRevisionRequired as exc:
        assert f'{CASCADE_REWIND_LIMIT}/{CASCADE_REWIND_LIMIT}' in str(exc)
    # 超界：typed 停止（不起轮、不写 open 记录）。
    try:
        discover_owner_issues(
            repo, raised_by='str06.stage4.compile', owner='ch01',
            node='str06.stage2.chapters',
            issues=[OwnerIssueV1(code='material_name_leak', item='chapter:2',
                                 message='超界问题', context={})])
        raise AssertionError('超界后必须停止')
    except OwnerRevisionStop as exc:
        assert '每根上界' in str(exc)
    record = json.loads(repo.path('runtime/issues/ch01.json').read_text(
        encoding='utf-8')) if repo.path('runtime/issues/ch01.json').exists() else None
    assert record is None, '超界停止不得写 open 记录（未起轮）'
    # 计数持久且含上界读数；重放读同一账（幂等对账）。
    counter = repo.read('runtime/cascade_rewinds.json')
    assert counter['count'] == CASCADE_REWIND_LIMIT + 1
    assert counter['limit'] == CASCADE_REWIND_LIMIT
