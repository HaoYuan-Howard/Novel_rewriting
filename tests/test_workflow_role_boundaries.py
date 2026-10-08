"""RC-18～25：职责边界从真实接受、回派、请求与正式产物验证。"""
from copy import deepcopy
import json

import pytest

from novel.stages.chain import ChainContext, run_node, run_workflow
from novel.stages.owner_revision import OwnerRevisionRequired, read_owner_issue_file
from novel.stages.stage2 import compile_chapter_observation
from tests.str06_facilities import json_ask, synthetic_ref, text_ask
from tests.str06_faults import looped_full_chain_wires
from tests.test_str06_body import _chapters, _make_session
from tests.test_str06_r3_selfheal import _ScriptedTransport, _json_session, _request


def _prepared(tmp_path, *, through_design=False, wires=None, before_associate=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    wires = wires or looped_full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    nodes = ['str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
             'str06.stage2.associate', 'str06.stage2.compile']
    if through_design:
        nodes += ['str06.stage4.preserve', 'str06.stage4.body_design']
    if before_associate:
        nodes = nodes[:3]
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=lambda o, p: None,
                 budget=2000, through='stage4', node_filter=tuple(nodes))
    ctx = ChainContext(repository=repo, chapters=_chapters(), ask=json_ask(session),
                       ask_text=text_ask(session), ask_body=lambda o, p: None,
                       budget=2000, provider='fake', model='fake')
    return wires, repo, session, transport, ctx


def test_rc18_duplicate_close_rejected_by_chapter_acceptance():
    wire = looped_full_chain_wires()['ch02']
    wire['beats'][0]['loops'] *= 2
    original = deepcopy(wire)
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=['e02_01'], chapter=2)
    assert observation is None and any('重复 close' in e for e in errors)
    assert wire == original
    wire['beats'][0]['loops'].pop()
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=['e02_01'], chapter=2)
    assert observation is not None and not errors


def _chapter_v3(wire, chapter):
    from novel.stages.chain import build_evidence
    from novel.stages.stage2.slim import slim_chapter_wire
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=build_evidence(_chapters()), chapter=chapter)
    assert observation is not None and not errors
    return slim_chapter_wire(observation.model_dump(mode='json'))


@pytest.mark.parametrize('same_beat', [False, True])
def test_rc18_close_before_local_open_rejected_and_located(same_beat):
    from novel.stages.chain import build_evidence
    from novel.stages.stage2.chapters import chapter_patch_locate
    wires = looped_full_chain_wires()
    wire = _chapter_v3(wires['ch01'], 1)
    opened = deepcopy(wire['beats'][0]['loops'][0])
    closed = deepcopy(wires['ch02']['beats'][0]['loops'][0])
    # v8 起两拍需要两个不同的 start：给章 1 的闭集补一段合成段。
    evidence = {**build_evidence(_chapters()),
                'e01_02': {'chapter': 1, 'text': '芸娘回房歇下。'}}
    if same_beat:
        wire['beats'][0]['loops'] = [closed, opened]
    else:
        later = deepcopy(wire['beats'][0])
        later.pop('new_scene', None)
        later.pop('change', None)
        later.pop('know', None)
        later['start'] = 'e01_02'
        wire['beats'][0]['loops'] = [closed]
        wire['beats'].append(later)
    original = deepcopy(wire)
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert observation is None and errors
    issue, = errors
    assert issue.owner == 'ch01' and issue.scope == 'chapter:1'
    assert issue.context['violation'] == 'close_before_open'
    assert issue.context['loop_path'] == ('beats', 0, 'loops', 0)
    assert chapter_patch_locate(wire, errors, evidence_ids=evidence, chapter=1) == [
        (('beats', 0), errors)]
    assert wire == original
    # 同拍顺序也明确；无本地open的close仍是合法跨章观察。
    if same_beat:
        wire['beats'][0]['loops'] = [opened, closed]
    else:
        wire['beats'][0]['loops'] = [opened]
        wire['beats'][1]['loops'] = [closed]
    assert compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)[0] is not None
    cross = _chapter_v3(wires['ch02'], 2)
    assert compile_chapter_observation(cross, evidence_ids=evidence, chapter=2)[0] is not None


