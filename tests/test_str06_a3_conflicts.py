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


def test_all_conflict_owners_are_registered_before_dispatch_and_replay(tmp_path):
    """同答多章及多个书级冲突一起登记；同 owner 只起一轮，重放不重复计数。"""
    repo, _wires = _repo_with_observations(tmp_path)
    conflicts = [
        {'anchor': 'b02_01', 'segment': 'e02_01', 'note': '第二章漏抽知情'},
        {'anchor': 's0_01', 'segment': 'e01_01', 'note': '初态冲突'},
        {'anchor': 'wc_01', 'segment': 'e01_01', 'note': '世界约束冲突'},
        {'anchor': 'b01_01', 'segment': 'e01_01', 'note': '第一章漏抽知情'},
    ]
    for _ in range(2):
        with pytest.raises(OwnerRevisionRequired) as excinfo:
            _backfill_source_evidence_conflicts(repo, conflicts)
        assert excinfo.value.owner == 'book'
        assert excinfo.value.node == 'str06.stage2.book'
        records = {owner: read_owner_issue_file(repo, owner)
                   for owner in ('book', 'ch01', 'ch02')}
        assert all(record is not None for record in records.values()), \
            '不能在第一个 owner 抛错后漏掉同一已购回答中的其他问题'
        assert all(record.rounds_total == 1 and record.dispatch_pending
                   for record in records.values())
        assert [issue.item for issue in records['book'].issues] == ['s0_01', 'wc_01']
        assert records['ch01'].issues[0].context['anchor'] == 'b01_01'
        assert records['ch02'].issues[0].context['anchor'] == 'b02_01'
        assert repo.read('runtime/cascade_rewinds.json')['count'] == 3
    assert not repo.path('runtime/preserve_checkpoint.json').exists()


def test_unresolvable_anchor_stops_without_guessing(tmp_path):
    """锚点不可解析 → 显式 ValueError（机械路由不猜，E-8）。"""
    repo, _wires = _repo_with_observations(tmp_path)
    with pytest.raises(ValueError, match='无法路由'):
        _backfill_source_evidence_conflicts(
            repo, [{'anchor': 'zzz', 'segment': 'e01_01', 'note': 'x'}])


def test_chapter_owner_patch_does_not_republish_other_chapters(tmp_path):
    """局部 owner patch 只执行目标章；其他章连同来源信封逐字节保留。"""
    from copy import deepcopy
    from novel.stages.chain import ChainContext, run_chapters, run_node
    from tests.str06_facilities import synthetic_ref
    from tests.test_str06_body import _chapters

    repo, wires = _repo_with_observations(tmp_path)
    with pytest.raises(OwnerRevisionRequired):
        _backfill_source_evidence_conflicts(repo, [{
            'anchor': 'b02_01', 'segment': 'e02_01', 'note': '另一个尚未处理的问题'}])
    pending_before = repo.path('runtime/issues/ch02.json').read_bytes()
    before = repo.path('stage2/observations/ch02.json').read_bytes()
    calls = []

    def ask(owner, prompt, validate, **kwargs):
        calls.append(owner)
        assert owner == 'ch01'
        wire = deepcopy(wires[owner])
        wire['beats'][0]['event'] += '（局部修订）'
        assert not validate(wire)
        return wire, synthetic_ref(owner, prompt)

    ctx = ChainContext(repository=repo, chapters=_chapters(), ask=ask,
                       ask_text=lambda *_: None, ask_body=lambda *_: None,
                       chapter_patch_owners=('ch01',))
    assert run_node('str06.stage2.chapters', ctx) == {'chapters': 1}
    assert calls == ['ch01']
    assert repo.path('runtime/issues/ch02.json').read_bytes() == pending_before
    assert repo.path('stage2/observations/ch02.json').read_bytes() == before
    assert repo.read('runtime/chapter_observation_failures.json')['attempted'] == [1]
    repo.path('stage2/observations/ch02.json').unlink()
    with pytest.raises(FileNotFoundError):
        run_chapters(ctx)
    assert calls == ['ch01']


def test_retained_chapter_check_verifies_proof_without_recording_input(tmp_path):
    """保留章不是修订内容依赖，但仍必须通过 seal 和 freshness。"""
    from novel.errors import UnsealedArtifact, UpstreamStale
    from tests.str06_facilities import synthetic_ref
    repo, _ = _repo_with_observations(tmp_path)
    rel = 'stage2/observations/ch02.json'
    model = repo.read_model(rel)
    repo.context.begin_run()
    assert repo.check_model(rel) == model
    assert repo.context.edges == {}
    path = repo.path(rel)
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(UnsealedArtifact):
        repo.check_model(rel)
    repo.context.record_edge('stage2/observations/ch01.json', '0' * 10)
    repo.write(rel, model, owners=('ch02',), refs=(synthetic_ref('ch02', '过期输入'),))
    repo.context.begin_run()
    with pytest.raises(UpstreamStale):
        repo.check_model(rel)
    assert repo.context.edges == {}


