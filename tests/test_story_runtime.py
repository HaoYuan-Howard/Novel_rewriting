"""story-v3 的耐久请求、额度、接受、封存及免费恢复责任。

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


@pytest.mark.parametrize('sibling_change', ('none', 'shrink', 'reorder'))
def test_atomic_revision_resegments_exact_field_and_keeps_other_array_layouts_frozen(tmp_path, sibling_change):
    class Metadata(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        positions: list[str]
    class Payload(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        parts: list[str]
        metadata: Metadata
    initial = {'parts': ['前半', '后半'], 'metadata': {'positions': ['第一位置', '第二位置']}}
    positions = (['第一位置'] if sibling_change == 'shrink' else ['第二位置', '第一位置']
                 if sibling_change == 'reorder' else initial['metadata']['positions'])
    class Script:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            value = initial if self.calls == 1 else {'patches': [
                {'path': ['parts'], 'value': ['前半后半']},
                {'path': ['metadata'], 'value': {'positions': positions}}]}
            return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    _base, repo, ledger, session = _make_session(tmp_path, transport)
    first = request_json(session, 'names', _request(), Payload.model_validate)
    seed = RevisionSeed(kind='repair', baseline=first.value, paths=(('parts',), ('metadata',)), refs=first.refs,
        trace=first.trace, append_paths=(), remove_paths=(), atomic_paths=(('parts',),))
    revision_request = _request(user='只允许parts显式原子重分段，metadata里的列表位置继续冻结')
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='book')
    if sibling_change == 'none':
        revised = request_json(session, 'names', revision_request, Payload.model_validate, revision_seed=seed)
        assert revised.value == {'parts': ['前半后半'], 'metadata': initial['metadata']}
        assert revised.trace[-1]['paths']['atomic'] == [['parts']]
        assert replay_semantic_trace(revised.trace) == revised.value
        assert request_json(readonly, 'names', revision_request, Payload.model_validate,
                            revision_seed=seed).value == revised.value
    else:
        for active in (session, readonly):
            with pytest.raises(ExecutionError) as caught:
                request_json(active, 'names', revision_request, Payload.model_validate, revision_seed=seed)
            assert caught.value.code == 'story_candidate'
            assert ('改变既有数组长度' if sibling_change == 'shrink' else '不得重排') in str(caught.value)
    assert transport.calls == 2
    assert first.value == initial, '原子字段授权不得修改真实已购基线'
    for paths, atomic in (((('metadata',),), (('metadata', 'positions'),)),
                          ((('parts',),), (('parts',), ('parts',))),
                          ((('metadata',),), (('metadata',),))):
        with pytest.raises(ValueError):
            RevisionSeed(kind='repair', baseline=first.value, paths=paths, refs=first.refs, trace=first.trace,
                append_paths=(), remove_paths=(), atomic_paths=atomic)


def test_owner_atomic_permission_is_only_existing_exact_names_surface_field():
    from novel.stages.chain import _owner_atomic_paths
    from tests.story_facilities import pipeline_materials
    _chapters, _index, wires = pipeline_materials()
    baseline = wires['names']
    person = next(iter(baseline['people']))
    surface = next(iter(baseline['people'][person]['surfaces']))
    exact = ('people', person, 'surfaces', surface)
    paths = (exact, ('people',), ('people', person), ('people', person, 'aliases'),
             ('people', person, 'surfaces'), (*exact, 0), ('people', person, 'surfaces', '未登记称谓'))
    assert _owner_atomic_paths('names', baseline, paths) == (exact,)
    for owner in ('book', 'associate', 'preserve', 'ch01'):
        assert _owner_atomic_paths(owner, baseline, paths) == ()


def test_atomic_repair_trace_drops_capability_when_next_diagnosis_only_targets_another_field(tmp_path):
    from contracts.schema.source_story import StoryIssue
    from novel.stages.stage2.story import StoryCompileError
    class Payload(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        parts: list[str]
        note: str
    def compile_value(wire):
        parsed = Payload.model_validate(wire)
        if parsed.note == 'bad':
            raise StoryCompileError((StoryIssue(code='bad_note', owner='names', item='note',
                path=('note',), message='只须继续修复note'),))
        return parsed
    class Script:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            if self.calls == 1:
                value = {'parts': ['前', '后'], 'note': 'original'}
            elif self.calls == 2:
                value = {'patches': [{'path': ['parts'], 'value': ['前后']}, {'path': ['note'], 'value': 'bad'}]}
            else:
                frozen, _end = json.JSONDecoder().raw_decode(request.user.split('【冻结候选与窄修权限】\n', 1)[1])
                assert frozen['allowed_paths'] == [['note']] and frozen['atomic_paths'] == []
                value = {'patches': [{'path': ['note'], 'value': 'fixed'}]}
            return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    _base, _repo, _ledger, session = _make_session(tmp_path, transport)
    first = request_json(session, 'names', _request(), compile_value)
    seed = RevisionSeed(kind='repair', baseline=first.value, paths=(('parts',), ('note',)), refs=first.refs,
        trace=first.trace, append_paths=(), remove_paths=(), atomic_paths=(('parts',),))
    result = request_json(session, 'names', _request(user='原子重分段parts并修订note'), compile_value, revision_seed=seed)
    assert transport.calls == 3 and result.value == {'parts': ['前后'], 'note': 'fixed'}
    assert [step['paths']['atomic'] for step in result.trace[1:]] == [[['parts']], []]
    assert replay_semantic_trace(result.trace) == result.value


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


def test_material_continuation_keeps_all_frozen_repair_diagnostics_and_replays_free(tmp_path):
    from contracts.schema.source_story import StoryIssue
    from novel.stages.stage2.story import StoryCompileError
    from tests.story_facilities import _materials
    index = _materials()
    initial = {'entries': ['bad1', 'bad2']}
    def compile_value(wire):
        parsed = _Payload.model_validate(wire)
        issues = [StoryIssue(code='broken_entry', owner='preserve', item=f'entry{number}',
            path=('entries', number), message=f'第 {number} 条须按原证据修复')
            for number, text in enumerate(parsed.entries) if text.startswith('bad')]
        if issues:
            raise StoryCompileError(issues)
        return parsed
    class Script:
        def __init__(self):
            self.requests = []
        def send(self, request):
            self.requests.append(request)
            value = (initial if len(self.requests) == 1 else {
                'kind': 'need_materials', 'requests': [{'operation': 'read_segments',
                    'segment_ids': [next(iter(index.chapter(1)))]}]} if len(self.requests) == 2 else {
                'patches': [{'path': ['entries', number], 'value': 'good'} for number in (0, 1)]})
            return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    request = _request(user='原任务及输出 schema：' + json.dumps(_Payload.model_json_schema(), ensure_ascii=False))
    first = request_json(session, 'preserve', request, compile_value, repository=repo, index=index)
    assert first.value == {'entries': ['good', 'good']}
    assert len(transport.requests) == 3
    for repair in transport.requests[1:]:
        assert request.user in repair.user, '材料补全与窄修都须保留原任务及 schema'
        frozen, _end = json.JSONDecoder().raw_decode(repair.user.split('【冻结候选与窄修权限】\n', 1)[1])
        assert frozen['candidate'] == initial
        assert frozen['allowed_paths'] == [['entries', 0], ['entries', 1]]
        assert [(row['code'], row['path'], row['message']) for row in frozen['diagnostics']] == [
            ('broken_entry', ['entries', number], f'第 {number} 条须按原证据修复') for number in (0, 1)]
        assert '仅返回 {"patches"' in repair.user
        assert '原任务schema、引用和证据约束' in repair.user
    assert '【已冻结补充材料】' in transport.requests[-1].user
    assert replay_semantic_trace(first.trace) == first.value
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='book')
    replayed = request_json(readonly, 'preserve', request, compile_value, repository=repo, index=index)
    assert replayed.value == first.value and len(transport.requests) == 3
    assert replayed.refs == first.refs


@pytest.mark.parametrize('interrupted', (False, True))
def test_associate_material_trace_authenticates_delivery_and_recovers_without_rebuy(tmp_path, interrupted):
    from types import SimpleNamespace
    from contracts.schema.source_story import STORY_VERSION
    from novel.llm.paid_requests import digest
    from novel.llm.story_requests import replay_material_trace
    from novel.stages.chain import _verified_semantic_candidate
    from novel.stages.stage2.story import associate_prompt, compile_associate, compile_book
    from tests.story_facilities import _inputs, _book_wire
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    initial_ref, supplement_ref = list(index.chapter(1))[0], list(index.chapter(2))[1]
    wire['unresolved'] = [{'key': '关联候选', 'kind': 'entity_identity', 'left': '叙述者',
        'candidates': ['来客'], 'question': '是否同一人？', 'evidence_refs': [initial_ref]}]
    book = compile_book(wire, index=index, chapters=chapters)
    query = {'kind': 'need_materials', 'requests': [{'candidate_key': '关联候选',
        'operation': 'read_segments', 'segment_ids': [supplement_ref], 'adjacent_paragraphs': 0}]}
    valid = {'kind': 'identity', 'key': '关联候选', 'verdict': 'distinct', 'right': None,
        'reason': '按补充原文分别判断', 'evidence_refs': [supplement_ref]}
    invalid = {**valid, 'verdict': 'same', 'right': '不在候选闭集'}
    class Script:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            value = [query, {'contract_version': STORY_VERSION, 'verdicts': [invalid]},
                     {'patches': [{'path': ['verdicts', 0], 'value': valid}]}][self.calls - 1]
            return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    _base, repo, ledger, session = _make_session(tmp_path, transport, sid='run')
    request = _request(user=associate_prompt(book))
    interrupted_once = False
    def material_prompt(materials):
        nonlocal interrupted_once
        if interrupted and not interrupted_once:
            interrupted_once = True
            raise OSError('注入：补料回答已耐久，后续请求尚未发出')
        return associate_prompt(book, index=index, materials=materials)
    def ask(active_session):
        return request_json(active_session, 'associate', request,
            lambda value: compile_associate(value, book=book, index=index), index=index,
            material_candidates=('关联候选',), material_prompt=material_prompt,
            compile_with_materials=lambda value, materials: compile_associate(
                value, book=book, index=index, materials=materials))
    if interrupted:
        with pytest.raises(OSError, match='后续请求尚未发出'):
            ask(session)
        assert transport.calls == 1
        session = PaidRequestSession(repo, ledger, transport, yes=True, allow_source_export=True,
                                      provider='fake', sid='run')
    result = ask(session)
    assert transport.calls == 3
    assert [step['kind'] for step in result.trace] == ['material', 'result', 'patch']
    assert replay_semantic_trace(result.trace, index=index, material_candidates=('关联候选',)) == result.value
    assert replay_material_trace(result.trace, index=index, material_candidates=('关联候选',)) == result.materials
    context = SimpleNamespace(repository=SimpleNamespace(root=repo.root, read_model=lambda _rel: book), index=index)
    saved = {'kind': 'result', 'contract_version': STORY_VERSION, 'wire': result.value, 'trace': result.trace,
        'request_sha256': result.answer.request_sha256, 'owner_prompt': request.user,
        'refs': [dict(ref._asdict()) for ref in result.refs]}
    assert _verified_semantic_candidate(context, 'associate', saved) == result.refs
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='run')
    assert ask(readonly).value == result.value and transport.calls == 3
    for tamper in ('source', 'segments', 'group', 'raw_control', 'delivery'):
        broken = deepcopy(saved)
        material_step = broken['trace'][0]
        if tamper == 'source':
            material_step['paths']['content_sha256'] = '0' * 64
        elif tamper == 'segments':
            material_step['paths']['materials'][0]['segments'][supplement_ref]['text'] += '伪造'
        elif tamper == 'group':
            material_step['paths']['materials'][0]['candidate_key'] = '别的候选'
        elif tamper == 'raw_control':
            material_step['value']['requests'][0]['candidate_key'] = '别的候选'
        else:
            broken['trace'][1]['ref']['prompts']['user'] = request.user
        material_step['paths']['materials_sha256'] = digest(material_step['paths']['materials'])
        with pytest.raises(ValueError):
            _verified_semantic_candidate(context, 'associate', broken)
    with pytest.raises(ValueError, match='尚无实际送达'):
        replay_semantic_trace(result.trace[:1], index=index, material_candidates=('关联候选',))


@pytest.mark.stage_scope('stage2')
@pytest.mark.parametrize('extra_query', (False, True))
def test_associate_owner_revision_inherits_authenticated_material_schema_and_replays_free(tmp_path, extra_query):
    from contracts.schema.source_story import STORY_VERSION
    from contracts.schema.workflow import OwnerIssueV1
    from contracts.story_paths import ASSOCIATION, SOURCE_STORY
    from novel.stages.owner_revision import discover_owner_issues, OwnerRevisionRequired, read_owner_issue_file
    from tests.story_facilities import pipeline_materials
    _chapters, index, wires = pipeline_materials()
    book = deepcopy(wires['book'])
    initial, supplemental, later = (next(iter(index.chapter(1))), *tuple(index.chapter(2))[:2])
    key = '两命题是否同一'
    book['unresolved'] = [{'key': key, 'kind': 'proposition_identity', 'left': '托付孩子',
        'candidates': ['来客受邀'], 'question': '是否同一命题？', 'evidence_refs': [initial]}]
    def query(ref):
        return {'kind': 'need_materials', 'requests': [{'candidate_key': key,
            'operation': 'read_segments', 'segment_ids': [ref], 'adjacent_paragraphs': 0}]}
    answer = {'contract_version': STORY_VERSION, 'verdicts': [{'kind': 'identity', 'key': key,
        'verdict': 'distinct', 'right': None, 'reason': '原始依据', 'evidence_refs': [supplemental]}]}
    case = make_pipeline(tmp_path, scripts={'book': [book], 'associate': [query(supplemental), answer]})
    case.run(through='stage2')
    repo = case.boundary.repository
    baseline = repo.read_model(ASSOCIATION)
    assert case.calls.count('associate') == 2 and baseline.supplemental_evidence
    paid_before = [row for row in read_cost_snapshot(tmp_path, readonly=True)[0] if not row['cached']]
    current = case
    for ordinal in range(1, 2 if extra_query else 3):
        issue = OwnerIssueV1(code=f'关联依据需具体化{ordinal}', item=key, message='依据说明须保留补证并更具体',
            context={'path': ['verdicts', 0, 'reason'], 'operation': 'replace'})
        with pytest.raises(OwnerRevisionRequired):
            discover_owner_issues(repo, owner='associate', node='str06.stage2.associate',
                raised_by='str06.stage2.compile', issues=[issue])
        script = ([query(later)] if extra_query else []) + [
            {'patches': [{'path': ['verdicts', 0, 'reason'], 'value': f'第{ordinal}次按补证具体说明'}]}]
        resumed = make_pipeline(tmp_path, existing=current, scripts={'associate': script})
        sent = []
        transport = resumed.boundary.session.transport
        class Capture:
            def send(self, request):
                sent.append(request)
                return transport.send(request)
        resumed.boundary.session.transport = Capture()
        resumed.run(through='stage2')
        assert resumed.calls == ['associate'] * len(script)
        assert read_owner_issue_file(repo, 'associate').status == 'resolved'
        for request in sent:
            assert '【限定工作单元的修订】' in request.user
            schema, _end = json.JSONDecoder().raw_decode(request.user.split('【输出 schema】', 1)[1])
            assert supplemental in json.dumps(schema, ensure_ascii=False)
            assert index.evidence[supplemental].text in request.user
        if extra_query:
            schema, _end = json.JSONDecoder().raw_decode(sent[-1].user.split('【输出 schema】', 1)[1])
            assert later in json.dumps(schema, ensure_ascii=False)
        observation = repo.read_model(ASSOCIATION)
        assert observation.verdicts[0].reason == f'第{ordinal}次按补证具体说明'
        assert observation.verdicts[0].evidence_refs == (supplemental,)
        assert repo.read_model(SOURCE_STORY).evidence[supplemental] == index.evidence[supplemental]
        saved = repo.inspect('runtime/semantic_candidates/associate.json')
        assert saved['kind'] == 'repair'
        readonly = make_pipeline(tmp_path, existing=resumed, readonly=True)
        readonly.run(through='stage2')
        assert readonly.calls == []
        assert repo.read_model(ASSOCIATION) == observation
        current = resumed
    paid_after = [row for row in read_cost_snapshot(tmp_path, readonly=True)[0] if not row['cached']]
    assert len(paid_after) - len(paid_before) == 2, '只购买新修订/新补料，不重购已认证基线'


@pytest.mark.parametrize('index_alias', (True, 1.0))
def test_narrow_patch_rejects_boolean_and_float_aliases_of_authorized_integer(tmp_path, index_alias):
    from contracts.schema.source_story import StoryIssue
    from novel.stages.stage2.story import StoryCompileError
    compiled = []
    def compile_value(wire):
        compiled.append(deepcopy(wire))
        parsed = _Payload.model_validate(wire)
        if parsed.entries[1] == 'bad':
            raise StoryCompileError((StoryIssue(code='broken_entry', owner='preserve', item='entry1',
                path=('entries', 1), message='只允许修复整数位置 1'),))
        return parsed
    class Script:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            value = {'entries': ['good', 'bad']} if self.calls == 1 else {
                'patches': [{'path': ['entries', index_alias], 'value': 'good'}]}
            return ChatResponse(content=json.dumps(value), finish_reason='stop', model=request.model)
    transport = Script()
    _base, repo, ledger, session = _make_session(tmp_path, transport)
    with pytest.raises(ExecutionError, match='不接受布尔或浮点数') as caught:
        request_json(session, 'preserve', _request(), compile_value)
    assert caught.value.code == 'story_candidate'
    assert compiled == [{'entries': ['good', 'bad']}]
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='book')
    with pytest.raises(ExecutionError, match='不接受布尔或浮点数'):
        request_json(readonly, 'preserve', _request(), compile_value)
    assert transport.calls == 2, '非法补丁失败也须从原已购响应免费复现'


def test_narrow_repair_can_fill_missing_fixed_field_without_replacing_parent(tmp_path):
    class Entry(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        label: str
        frozen: str
    class Payload(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        entries: dict[str, Entry]
    class Script:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            if self.calls == 1:
                value = {'entries': {'既有条目': {'frozen': '原值'}}}
            else:
                frozen, _end = json.JSONDecoder().raw_decode(request.user.split('【冻结候选与窄修权限】\n', 1)[1])
                assert frozen['allowed_paths'] == [['entries', '既有条目', 'label']]
                value = {'patches': [{'path': ['entries', '既有条目', 'label'], 'value': '补齐字段'}]}
            return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    _base, _repo, _ledger, session = _make_session(tmp_path, transport)
    result = request_json(session, 'preserve', _request(), Payload.model_validate)
    assert result.value == {'entries': {'既有条目': {'label': '补齐字段', 'frozen': '原值'}}}
    assert transport.calls == 2


def test_explicit_revision_stops_when_one_of_same_item_defects_is_outside_scope(tmp_path):
    from contracts.schema.source_story import StoryIssue
    from novel.stages.stage2.story import StoryCompileError
    class Entry(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        allowed: str
        frozen: str
    class Payload(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        entries: list[Entry]
    def compile_value(wire):
        parsed = Payload.model_validate(wire)
        if parsed.entries[0].allowed == 'bad':
            raise StoryCompileError(tuple(StoryIssue(code='coupled_invalid', owner='preserve', item='entry0',
                path=('entries', 0, field), message=f'{field} 的约束不成立') for field in ('allowed', 'frozen')))
        return parsed
    class Script:
        def __init__(self):
            self.calls = 0
        def send(self, request):
            self.calls += 1
            assert self.calls <= 2, '存在授权外错误时须在下一次付费前停止'
            value = {'entries': [{'allowed': 'good', 'frozen': 'good'}]} if self.calls == 1 else {
                'patches': [{'path': ['entries', 0, 'allowed'], 'value': 'bad'}]}
            return ChatResponse(content=json.dumps(value), finish_reason='stop', model=request.model)
    transport = Script()
    _base, repo, ledger, session = _make_session(tmp_path, transport)
    first = request_json(session, 'preserve', _request(), compile_value)
    seed = RevisionSeed(kind='repair', baseline=first.value, paths=(('entries', 0, 'allowed'),),
        refs=first.refs, trace=first.trace, append_paths=(), remove_paths=())
    revision_request = _request(user='只修改显式授权字段 allowed')
    with pytest.raises(ExecutionError) as caught:
        request_json(session, 'preserve', revision_request, compile_value, revision_seed=seed)
    assert caught.value.code == 'revision_scope_required'
    assert [row['path'] for row in caught.value.context['diagnostics']] == [
        ('entries', 0, 'allowed'), ('entries', 0, 'frozen')]
    assert first.value == {'entries': [{'allowed': 'good', 'frozen': 'good'}]}
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='book')
    with pytest.raises(ExecutionError) as replayed:
        request_json(readonly, 'preserve', revision_request, compile_value, revision_seed=seed)
    assert replayed.value.code == 'revision_scope_required' and transport.calls == 2


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


@pytest.mark.parametrize('raw', (
    '{"entries":[NaN]}', '{"entries":[Infinity]}', '{"entries":[-Infinity]}',
    '{"entries":[],"entries":["重复键"]}', '{"entries":["原句","],"other":[]}',
))
def test_strict_json_rejects_non_json_and_ambiguous_syntax(raw):
    from novel.llm.story_requests import strict_json
    with pytest.raises(ValueError):
        strict_json(raw)


def test_ambiguous_json_is_kept_raw_and_requires_a_real_format_answer(tmp_path):
    from novel.llm.story_requests import strict_json
    malformed = '{"entries":["原句","],"other":[]}'
    class Script:
        def __init__(self):
            self.requests = []
        def send(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ChatResponse(content=malformed, finish_reason='stop', model=request.model)
            assert '【格式修复原始回答】' in request.user
            frozen = json.loads(request.user.split('【格式修复原始回答】\n', 1)[1])
            assert frozen['raw_response'] == malformed
            return ChatResponse(content='{"entries":["原句"]}', finish_reason='stop', model=request.model)
    script = Script()
    base, repo, _, session = _make_session(tmp_path, script)
    result = request_json(session, 'preserve', _request(), _Payload.model_validate, repository=repo)
    assert len(script.requests) == 2, '歧义语法不能由代码猜补后当成已购合法回答'
    assert result.value == {'entries': ['原句']}
    assert strict_json(script.requests[1].user.split('【格式修复原始回答】\n', 1)[1])['raw_response'] == malformed
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book')
    first_turn = unit._read().turns[0]
    attempt = first_turn.attempts[0]
    durable = unit.attempt_request_payload(first_turn.turn_id, attempt.attempt_id)
    assert durable['response']['content'] == malformed
    records = dict(unit.acceptance_records())
    assert records[attempt.attempt_id]['response_kind'] == 'format_failure'
    assert records[attempt.attempt_id]['accepted'] is False
    assert all(row['semantic'] == 'unver' for row in records.values())


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


def test_chapter_schema_closes_full_evidence_ids_and_only_delivered_context():
    from jsonschema import Draft202012Validator
    from novel.llm.story_requests import _material
    from novel.stages.stage2.story import chapter_prompt, chapter_schema, StoryCompileError
    from tests.story_facilities import _chapter_wire, _materials
    index = _materials()
    local, prior = tuple(index.chapter(2)), next(iter(index.chapter(1)))
    prior_number = index.evidence_numbers[prior]
    wire = _chapter_wire(index, 2)
    wire['context_needs'] = [{'item': '前因', 'question': '需核对先前行动', 'source_refs': [prior_number]}]
    initial = chapter_schema(index, 2)
    assert list(Draft202012Validator(initial).iter_errors(wire)), '未送达的前章段不可自报为已见证据'
    material = _material(index, {'operation': 'read_segments', 'segment_ids': [prior_number]}, 2,
                         reference_mode='chapter_numbers')
    expanded = chapter_schema(index, 2, materials=(material,))
    Draft202012Validator(expanded).validate(wire)
    event = next(iter(wire['events'].values()))
    for invalid in (prior_number, 0, -1, True, '1', 'e002_1', max(index.evidence_ids) + 1):
        event['evidence_refs'] = [invalid]
        assert list(Draft202012Validator(expanded).iter_errors(wire)), invalid
    event['evidence_refs'] = [index.evidence_numbers[local[0]]]
    prompt = chapter_prompt(index, 2, materials=(material,))
    assert 'JSON正整数' in prompt and '全书阅读序固定' in prompt
    assert index.evidence[prior].text not in prompt, '补料由实际冻结送达块提供，不把前文重新塞进本章原文'
    forged = deepcopy(material)
    forged['segments'][0]['text'] += '伪造'
    with pytest.raises(StoryCompileError):
        chapter_schema(index, 2, materials=(forged,))
    future = _material(index, {'operation': 'read_segments', 'segment_ids': [index.evidence_numbers[local[0]]]},
                       None, reference_mode='chapter_numbers')
    with pytest.raises(StoryCompileError):
        chapter_schema(index, 1, materials=(future,))


@pytest.mark.parametrize('invalid', (True, 1.0, 0, -1, '1', 'e001_00001', 99999))
def test_chapter_material_query_uses_strict_integer_addresses_and_owner_selects_mode(invalid):
    from novel.llm.story_requests import _material, material_reference_mode
    from tests.story_facilities import _materials
    index = _materials()
    canonical = next(iter(index.chapter(2)))
    number = index.evidence_numbers[canonical]
    assert material_reference_mode('ch02') == 'chapter_numbers'
    assert material_reference_mode('book') == material_reference_mode('associate') == 'canonical'
    with pytest.raises(ValueError, match='严格正整数'):
        _material(index, {'operation': 'read_segments', 'segment_ids': [invalid]}, 2,
                  reference_mode=material_reference_mode('ch02'))
    shown = _material(index, {'operation': 'read_segments', 'segment_ids': [number]}, 2,
                      reference_mode=material_reference_mode('ch02'))
    assert shown['reference_mode'] == 'chapter_numbers'
    assert shown['segments'][0]['ref'] == number and number > 1, '补料不能按单次查询重新编号'
    assert shown['query']['segment_ids'] == [number]
    canonical_shown = _material(index, {'operation': 'read_segments', 'segment_ids': [canonical]}, 2,
                                reference_mode=material_reference_mode('book'))
    assert canonical_shown['reference_mode'] == 'canonical'
    assert list(canonical_shown['segments']) == [canonical], '同查询上界的book仍使用正式地址'
    with pytest.raises(ValueError, match='超出查询章范围'):
        _material(index, {'operation': 'read_segments', 'segment_ids': [number]}, 1,
                  reference_mode='chapter_numbers')


def test_chapter_patch_schema_projects_real_paths_local_values_and_reference_enum(tmp_path):
    from jsonschema import Draft202012Validator
    from novel.stages.stage2.story import chapter_prompt, chapter_schema, compile_chapter
    from tests.story_facilities import _chapter_wire, _materials
    index = _materials()
    initial = _chapter_wire(index, 2)
    event_key = next(iter(initial['events']))
    initial['events'][event_key]['evidence_refs'] = [max(index.evidence_ids) + 1]
    initial['context_needs'] = [{'item': '前因', 'question': '需核对前文',
                                'source_refs': [index.evidence_numbers[next(iter(index.chapter(1)))]]}]
    paths = [('events', event_key, 'evidence_refs'), ('context_needs', 0)]
    corrected_need = {**initial['context_needs'][0], 'source_refs': []}
    patch = {'patches': [{'path': list(paths[0]),
                         'value': [index.evidence_numbers[next(iter(index.chapter(2)))]]},
                         {'path': list(paths[1]), 'value': corrected_need}]}
    class Script:
        def __init__(self):
            self.requests = []
        def send(self, request):
            self.requests.append(request)
            value = initial if len(self.requests) == 1 else patch
            return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    _base, repo, ledger, session = _make_session(tmp_path, transport)
    request = _request(user=chapter_prompt(index, 2))
    compiler = lambda value: compile_chapter(value, index=index, chapter_ordinal=2)
    options = {'index': index, 'max_chapter': 2, 'repository': repo,
               'candidate_schema': lambda materials: chapter_schema(index, 2, materials=materials)}
    result = request_json(session, 'ch02', request, compiler, **options)
    assert len(transport.requests) == 2, '已见证据闭集错误和compiler错误须同轮暴露'
    frozen, _end = json.JSONDecoder().raw_decode(
        transport.requests[-1].user.split('【冻结候选与窄修权限】\n', 1)[1])
    assert {tuple(path) for path in frozen['allowed_paths']} == set(paths)
    patch_schema, _end = json.JSONDecoder().raw_decode(
        transport.requests[-1].user.split('【本轮补丁回答 schema】', 1)[1])
    validator = Draft202012Validator(patch_schema)
    validator.validate(patch)
    assert list(validator.iter_errors(result.value)), '完整候选不能冒充补丁回答'
    wrong_ref = deepcopy(patch)
    wrong_ref['patches'][0]['value'] = ['e2_1']
    assert list(validator.iter_errors(wrong_ref)), '局部value仍必须使用合法整数地址'
    wrong_shape = deepcopy(patch)
    wrong_shape['patches'][1]['value'] = '整条context_need不能退成字符串'
    assert list(validator.iter_errors(wrong_shape))
    duplicate = {'patches': [patch['patches'][0], patch['patches'][0]]}
    assert list(validator.iter_errors(duplicate)), '每个const路径必须恰答一次'
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='book')
    assert request_json(readonly, 'ch02', request, compiler, **options).value == result.value
    assert len(transport.requests) == 2


@pytest.mark.stage_scope('stage2')
@pytest.mark.parametrize('interrupted', (False, True))
def test_chapter_production_material_continuation_refreshes_patch_schema_and_replays_free(
        tmp_path, monkeypatch, interrupted):
    from contracts.story_paths import CHAPTER_OBSERVATION, chapter_rel
    from jsonschema import Draft202012Validator
    from tests.story_facilities import pipeline_materials
    _chapters, index, wires = pipeline_materials()
    prior = next(iter(index.chapter(1)))
    prior_number = index.evidence_numbers[prior]
    initial = deepcopy(wires['ch02'])
    initial['context_needs'] = [{'item': '前文', 'question': '核对原先托付',
                                'source_refs': [max(index.evidence_ids) + 1]}]
    query = {'kind': 'need_materials', 'requests': [{'operation': 'read_segments',
        'segment_ids': [prior_number], 'adjacent_paragraphs': 0}]}
    fixed = {**initial['context_needs'][0], 'source_refs': [prior_number]}
    patch = {'patches': [{'path': ['context_needs', 0], 'value': fixed}]}
    case = make_pipeline(tmp_path, scripts={'ch02': [initial, query, patch]})
    sent = []
    transport = case.boundary.session.transport
    class Capture:
        def send(self, request):
            sent.append(request)
            return transport.send(request)
    case.boundary.session.transport = Capture()
    if interrupted:
        from novel.stages.stage2 import story as engine
        prompt, tripped = engine.chapter_prompt, False
        def interrupt_material_prompt(index, ordinal, *, materials=()):
            nonlocal tripped
            if materials and not tripped:
                tripped = True
                raise OSError('注入：整数补料耐久后、送达请求构造前中断')
            return prompt(index, ordinal, materials=materials)
        monkeypatch.setattr(engine, 'chapter_prompt', interrupt_material_prompt)
        with pytest.raises(OSError, match='整数补料耐久后'):
            case.run(through='stage2')
    case.run(through='stage2')
    chapter_requests = [request for request in sent if '忠实理解第 2 章' in request.user]
    assert len(chapter_requests) == 3
    before, _end = json.JSONDecoder().raw_decode(chapter_requests[1].user.split('【本轮补丁回答 schema】', 1)[1])
    after, _end = json.JSONDecoder().raw_decode(chapter_requests[2].user.split('【本轮补丁回答 schema】', 1)[1])
    assert list(Draft202012Validator(before).iter_errors(patch)), '尚未送达前章材料不能先用前章证据'
    Draft202012Validator(after).validate(patch)
    assert '【已冻结补充材料】' in chapter_requests[2].user
    repo = case.boundary.repository
    observation = repo.read_model(chapter_rel(CHAPTER_OBSERVATION, 2))
    assert observation.context_needs[0].source_refs == (prior,)
    saved = repo.inspect('runtime/semantic_candidates/ch02.json')
    assert [step['kind'] for step in saved['trace']] == ['result', 'material', 'patch']
    material_step = saved['trace'][1]
    assert material_step['paths']['reference_mode'] == 'chapter_numbers'
    projected = material_step['paths']['materials'][0]
    assert projected['query']['segment_ids'] == [prior_number]
    assert projected['segments'] == [{'ref': prior_number, **index.evidence[prior].model_dump(mode='json')}]
    assert not any(ref in json.dumps(projected, ensure_ascii=False) for ref in index.evidence)
    from novel.llm.story_requests import replay_material_trace
    for tamper in ('mode', 'ref', 'query', 'owner'):
        trace = deepcopy(saved['trace'])
        step = trace[1]
        if tamper == 'mode':
            step['paths']['reference_mode'] = 'canonical'
        elif tamper == 'ref':
            step['paths']['materials'][0]['segments'][0]['ref'] = prior_number + 1
        elif tamper == 'query':
            step['value']['requests'][0]['segment_ids'] = [float(prior_number)]
        else:
            step['ref']['owner'] = 'book'
        with pytest.raises(ValueError):
            replay_material_trace(trace, index=index, max_chapter=2, owner='ch02')
    readonly = make_pipeline(tmp_path, existing=case, readonly=True)
    readonly.run(through='stage2')
    assert readonly.calls == [] and repo.read_model(chapter_rel(CHAPTER_OBSERVATION, 2)) == observation


@pytest.mark.stage_scope('stage2')
@pytest.mark.parametrize('material_timing', ('before_format', 'during_format', 'both'))
@pytest.mark.parametrize('interrupted', ('none', 'answer', 'publish'))
def test_chapter_format_recovery_keeps_material_delivery_and_resumes_free(
        tmp_path, monkeypatch, material_timing, interrupted):
    """格式恢复前后取得的补料均须实际送达，耐久回答和完整候选分别可免费恢复。"""
    from contracts.story_paths import CHAPTER_OBSERVATION, SOURCE_STORY, chapter_rel
    from novel.llm.story_requests import replay_material_trace
    from tests.story_facilities import pipeline_materials
    _chapters, index, wires = pipeline_materials()
    prior = tuple(index.chapter(1))[:2]
    def query(ref):
        return {'kind': 'need_materials', 'requests': [{'operation': 'read_segments',
            'segment_ids': [index.evidence_numbers[ref]], 'adjacent_paragraphs': 0}]}
    script, delivered = [], []
    if material_timing in ('before_format', 'both'):
        script.append(query(prior[0]))
        delivered.append(prior[0])
    malformed = '{"未闭合":'
    script.append(ChatResponse(content=malformed, finish_reason='stop',
        usage={'prompt_tokens': 12, 'completion_tokens': 8}))
    if material_timing in ('during_format', 'both'):
        script.append(query(prior[1]))
        delivered.append(prior[1])
    good = deepcopy(wires['ch02'])
    good['context_needs'] = [{'item': '前文', 'question': '核对原先托付',
                            'source_refs': [index.evidence_numbers[ref] for ref in delivered]}]
    script.append(good)
    case = make_pipeline(tmp_path, scripts={'ch02': script})
    repo, session = case.boundary.repository, case.boundary.session
    rel = chapter_rel(CHAPTER_OBSERVATION, 2)
    with monkeypatch.context() as patch:
        if interrupted == 'answer':
            accept = session.record_acceptance
            def interrupt_acceptance(answer, *args, **kwargs):
                if (answer.owner == 'ch02' and kwargs.get('accepted') is True
                        and json.loads(answer.content) == good):
                    raise OSError('注入：格式修复回答耐久后中断')
                return accept(answer, *args, **kwargs)
            patch.setattr(session, 'record_acceptance', interrupt_acceptance)
        elif interrupted == 'publish':
            write = repo.write
            def interrupt_publish(path, value, **kwargs):
                if path == rel:
                    raise OSError('注入：完整章候选发布前中断')
                return write(path, value, **kwargs)
            patch.setattr(repo, 'write', interrupt_publish)
        if interrupted == 'none':
            case.run(node_filter=['str06.stage2.chapters'])
        else:
            with pytest.raises(OSError, match='中断'):
                case.run(node_filter=['str06.stage2.chapters'])
    assert case.calls.count('ch02') == len(script)
    paid_before = [row for row in read_cost_snapshot(tmp_path, readonly=True)[0] if not row['cached']]
    used = read_book_budget(tmp_path, 'book').used
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run(node_filter=['str06.stage2.chapters'])
    assert resumed.calls == [] and read_book_budget(tmp_path, 'book').used == used
    assert [row for row in read_cost_snapshot(tmp_path, readonly=True)[0] if not row['cached']] == paid_before
    saved = repo.inspect('runtime/semantic_candidates/ch02.json')
    materials = replay_material_trace(saved['trace'], index=index, max_chapter=2, owner='ch02')
    assert len(materials) == len(delivered)
    assert repo.inspect_sealed_model(rel).context_needs[0].source_refs == tuple(delivered)
    chapter_requests = [request for request in case.transport.calls if '忠实理解第 2 章' in request.user]
    for request in chapter_requests:
        if '【格式修复原始回答】' in request.user:
            frozen, _end = json.JSONDecoder().raw_decode(request.user.split('【格式修复原始回答】\n', 1)[1])
            assert frozen == {'raw_response': malformed}
    assert chapter_requests[-1].user.endswith('\n【已冻结补充材料】\n' + json.dumps(materials, ensure_ascii=False))
    unit = WorkUnitBudget(tmp_path, 'book', 'str06.stage2.chapters', 'executor', 'chapter:2')
    failures = [record for _attempt, record in unit.acceptance_records()
                if record['response_kind'] == 'format_failure']
    assert failures and all(record['accepted'] is False for record in failures)
    # 真实book消费修复后的章观察，随后完整Stage2重放也不能重购。
    case.run(through='stage2')
    assert case.calls.count('ch02') == len(script)
    source = repo.inspect_sealed_model(SOURCE_STORY)
    readonly = make_pipeline(tmp_path, existing=case, readonly=True)
    readonly.run(through='stage2')
    assert readonly.calls == [] and repo.inspect_sealed_model(SOURCE_STORY) == source


@pytest.mark.stage_scope('stage2')
def test_chapter_format_recovery_refuses_unproven_predecessor_materials(tmp_path):
    """保留材料的恢复绿例不能放宽为只凭错误文案或当前材料猜旧请求。"""
    from novel.llm.story_requests import _format_repair_user, replay_material_trace, strict_json
    from novel.stages.chain import ChainContext, _verify_chapter_format_request
    from tests.story_facilities import pipeline_materials
    _chapters, index, wires = pipeline_materials()
    prior = tuple(index.chapter(1))[:2]
    queries = [{'kind': 'need_materials', 'requests': [{'operation': 'read_segments',
        'segment_ids': [index.evidence_numbers[ref]], 'adjacent_paragraphs': 0}]} for ref in prior]
    malformed = '{"未闭合":'
    case = make_pipeline(tmp_path, scripts={'ch02': [*queries,
        ChatResponse(content=malformed, finish_reason='stop',
                     usage={'prompt_tokens': 12, 'completion_tokens': 8}), wires['ch02']]})
    case.run(node_filter=['str06.stage2.chapters'])
    saved = case.boundary.repository.inspect('runtime/semantic_candidates/ch02.json')
    materials = replay_material_trace(saved['trace'], index=index, max_chapter=2, owner='ch02')
    assert len(materials) == 2
    with pytest.raises(ValueError) as error:
        strict_json(malformed)
    tail = _format_repair_user('', [str(error.value)], malformed)
    ctx = ChainContext(case.boundary.repository, case.ask, case.chapters,
        source_identity=case.index.source_identity, provider=case.boundary.provider,
        model=case.boundary.model, request_profile=case.boundary.profile)
    ref = saved['trace'][-1]['ref']
    _verify_chapter_format_request(ctx, 'ch02', ref, tail, materials=materials)
    forged = deepcopy(materials)
    forged[0]['segments'][0]['text'] += '伪造'
    for invalid in ((), materials[:1], tuple(reversed(materials)), forged):
        with pytest.raises(ValueError, match='真实坏响应前驱'):
            _verify_chapter_format_request(ctx, 'ch02', ref, tail, materials=invalid)
    with pytest.raises(ValueError, match='真实坏响应前驱'):
        _verify_chapter_format_request(ctx, 'ch02', ref,
            _format_repair_user('', [str(error.value)], '{"另一个回答":'), materials=materials)


@pytest.mark.parametrize('control', ('upstream', 'validation_stop', 'source_issues'))
def test_candidate_schema_guard_preserves_control_exceptions_without_buying_local_patch(tmp_path, control):
    from contracts.schema.source_story import SourceObservationIssue, StoryIssue
    from novel.llm.paid_requests import ValidationStop
    from novel.stages.stage2.story import SourceObservationIssueRequired
    class Upstream(ValueError):
        node = 'str06.stage2.names'
        issues = (StoryIssue(code='upstream_fact', owner='names', item='人物',
                             path=('entries',), message='须由其他owner修订'),)
    failure = (Upstream('源姓名错误') if control == 'upstream' else
        ValidationStop('当前范围不足', code='scope_required', node='str06.stage2.chapters',
            stage='stage2', item='chapter:2', phase='accept') if control == 'validation_stop' else
        SourceObservationIssueRequired((SourceObservationIssue(chapter_ordinal=1, item='事件',
            path=('events', '事件'), operation='replace', kind='unsupported', message='前章观察错漏',
            evidence_refs=('e001_00001',)),)))
    class Script:
        calls = 0
        def send(self, request):
            self.calls += 1
            return ChatResponse(content='{"entries": []}', finish_reason='stop', model=request.model)
    transport = Script()
    _base, _repo, _ledger, session = _make_session(tmp_path, transport)
    def compiler(_value):
        raise failure
    # 动态schema本身也有局部错误，但它不得覆盖原有控制异常、改派当前owner窄修。
    schema = {'type': 'object', 'required': ['required_field'],
              'properties': {'required_field': {'type': 'string'}}}
    with pytest.raises(type(failure)) as caught:
        request_json(session, 'ch02', _request(), compiler, candidate_schema=lambda _materials: schema)
    assert caught.value is failure and transport.calls == 1


def test_chapter_candidate_schema_required_field_keeps_exact_free_repair_path(tmp_path):
    from jsonschema import Draft202012Validator
    from novel.stages.stage2.story import chapter_prompt, chapter_schema, compile_chapter
    from tests.story_facilities import _chapter_wire, _materials
    index = _materials()
    initial = _chapter_wire(index, 1)
    del initial['context_needs']
    patch = {'patches': [{'path': ['context_needs'], 'value': []}]}
    class Script:
        def __init__(self):
            self.requests = []
        def send(self, request):
            self.requests.append(request)
            return ChatResponse(content=json.dumps(initial if len(self.requests) == 1 else patch,
                ensure_ascii=False), finish_reason='stop', model=request.model)
    transport = Script()
    _base, repo, ledger, session = _make_session(tmp_path, transport)
    request = _request(user=chapter_prompt(index, 1))
    compiler = lambda value: compile_chapter(value, index=index, chapter_ordinal=1)
    options = {'index': index, 'max_chapter': 1,
               'candidate_schema': lambda materials: chapter_schema(index, 1, materials=materials)}
    result = request_json(session, 'ch01', request, compiler, **options)
    assert len(transport.requests) == 2 and result.value['context_needs'] == []
    user = transport.requests[-1].user
    frozen, _end = json.JSONDecoder().raw_decode(user.split('【冻结候选与窄修权限】\n', 1)[1])
    assert frozen['allowed_paths'] == [['context_needs']]
    assert all(row['path'] == ['context_needs'] for row in frozen['diagnostics'])
    schema, _end = json.JSONDecoder().raw_decode(user.split('【本轮补丁回答 schema】', 1)[1])
    Draft202012Validator(schema).validate(patch)
    variants = schema['properties']['patches']
    assert all(set(row) == {'$ref'} for row in variants['items']['oneOf'])
    assert all(set(row['contains']) == {'$ref'} for row in variants['allOf'])
    readonly = PaidRequestSession(repo, ledger, transport, yes=False, allow_source_export=False,
                                  provider='fake', sid='book')
    assert request_json(readonly, 'ch01', request, compiler, **options).value == result.value
    assert len(transport.requests) == 2


def test_chapter_joint_repair_keeps_courtyard_name_and_changes_only_its_evidence(tmp_path):
    from contracts.schema.source_story import STORY_VERSION
    from novel.core.source_index import build_source_index
    from novel.stages.stage2.story import chapter_prompt, chapter_schema, compile_chapter
    index = build_source_index([{'chapter_ordinal': 1,
        'text': '侍从把客人请了出去。\n院子里重新安静下来。'}])
    first, second = (index.evidence_numbers[ref] for ref in index.chapter(1))
    wire = {'contract_version': STORY_VERSION,
        'referents': {'院子': {'kind': 'place', 'label': '送客后重新安静的院子',
                            'surface': '院子', 'evidence_refs': [first]}},
        'events': {'送客': {'summary': '侍从把客人请出去', 'participants': [],
            'temporal': {'frame': 'current', 'anchor_event': None}, 'scene_boundary': 'new',
            'evidence_refs': [first], 'claims': [], 'causal_hints': [], 'thread_hints': []}},
        'context_needs': []}
    class Script:
        def __init__(self):
            self.requests = []
        def send(self, request):
            self.requests.append(request)
            value = wire
            if len(self.requests) == 2:
                frozen, _end = json.JSONDecoder().raw_decode(
                    request.user.split('【冻结候选与窄修权限】\n', 1)[1])
                paths = {tuple(path) for path in frozen['allowed_paths']}
                assert paths == {('referents', '院子', 'surface'), ('referents', '院子', 'evidence_refs')}
                assert all('主体含义' in row['message'] and '截词' in row['message']
                           for row in frozen['diagnostics'])
                value = {'patches': [
                    {'path': ['referents', '院子', 'surface'], 'value': '院子'},
                    {'path': ['referents', '院子', 'evidence_refs'], 'value': [second]}]}
            return ChatResponse(content=json.dumps(value, ensure_ascii=False),
                                finish_reason='stop', model=request.model)
    transport = Script()
    _base, _repo, _ledger, session = _make_session(tmp_path, transport)
    compiler = lambda value: compile_chapter(value, index=index, chapter_ordinal=1)
    result = request_json(session, 'ch01', _request(user=chapter_prompt(index, 1)), compiler,
        index=index, max_chapter=1,
        candidate_schema=lambda materials: chapter_schema(index, 1, materials=materials))
    expected = deepcopy(wire)
    expected['referents']['院子']['evidence_refs'] = [second]
    assert result.value == expected and len(transport.requests) == 2
    assert result.value['referents']['院子']['surface'] == '院子'
    assert result.value['referents']['院子']['kind'] == 'place'
    assert wire['referents']['院子']['evidence_refs'] == [first]


def test_chapter_candidate_unknown_field_stops_before_buying_unauthorized_delete(tmp_path):
    from novel.stages.stage2.story import chapter_prompt, chapter_schema, compile_chapter
    from tests.story_facilities import _chapter_wire, _materials
    index = _materials()
    initial = {**_chapter_wire(index, 1), 'undeclared_field': '不能靠replace移除未知固定字段'}
    class Script:
        calls = 0
        def send(self, request):
            self.calls += 1
            return ChatResponse(content=json.dumps(initial, ensure_ascii=False),
                                finish_reason='stop', model=request.model)
    transport = Script()
    _base, _repo, _ledger, session = _make_session(tmp_path, transport)
    with pytest.raises(ExecutionError) as caught:
        request_json(session, 'ch01', _request(user=chapter_prompt(index, 1)),
            lambda value: compile_chapter(value, index=index, chapter_ordinal=1),
            candidate_schema=lambda materials: chapter_schema(index, 1, materials=materials))
    assert caught.value.code == 'repair_schema_scope' and transport.calls == 1
    assert caught.value.context['allowed_paths'] == (('undeclared_field',),)