@pytest.mark.parametrize('detector', ['str06.stage2.associate', 'str06.stage2.compile'])
def test_rc19_saved_close_before_open_routes_and_recovers(tmp_path, detector):
    _, repo, session, transport, ctx = _prepared(tmp_path)
    rel = 'stage2/observations/ch01.json'
    original = repo.read_model(rel)
    bad = original.model_dump(mode='json')
    later = deepcopy(bad['beats'][0])
    later.update(effects=[], cognition=[], scene_boundary='continue')
    bad['beats'][0]['loops'] = [{'ref': 'q_yu', 'op': 'close', 'resolution': '旧案已解。'}]
    bad['beats'].append(later)
    repo.write(rel, type(original).model_validate(bad), owners=('ch01',),
               refs=(synthetic_ref('ch01'),))
    before = len(transport.calls)
    with pytest.raises(OwnerRevisionRequired) as exc:
        run_node(detector, ctx)
    assert exc.value.node == 'str06.stage2.chapters'
    record = read_owner_issue_file(repo, 'ch01')
    assert record.issues[0].context['violation'] == 'close_before_open'
    assert record.issues[0].item == 'chapter:1'
    assert len(transport.calls) == before and read_owner_issue_file(repo, 'associate') is None
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=lambda o, p: None, budget=2000,
                 through='stage2', node_filter=('str06.stage2.chapters',))
    assert transport.calls[before:] == ['ch01']
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=lambda o, p: None, budget=2000,
                 through='stage2', node_filter=('str06.stage2.names',
                     'str06.stage2.associate', 'str06.stage2.compile'))
    from novel.store import Str06Repository
    restarted = Str06Repository(repo.root)
    assert read_owner_issue_file(restarted, 'ch01').status == 'resolved'
    assert restarted.read_model('stage2/blueprints/chapter_blueprints.json').chapters


def test_rc20_repeated_loop_instances_have_stable_distinct_identities():
    from novel.stages.stage2 import chapter_loop_issues
    from novel.core.validation import validation_identity
    closed = {'ref': 'q', 'op': 'close', 'resolution': '已解。'}
    beats = [{'loops': [deepcopy(closed) for _ in range(3)]}]
    issues = chapter_loop_issues(beats, chapter=2)
    identities = {validation_identity(i) for i in issues}
    assert len(issues) == len(identities) == 2
    # 无关条目和拍位移动不改变同一重复实例的身份。
    shifted = deepcopy(beats)
    shifted[0]['loops'].insert(0, {'ref': 'other', 'op': 'close', 'resolution': '已解。'})
    shifted.insert(0, {'loops': []})
    assert {validation_identity(i) for i in chapter_loop_issues(shifted, chapter=2)} == identities
    beats[0]['loops'].pop()
    remaining = chapter_loop_issues(beats, chapter=2)
    assert {validation_identity(i) for i in remaining} < identities
    beats[0]['loops'][0]['resolution'] = '改写收束展示。'
    assert [validation_identity(i) for i in chapter_loop_issues(beats, chapter=2)] == [
        validation_identity(i) for i in remaining]


@pytest.mark.parametrize('patch', [False, True])
def test_rc20_repeated_close_shrinks_through_acceptance_and_replays_free(tmp_path, patch):
    from novel.llm.paid_requests import ask_json
    from novel.stages.stage2.chapters import chapter_patch_locate
    candidates = []
    base = _chapter_v3(looped_full_chain_wires()['ch02'], 2)
    for count in (3, 2, 1):
        wire = deepcopy(base)
        wire['beats'][0]['loops'] *= count
        candidates.append(wire)
    contents = [json.dumps(candidates[0])]
    contents.extend(json.dumps({'patches': [{'path': 'beats[0]', 'value': wire['beats'][0]}]})
                    if patch else json.dumps(wire) for wire in candidates[1:])
    transport = _ScriptedTransport(contents)
    _, _, session = _json_session(tmp_path, transport)
    def ask():
        return ask_json(session, 'ch02', _request('章观察原始请求'),
            lambda w: compile_chapter_observation(w, evidence_ids=['e02_01'], chapter=2)[1],
            patch=(lambda w, e: chapter_patch_locate(
                w, e, evidence_ids=['e02_01'], chapter=2)) if patch else None)
    data, _ = ask()
    assert data == candidates[-1] and len(transport.calls) == 3
    replay, _ = ask()
    assert replay == data and len(transport.calls) == 3


