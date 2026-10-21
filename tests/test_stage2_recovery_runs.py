#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选链恢复运行：workflow 身份记录的落盘形状、损坏 fail-closed 与 epoch 复用。

⚠ **F8b-3 载荷切换**：原文件是旧链 Stage2 blueprint 的验证后 checkpoint／一次性
batch workpack／run_id 闭环（`novel.llm.stage2_runs` 与 `novel.stages.stage2.extract`，
约 35 条用例）。那套机器随旧链整层退役：新链的「恢复运行」是**候选根自身的
workflow 身份生命周期**（`novel/llm/paid_requests.py`）＋ 付费会话的续跑
（`novel/llm/paid_requests.py::PaidRequestSession`）。旧反例的历史字节见 Git；
本文件只保留新链上仍成立的
恢复运行纪律，并不复述已由 `tests/test_str06_f7a_wiring.py` 覆盖的
init/resume/drift 正向面（那条已钉住「同代码复用、漂移滚动、源身份拒绝」）。

【本文件钉的三件事】
  ① `workflow_to_disk`／`workflow_from_disk` round-trip 后身份逐字相等；
  ② 记录形状损坏（缺键／错型／非 dict）一律 fail-closed，不猜、不补默认值；
  ③ 复用路径里 ledger 的 parent 恒为当前 epoch 的 session_run_id——
     每次 CLI 调用是新 run，epoch 是稳定窗口（Q-10 的账不能断）。
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from novel.errors import ProvenanceRefused
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.paid_requests import (
    WORKFLOW_REL,
    init_or_resume_candidate_workflow,
    open_candidate_epoch,
    roll_candidate_epoch,
    workflow_from_disk,
    workflow_to_disk,
)
from novel.store import Str06Repository
from tests.story_facilities import make_context

_SOURCE = 'b' * 64


def _repo(tmp_path, *, node='str06.stage2.book'):
    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(
        tmp_path / 'run', ledger=CostLedger(path=None), node=node))
    return repo


def _init(repo, *, source_identity=_SOURCE, code='code-a', created_at=1759000000,
          readonly=False):
    return init_or_resume_candidate_workflow(
        repository=repo, base=repo.root.parent, code=code, root_sha1='a' * 40,
        source_identity=source_identity, source_content_sha256='c' * 64, task_id='task-1', configured_model='fake',
        created_at=created_at, readonly=readonly)


def _epoch_identity(workflow) -> tuple[str, int]:
    """`(epoch_id, ordinal)`——并发续跑对账用的最小身份对。"""
    return workflow.epoch.epoch_id, workflow.epoch.ordinal


def _ledger_parent(context) -> tuple[str, str]:
    """`(ledger 路径, parent run)`——并发下父身份不许分叉（Q-10 的账不能断）。"""
    return str(context.ledger.path), context.ledger.parent_run_id


def test_workflow_record_round_trips_byte_for_byte_identity(tmp_path):
    """落盘形状 round-trip 后 epoch／契约表／源身份逐字相等（不允许静默补默认值）。"""
    repo = _repo(tmp_path)
    workflow, _context = _init(repo)
    stored = repo.read(WORKFLOW_REL)
    assert workflow_from_disk(stored) == workflow
    assert workflow_from_disk(workflow_to_disk(workflow)) == workflow
    assert stored['epoch']['epoch_id'] == workflow.epoch.epoch_id
    assert stored['contract_identity'] == workflow.contract_identity


@pytest.mark.parametrize('old_version', ('story-v1', 'story-v2'))
def test_previous_story_epoch_cannot_be_opened_rolled_or_resumed(tmp_path, old_version):
    options = dict(code_version='code-a', task_id='task-1', session_run_id='run-1', created_at=1759000000)
    with pytest.raises(ValueError, match='契约版本'):
        open_candidate_epoch(**options, contract_version=old_version)
    current = open_candidate_epoch(**options)
    assert current.contract_version == 'story-v3'
    with pytest.raises(ValueError, match='契约版本'):
        roll_candidate_epoch(current, reason='repair', **options, contract_version=old_version)
    with pytest.raises(ValueError, match='契约版本'):
        roll_candidate_epoch(current._replace(contract_version=old_version), reason='repair', **options)
    repo = _repo(tmp_path)
    _init(repo)
    old = repo.read(WORKFLOW_REL)
    # 即使保留当前表摘要，也不能把旧epoch伪装成可采用的当前运行。
    old['epoch']['contract_version'] = old_version
    path = repo.path(WORKFLOW_REL)
    path.write_bytes(json.dumps(old, ensure_ascii=False).encode('utf-8'))
    before = {file: file.read_bytes() for file in tmp_path.rglob('*') if file.is_file()}
    with pytest.raises(ProvenanceRefused, match='复校失败'):
        _init(repo)
    assert all(file.read_bytes() == raw for file, raw in before.items())