def test_chapter_patch_activation_does_not_touch_unselected_revision(tmp_path):
    """单章修订不能激活另一章尚未提交的 staged 记录。"""
    from contracts.schema.workflow import OwnerRevisionRecordV2
    from novel.stages.revisions import activate_node_revisions
    repo, _ = _repo_with_observations(tmp_path)
    record = OwnerRevisionRecordV2(kind='owner_patch', owner='ch02', node='str06.stage2.chapters',
        baseline_request_sha256='a' * 64, baseline_response_sha256='b' * 64,
        baseline_source_root=str(repo.root), candidate_sha256='c' * 64,
        evidence_rel=f'runtime/revisions/evidence/{"c" * 64}.json', diff_sha256='d' * 64,
        reason='尚未提交的另一章补丁', sequence=0, product_rel='stage2/observations/ch02.json',
        before_product_sha256=None, applies_to_request_sha256='a' * 64,
        status='staged', created_at=1759000000)
    rel = 'runtime/revisions/active/ch02.json'
    repo.write(rel, record)
    before = repo.path(rel).read_bytes()
    activate_node_revisions(repo, 'str06.stage2.chapters', owners=('ch01',))
    assert repo.path(rel).read_bytes() == before
    with pytest.raises(FileNotFoundError):
        activate_node_revisions(repo, 'str06.stage2.chapters', owners=('ch02',))


@pytest.mark.parametrize('out_of_scope', [False, True])
def test_empty_beat_conflict_revises_only_reported_beat(tmp_path, out_of_scope):
    """空拍报告回到真实 chapters runner：知情由 owner 补，越界整答拒发。"""
    from copy import deepcopy
    from contracts.schema.observation import ChapterObservation
    from novel.stages.chain import ChainContext, run_chapters
    from novel.stages.owner_revision import MechanicalDefectStop
    from novel.stages.stage2.slim import slim_chapter_wire
    from tests.str06_facilities import synthetic_ref
    from tests.test_str06_body import _chapters

    repo, wires = _repo_with_observations(tmp_path)
    chapters = deepcopy(_chapters())
    chapters[0]['text'] += '\n我看见夫君从床底爬出来。\n我坐回椅子上。'
    initial = deepcopy(wires['ch01'])
    for event, ref in [('我看见夫君现身', 'e01_02'), ('我坐回椅子上', 'e01_03')]:
        beat = deepcopy(initial['beats'][0])
        beat.update(event=event, source_refs=[ref], scene_boundary='continue',
                    effects=[], cognition=[], loops=[])
        initial['beats'].append(beat)
    repo.write('stage2/observations/ch01.json', ChapterObservation.model_validate(initial),
               owners=('ch01',), refs=(synthetic_ref('ch01', '遗漏基线'),))
    initial = repo.read_model('stage2/observations/ch01.json').model_dump(mode='json')
    before = {n: repo.path(f'stage2/observations/ch{n:02d}.json').read_bytes() for n in (1, 2)}
    with pytest.raises(OwnerRevisionRequired) as exc:
        _backfill_source_evidence_conflicts(repo, [{
            'anchor': 'b01_02', 'segment': 'e01_02', 'note': '缺少芸娘目睹现身的知情'}])
    assert exc.value.owner == 'ch01'
    assert not repo.path('runtime/preserve_checkpoint.json').exists()
    calls = []

    def ask(owner, prompt, validate, **kwargs):
        calls.append(owner)
        assert owner == 'ch01'
        assert '本轮源证据窄修只允许修改以下拍：b01_02' in prompt
        wire = slim_chapter_wire(initial)
        wire['beats'][1]['know'] = [{'op': 'set', 'polarity': 'positive', 'support': 'confirmed', 'reader_visible': False, 'who': 'yun_niang', 'how': 'learns',
            'what': '夫君从床底现身', 'evidence_refs': ['e01_02']}]
        if out_of_scope:
            wire['beats'][2]['event'] = '我离开了房间'
            assert any('窄修越界' in str(e) for e in validate(wire))
        else:
            assert validate(wire) == []
        return wire, synthetic_ref(owner, prompt)

    ctx = ChainContext(repository=repo, chapters=chapters, ask=ask,
                       ask_text=lambda *_: None, ask_body=lambda *_: None)
    if out_of_scope:
        with pytest.raises(MechanicalDefectStop, match='窄修越界'):
            run_chapters(ctx)
        assert repo.path('stage2/observations/ch01.json').read_bytes() == before[1]
    else:
        assert run_chapters(ctx) == {'chapters': 1}
        after = slim_chapter_wire(repo.read_model('stage2/observations/ch01.json').model_dump(mode='json'))
        for i in (0, 2):
            assert after['beats'][i] == slim_chapter_wire(initial)['beats'][i]
        assert after['beats'][1]['know'][0]['what'] == '夫君从床底现身'
    assert calls == ['ch01']
    assert repo.path('stage2/observations/ch02.json').read_bytes() == before[2]


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