def test_rc20_repeated_close_shrinks_in_saved_owner_fingerprint(tmp_path):
    _, repo, session, transport, ctx = _prepared(tmp_path)
    rel = 'stage2/observations/ch02.json'
    original = repo.read_model(rel)
    before = len(transport.calls)
    fingerprints = []
    for count in (3, 2):
        raw = original.model_dump(mode='json')
        raw['beats'][0]['loops'] *= count
        repo.write(rel, type(original).model_validate(raw), owners=('ch02',),
                   refs=(synthetic_ref('ch02'),))
        with pytest.raises(OwnerRevisionRequired):
            run_node('str06.stage2.compile', ctx)
        record = read_owner_issue_file(repo, 'ch02')
        assert record.rounds_total == 4 - count and not record.no_progress
        fingerprints.append(record.fingerprint)
        # 相同盘面重放不得重复起轮。
        with pytest.raises(OwnerRevisionRequired):
            run_node('str06.stage2.compile', ctx)
        assert read_owner_issue_file(repo, 'ch02').rounds_total == record.rounds_total
    assert len(set(fingerprints)) == 2 and len(transport.calls) == before
    repo.write(rel, original, owners=('ch02',), refs=(synthetic_ref('ch02'),))
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=lambda o, p: None, budget=2000,
                 through='stage2', node_filter=('str06.stage2.names',
                     'str06.stage2.associate', 'str06.stage2.compile'))
    assert read_owner_issue_file(repo, 'ch02').status == 'resolved'
    assert read_owner_issue_file(repo, 'ch02').rounds_total == 2
    assert repo.read_model('stage2/blueprints/chapter_blueprints.json').chapters


@pytest.mark.parametrize('detector', ['str06.stage2.associate', 'str06.stage2.compile'])
def test_rc19_saved_duplicate_routes_to_chapter_without_associate_purchase(tmp_path, detector):
    wires, repo, session, transport, ctx = _prepared(tmp_path)
    rel = 'stage2/observations/ch02.json'
    original = repo.read_model(rel)
    bad = original.model_dump(mode='json')
    bad['beats'][0]['loops'] *= 2
    repo.write(rel, type(original).model_validate(bad), owners=('ch02',),
               refs=(synthetic_ref('ch02'),))
    before = len(transport.calls)
    with pytest.raises(OwnerRevisionRequired) as exc:
        run_node(detector, ctx)
    assert exc.value.node == 'str06.stage2.chapters'
    record = read_owner_issue_file(repo, 'ch02')
    assert record.raised_by == detector
    assert all(i.item == 'chapter:2' for i in record.issues)
    assert len(transport.calls) == before
    assert read_owner_issue_file(repo, 'associate') is None
    # 重启读取同一正式账，重复发现不增加轮数。
    from novel.store import Str06Repository
    restarted = Str06Repository(repo.root)
    assert read_owner_issue_file(restarted, 'ch02').fingerprint == record.fingerprint
    with pytest.raises(OwnerRevisionRequired):
        run_node(detector, ctx)
    assert read_owner_issue_file(repo, 'ch02').rounds_total == 1
    # 真实章owner消费回派要求并修订；未受影响章采用历史回答，不购买。
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=lambda o, p: None, budget=2000,
                 through='stage2', node_filter=('str06.stage2.chapters',))
    assert transport.calls[before:] == ['ch02']
    assert read_owner_issue_file(repo, 'ch02').status == 'open'
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=lambda o, p: None, budget=2000,
                 through='stage2', node_filter=('str06.stage2.names',
                     'str06.stage2.associate', 'str06.stage2.compile'))
    assert read_owner_issue_file(repo, 'ch02').status == 'resolved'
    assert restarted.read_model('stage2/blueprints/chapter_blueprints.json').chapters


def test_rc20_identity_does_not_depend_on_wording_or_order():
    from novel.core.validation import validation_identity
    from novel.stages.stage2.associate import association_issue
    first = [association_issue('loop_unpaired', (ref,), f'关联未决：{ref}')
             for ref in ('ch02.a', 'ch02.b')]
    reordered = [association_issue('loop_unpaired', (ref,), f'展示文案改变：{ref}')
                 for ref in ('ch02.b', 'ch02.a')]
    assert len({validation_identity(i) for i in first}) == 2
    assert {validation_identity(i) for i in first} == {
        validation_identity(i) for i in reordered}