@pytest.mark.parametrize('tamper', [
    {'drop': 'epoch'},
    {'drop': 'contract_identity'},
    {'drop': 'source_identity'},
    {'drop': 'created_at'},
    {'blank': 'epoch'},
    {'not_dict': True},
    {'epoch_ordinal': 'one'},
    {'epoch_reason': 'because'},
])


def test_corrupted_workflow_record_fails_closed_before_any_resume(tmp_path, tamper):
    """形状损坏的 workflow 记录是关闭式拒绝，不是「当没记录重开」也不是补默认值。

    ⚠ 重开会把旧根的已购采用结论接到新输入上；补默认值会让 epoch 身份悄悄变形。
    两种都等于拿旧账给新跑背书，所以这里只接受 `ProvenanceRefused`——
    装载期缺键/非 dict 由 `workflow_from_disk` 翻成「形状损坏」，epoch 内字段
    型错误由 `check_candidate_workflow` 复校成「epoch 形状损坏」问题。
    """
    repo = _repo(tmp_path)
    _init(repo)
    record = repo.read(WORKFLOW_REL)
    if tamper.get('not_dict'):
        broken = ['not', 'a', 'dict']
    elif 'epoch_ordinal' in tamper:
        broken = dict(record)
        broken['epoch'] = {**record['epoch'], 'ordinal': tamper['epoch_ordinal']}
    elif 'epoch_reason' in tamper:
        broken = dict(record)
        broken['epoch'] = {**record['epoch'], 'reason': tamper['epoch_reason']}
    elif tamper.get('blank'):
        broken = dict(record)
        broken[tamper['blank']] = {}
    else:
        broken = dict(record)
        broken.pop(tamper['drop'])
    repo.path(WORKFLOW_REL).write_text(
        json.dumps(broken, ensure_ascii=False), encoding='utf-8')

    with pytest.raises(ProvenanceRefused, match='形状损坏|复校失败'):
        _init(repo)


def test_resume_keeps_ledger_parent_on_the_current_epoch_session(tmp_path):
    """复用不开新 epoch；ledger parent 恒为当前 epoch 的 session_run_id。

    （Q-10：每次 CLI 调用是一条新 run，epoch 是稳定窗口——parent 断了，
    累计额度与工作单元账就再也归不到同一本书上。）
    """
    repo = _repo(tmp_path)
    first, first_ctx = _init(repo)
    second, second_ctx = _init(repo)

    assert second.epoch.epoch_id == first.epoch.epoch_id
    assert first_ctx.ledger.parent_run_id == first.epoch.session_run_id
    assert second_ctx.ledger.parent_run_id == second.epoch.session_run_id
    assert second_ctx.ledger.parent_run_id == first_ctx.ledger.parent_run_id
    assert second_ctx.ledger.path == ledger_path(repo.root.parent), (
        '候选根的 ledger 必须落在本根 `_service/` 下——父身份换根就归不到同一本书')


def test_readonly_resume_reuses_the_epoch_and_refuses_nothing_extra(tmp_path):
    """只读复用（检查/监控路径）同样原样接当前 epoch，不滚动、不拒。"""
    repo = _repo(tmp_path)
    first, _first_ctx = _init(repo)
    again, readonly_ctx = _init(repo, created_at=1759000001, readonly=True)

    assert again.epoch.epoch_id == first.epoch.epoch_id
    assert readonly_ctx.readonly is True
    assert readonly_ctx.ledger.parent_run_id == first.epoch.session_run_id