def _run_to_preserve(tmp_path, transport_factory, monkeypatch, prepare=None):
    from novel.stages.chain import run_workflow
    from tests.str06_facilities import json_ask, text_ask, stub_body_runner
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_faults import looped_full_chain_wires

    wires = looped_full_chain_wires()
    chapters = _chapters()
    if prepare is not None:
        prepare(wires, chapters)
    repo, session, transport = _make_session(
        tmp_path, wires, name='heal', transport=transport_factory(wires))
    stub_body_runner(monkeypatch)

    def runner():
        return run_workflow(
            repository=repo, chapters=chapters, ask=json_ask(session),
            ask_text=text_ask(session),
            ask_body=lambda o, p: (_ for _ in ()).throw(AssertionError('stub')),
            budget=2000, through='stage4',
            node_filter=['str06.stage2.book', 'str06.stage2.chapters',
                         'str06.stage2.names', 'str06.stage2.associate',
                         'str06.stage2.compile', 'str06.stage4.naming',
                         'str06.stage4.preserve'])
    return runner, repo, transport


def test_empty_beat_missing_conflict_full_chain_heals(tmp_path, monkeypatch):
    """Preserve 空拍地址经真实验收、回派、章修订、重编译到复验发布。"""
    from copy import deepcopy
    from novel.llm.transport import ChatResponse
    import json

    def prepare(wires, chapters):
        chapters[0]['text'] += '\n我看见夫君从床底爬出来。'
        beat = deepcopy(wires['ch01']['beats'][0])
        beat.update(event='我看见夫君现身', source_refs=['e01_02'],
                    scene_boundary='continue', effects=[], cognition=[], loops=[])
        wires['ch01']['beats'].append(beat)

    class Transport(_ConflictPreserveTransport):
        def send(self, request):
            owner = self._inner._owner(request)
            if owner == 'ch01' and self._served:
                self.calls.append(owner)
                wire = deepcopy(self._inner.wires[owner])
                wire['beats'][1]['cognition'] = [{'op': 'set', 'polarity': 'positive', 'support': 'confirmed', 'reader_visible': False,
                    'holder': 'yun_niang', 'mode': 'learns',
                    'proposition': {'ref': 'k01', 'statement': '夫君从床底现身'},
                    'basis': [{'segment': 'e01_02', 'quote': '我看见夫君从床底爬出来。'}]}]
                return ChatResponse(content=json.dumps(wire, ensure_ascii=False),
                                    finish_reason='stop', model='fake',
                                    usage={'prompt_tokens': 100, 'completion_tokens': 40})
            return super().send(request)

    runner, repo, transport = _run_to_preserve(tmp_path, lambda wires: Transport(wires, [{
        'anchor': 'b01_02', 'segment': 'e01_02', 'note': '关键现身漏记芸娘获知'}]),
        monkeypatch, prepare=prepare)
    assert 'obligations' in runner()
    assert read_owner_issue_file(repo, 'ch01').status == 'resolved'
    assert transport.calls.count('ch01') == 2 and transport.calls.count('ch02') == 1
    assert transport.calls.count('preserve') == 2
    assert repo.read_model('stage2/observations/ch01.json').beats[1].cognition[0].holder == 'yun_niang'
    assert set(repo.inspect('stage2/observations/ch01.json')['_provenance']['inputs']) == {
        'stage2/observations/book.json'}
    assert repo.path('runtime/preserve_checkpoint.json').exists()


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