def test_rc19_three_digit_chapter_can_persist_and_resume_its_revision(tmp_path):
    from contracts.schema.workflow import OwnerIssueV1, route_owner_issue
    from novel.stages.owner_revision import discover_owner_issues, owner_product_digest, revision_gate
    from novel.stages.stage2 import chapter_loop_issues
    from novel.store import Str06Repository
    _, repo, _, _, _ = _prepared(tmp_path)
    raw = repo.read_model('stage2/observations/ch02.json').model_dump(mode='json')
    raw['beats'][0]['loops'] *= 2
    from contracts.schema.observation import ChapterObservation
    repo.context.begin_run()
    repo.write('stage2/observations/ch100.json', ChapterObservation.model_validate(raw),
               owners=('ch100',), refs=(synthetic_ref('ch100'),))
    issue, = chapter_loop_issues(raw['beats'], chapter=100)
    action, node = route_owner_issue(issue.code)
    assert action == 'revise_upstream'
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, owner=issue.owner, node=node, raised_by='str06.stage2.compile',
            issues=[OwnerIssueV1(code=issue.code, item=issue.scope, message=str(issue),
                                context={'validation_identity': issue.identity})])
    reopened = Str06Repository(repo.root)
    record = read_owner_issue_file(reopened, 'ch100')
    assert record.issues[0].item == 'chapter:100'
    verdict, _ = revision_gate(record.against_sha256, owner_product_digest(reopened, 'ch100'),
                              record.rounds_total, dispatch_pending=record.dispatch_pending)
    assert verdict == 'redo' and record.rounds_total == 1


def test_rc20_real_associate_issues_shrink_and_replay_free(tmp_path):
    wires = looped_full_chain_wires()
    wires['ch01']['beats'][0]['loops'].append(
        {'ref': 'q_other', 'op': 'open', 'question': '旧约何时兑现？'})
    wires['ch02']['beats'][0]['loops'].append(
        {'ref': 'q_other', 'op': 'close', 'resolution': '旧约在今晚兑现。'})
    a = wires['associate']['loop_pairing'][0]
    # v4：两组 open×close ⇒ 四个 loop 候选，逐候选恰答一次（错配判 distinct）。
    full = [a, {'open': 'ch01.q_other', 'close': 'ch02.q_other', 'verdict': 'pair'},
            {'open': 'ch01.q_yu', 'close': 'ch02.q_other', 'verdict': 'distinct'},
            {'open': 'ch01.q_other', 'close': 'ch02.q_yu', 'verdict': 'distinct'}]
    candidates = []
    for pairs in ([], [a], full):
        candidate = deepcopy(wires['associate'])
        candidate['loop_pairing'] = pairs
        candidates.append(candidate)
    # 第 4 通＝因果窄调用（两组 loop 配对同拍，去重后恰一候选，默认否决）。
    cross_cause = {'protocol_version': 'stage2-cross-cause-v1',
                   'causes': [{'from_beat': 'ch01.b1', 'to_beat': 'ch02.b1',
                               'kind': 'none'}]}
    _, repo, session, _, ctx = _prepared(tmp_path, wires=wires, before_associate=True)
    transport = _ScriptedTransport([json.dumps(w) for w in candidates]
                                   + [json.dumps(cross_cause)])
    session.transport = transport
    run_node('str06.stage2.associate', ctx)
    saved = repo.read_model('stage2/observations/associate.json').loop_pairing
    assert sum(1 for row in saved if row.verdict == 'pair') == 2
    assert len(transport.calls) == 4
    run_node('str06.stage2.associate', ctx)
    assert len(transport.calls) == 4
    run_node('str06.stage2.compile', ctx)
    assert len(repo.read_model('stage2/blueprints/chapter_blueprints.json').chapters) == 2


@pytest.mark.parametrize('patch', [False, True])
def test_rc21_actual_revision_request_allows_correcting_observation(tmp_path, patch):
    from novel.llm.paid_requests import ask_json
    transport = _ScriptedTransport([
        '{"subject":"wrong"}',
        '{"patches":[{"path":"subject","value":"right"}]}' if patch
        else '{"subject":"right"}'])
    _, _, session = _json_session(tmp_path, transport)
    ask_json(session, 'ch02', _request('原文：甲向乙借钱'),
             lambda w: ['请改正借钱双方'] if w['subject'] == 'wrong' else [],
             patch=(lambda w, e: [(('subject',), e)]) if patch else None)
    prompt = transport.calls[-1].user
    assert '不得新增或改换事实' not in prompt
    assert '原文' in prompt and '观察' in prompt and '纠正' in prompt