def test_concurrent_resume_of_one_root_converges_on_a_single_epoch(tmp_path):
    """同一候选根被多个执行者同时续跑：epoch 与 ledger 父身份必须收敛到一份。

    恢复运行的并发面：盘上 workflow 记录是唯一真相源，同代码下复用路径不写盘，
    因此多少个线程同时 init/resume 都只能读到**同一份**身份；ledger 的 parent
    也不许按线程分叉——分叉了累计额度与工作单元账就再也归不到同一本书（Q-10）。
    """
    import threading

    repo = _repo(tmp_path)
    first, _first_ctx = _init(repo)
    seen: list[tuple[str, int]] = []
    parents: list[tuple[str, str]] = []
    lock = threading.Lock()

    def resume() -> None:
        workflow, context = _init(repo)
        with lock:
            seen.append(_epoch_identity(workflow))
            parents.append(_ledger_parent(context))

    threads = [threading.Thread(target=resume) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == [_epoch_identity(first)] * len(threads), (
        '并发续跑给出了不同 epoch——身份真相源出现了第二个落点')
    assert len(set(parents)) == 1, f'ledger 父身份按线程分叉了：{sorted(set(parents))}'


def _buy_chapter_before_diagnostic_change(tmp_path, monkeypatch, *, task_change=None, interrupt=False,
                                         format_failure=False):
    """同一v3整数任务改变诊断顺序，仍经真实付费边界保存完整候选。"""
    from contracts.story_paths import CHAPTER_OBSERVATION, chapter_rel
    from novel.stages.stage2 import story as engine
    from novel.llm import story_requests
    from tests.story_facilities import make_pipeline, pipeline_materials
    _chapters, index, wires = pipeline_materials()
    good = deepcopy(wires['ch01'])
    good['context_needs'] = [{'item': '来客', 'question': '来客为何出现？',
                              'source_refs': [index.evidence_numbers[next(iter(index.chapter(1)))]]}]
    outside = max(index.evidence_ids) + 1
    bad = deepcopy(good)
    bad['referents']['叙述者']['evidence_refs'] = [outside]
    bad['context_needs'][0]['source_refs'] = [outside + 1]
    scripts = {'ch01': [bad,
        {'patches': [{'path': ['referents', '叙述者', 'evidence_refs'],
                      'value': good['referents']['叙述者']['evidence_refs']}]},
        {'patches': [{'path': ['context_needs', 0], 'value': good['context_needs'][0]}]}]}
    if format_failure:
        from novel.llm.transport import ChatResponse
        scripts['ch01'].insert(0, ChatResponse(content='{"未闭合":', finish_reason='stop',
            usage={'prompt_tokens': 12, 'completion_tokens': 8}))
    case = make_pipeline(tmp_path, scripts=scripts)
    current_schema, current_prompt, current_compile = engine.chapter_schema, engine.chapter_prompt, engine.compile_chapter
    def schema_for_task(index, ordinal, *, materials=()):
        schema = current_schema(index, ordinal, materials=materials)
        if task_change == 'schema':
            schema['description'] = '只记录开场事件，省略后续内容'
        return schema
    def prompt_for_task(index, ordinal, *, materials=()):
        prompt = current_prompt(index, ordinal, materials=materials)
        prefix = prompt.rsplit('\n【输出 schema】', 1)[0]
        if task_change == 'task':
            prefix += '\n只记录本章第一个事件，省略其余事件。'
        elif task_change == 'format':
            prefix += '\n【引用格式约束】\n只记录本章第一个事件，省略其余事件。'
        return prefix + '\n【输出 schema】' + json.dumps(schema_for_task(index, ordinal, materials=materials),
                                                          ensure_ascii=False, separators=(',', ':'))
    def old_compile(wire, *, index, chapter_ordinal):
        if chapter_ordinal == 1:
            if wire['referents']['叙述者']['evidence_refs'] == [outside]:
                raise engine.StoryCompileError((engine._issue('evidence_outside_scope', 'chapters',
                    ('referents', '叙述者', 'evidence_refs'), '首轮只发现核心证据错误'),))
            if wire['context_needs'][0]['source_refs'] == [outside + 1]:
                raise engine.StoryCompileError((engine._issue('evidence_outside_scope', 'chapters',
                    ('context_needs', 0, 'source_refs'), '第二轮才发现context证据错误'),))
        return current_compile(wire, index=index, chapter_ordinal=chapter_ordinal)
    rel = chapter_rel(CHAPTER_OBSERVATION, 1)
    with monkeypatch.context() as patch:
        patch.setattr(engine, 'chapter_schema', schema_for_task)
        patch.setattr(engine, 'chapter_prompt', prompt_for_task)
        patch.setattr(engine, 'compile_chapter', old_compile)
        # 模拟同契约早期接受器仅报告compiler首批诊断；实际任务和schema字节保持当前版本。
        patch.setattr(story_requests, '_compile_candidate', lambda value, compiler, **_options: compiler(value))
        if interrupt:
            write = case.boundary.repository.write
            def interrupted_write(path, value, **kwargs):
                if path == rel:
                    raise OSError('注入：完整候选接受后、正式章观察发布前断电')
                return write(path, value, **kwargs)
            patch.setattr(case.boundary.repository, 'write', interrupted_write)
            with pytest.raises(OSError, match='正式章观察发布前'):
                case.run(node_filter=['str06.stage2.chapters'])
        else:
            case.run(node_filter=['str06.stage2.chapters'])
    assert case.calls.count('ch01') == 3 + bool(format_failure)
    return case, good, rel


@pytest.mark.stage_scope('stage2')
@pytest.mark.parametrize('interrupt', (False, True))
def test_chapter_complete_paid_candidate_recompiles_after_diagnostic_change(
        tmp_path, monkeypatch, interrupt):
    """同契约诊断汇总变化不应让完整已购候选从坏首答重新修一遍。"""
    from novel.stages.stage2 import story as engine
    from tests.story_facilities import make_pipeline
    case, good, rel = _buy_chapter_before_diagnostic_change(tmp_path, monkeypatch, interrupt=interrupt)
    repository = case.boundary.repository
    record_path = repository.path('runtime/semantic_candidates/ch01.json')
    before_record, before_ledger = record_path.read_bytes(), ledger_path(tmp_path).read_bytes()
    saved = repository.inspect('runtime/semantic_candidates/ch01.json')
    assert saved['wire'] == good and [step['kind'] for step in saved['trace']] == ['result', 'patch', 'patch']
    current_compile, checked = engine.compile_chapter, []
    def compile_complete(wire, **options):
        if options['chapter_ordinal'] == 1:
            checked.append(deepcopy(wire))
            assert wire == good, '免费恢复不应先重编历史坏首答'
        return current_compile(wire, **options)
    monkeypatch.setattr(engine, 'compile_chapter', compile_complete)
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run(node_filter=['str06.stage2.chapters'])
    assert resumed.calls == [] and checked
    assert record_path.read_bytes() == before_record
    assert ledger_path(tmp_path).read_bytes() == before_ledger
    assert repository.inspect_sealed_model(rel).context_needs[0].source_refs == tuple(
        case.index.evidence_ids[number] for number in good['context_needs'][0]['source_refs'])


@pytest.mark.stage_scope('stage2')
@pytest.mark.parametrize('task_change', ('task', 'schema', 'format'))
def test_chapter_complete_paid_candidate_refuses_different_task_even_with_same_source(
        tmp_path, monkeypatch, task_change):
    from tests.story_facilities import make_pipeline
    case, _good, _rel = _buy_chapter_before_diagnostic_change(tmp_path, monkeypatch, task_change=task_change)
    resumed = make_pipeline(tmp_path, existing=case)
    before = ledger_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match='任务|schema|协议'):
        resumed.run(node_filter=['str06.stage2.chapters'])
    assert resumed.calls == [] and ledger_path(tmp_path).read_bytes() == before