def test_multi_chapter_conflicts_heal_together_before_preserve_reverification(tmp_path, monkeypatch):
    """真实消费链把同答两章一起窄修，重编译后只复验一次 Preserve 并清两笔账。"""
    conflicts = [*CONFLICTS, {'anchor': 'b02_01', 'segment': 'e02_01',
                             'note': '第二章关键揭示漏抽知情'}]
    runner, repo, transport = _run_to_preserve(
        tmp_path, lambda wires: _ConflictPreserveTransport(wires, conflicts), monkeypatch)
    assert 'obligations' in runner()
    assert transport.calls.count('ch01') == transport.calls.count('ch02') == 2
    assert transport.calls.count('preserve') == 2
    for owner in ('ch01', 'ch02'):
        record = read_owner_issue_file(repo, owner)
        assert record.status == 'resolved' and record.rounds_total == 1
        assert not record.dispatch_pending
        assert set(repo.inspect(f'stage2/observations/{owner}.json')['_provenance']['inputs']) == {
            'stage2/observations/book.json'}
    assert repo.read('runtime/cascade_rewinds.json')['count'] == 2
    assert repo.read_model('runtime/preserve_checkpoint.json').obligations
    assert repo.read_model('stage4/grounding/reconstruction_brief.json').requirements


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
    assert '只读语义对照' in prompt and '不是回答模板' in prompt


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
    assert counter['count'] == CASCADE_REWIND_LIMIT
    # 拒绝不占已执行次数；重启再次拒绝也不得持续增加计数。
    with pytest.raises(OwnerRevisionStop):
        discover_owner_issues(
            repo, raised_by='str06.stage4.compile', owner='ch01',
            node='str06.stage2.chapters',
            issues=[OwnerIssueV1(code='material_name_leak', item='chapter:2',
                                 message='超界问题', context={})])
    assert repo.read('runtime/cascade_rewinds.json')['count'] == CASCADE_REWIND_LIMIT
    assert counter['limit'] == CASCADE_REWIND_LIMIT


class _RevisionTemplateTransport:
    """通过真实 producer 捕获返修请求；canonical 误答只在首轮注入。"""
    def __init__(self, wires, canonical_first=False):
        from tests.test_str06_body import _RoutingTransport
        self.inner = _RoutingTransport(wires)
        self.calls = []
        self.requests = []
        self.canonical_first = canonical_first

    def send(self, request):
        import json
        from novel.llm.transport import ChatResponse
        owner = self.inner._owner(request)
        self.calls.append(owner)
        if owner == 'preserve':
            self.requests.append(request.user)
            if self.canonical_first and len(self.requests) == 1:
                return ChatResponse(content=json.dumps({
                    'checkpoint_id': 'cp_wrong', 'obligations': [{
                        'anchors': [{'handle': 'b01_01.e1'}]}]}),
                    finish_reason='stop', model='fake',
                    usage={'prompt_tokens': 100, 'completion_tokens': 40})
        return self.inner.send(request)


def test_preserve_malformed_canonical_answer_enters_bounded_revision(tmp_path, monkeypatch):
    runner, repo, transport = _run_to_preserve(
        tmp_path, lambda wires: _RevisionTemplateTransport(wires, canonical_first=True),
        monkeypatch)
    report = runner()
    assert report['obligations'] > 0
    assert transport.calls.count('preserve') == 2
    assert '不是锚点对象' in transport.requests[1]
    assert repo.path('runtime/preserve_checkpoint.json').is_file()


def test_preserve_cross_node_revision_uses_wire_not_checkpoint_template(tmp_path, monkeypatch):
    import json
    from novel.stages.owner_revision import OwnerIssueV1, discover_owner_issues

    runner, repo, transport = _run_to_preserve(
        tmp_path, _RevisionTemplateTransport, monkeypatch)
    runner()
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, raised_by='str06.stage4.compile',
            owner='preserve', node='str06.stage4.preserve', issues=[OwnerIssueV1(
                code='positive_material_leak', item='chapter:1',
                message='修订义务中的提前披露称谓', context={})])
    runner()
    previous = transport.requests[-1].split('〔上一版〕\n', 1)[1].rsplit('\n请输出', 1)[0]
    wire = json.loads(previous)
    from contracts.schema.workflow import STAGE4_PRESERVE_PROTOCOL
    assert wire['protocol_version'] == STAGE4_PRESERVE_PROTOCOL
    assert set(wire) == {'protocol_version', 'obligations', 'series', 'evidence_conflicts'}
    assert all(isinstance(a, str) for o in wire['obligations'] for a in o['anchors'])
    assert all('obligation_id' not in o and 'lineage' not in o for o in wire['obligations'])
    assert all(isinstance(ref, int) for o in wire['obligations'] for ref in o['requires'])