@pytest.mark.parametrize('missing', [False, True])
@pytest.mark.parametrize('domain', ['knowledge', 'world'])
def test_rc22_hook_request_contains_referenced_proposition_or_stops(tmp_path, missing, domain):
    from contracts.schema.grounding import BriefProposition, BookConstraint
    from novel.stages.owner_revision import MechanicalDefectStop
    _, repo, _, _, ctx = _prepared(tmp_path, through_design=True)
    kp = 'kp_1234567890abcdef' if domain == 'knowledge' else 'wc_test'
    statement = '夫君像是看穿了什么' if domain == 'knowledge' else '族规禁止隐瞒婚约'
    body = repo.read_model('stage4/design/body_design.json')
    raw = body.model_dump(mode='json')
    raw['scenes'][0]['beats'][0]['target_delta'].append({
        'domain': domain, 'subjects': [], 'holder': 'yun_niang' if domain == 'knowledge' else None,
        'key': kp, 'op': 'set', 'value': 'true', 'scope': 'book',
        'modality': None if domain == 'knowledge' else 'actual',
        'knowledge_mode': 'suspected' if domain == 'knowledge' else None})
    brief = repo.read_model('stage4/grounding/reconstruction_brief.json')
    if not missing:
        field = 'propositions' if domain == 'knowledge' else 'book_constraints'
        item = (BriefProposition(kp_id=kp, statement=statement) if domain == 'knowledge'
                else BookConstraint(constraint_id=kp, statement=statement))
        brief = brief.model_copy(update={field: (*getattr(brief, field), item)})
        repo.context.begin_run()
        repo.read_model('runtime/preserve_checkpoint.json')
        repo.write('stage4/grounding/reconstruction_brief.json', brief,
                   owners=('preserve',), refs=(synthetic_ref('preserve'),))
    repo.context.begin_run()
    repo.read_model('stage4/grounding/reconstruction_brief.json')
    repo.write('stage4/design/body_design.json', type(body).model_validate(raw),
               owners=('design',), refs=(synthetic_ref('design'),))
    captured = []
    class Captured(Exception):
        pass
    def ask(owner, prompt, validate, patch=None, **kwargs):
        captured.append(prompt)
        raise Captured()
    ctx.ask = ask
    with pytest.raises(MechanicalDefectStop if missing else Captured):
        run_node('str06.stage4.hook_design', ctx)
    if missing:
        assert not captured
    else:
        assert kp in captured[0] and statement in captured[0]


def test_rc24_design_and_writer_instructions_match_time_contract():
    from novel.stages.common import DESIGN_RULES, BODY_WRITER_SYSTEM
    assert 'B 的接受拍必须早于 A 的接受拍' not in DESIGN_RULES
    assert '同场' in DESIGN_RULES and '交错' in DESIGN_RULES
    assert '逐拍结果是本章结束后' not in BODY_WRITER_SYSTEM
    assert '对应拍' in BODY_WRITER_SYSTEM and '替代' in BODY_WRITER_SYSTEM


def test_rc24_successive_states_reach_their_own_beats():
    from tests.test_str06_body import _green_design, _green_binding
    from tests.test_str06_hook import _characters
    from novel.stages.stage4 import compile_execution_plan, compile_writer_book_ir
    from novel.stages.common import project_chapter_packet, render_chapter_packet
    body = _green_design()
    raw = body.model_dump(mode='json')
    for index, value in enumerate(('屋内', '院内')):
        raw['scenes'][0]['beats'][index]['target_delta'].append({
            'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
            'key': 'scene_location', 'op': 'set', 'value': value, 'scope': 'scene',
            'modality': 'actual'})
    body = type(body).model_validate(raw)
    binding = _green_binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors
    chars = _characters()
    ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
        total_budget=2000, length_band=(0.6, 1.3), book_title='测试',
        projection=chars.name_projection(), structure=chars.name_structure)
    prompt = render_chapter_packet(project_chapter_packet(ir, 1))
    assert any('第1拍' in line and '屋内' in line for line in prompt.splitlines())
    assert any('第2拍' in line and '院内' in line for line in prompt.splitlines())
    assert '本章结束后须成立' not in prompt