@pytest.mark.stage_scope('stage2')
@pytest.mark.parametrize('old_version', ('story-v1', 'story-v2'))
def test_chapter_complete_paid_candidate_rejects_previous_story_versions_without_purchase(
        tmp_path, monkeypatch, old_version):
    from tests.story_facilities import make_pipeline
    case, _good, _rel = _buy_chapter_before_diagnostic_change(tmp_path, monkeypatch)
    repo = case.boundary.repository
    saved = repo.inspect('runtime/semantic_candidates/ch01.json')
    saved['contract_version'] = old_version
    repo.write('runtime/semantic_candidates/ch01.json', saved)
    before_candidate = repo.path('runtime/semantic_candidates/ch01.json').read_bytes()
    resumed = make_pipeline(tmp_path, existing=case)
    before_ledger = ledger_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match='版本'):
        resumed.run(node_filter=['str06.stage2.chapters'])
    assert resumed.calls == []
    assert ledger_path(tmp_path).read_bytes() == before_ledger
    assert repo.path('runtime/semantic_candidates/ch01.json').read_bytes() == before_candidate


@pytest.mark.stage_scope('stage2')
def test_chapter_complete_candidate_replays_authenticated_format_recovery_without_rebuying(
        tmp_path, monkeypatch):
    from tests.story_facilities import make_pipeline
    case, good, rel = _buy_chapter_before_diagnostic_change(tmp_path, monkeypatch, format_failure=True)
    before = ledger_path(tmp_path).read_bytes()
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run(node_filter=['str06.stage2.chapters'])
    assert resumed.calls == [] and ledger_path(tmp_path).read_bytes() == before
    assert resumed.boundary.repository.inspect_sealed_model(rel).context_needs[0].source_refs == tuple(
        case.index.evidence_ids[number] for number in good['context_needs'][0]['source_refs'])


