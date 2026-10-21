"""story-v1 的耐久请求、额度、接受、封存及免费恢复责任。

从 STR-06 记录测试迁移现役故障反例；全部经真实付费边界及当前故事入口，
transport 只模拟提供方响应。跨根沿用、旧 writer 和 V1/V2 修订迁移已退役。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from contracts.story_paths import (
    ADAPTATION_MAP, BODY, BODY_RECEIPT, CHAPTER_PACKET, FINAL, HOOK,
    HOOK_PACKET, HOOK_RECEIPT, NAME_MAP, TARGET_STORY,
)
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.paid_requests import (
    ExecutionError, PaidRequestSession, commit_bundles, sealed_turn_references,
)
from novel.llm.retry import TransportRetryPolicy
from novel.llm.story_requests import RevisionSeed, request_json, replay_semantic_trace
from novel.llm.transport import ChatRequest, ChatResponse
from novel.store import Str06Repository
from novel.work_units import WorkUnitBudget, initialize_workflow, read_book_budget
from tests.story_facilities import make_context, make_pipeline


class _Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    entries: list[str]


def _ask(session, owner, request):
    return request_json(session, owner, request, _Payload.model_validate)


class _EmptyThenGoodTransport:
    def __init__(self, payload='{"entries": []}'):
        self.payload, self.calls, self.requests = payload, 0, []

    def send(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            return ChatResponse(content='', reasoning='想了很多，但答案没流出来',
                finish_reason='stop', usage={'prompt_tokens': 7, 'completion_tokens': 3},
                model=request.model)
        return ChatResponse(content=self.payload, finish_reason='stop',
            usage={'prompt_tokens': 7, 'completion_tokens': 20}, model=request.model)


def _make_session(tmp_path, transport, *, yes=True, sid='book', ceiling=None):
    base = tmp_path / 'out'
    base.mkdir(exist_ok=True)
    initialize_workflow(base)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(repo.root, ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=yes,
        allow_source_export=yes, provider='fake', sid=sid, book_call_ceiling=ceiling)
    return base, repo, ledger, session


def _request(**changes):
    values = dict(model='deepseek-flash', system='系统', user='用户',
                  reasoning_effort='high', thinking=True, json_mode=True, max_tokens=131072)
    values.update(changes)
    return ChatRequest(**values)


def test_explicit_revision_append_preserves_existing_positions_and_replays_free(tmp_path):
    class Script:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            value = {'entries': ['原条目']} if self.calls == 1 else {
                'patches': [], 'appends': [{'path': ['entries'], 'value': ['新增条目']}]}
            return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    first = request_json(session, 'preserve', _request(), _Payload.model_validate)
    seed = RevisionSeed(kind='repair', baseline=first.value, paths=(), refs=first.refs,
                        trace=first.trace, append_paths=(('entries',),), remove_paths=())
    revision_request = _request(user='显式追加，只可在原数组尾部增配')
    revised = request_json(session, 'preserve', revision_request, _Payload.model_validate, revision_seed=seed)
    assert revised.value == {'entries': ['原条目', '新增条目']}
    assert first.value == {'entries': ['原条目']}
    assert replay_semantic_trace(revised.trace) == revised.value
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='book')
    replayed = request_json(readonly, 'preserve', revision_request, _Payload.model_validate, revision_seed=seed)
    assert replayed.value == revised.value and transport.calls == 2
    with pytest.raises(ValueError, match='整体替换列表'):
        RevisionSeed(kind='repair', baseline=first.value, paths=(('entries',),), refs=first.refs,
                     trace=first.trace, append_paths=(), remove_paths=())


@pytest.mark.parametrize('unauthorized', (False, True))
def test_explicit_revision_removes_only_authorized_dictionary_key_and_replays_free(tmp_path, unauthorized):
    class Payload(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        entries: dict[str, str]
        positions: list[str]
    class Script:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            value = {'entries': {'错误指称': '误抽', '未改指称': '原值'}, 'positions': ['旧位置']} if self.calls == 1 else {
                'patches': [], 'removes': [{'path': ['entries', '未改指称' if unauthorized else '错误指称']}]}
            return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    _base, repo, ledger, session = _make_session(tmp_path, transport)
    first = request_json(session, 'preserve', _request(), Payload.model_validate)
    seed = RevisionSeed(kind='repair', baseline=first.value, paths=(), refs=first.refs,
        trace=first.trace, append_paths=(), remove_paths=(('entries', '错误指称'),))
    revision_request = _request(user='仅删除已授权误抽的字典语义键')
    if unauthorized:
        from novel.llm.paid_requests import ValidationStop
        with pytest.raises(ValidationStop, match='remove越界'):
            request_json(session, 'preserve', revision_request, Payload.model_validate, revision_seed=seed)
        assert first.value['entries'] == {'错误指称': '误抽', '未改指称': '原值'}
        assert transport.calls == 2
        return
    revised = request_json(session, 'preserve', revision_request, Payload.model_validate, revision_seed=seed)
    assert revised.value == {'entries': {'未改指称': '原值'}, 'positions': ['旧位置']}
    assert first.value['entries'] == {'错误指称': '误抽', '未改指称': '原值'}
    assert replay_semantic_trace(revised.trace) == revised.value
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='book')
    assert request_json(readonly, 'preserve', revision_request, Payload.model_validate,
                        revision_seed=seed).value == revised.value
    assert transport.calls == 2
    with pytest.raises(ValueError, match='不得压缩列表位置'):
        RevisionSeed(kind='repair', baseline=first.value, paths=(), refs=first.refs,
            trace=first.trace, append_paths=(), remove_paths=(('positions', 0),))


@pytest.mark.stage_scope('stage2')
def test_retired_name_variants_scope_cannot_allocate_budget_or_send(tmp_path):
    """旧独占scope在授权之下仍须拒绝，不能产生单元、费用或提供方调用。"""
    from novel.llm.paid_requests import semantic_owner_unit
    from novel.work_units import WorkUnitRefused
    with pytest.raises(ValueError, match='未登记'):
        semantic_owner_unit('name_variants')
    forbidden = tmp_path / 'forbidden'
    with pytest.raises(WorkUnitRefused):
        WorkUnitBudget(forbidden, 'book', 'str06.stage2.names', scope='book:name_variants')
    assert not forbidden.exists()
    transport = _EmptyThenGoodTransport()
    base, _repo, _ledger, session = _make_session(tmp_path, transport, yes=True)
    before = {p.relative_to(base): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='未登记'):
        session.send('name_variants', _request())
    assert transport.calls == 0
    assert read_cost_snapshot(base, readonly=True) == ([], False)
    assert {p.relative_to(base): p.read_bytes() for p in base.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('shrinks', (False, True))
def test_narrow_repair_progress_uses_typed_defects_instead_of_changed_error_prose(tmp_path, shrinks):
    from contracts.schema.source_story import StoryIssue
    from novel.stages.stage2.story import StoryCompileError
    def compile_value(wire):
        parsed = _Payload.model_validate(wire)
        defects = [StoryIssue(code='broken_entry', owner='preserve', item=f'entry{n}',
            path=('entries', n), message='坏值现在是：' + text)
            for n, text in enumerate(parsed.entries) if text.startswith('bad')]
        if defects:
            raise StoryCompileError(defects)
        return parsed
    initial = {'entries': ['bad1', 'bad2']} if shrinks else {'entries': ['bad1']}
    patches = [{'path': ['entries', 0], 'value': 'good' if shrinks else 'bad_changed'}]
    if shrinks:
        patches.append({'path': ['entries', 1], 'value': 'bad_changed'})
    class Script:
        def __init__(self):
            self.calls = 0
            self.items = [initial, {'patches': patches},
                          {'patches': [{'path': ['entries', 1], 'value': 'good'}]}]
        def send(self, request):
            self.calls += 1
            return ChatResponse(content=json.dumps(self.items.pop(0)), finish_reason='stop', model=request.model)
    transport = Script()
    _base, repo, _ledger, session = _make_session(tmp_path, transport)
    if shrinks:
        result = request_json(session, 'preserve', _request(), compile_value, repository=repo)
        assert result.value == {'entries': ['good', 'good']}
        assert len(result.trace) == transport.calls == 3
    else:
        with pytest.raises(ExecutionError) as caught:
            request_json(session, 'preserve', _request(), compile_value, repository=repo)
        assert caught.value.code == 'story_no_progress'
        assert transport.calls == 2, '同code/item/path只换坏值和报错文字，不应继续付费'


def test_empty_first_body_answer_resends_packet_instead_of_indexerror(tmp_path):
    """现役写手首答空壳后重发原章包，同轮第二 attempt 正常接受。"""
    case = make_pipeline(tmp_path, scripts={'body_ch01': [ChatResponse(
        content='', reasoning='正文流丢失', finish_reason='stop', model='deepseek-flash')]})
    case.run()
    assert case.calls.count('body_ch01') == 2
    unit = WorkUnitBudget(tmp_path, 'book', 'str06.stage5.body', 'executor', 'chapter:1')
    turns = unit._read().turns
    assert len(turns) == 1
    assert [attempt.outcome for attempt in turns[0].attempts] == ['failed', 'ok']
    assert case.boundary.repository.inspect_sealed_model(BODY_RECEIPT.format(nn='01'))


def test_empty_answer_does_not_occupy_request_identity(tmp_path):
    transport = _EmptyThenGoodTransport()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    first = _ask(session, 'preserve', _request())
    replay = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                provider='fake', sid='book')
    second = _ask(replay, 'preserve', _request())
    assert first.value == second.value == {'entries': []}
    assert transport.calls == 2
    assert first.answer.attempt_id == second.answer.attempt_id
    assert transport.requests[0].user == transport.requests[1].user


def test_empty_answer_retries_within_one_content_turn_and_counts_attempts(tmp_path):
    transport = _EmptyThenGoodTransport()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    _ask(session, 'preserve', _request())
    turns = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book')._read().turns
    assert len(turns) == 1
    attempts = turns[0].attempts
    assert [a.outcome for a in attempts] == ['failed', 'ok']
    assert all(a.response_sha256 for a in attempts)
    assert attempts[0].response_sha256 != attempts[1].response_sha256
    assert read_book_budget(base, 'book').used == 2
    rows, partial = read_cost_snapshot(base)
    assert not partial and [row['attempt'] for row in rows] == [1, 2]
    assert [row['outcome'] for row in rows] == ['error', 'ok']
    assert sum(row['completion_tokens'] for row in rows) == 23


@pytest.mark.parametrize('empty_content', ('', ' \n'))
def test_json_empty_answer_is_salvaged_from_reasoning_free(tmp_path, empty_content):
    class ReasoningOnly:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            return ChatResponse(content=empty_content, reasoning='{"entries": []}', finish_reason='stop',
                usage={'prompt_tokens': 7, 'completion_tokens': 3}, model=request.model)
    transport = ReasoningOnly()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    first = _ask(session, 'preserve', _request())
    assert first.value == {'entries': []} and transport.calls == 1
    records = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book').acceptance_records()
    assert records[-1][1]['salvage'] == 'reasoning'
    replay = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                provider='fake', sid='book')
    assert _ask(replay, 'preserve', _request()).value == {'entries': []}
    assert transport.calls == 1, '整段救回并过闸的原响应应可免费重放'
    bad = _EmptyThenGoodTransport()
    bad.send = lambda request: ChatResponse(content='', reasoning='{"entries":"坏形状"}',
                                            finish_reason='stop', model=request.model)
    child = tmp_path / 'bad'
    child.mkdir()
    _, _, _, failed = _make_session(child, bad)
    with pytest.raises(ExecutionError) as excinfo:
        _ask(failed, 'preserve', _request())
    assert excinfo.value.code == 'attempt_exhausted', '救回未过同一schema时不得接受'


@pytest.mark.parametrize('mode', ('material', 'patch', 'format_failure'))
def test_acceptance_distinguishes_control_patch_and_unparseable_answers(tmp_path, mode):
    from tests.story_facilities import _materials
    index = _materials()
    first = {
        'material': json.dumps({'kind': 'need_materials', 'requests': [{
            'operation': 'read_segments', 'segment_ids': [next(iter(index.chapter(1)))]}]}),
        'patch': '{"entries":"坏形状"}',
        'format_failure': '{不是JSON',
    }[mode]
    second = ('{"patches":[{"path":["entries"],"value":[]}]}'
              if mode == 'patch' else '{"entries":[]}')
    class Script:
        def __init__(self):
            self.items = [first, second]
        def send(self, request):
            return ChatResponse(content=self.items.pop(0), finish_reason='stop', model=request.model)
    base, repo, ledger, session = _make_session(tmp_path, Script())
    answer = request_json(session, 'preserve', _request(), _Payload.model_validate,
                          repository=repo, index=index)
    assert answer.value == {'entries': []}
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book')
    records = dict(unit.acceptance_records())
    ordered = [records[attempt.attempt_id] for turn in unit._read().turns for attempt in turn.attempts]
    expected = [('material', True), ('result', True)] if mode == 'material' else (
        [('result', False), ('patch', True)] if mode == 'patch' else
        [('format_failure', False), ('result', True)])
    assert [(row['response_kind'], row['accepted']) for row in ordered] == expected


def test_acceptance_record_replaces_validation_file_and_adopts_by_seal(tmp_path):
    from contracts.schema.workflow import AcceptanceRecordV1, CommitAdoptionRef
    case = make_pipeline(tmp_path)
    case.run(through='stage4')
    repo = case.boundary.repository
    assert not list((repo.root / 'runtime').glob('validation_*.json'))
    unit = WorkUnitBudget(tmp_path, 'book', 'str06.stage4.naming', 'executor', 'book')
    records = unit.acceptance_records()
    assert records and all(value['semantic'] == 'unver' for _, value in records)
    bundle = next(bundle for bundle in commit_bundles(repo.root) if bundle.artifact == NAME_MAP)
    assert bundle.seal and len(bundle.adoptions) == 1
    adoption = bundle.adoptions[0]
    assert (adoption.unit_id, adoption.turn_id) in sealed_turn_references(repo.root)
    current_ref = adoption.model_dump(mode='json')
    for retired in ('historical', 'patch'):
        with pytest.raises(ValidationError):
            CommitAdoptionRef.model_validate({**current_ref, retired: {}})
    for identity in ('request_sha256', 'unit_id', 'turn_id', 'attempt_id', 'response_sha256'):
        with pytest.raises(ValidationError):
            CommitAdoptionRef.model_validate({**current_ref, identity: ''})
        missing = {key: value for key, value in current_ref.items() if key != identity}
        with pytest.raises(ValidationError):
            CommitAdoptionRef.model_validate(missing)
    acceptance = records[0][1]
    with pytest.raises(ValidationError):
        AcceptanceRecordV1.model_validate({**acceptance, 'turn_id': ''})
    session = case.boundary.session
    session.transport = _EmptyThenGoodTransport()
    session.transport.calls = 1
    _ask(session, 'naming', _request(user='另一个结构请求'))
    assert unit._read().turns[0].state == 'adopted'
    _ask(session, 'naming', _request(user='第三个结构请求'))
    rejected = unit._read().turns[1]
    assert rejected.state == 'failed' and rejected.reason_code == 'superseded_unadopted'


def test_reasoning_salvage_publishes_with_original_evidence_and_reseals_free(tmp_path):
    from tests.story_facilities import pipeline_materials
    _, _, wires = pipeline_materials()
    case = make_pipeline(tmp_path, scripts={'naming': [ChatResponse(content='',
        reasoning=json.dumps(wires['naming'], ensure_ascii=False), finish_reason='stop',
        usage={'prompt_tokens': 7, 'completion_tokens': 3}, model='deepseek-flash')]})
    case.run(through='stage4')
    bundle = next(bundle for bundle in commit_bundles(case.root) if bundle.artifact == NAME_MAP)
    assert bundle.seal and bundle.adoptions[0].response_sha256
    rows, partial = read_cost_snapshot(tmp_path)
    naming = [row for row in rows if row['purpose'] == 'naming' and not row['cached']]
    assert not partial and len(naming) == 1 and naming[0]['outcome'] == 'error'
    assert naming[0]['completion_tokens'] == 3, '网络空答与真实用量保持原始留证'
    _remove_seals(case)
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run(through='stage4')
    assert resumed.calls == [] and resumed.boundary.repository.inspect_sealed_model(NAME_MAP)


def _remove_seals(case):
    paths = list((case.root / 'runtime' / 'commit').rglob('*.json'))
    assert paths
    for path in paths:
        path.unlink()


def test_commit_bundle_loss_replays_and_reseals_free(tmp_path):
    case = make_pipeline(tmp_path)
    case.run()
    final = case.root / FINAL.format(title='三层样书')
    before = final.read_bytes()
    _remove_seals(case)
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run()
    assert resumed.calls == [] and final.read_bytes() == before
    assert resumed.boundary.repository.inspect_sealed_model(TARGET_STORY)
    assert len(commit_bundles(case.root)) >= 15


def test_hook_revision_recompiles_without_invalidating_accepted_body(tmp_path):
    """只改楔子组织：原正文及其回执不变，楔子必须重新绑定新章包。"""
    from novel.errors import UpstreamStale
    case = make_pipeline(tmp_path)
    case.run()
    rels = [template.format(nn=f'{i:02d}') for i in (1, 2) for template in (BODY, BODY_RECEIPT)]
    before = {rel: (case.root / rel).read_bytes() for rel in rels}
    hook = deepcopy(case.wires['hook_design'])
    hook['organization'] = '先让动作承接婴儿的处境，再停在进屋之处'
    case.wires['hook_design'] = hook
    resumed = make_pipeline(tmp_path, existing=case)
    original_ask = resumed.ask
    def revised_ask(owner, prompt, compiler, **kwargs):
        if owner == 'hook_design':
            prompt += '\n本次授权只修订楔子组织：' + hook['organization']
        return original_ask(owner, prompt, compiler, **kwargs)
    resumed.ask = revised_ask
    resumed.run(node_filter=('str06.stage4.hook_design', 'str06.stage4.hook_compile'))
    assert resumed.calls == ['hook_design']
    assert {rel: (case.root / rel).read_bytes() for rel in rels} == before
    with pytest.raises(UpstreamStale):
        resumed.run(node_filter=('str06.stage7.assemble',))
    resumed.run(node_filter=('str06.stage5.hook', 'str06.stage7.assemble'))
    assert resumed.calls == ['hook_design', 'hook_writer']
    assert {rel: (case.root / rel).read_bytes() for rel in rels} == before


def test_free_artifacts_are_sealed_like_paid_ones(tmp_path):
    case = make_pipeline(tmp_path)
    case.run(through='stage4')
    bundles = [bundle for bundle in commit_bundles(case.root) if bundle.artifact == TARGET_STORY]
    assert len(bundles) == 1 and bundles[0].adoptions == () and bundles[0].seal
    assert {output.key for output in bundles[0].outputs} == {
        TARGET_STORY, ADAPTATION_MAP, CHAPTER_PACKET.format(nn='01'), CHAPTER_PACKET.format(nn='02')}


def test_unsealed_formal_read_is_refused_then_free_recovery_reseals(tmp_path):
    from novel.errors import UnsealedArtifact
    case = make_pipeline(tmp_path)
    case.run()
    _remove_seals(case)
    with pytest.raises(UnsealedArtifact):
        case.run(node_filter=('str06.stage7.assemble',))
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run()
    resumed.run(node_filter=('str06.stage7.assemble',))
    assert resumed.calls == []
    path = case.root / CHAPTER_PACKET.format(nn='01')
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(UnsealedArtifact, match='摘要'):
        resumed.boundary.repository.read_model(CHAPTER_PACKET.format(nn='01'))


def test_compile_stage4_keeps_formal_positions_unpublished_on_guard_failure(tmp_path, monkeypatch):
    from novel.stages import chain
    from novel.stages.stage4 import story
    from novel.stages.stage4.story import TargetStoryIssue
    case = make_pipeline(tmp_path)
    before_compile = chain.NODE_ORDER[:chain.NODE_ORDER.index('str06.stage4.compile')]
    case.run(node_filter=before_compile)
    original = story.compile_target_story
    def reject(model, plans, preservation, names):
        invalid = model.model_copy(update={'events': tuple(
            value.model_copy(update={'realizes': ()}) for value in model.events)})
        return original(invalid, plans, preservation, names)
    monkeypatch.setattr(story, 'compile_target_story', reject)
    with pytest.raises((TargetStoryIssue, ValueError)):
        case.run(node_filter=('str06.stage4.compile',))
    assert not any((case.root / rel).exists() for rel in
        (TARGET_STORY, ADAPTATION_MAP, CHAPTER_PACKET.format(nn='01')))


def test_restarted_run_reuses_purchased_answers_across_sessions(tmp_path):
    case = make_pipeline(tmp_path)
    case.run()
    used = read_book_budget(tmp_path, 'book').used
    replay = make_pipeline(tmp_path, existing=case, readonly=True)
    replay.run()
    assert replay.calls == [] and read_book_budget(tmp_path, 'book').used == used


def test_budget_competition_stops_at_ceiling_with_attempt_accounting(tmp_path):
    """不同章并发争同一整书额度，不得双双越过准入线。"""
    class Good:
        calls = 0
        def send(self, request):
            self.calls += 1
            return ChatResponse(content='{"entries":[]}', finish_reason='stop', model=request.model)
    transport = Good()
    base, repo, ledger, session = _make_session(tmp_path, transport, ceiling=2)
    def send(ordinal):
        try:
            return _ask(session, f'ch{ordinal:02d}', _request(user=str(ordinal)))
        except ExecutionError as exc:
            return exc
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(send, range(1, 9)))
    errors = [answer for answer in answers if isinstance(answer, ExecutionError)]
    assert len(errors) == 6 and all(error.code == 'book_call_ceiling' for error in errors)
    assert transport.calls == read_book_budget(base, 'book').used == 2
    rows, partial = read_cost_snapshot(base)
    assert not partial and len(rows) == 2



def test_unknown_delivery_stops_without_automatic_retry(tmp_path):
    """已送达后结果未知：记 unknown，不自动重试（Q-13）。"""
    from novel.llm.paid_requests import ExecutionError

    class Unconfirmed:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            raise TimeoutError('模拟读超时：已送达但结果未知')

    transport = Unconfirmed()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    with pytest.raises((ExecutionError, TimeoutError)):
        _ask(session, 'preserve', _request())
    assert transport.calls == 1, 'unknown 不得自动重试'
    from novel.work_units import WorkUnitBudget, read_book_budget
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book')
    turn = unit._read().turns[0]
    assert turn.state == 'unknown'
    assert read_book_budget(base, 'book').used == 1, 'unknown 也占额'


def test_errors_carry_stage_node_item_phase_code_context(tmp_path):
    from novel.llm.paid_requests import ExecutionError, PurchaseRequired

    transport = _EmptyThenGoodTransport()
    base, repo, ledger, session = _make_session(tmp_path, transport, yes=False)
    with pytest.raises(PurchaseRequired):
        _ask(session, 'preserve', _request())
    # 未知付费 owner 的错误带完整定位字段。
    class Broken:
        def send(self, request):
            raise RuntimeError('boom')

    base2 = tmp_path / 'b2'
    base2.mkdir()
    initialize_workflow(base2)
    repo2 = Str06Repository(base2 / 'run')
    ledger2 = CostLedger(path=ledger_path(base2))
    repo2.attach_context(make_context(base2 / 'run', ledger=ledger2))
    session2 = PaidRequestSession(repo2, ledger2, Broken(), yes=True,
                                  allow_source_export=True, provider='fake', sid='book')
    with pytest.raises(ExecutionError) as excinfo:
        _ask(session2, 'names', _request())
    err = excinfo.value
    assert err.code and err.phase and err.node and err.stage and err.context


def test_not_delivered_transport_failure_retries_within_same_turn(tmp_path):
    """未送达（429/连接失败）：同一内容轮内有界退避重试，占尝试额度、如实计费。"""
    from novel.llm.transport import TransportError

    class RateLimitedThenOk:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            if self.calls == 1:
                raise TransportError('429 rate limit', retryable=True, status=429,
                                     kind='rate_limit')
            return ChatResponse(content='{"entries": []}', finish_reason='stop',
                                usage={'prompt_tokens': 7, 'completion_tokens': 20},
                                model='fake')

    transport = RateLimitedThenOk()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    session.retry_policy = TransportRetryPolicy(max_retries=3, backoff_base=0)
    answer = _ask(session, 'preserve', _request())
    assert answer.value == {'entries': []}
    assert transport.calls == 2, '未送达在同轮内重试一次后成功'
    from novel.work_units import WorkUnitBudget, read_book_budget
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book')
    turn = unit._read().turns[0]
    assert len(turn.attempts) == 2 and [a.outcome for a in turn.attempts] == ['failed', 'ok']
    assert turn.state == 'delivered'
    assert read_book_budget(base, 'book').used == 2, '重试 attempt 也占整书总闸'
    rows, partial = read_cost_snapshot(base)
    assert not partial and len([r for r in rows if not r['cached']]) == 2
    assert rows[0]['error_kind'] == 'rate_limit' and rows[0]['will_retry'] is True


def test_truncated_answer_stops_item_without_retry_or_extra_budget(tmp_path):
    """截断（finish_reason=length）：即停该 item，留证、不重试、不加预算（D6）。"""
    from novel.llm.paid_requests import ExecutionError

    class Truncated:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            return ChatResponse(
                content='{"entries": [', reasoning='想了一半被切断',
                finish_reason='length',
                usage={'prompt_tokens': 700, 'completion_tokens': 131072,
                       'reasoning_tokens': 90000},
                model='fake')

    transport = Truncated()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    with pytest.raises(ExecutionError) as excinfo:
        _ask(session, 'preserve', _request())
    err = excinfo.value
    assert err.code == 'truncated' and err.phase == 'accept'
    assert err.context['completion_tokens'] == 131072
    assert err.context['reasoning_tokens'] == 90000, '截断必须如实记录 reasoning 与正文 token'
    assert transport.calls == 1, '截断不自动重试'
    from novel.work_units import WorkUnitBudget, read_book_budget
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book')
    turn = unit._read().turns[0]
    assert turn.state == 'delivered', '截断响应已完整交付并耐久留证'
    assert [a.outcome for a in turn.attempts] == ['ok'], 'ok 留证保证重跑免费命中，不自动重购'
    assert read_book_budget(base, 'book').used == 1, '外发占额一次，截断不追加预算'
    rows, partial = read_cost_snapshot(base)
    assert not partial
    paid = [r for r in rows if not r['cached']]
    assert len(paid) == 1 and paid[0]['finish_reason'] == 'length'
    assert paid[0]['completion_tokens'] == 131072 and paid[0]['reasoning_tokens'] == 90000


def test_truncated_cached_replay_stops_again_without_rebuying(tmp_path):
    """重启重放命中同一截断响应：仍即停，不重购、不换回答。"""
    from novel.llm.paid_requests import ExecutionError

    class Truncated:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            return ChatResponse(content='{"entries": [', finish_reason='length',
                                usage={'prompt_tokens': 700, 'completion_tokens': 131072},
                                model='fake')

    transport = Truncated()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    with pytest.raises(ExecutionError, match='截断'):
        _ask(session, 'preserve', _request())
    replay = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                provider='fake', sid='book')
    with pytest.raises(ExecutionError) as excinfo:
        _ask(replay, 'preserve', _request())
    assert excinfo.value.code == 'truncated'
    assert transport.calls == 1, '免费命中同一截断响应，不得再付费重购'
    assert excinfo.value.context['cached'] is True


def test_execution_profile_freezes_config_for_whole_run():
    """ExecutionProfile：effort/max_tokens 取值面收紧；配置只冻结一次。"""
    from novel.config import Config
    from novel.stages.chain import (
        ExecutionProfile, STR06_EFFORT_LEVELS, str06_chat_request,
    )
    from contracts.invariants import LLM_MAX_OUTPUT_TOKENS

    assert STR06_EFFORT_LEVELS == ('low', 'high', 'max')
    cfg = Config(model='fake-model', thinking_mode='on')
    profile = ExecutionProfile(cfg=cfg, reasoning_effort='high')
    assert profile.max_tokens == LLM_MAX_OUTPUT_TOKENS == 131072
    for bad in ('medium', 'xhigh', '', 'HIGH'):
        with pytest.raises(ValueError, match='effort'):
            ExecutionProfile(cfg=cfg, reasoning_effort=bad)
    with pytest.raises(ValueError, match='131072'):
        ExecutionProfile(cfg=cfg, reasoning_effort='high', max_tokens=65536)
    request = str06_chat_request(profile, 'preserve', '提示', json_mode=True,
                                 system='系统', temperature=0.7, enforce=False)
    assert request.model == 'fake-model'
    assert request.reasoning_effort == 'high' and request.thinking is True
    assert request.max_tokens == 131072, '全部付费节点统一 131072（D6）'
    # 请求身份与 ledger 列由同一份 profile 决定：model/effort/thinking/max_tokens
    # 全部落在 cache_payload（= 请求身份）里。
    payload = request.cache_payload()
    assert payload['max_tokens'] == 131072 and payload['reasoning_effort'] == 'high'


def test_assemble_paid_boundary_never_rereads_config_at_execution(tmp_path):
    """删执行时 `load_config()` 重读：装配只消费 run 启动时冻结的 profile。"""
    import novel.stages.chain as chain_mod
    from novel.config import Config
    from novel.stages.chain import assemble_paid_boundary, freeze_execution_profile

    base = tmp_path / 'out'
    base.mkdir()
    from novel.work_units import require_workflow
    require_workflow(base)
    cfg = Config(model='frozen-model')
    profile = freeze_execution_profile(cfg=cfg, reasoning_effort='low',
                                       base=base, sid='run')
    assert profile.book_call_ceiling == 512, '账缺失时冻结默认整书上限'

    original_load_config = chain_mod.load_config

    def _refuse(*a, **kw):
        raise AssertionError('执行期不得重读 load_config()')

    chain_mod.load_config = _refuse
    try:
        boundary = assemble_paid_boundary(
            base=base, run_dir=base / 'run',
            source_identity='a' * 64, source_content_sha256='c' * 64, root_sha1='b' * 40, profile=profile)
    finally:
        chain_mod.load_config = original_load_config
    assert boundary.model == 'frozen-model'
    assert boundary.session.transport.cfg is profile.cfg, 'transport 实参来自冻结 profile'
    assert boundary.session.book_call_ceiling == 512, '整书上限贯通到付费边界'
    assert boundary.profile.reasoning_effort == 'low'


def test_execution_profile_ceiling_derives_from_admission_ledger(tmp_path):
    """单次运行上限从同一准入账派生：ceiling＋grants；显式值优先（I-17）。"""
    from novel.config import Config
    from novel.stages.chain import freeze_execution_profile
    from novel.work_units import grant_book_budget, read_book_budget

    base = tmp_path / 'out'
    base.mkdir()
    from novel.work_units import require_workflow
    require_workflow(base)
    profile = freeze_execution_profile(cfg=Config(), reasoning_effort='high',
                                       base=base, sid='book')
    assert profile.book_call_ceiling == 512
    grant_book_budget(base, 'book', actor='人工', reason='追加', additional=3)
    budget = read_book_budget(base, 'book')
    profile2 = freeze_execution_profile(cfg=Config(), reasoning_effort='high',
                                        base=base, sid='book')
    assert profile2.book_call_ceiling == budget.ceiling + 3 == 515, '含显式追加'
    profile3 = freeze_execution_profile(cfg=Config(), reasoning_effort='high',
                                        base=base, sid='book', book_call_ceiling=7)
    assert profile3.book_call_ceiling == 7, '显式 --book-call-ceiling 优先'


def test_parallel_registry_registers_new_chain_chapter_node_only():
    """并发登记改新链逐章节点：退役旧链键删除，键必须在 NODES 且逐章 scope。"""
    from contracts.invariants import NODES, PIPELINE_PARALLEL_NODES

    # 源章观察与目标章展开可以并行，依赖前章短尾的正文按顺序执行。
    assert PIPELINE_PARALLEL_NODES == ('str06.stage2.chapters', 'str06.stage4.chapters')
    for node in PIPELINE_PARALLEL_NODES:
        assert node in NODES, f'并发覆盖节点必须在现役注册表：{node}'
        # 关闭批：退役名册已删，并发面只认现役注册表。
        spec = NODES[node].lifecycle
        assert spec is not None and spec.unit_kind == 'chapter', \
            f'{node} 必须是逐章节点（scope=chapter:N）'
    for legacy in ('stage2.blueprint', 'stage4.design', 'stage4.plan', 'stage5.write'):
        assert legacy not in PIPELINE_PARALLEL_NODES, '退役旧链键已删除'
    from contracts.schema.orchestration import PipelineConcurrency
    overrides = PipelineConcurrency(node_overrides={'str06.stage4.chapters': 32})
    assert overrides.node_overrides == {'str06.stage4.chapters': 32}
    with pytest.raises(ValueError, match='逐章节点'):
        PipelineConcurrency(node_overrides={'stage4.design': 4})
    with pytest.raises(ValueError, match='节点并发必须是'):
        PipelineConcurrency(node_overrides={'str06.stage4.chapters': 201})
    with pytest.raises(ValueError, match='节点并发必须是'):
        PipelineConcurrency(node_overrides={'str06.stage2.chapters': 0})


def test_frozen_concurrency_keeps_each_parallel_node():
    """源观察和目标章展开各取自己的整数；没写覆盖的节点保持运行默认。"""
    from contracts.schema.orchestration import PipelineConcurrency
    from novel.config import Config
    from novel.stages.chain import frozen_concurrency

    cfg = Config(concurrency=4, concurrency_policy=PipelineConcurrency(
        node_overrides={'str06.stage2.chapters': 11},
        per_book=32, per_provider=200))
    assert cfg.concurrency_for('str06.stage2.chapters') == 11
    assert cfg.concurrency_for('str06.stage4.chapters') == 4
    frozen = frozen_concurrency(cfg)
    assert frozen == {
        'concurrency': 4,
        'node_concurrency': {
            'str06.stage2.chapters': 11,
            'str06.stage4.chapters': 4,
        },
    }