def test_rc19_legacy_wrong_owner_correction_keeps_history_and_cost(tmp_path, capsys):
    from contracts.schema.workflow import OwnerIssueV1, owner_issues_rel
    from novel.stages.owner_revision import discover_owner_issues
    from novel.stages.issue_recovery import preview_issue_correction, apply_issue_correction
    from novel.llm.ledger import ledger_path
    from contracts.schema.stage1 import MainText
    from contracts.schema import encode_artifact
    from novel.cli.main import main
    _, repo, _, transport, ctx = _prepared(tmp_path)
    association = repo.read_model('stage2/observations/associate.json')
    chapter = repo.read_model('stage2/observations/ch02.json')
    raw = chapter.model_dump(mode='json')
    raw['beats'][0]['loops'] *= 2
    repo.context.begin_run()
    repo.write('stage2/observations/ch02.json', type(chapter).model_validate(raw),
               owners=('ch02',), refs=(synthetic_ref('ch02'),))
    repo.read_model('stage2/observations/ch02.json')
    repo.write('stage2/observations/associate.json', association,
               owners=('associate',), refs=(synthetic_ref('associate'),))
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, owner='associate', node='str06.stage2.associate',
            raised_by='str06.stage2.compile', issues=[OwnerIssueV1(
                code='association_undecided',
                message='第2章拍1 的悬念 loop01 已 close，不得重复 close（R-2.11）')])
    old = read_owner_issue_file(repo, 'associate').model_copy(update={
        'rounds_total': 2, 'no_progress': True, 'dispatch_pending': False})
    repo.write(owner_issues_rel('associate'), old)
    cost_path = ledger_path(repo.root.parent)
    cost_before = cost_path.read_bytes()
    calls_before = len(transport.calls)
    source = repo.root / 'stage1/main_text.json'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(encode_artifact(
        'stage1/main_text.json', MainText.model_validate(_chapters()).model_dump(mode='json'))),
        encoding='utf-8')
    preview = preview_issue_correction(repo, 'associate', budget=2000, chapter_ordinals=(1, 2))
    assert preview['target_owners'] == ['ch02'] and not preview['paid']
    with pytest.raises(ValueError, match='预览已过期'):
        apply_issue_correction(repo, 'associate', budget=2000, chapter_ordinals=(1, 2),
                               expected_sha256='0' * 64, reason='测试过期预览')
    # 正式CLI必须真的允许该owner，并从Stage1取完整章集合；不手工伪造恢复许可。
    argv = ['issue-correction', 'run', '--base', str(repo.root.parent), '--owner', 'associate']
    capsys.readouterr()
    assert main(argv) == 0
    cli_preview = json.loads(capsys.readouterr().out)
    assert cli_preview['target_owners'] == ['ch02']
    assert main([*argv, '--apply', '--subject-sha256', cli_preview['evidence_sha256'],
                 '--reason', '正式章观察已证明旧路由错误']) == 0
    corrected = read_owner_issue_file(repo, 'associate')
    assert corrected.status == 'superseded' and corrected.rounds_total == 2
    assert corrected.corrections[-1].original.issues == old.issues
    with pytest.raises(OwnerRevisionRequired) as exc:
        run_node('str06.stage2.compile', ctx)
    assert exc.value.node == 'str06.stage2.chapters'
    assert cost_path.read_bytes() == cost_before and len(transport.calls) == calls_before


@pytest.mark.parametrize('owner,expected', [
    ('ch02', '纠正本章错误观察'), ('associate', '只能纠正本单位逐候选判定'),
    ('design', '完整义务'), ('hook_design', '命题含义'), ('body_ch02', '写作材料')])
def test_rc21_owner_permissions_reach_full_and_patch_requests(owner, expected):
    from novel.stages.common import semantic_revision_rules
    from novel.llm.paid_requests import revision_user_prompt, patch_user_prompt, PatchTarget
    rules = semantic_revision_rules(owner)
    full = revision_user_prompt('原请求', '{}', ['待修复问题'], revision_rules=rules)
    patch = patch_user_prompt('原请求', [PatchTarget(path=('a',), issues=('待修复问题',))],
                              {'a': 1}, revision_rules=rules)
    assert expected in full and expected in patch
    assert '不得新增或改换事实' not in full + patch