@pytest.mark.stage_scope('stage2')
def test_chapter_complete_candidate_replays_owner_revision_with_automatic_narrow_repair(tmp_path):
    from contracts.schema.workflow import OwnerIssueV1
    from novel.stages.owner_revision import discover_owner_issues, OwnerRevisionRequired, read_owner_issue_file
    from tests.story_facilities import make_pipeline, pipeline_materials
    _chapters, _index, wires = pipeline_materials()
    first = deepcopy(wires['ch01'])
    first['referents']['本章来客']['surface'] = '我猜来客'
    case = make_pipeline(tmp_path, scripts={'ch01': [first]})
    case.run(through='stage2')
    issue = OwnerIssueV1(code='称呼需忠实', item='本章来客', message='来客称呼取连续原文称呼',
        context={'path': ['referents', '本章来客'], 'operation': 'replace'})
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(case.boundary.repository, owner='ch01', node='str06.stage2.chapters',
                              raised_by='str06.stage2.compile', issues=[issue])
    bad = deepcopy(wires['ch01']['referents']['本章来客'])
    bad['surface'] = '来客/姐姐'
    scripts = {'ch01': [{'patches': [{'path': ['referents', '本章来客'], 'value': bad}]},
        {'patches': [{'path': ['referents', '本章来客'],
                      'value': wires['ch01']['referents']['本章来客']}]}],
        'book': [{'contract_version': 'story-v3', 'kind': 'book_repair', 'changes': [
            {'path': ['source_issues'], 'operation': 'set', 'value': [],
             'reason': '局部称呼修订不改变整书人物统一或命题，复验原空问题集',
             'evidence_refs': [_index.evidence_ids[number]
                              for number in wires['ch01']['referents']['本章来客']['evidence_refs']]}]}]}
    corrected = make_pipeline(tmp_path, existing=case, scripts=scripts)
    corrected.run(through='stage2')
    assert corrected.calls.count('ch01') == 2
    assert read_owner_issue_file(corrected.boundary.repository, 'ch01').status == 'resolved'
    saved = corrected.boundary.repository.inspect('runtime/semantic_candidates/ch01.json')
    assert saved['kind'] == 'repair' and [step['kind'] for step in saved['trace']] == ['result', 'repair', 'repair']
    before = ledger_path(tmp_path).read_bytes()
    resumed = make_pipeline(tmp_path, existing=corrected, readonly=True)
    resumed.run(node_filter=['str06.stage2.chapters'])
    assert resumed.calls == [] and ledger_path(tmp_path).read_bytes() == before


@pytest.mark.stage_scope('stage2')
@pytest.mark.parametrize('drift', ('profile', 'wire', 'paths', 'compiler', 'ledger_tail'))
def test_chapter_complete_paid_candidate_closes_identity_and_validation_failures_without_purchase(
        tmp_path, monkeypatch, drift):
    from novel.stages.stage2 import story as engine
    from tests.story_facilities import make_pipeline
    case, _good, _rel = _buy_chapter_before_diagnostic_change(tmp_path, monkeypatch)
    repository = case.boundary.repository
    saved = repository.inspect('runtime/semantic_candidates/ch01.json')
    options = {}
    if drift == 'profile':
        options['request_profile'] = replace(case.boundary.profile, reasoning_effort='low')
    elif drift == 'wire':
        saved['wire']['events']['抱婴儿入屋']['summary'] = '捏造的采用结果'
    elif drift == 'paths':
        saved['trace'][1]['paths'] = [['referents', '本章来客', 'evidence_refs']]
    elif drift == 'compiler':
        current_compile = engine.compile_chapter
        def reject(wire, **kwargs):
            if kwargs['chapter_ordinal'] == 1:
                raise engine.StoryCompileError((engine._issue('new_constraint', 'chapters',
                    ('events',), '当前完整编译仍不合法'),))
            return current_compile(wire, **kwargs)
        monkeypatch.setattr(engine, 'compile_chapter', reject)
    if drift in ('wire', 'paths'):
        repository.write('runtime/semantic_candidates/ch01.json', saved)
    resumed = make_pipeline(tmp_path, existing=case)
    if drift == 'ledger_tail':
        with ledger_path(tmp_path).open('ab') as stream:
            stream.write(b'{"uncommitted":')
    before = ledger_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        resumed.run(node_filter=['str06.stage2.chapters'], **options)
    assert resumed.calls == [] and ledger_path(tmp_path).read_bytes() == before


