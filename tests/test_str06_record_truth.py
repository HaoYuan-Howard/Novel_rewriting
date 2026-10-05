"""CS-H H1（RC-54）：接受记录如实——被拒或被回派的候选不再记成已接受。

此前两处记录不真：①Design 的材料问题归上游时，validate 返回空列表以结束接受循环，该 attempt 被记成
``accepted=true``；②文本节点（正文、楔子）只看「回答非空」就记 ``accepted=true``，被机械闸拒绝的首稿
同样如此。另：已交付的书在单节点路径下 ``accepted_body=false``，回派问题同一材料多处命中各占一行。
这里全部走真实入口（真实 producer、fake transport、零付费），断言落盘的接受记录、run 状态与回派账。
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from contracts.schema.workflow import OwnerIssueV1
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.paid_requests import (
    PaidRequestSession, UpstreamOwned, ask_json, ask_text_once,
)
from novel.llm.transport import ChatRequest, ChatResponse
from novel.stages.chain import run_workflow
from novel.stages.owner_revision import (
    OwnerRevisionRequired, discover_owner_issues, read_owner_issue_file,
)
from novel.store import Str06Repository
from novel.work_units import WorkUnitBudget
from tests.str06_facilities import make_context
from tests.test_str06_body import _RoutingTransport, _make_session, _run
from tests.test_str06_candidate_preflight import _bad_design, _prepare
from tests.test_str06_hook import _full_chain_wires


def _records(base, sid, node, scope):
    """该单元全部接受记录，按内容轮与 attempt 序：[(轮序, accepted, parse, errors)]。"""
    unit = WorkUnitBudget(base, sid, node, 'executor', scope, read_only=True)
    by_attempt = dict(unit.acceptance_records())
    rows = []
    for turn in unit._read().turns:
        for attempt in turn.attempts:
            record = by_attempt.get(attempt.attempt_id)
            if record is not None:
                rows.append((turn.ordinal, record['accepted'], record['parse'], tuple(record['errors'])))
    return rows


# ─── 纯机制：ask_json／ask_text_once 的接受记录 ──────────────────────────────

class _CountingTransport:
    def __init__(self, content='{"k": 1}'):
        self.calls = 0
        self.content = content

    def send(self, request):
        self.calls += 1
        return ChatResponse(content=self.content, finish_reason='stop',
                            usage={'prompt_tokens': 10, 'completion_tokens': 5}, model='fake')


def _plain_session(tmp_path, transport):
    base = tmp_path / 'out'
    base.mkdir()
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=True, allow_source_export=True,
                                 provider='fake', sid='run')
    return base, repo, session


def _request(user='请求', json_mode=True):
    return ChatRequest(model='fake', system='sys', user=user, reasoning_effort='high',
                       thinking=True, json_mode=json_mode, max_tokens=1000)


def test_upstream_owned_answer_is_recorded_rejected_and_propagates_without_a_second_call(tmp_path):
    transport = _CountingTransport()
    base, repo, session = _plain_session(tmp_path, transport)

    def validate(_wire):
        raise UpstreamOwned(['材料问题 A：归 Preserve', '材料问题 B：归 Preserve'])

    with pytest.raises(UpstreamOwned) as routed:
        ask_json(session, 'design', _request(), validate)
    assert routed.value.errors == ('材料问题 A：归 Preserve', '材料问题 B：归 Preserve')
    assert transport.calls == 1, '问题归上游：不在节点内重问，更不付费修订'
    assert _records(base, 'run', 'str06.stage4.body_design', 'book') == [
        (1, False, 'failed', ('材料问题 A：归 Preserve', '材料问题 B：归 Preserve'))], \
        '回答已耐久但不被接受：accepted=false 并带这些问题（此前记成 accepted=true）'
    # 免费重放（同请求身份、新会话）：同一结论、不再购买、记录仍是 false
    replay = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport, yes=True,
                                allow_source_export=True, provider='fake', sid='run')
    with pytest.raises(UpstreamOwned):
        ask_json(replay, 'design', _request(), validate)
    assert transport.calls == 1
    assert [row[1] for row in _records(base, 'run', 'str06.stage4.body_design', 'book')] == [False]


def test_text_validation_is_recorded_and_empty_validate_keeps_the_old_semantics(tmp_path):
    transport = _CountingTransport(content='正文稿')
    base, repo, session = _plain_session(tmp_path, transport)

    def validate(text):
        return ['[length_band] 篇幅越界'] if text == '正文稿' else []

    text, errors, _answer = ask_text_once(session, 'hook_writer', _request('A', json_mode=False), validate)
    assert (text, errors) == ('正文稿', ['[length_band] 篇幅越界'])
    # 另一请求身份、校验通过
    transport.content = '合格稿'
    text, errors, _answer = ask_text_once(session, 'hook_writer', _request('B', json_mode=False), validate)
    assert (text, errors) == ('合格稿', [])
    # 不给校验回调：保持原语义（只判空），不凭空多出错误
    transport.content = '正文稿'
    text, errors, _answer = ask_text_once(session, 'hook_writer', _request('C', json_mode=False))
    assert (text, errors) == ('正文稿', [])
    assert _records(base, 'run', 'str06.stage5.hook', 'book') == [
        (1, False, 'failed', ('[length_band] 篇幅越界',)),
        (2, True, 'ok', ()),
        (3, True, 'ok', ())]


# ─── 真实入口：Design 材料问题归 Preserve ────────────────────────────────────

def test_design_whose_materials_belong_to_preserve_is_recorded_rejected_on_the_real_entry(tmp_path):
    ctx, wires, transport, _ = _prepare(tmp_path, bad_preserve=True)
    _bad_design(wires['design'])
    with pytest.raises(OwnerRevisionRequired) as stopped:
        run_workflow(repository=ctx.repository, chapters=ctx.chapters, ask=ctx.ask,
                     ask_text=ctx.ask_text, ask_body=ctx.ask_body, budget=ctx.budget,
                     node_filter=('str06.stage4.body_design',))
    assert stopped.value.node == 'str06.stage4.preserve'
    base = ctx.repository.root.parent
    rows = _records(base, 'run', 'str06.stage4.body_design', 'book')
    assert len(rows) == 1 and transport.calls.count('design') == 1
    turn, accepted, parse, errors = rows[0]
    assert accepted is False and parse == 'failed', '被回派的 Design 候选不是已接受的候选'
    assert any('positive_material_leak' in e for e in errors), '记录带这些材料问题'
    # 回派账里每个材料问题只占一行（同一材料多处命中不重复）
    record = read_owner_issue_file(ctx.repository, 'preserve')
    assert record and len(record.issues) == len({
        json.dumps(i.context['material_identity'], sort_keys=True, ensure_ascii=False)
        for i in record.issues})
    # 免费重放：不再购买，记录仍如实
    calls = len(transport.calls)
    with pytest.raises(OwnerRevisionRequired):
        run_workflow(repository=ctx.repository, chapters=ctx.chapters, ask=ctx.ask,
                     ask_text=ctx.ask_text, ask_body=ctx.ask_body, budget=ctx.budget,
                     node_filter=('str06.stage4.body_design',))
    assert len(transport.calls) == calls
    assert [row[1] for row in _records(base, 'run', 'str06.stage4.body_design', 'book')] == [False]


# ─── 真实入口：文本节点首稿被机械闸拒 ────────────────────────────────────────

class _BadFirstDraft(_RoutingTransport):
    """正文第 1 章与楔子的**首稿**违规（篇幅越界），重写后给合格稿。"""

    BAD_BODY = '字' * 5000
    BAD_HOOK = '空' * 400

    def __init__(self, wires):
        super().__init__(wires)
        self._spoiled: set[str] = set()

    def send(self, request):
        owner = self._owner(request)
        if owner in ('body_ch01', 'hook_writer') and owner not in self._spoiled:
            self._spoiled.add(owner)
            self.calls.append(owner)
            bad = self.BAD_BODY if owner == 'body_ch01' else self.BAD_HOOK
            return ChatResponse(content=bad, finish_reason='stop',
                                usage={'prompt_tokens': 100, 'completion_tokens': 40}, model='fake')
        return super().send(request)


def test_first_draft_rejected_by_the_mechanical_gate_is_recorded_rejected(tmp_path):
    repo, session, transport = _make_session(
        tmp_path, _full_chain_wires(), transport=_BadFirstDraft(_full_chain_wires()))
    _run(repo, session)
    base = repo.root.parent
    body = _records(base, 'run', 'str06.stage5.body', 'chapter:1')
    assert [(turn, accepted) for turn, accepted, _p, _e in body] == [(1, False), (2, True)]
    assert any('[length_band]' in e for e in body[0][3]), '被机械闸拒的首稿记录带 typed 错误'
    assert body[1][3] == ()
    assert [(turn, accepted) for turn, accepted, _p, _e in _records(
        base, 'run', 'str06.stage5.body', 'chapter:2')] == [(1, True)], '无关章不受影响'
    hook = _records(base, 'run', 'str06.stage5.hook', 'book')
    assert [(turn, accepted) for turn, accepted, _p, _e in hook] == [(1, False), (2, True)]
    assert hook[0][2] == 'failed' and hook[0][3], '楔子首稿同理'


# ─── 单节点路径跑完交付：accepted_body 与整链同源 ────────────────────────────

def test_single_node_assemble_after_delivery_reports_the_delivery_state(tmp_path):
    repo, session, transport = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    assert repo.read('runtime/workflow_status.json')['accepted_body'] is True
    calls = len(transport.calls)
    # 单节点重跑交付组装（csf4 根的真实情形：整链已交付，状态仍写 node_run／accepted_body=false）
    report = _run(repo, session, node_filter=('str06.stage7.assemble',))
    status = repo.read('runtime/workflow_status.json')
    assert report['assembly']['status'] == 'ready'
    assert status['status'] == 'delivered' and status['accepted_body'] is True
    assert report['accepted_body'] is True
    assert len(transport.calls) == calls, '免费组装不外发'


def test_single_node_that_is_not_assembly_stays_node_run(tmp_path):
    repo, session, _ = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session, node_filter=('str06.stage2.book',))
    status = repo.read('runtime/workflow_status.json')
    assert status['status'] == 'node_run' and status['accepted_body'] is False


def test_delivery_terminal_is_one_judgement_for_both_paths():
    from novel.stages.chain import _delivery_terminal
    assert _delivery_terminal({'status': 'ready'}) == ('delivered', True)
    assert _delivery_terminal({'status': 'blocked', 'reason': '缺章'}) == ('assembly_not_ready', False)
    assert _delivery_terminal({}) == ('assembly_not_ready', False)


# ─── 回派问题按材料身份去重 ──────────────────────────────────────────────────

def _leak(literal, *, field, start):
    identity = {'code': 'positive_material_leak', 'literal': literal, 'chapter_ordinal': 1,
                'sources': [['design', 'book', field, 'book']]}
    return OwnerIssueV1(code='material_name_leak', item='chapter:1',
                        message=f'第1章材料含未释放字面 {literal!r}（{field}@{start}）',
                        context={'material_identity': identity, 'literal': literal})


def test_owner_issues_dedupe_by_material_identity_without_changing_the_fingerprint(tmp_path):
    from novel.stages.owner_revision import _fingerprint
    _base, repo, _session = _plain_session(tmp_path, _CountingTransport())
    repo.path('stage4/design/body_design.json').parent.mkdir(parents=True)
    repo.path('stage4/design/body_design.json').write_text('{}', encoding='utf-8')
    issues = [_leak('陆郎', field='book_style', start=at) for at in (3, 30, 90)]
    issues.append(_leak('裴书远', field='voice', start=7))
    with pytest.raises(OwnerRevisionRequired) as required:
        discover_owner_issues(repo, raised_by='str06.stage4.compile', owner='design',
                              node='str06.stage4.body_design', issues=issues)
    assert len(required.value.issues) == 2, '同一材料身份的 3 处命中只算一个问题（留首）'
    assert required.value.issues[0].message.endswith('@3）') and '陆郎' in required.value.issues[0].message
    record = read_owner_issue_file(repo, 'design')
    assert [i.context['literal'] for i in record.issues] == ['陆郎', '裴书远']
    assert record.fingerprint == _fingerprint(issues) == _fingerprint(list(required.value.issues)), \
        '去重不改变问题集指纹，无进展判定不受影响'
    assert str(required.value).count('陆郎') == 1, '提示词与消息里不再逐处重复'