@pytest.mark.stage_scope('stage2')
@pytest.mark.parametrize('frozen', (False, True))
def test_chapter_owner_revision_checks_complete_schema_before_freezing_or_replaying_seed(
        tmp_path, monkeypatch, frozen):
    """先前接受器漏验的未见前章引用，不能靠新修订先购买一轮再暴露。"""
    from contracts.schema.workflow import OwnerIssueV1
    from contracts.story_paths import CHAPTER_OBSERVATION, chapter_rel
    from novel.llm import story_requests
    from novel.llm.paid_requests import ValidationStop
    from novel.stages.owner_revision import discover_owner_issues, OwnerRevisionRequired, read_owner_issue_file
    from tests.story_facilities import make_pipeline, pipeline_materials
    _chapters, index, wires = pipeline_materials()
    prior = next(iter(index.chapter(1)))
    unseen = deepcopy(wires['ch02'])
    unseen['context_needs'] = [{'item': '前因', 'question': '前章依据待核对',
                              'source_refs': [index.evidence_numbers[prior]]}]
    case = make_pipeline(tmp_path, scripts={'ch02': [unseen]})
    with monkeypatch.context() as patch:
        # 保留真实当前任务/schema/响应字节，仅模拟早期接受器漏做动态schema复验。
        patch.setattr(story_requests, '_compile_candidate', lambda value, compiler, **_options: compiler(value))
        case.run(node_filter=['str06.stage2.chapters'])
    repo = case.boundary.repository
    issue = OwnerIssueV1(code='前因问题表述需准确', item='前因', message='具体说明需要核对哪项前因',
        context={'path': ['context_needs', 0, 'question'], 'operation': 'replace',
                 'evidence_refs': [prior]})
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, owner='ch02', node='str06.stage2.chapters',
                              raised_by='str06.stage2.compile', issues=[issue])
    record = read_owner_issue_file(repo, 'ch02')
    request_rel = f'runtime/revision_requests/ch02/{record.fingerprint}.json'
    if frozen:
        preparing = make_pipeline(tmp_path, existing=case)
        ask = preparing.ask
        def interrupt_before_purchase(owner, *args, **kwargs):
            if owner == 'ch02':
                raise OSError('注入：原修订seed已冻结、任何新请求尚未发出')
            return ask(owner, *args, **kwargs)
        preparing.ask = interrupt_before_purchase
        with monkeypatch.context() as patch:
            patch.setattr(story_requests, '_compile_candidate', lambda value, compiler, **_options: compiler(value))
            with pytest.raises(OSError, match='seed已冻结'):
                preparing.run(node_filter=['str06.stage2.chapters'])
        assert preparing.calls == [] and repo.path(request_rel).is_file()
        control, _end = json.JSONDecoder().raw_decode(
            repo.inspect(request_rel)['prompt'].split('【限定工作单元的修订】\n', 1)[1])
        assert control['issues'][0]['context']['evidence_refs'] == [index.evidence_numbers[prior]]
        assert read_owner_issue_file(repo, 'ch02').issues[0].context['evidence_refs'] == [prior]
    rel = chapter_rel(CHAPTER_OBSERVATION, 2)
    preserve = {path: repo.path(path).read_bytes() for path in (
        'runtime/semantic_candidates/ch02.json', rel, *((request_rel,) if frozen else ()))}
    resumed = make_pipeline(tmp_path, existing=case)
    before_ledger = ledger_path(tmp_path).read_bytes()
    with pytest.raises(ValidationStop) as caught:
        resumed.run(node_filter=['str06.stage2.chapters'])
    failure = caught.value
    assert failure.code == 'revision_scope_required' and failure.item == 'chapter:2'
    assert any(tuple(row['path']) == ('context_needs', 0, 'source_refs', 0)
               for row in failure.context['baseline_issues'])
    assert resumed.calls == [] and ledger_path(tmp_path).read_bytes() == before_ledger
    assert all(repo.path(path).read_bytes() == raw for path, raw in preserve.items())
    assert repo.path(request_rel).is_file() is frozen
