"""R9 免费反例：材料终检、耐久补丁、采样身份与恢复；模型均为 fake。"""
from __future__ import annotations

import hashlib
import json

import pytest

from novel.llm.paid_requests import ask_json, purchased_baseline
from novel.stages.common import validate_positive_materials, build_writer_packet, project_chapter_packet
from novel.stages.revisions import (
    activate_node_revisions, adopted_patch, preview_owner_patch, revision_states_at,
    sampling_request, stage_revision, resolve_node_revisions,
)
from tests.test_str06_r3_selfheal import _json_session, _ScriptedTransport, _request
from tests.test_str06_body import _green_book_ir


def test_positive_material_leaks_claim_full_names_before_single_char():
    assert not validate_positive_materials(
        {'creative': '苏芸娘等待。'}, released=['苏芸娘'], withheld=['苏'], internal_ids=['girl'])
    errors = validate_positive_materials(
        {'creative': '苏姑娘等待 girl。'}, released=['苏芸娘'], withheld=['苏姑娘'], internal_ids=['girl'])
    assert any('positive_material_leak[creative@' in e for e in errors)
    assert any('internal_id_leak[creative@' in e for e in errors)


def test_renderer_omits_unreleased_voice_and_unresolved_present_person():
    from contracts.schema.design import CharacterVoice
    _body, _binding, _plan, ir = _green_book_ir()
    ir = ir.model_copy(update={'book_brief': ir.book_brief.model_copy(update={
        'character_voices': (*ir.book_brief.character_voices,
                             CharacterVoice(char_id='unreleased', voice='不应提前出现的声线'))})})
    packet = project_chapter_packet(ir, 2)
    ending = packet.chapter.continuity.previous_ending.model_copy(update={
        'present_chars': ('unreleased',)})
    packet = packet.model_copy(update={'chapter': packet.chapter.model_copy(update={
        'continuity': packet.chapter.continuity.model_copy(update={'previous_ending': ending})})})
    view = build_writer_packet(packet)
    assert 'unreleased' not in json.dumps(view.model_dump())
    assert '不应提前出现的声线' not in ' '.join(view.creative_guidance)
    assert '收尾时在场' not in ' '.join(view.continuity)


def _baseline(tmp_path, owner='preserve'):
    transport = _ScriptedTransport(['{"value": 1}'])
    _base, repo, session = _json_session(tmp_path, transport)
    _raw, answer = ask_json(session, owner, _request(), lambda w: [])
    baseline = purchased_baseline(repo.root, owner, answer.request_sha256)
    return repo, session, transport, answer, baseline


def _equal_wire_adoption(tmp_path, evidence_kind):
    """同一 fake 实购回答分别留作工作单元或请求行证据，不伪造购买结论。"""
    from novel.llm.ledger import ledger_path
    from novel.llm.paid_requests import digest
    from tests.test_str06_target import _preserve_wire

    wire = _preserve_wire()
    content = json.dumps(wire, ensure_ascii=False)
    transport = _ScriptedTransport([content])
    source_base, source_repo, source_session = _json_session(tmp_path, transport, 'source')
    request = _request('原合同下的 Preserve 请求')
    _wire, answer = ask_json(source_session, 'preserve', request, lambda w: [])
    root = source_repo.root
    if evidence_kind == 'request_record':
        # 历史 request 行沿用同一实购 attempt 和成本原字节；此根没有工作单元，
        # 让 bind 和 purchased_baseline 都实际走请求行消费面。
        historical_base = tmp_path / 'source-request'
        root = historical_base / 'run'
        (root / 'runtime').mkdir(parents=True)
        source_ledger = ledger_path(source_base)
        target_ledger = ledger_path(historical_base)
        target_ledger.parent.mkdir(parents=True, exist_ok=True)
        target_ledger.write_bytes(source_ledger.read_bytes())
        row = {'identity': answer.request_sha256, 'status': 'answered',
               'attempt_id': answer.attempt_id, 'provider': 'fake',
               'request': request.cache_payload(), 'content': content,
               'content_sha256': digest(content), 'model': answer.model,
               'usage': answer.usage}
        (root / 'runtime' / f'request_{answer.request_sha256}.json').write_text(
            json.dumps(row, ensure_ascii=False), encoding='utf-8')
    else:
        assert evidence_kind == 'work_unit'
        assert not list((root / 'runtime').glob('request_*.json'))
    empty_transport = _ScriptedTransport([])
    candidate_base, repo, session = _json_session(tmp_path, empty_transport, 'candidate')
    session.adopt_roots = (root,)
    session.yes = False
    wire_path = candidate_base / 'preserve_wire.json'
    # 序列化不同仍是同一 JSON 内容；不靠修改模型字段绕过 exact-payload 分支。
    wire_path.write_text(json.dumps(wire, ensure_ascii=False, indent=2), encoding='utf-8')
    return root, repo, session, wire_path, wire, request, answer, transport, empty_transport


@pytest.mark.stage_scope('stage4')
@pytest.mark.parametrize('evidence_kind', ['request_record', 'work_unit'])
def test_equal_wire_cross_identity_owner_patch_stages_and_replays(tmp_path, evidence_kind):
    from dataclasses import replace
    from contracts.schema.workflow import STAGE4_PRESERVE_POLICY_VERSION
    from novel.llm.ledger import ledger_path, read_cost_snapshot
    from novel.llm.paid_requests import AdoptionRef, digest
    from novel.stages.revisions import owner_patch_identity, registered_owner_patch
    from novel.stages.stage4 import assemble_preserve
    from tests.test_str06_target import _source_pair

    root, repo, session, path, wire, request, answer, bought, replay_transport = (
        _equal_wire_adoption(tmp_path, evidence_kind))
    original_ledger = ledger_path(root.parent).read_bytes()
    current_request = replace(request, user=request.user + '；现合同完整重验')
    current = digest({'owner': 'preserve', 'provider': 'fake',
                      'request': current_request.cache_payload()})
    assert current != answer.request_sha256
    note = {'base_response_identity': answer.request_sha256,
            'applies_to_request_sha256': current, 'reason': '等内容按现合同免费复验'}
    baseline_identity, reason = owner_patch_identity('preserve', note, current)
    bound = session.bind_wire_adoption('preserve', baseline_identity, path, allow_patch=True)
    assert bound['owner_patch'] is True and bound['cost_link'] == 'ledger-row'
    assert bound['identity'] == answer.request_sha256 and bound['attempt_id'] == answer.attempt_id
    assert ('work_units' in bound['source_rel']) == (evidence_kind == 'work_unit')
    baseline = purchased_baseline(root, 'preserve', baseline_identity)
    chars, blueprint = _source_pair()
    def compile_current(candidate):
        return assemble_preserve(candidate, policy_id='preserve-default',
            policy_version=STAGE4_PRESERVE_POLICY_VERSION,
            characters=chars, blueprint=blueprint, provider='fake', model=answer.model,
            request_sha256=current)
    def validate(candidate):
        return compile_current(candidate)[1]
    preview = preview_owner_patch(owner='preserve', identity=baseline_identity,
        candidate=wire, baseline=baseline, validate=validate,
        dry_run=lambda _w: {'status': 'not_run', 'reason': '本例只验 Preserve 恢复'})
    assert not preview['owner_errors'] and not preview['id_changes'] and not preview['text_changes']
    record = stage_revision(repo, owner='preserve', identity=baseline_identity, baseline=baseline,
        raw_bytes=path.read_bytes(), preview=preview, reason=reason,
        applies_to_request_sha256=current)
    checkpoint, errors = compile_current(wire)
    assert checkpoint is not None and not errors
    repo.context.enter_node(record.node)
    ref = AdoptionRef(owner='preserve', model=answer.model,
        prompts={'system': current_request.system, 'user': current_request.user},
        sid='run', request_sha256=current, historical=bound,
        patch={'candidate_sha256': record.candidate_sha256,
               'baseline_request_sha256': baseline_identity, 'reason': reason})
    repo.write(record.product_rel, checkpoint, owners=('preserve',), refs=(ref,))
    activate_node_revisions(repo, record.node)
    assert registered_owner_patch(repo, 'preserve', current)
    assert repo.read_model(record.product_rel).request_sha256 == current
    data, replay, paid = adopted_patch(repo, 'preserve', current, validate)
    assert data == wire and replay.applies_to_request_sha256 == current
    assert paid['attempt_id'] == answer.attempt_id
    assert adopted_patch(repo, 'preserve', digest('另一个未登记请求'), validate) is None
    assert ledger_path(root.parent).read_bytes() == original_ledger
    rows, tail = read_cost_snapshot(repo.root.parent, readonly=True)
    assert not tail and not [r for r in rows if not r.get('cached')]
    assert len(bought.calls) == 1 and not replay_transport.calls


@pytest.mark.stage_scope('stage4')
@pytest.mark.parametrize('evidence_kind', ['request_record', 'work_unit'])
def test_equal_wire_without_patch_note_keeps_historical_adoption(tmp_path, evidence_kind):
    root, repo, session, path, _wire, _request_row, answer, bought, replay_transport = (
        _equal_wire_adoption(tmp_path, evidence_kind))
    bound = session.bind_wire_adoption('preserve', answer.request_sha256, path, allow_patch=False)
    assert bound['mode'] == 'historical-adoption' and bound['cost_link'] == 'ledger-row'
    assert not bound.get('owner_patch') and bound['attempt_id'] == answer.attempt_id
    assert purchased_baseline(root, 'preserve', answer.request_sha256)['content'] == answer.content
    assert not repo.path('runtime/revisions/active/preserve.json').exists()
    assert len(bought.calls) == 1 and not replay_transport.calls


@pytest.mark.stage_scope('stage4')
@pytest.mark.parametrize('evidence_kind', ['request_record', 'work_unit'])
@pytest.mark.parametrize('fault', ['unknown_cost', 'response_hash_drift'])
def test_equal_wire_patch_refuses_broken_purchase_evidence(tmp_path, monkeypatch, evidence_kind, fault):
    from novel.llm import paid_requests
    root, repo, session, path, _wire, _request_row, answer, bought, replay_transport = (
        _equal_wire_adoption(tmp_path, evidence_kind))
    bound = session.bind_wire_adoption('preserve', answer.request_sha256, path, allow_patch=False)
    if fault == 'unknown_cost':
        monkeypatch.setattr(paid_requests, 'read_cost_snapshot', lambda *_a, **_kw: ([], False))
        message = '成本'
    else:
        evidence = (root / bound['source_rel'] if evidence_kind == 'request_record'
                    else root.parent / bound['source_rel'])
        payload = json.loads(evidence.read_text(encoding='utf-8'))
        if evidence_kind == 'request_record':
            payload['content'] += '被改'
        else:
            # 不重签 blob/hash；真实最低读取边界应拒绝响应证据漂移。
            payload['payload']['response']['content'] += '被改'
        evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        message = '内容损坏|响应证据身份漂移'
    with pytest.raises(ValueError, match=message):
        session.bind_wire_adoption('preserve', answer.request_sha256, path, allow_patch=True)
    assert not repo.path('runtime/revisions/active/preserve.json').exists()
    assert len(bought.calls) == 1 and not replay_transport.calls


def test_purchased_baseline_rejects_response_cost_hash_drift_with_coordinates(tmp_path, monkeypatch):
    from novel.llm import paid_requests
    from novel.stages.revisions import RevisionEvidenceStop
    repo, _session, _transport, answer, _row = _baseline(tmp_path)
    rows, tail = paid_requests.read_cost_snapshot(repo.root.parent, readonly=True)
    altered = [{**r, 'response_sha256': '0' * 64} if not r.get('cached') else r for r in rows]
    monkeypatch.setattr(paid_requests, 'read_cost_snapshot', lambda *_a, **_kw: (altered, tail))
    with pytest.raises(RevisionEvidenceStop) as stopped:
        purchased_baseline(repo.root, 'preserve', answer.request_sha256)
    assert stopped.value.code == 'revision_evidence_invalid'
    assert stopped.value.node == 'str06.stage4.preserve' and stopped.value.item == 'book'
    assert stopped.value.context['baseline_request_sha256'] == answer.request_sha256


@pytest.mark.parametrize('wire', [[], {'prose': 42}, {'prose': '正文', 'extra': True}])
def test_text_patch_preview_rejects_same_wire_shape_as_production(wire):
    from novel.config import Config
    from novel.stages.chain import ExecutionProfile
    from tools.str06_review import request_preview
    from types import SimpleNamespace
    _body, _binding, _plan, ir = _green_book_ir()
    class ReadOnlyInput:
        def read_model(self, rel):
            assert rel == 'stage4/ir/writer_book_ir.json'
            return ir
    source = SimpleNamespace(chapters=[{'chapter_ordinal': 1, 'text': '源文'}], hook_copy=None)
    _request_view, validate = request_preview(ReadOnlyInput(), 'body_ch01', source, ExecutionProfile(Config(), 'high'))
    assert validate(wire) == ['文本 wire 必须只带字符串 prose']


def test_patch_preview_is_readonly_and_separates_ids_text_and_residuals(tmp_path):
    repo, _session, _transport, answer, baseline = _baseline(tmp_path)
    baseline = {**baseline, 'content': '{"participants": ["old"], "event": "old 出场"}'}
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.rglob('*') if p.is_file()}
    preview = preview_owner_patch(owner='preserve', identity=answer.request_sha256,
        candidate={'participants': ['new'], 'event': 'old 出场'}, baseline=baseline,
        validate=lambda w: [], dry_run=lambda w: {'status': 'ran', 'errors': []})
    assert preview['id_changes'] and not preview['text_changes']
    assert preview['residual_references'] == [{'path': '/event', 'char_id': 'old'}]
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='基线身份漂移'):
        preview_owner_patch(owner='preserve', identity='f' * 64, candidate={}, baseline=baseline,
                            validate=lambda w: [], dry_run=lambda w: {})


def test_explicit_cross_identity_patch_replays_and_activates_with_original_cost(tmp_path):
    from novel.stages.revisions import owner_patch_identity, registered_owner_patch
    from tests.str06_facilities import synthetic_ref
    repo, session, transport, answer, baseline = _baseline(tmp_path)
    current = 'f' * 64
    note = {'base_response_identity': answer.request_sha256, 'reason': '协议升版后完整复验'}
    with pytest.raises(ValueError, match='应用身份'):
        owner_patch_identity('preserve', note, current)
    note['applies_to_request_sha256'] = current
    assert owner_patch_identity('preserve', note, current)[0] == answer.request_sha256
    preview = preview_owner_patch(owner='preserve', identity=answer.request_sha256,
        candidate={'value': 2}, baseline=baseline, validate=lambda w: [], dry_run=lambda w: {})
    record = stage_revision(repo, owner='preserve', identity=answer.request_sha256,
        baseline=baseline, raw_bytes=b'{"value":2}', preview=preview, reason=note['reason'],
        applies_to_request_sha256=current)
    assert registered_owner_patch(repo, 'preserve', current)
    assert registered_owner_patch(repo, 'preserve', answer.request_sha256)
    assert not registered_owner_patch(repo, 'preserve', 'e' * 64)
    repo.context.enter_node(record.node)
    ref = synthetic_ref('preserve')._replace(request_sha256=current,
        patch={'candidate_sha256': record.candidate_sha256,
               'baseline_request_sha256': answer.request_sha256})
    repo.write(record.product_rel, {'value': 2}, owners=('preserve',), refs=(ref,))
    activate_node_revisions(repo, record.node)
    data, replay, cost = adopted_patch(repo, 'preserve', current, lambda w: [])
    assert data == {'value': 2} and replay.baseline_request_sha256 == answer.request_sha256
    assert cost['attempt_id'] == answer.attempt_id and len(transport.calls) == 1
    # 回派关闭后回到同一已购基线身份，仍须用补丁；新输入身份不能借道。
    assert adopted_patch(repo, 'preserve', 'e' * 64, lambda w: []) is None
    data, replay, cost = adopted_patch(repo, 'preserve', answer.request_sha256, lambda w: [])
    assert data == {'value': 2} and replay.applies_to_request_sha256 == answer.request_sha256
    ref = ref._replace(request_sha256=answer.request_sha256)
    repo.write(record.product_rel, data, owners=('preserve',), refs=(ref,))
    activate_node_revisions(repo, record.node)
    # 同一候选的显式新应用必须重绑，不能因原字节相同沿用上一身份。
    next_identity = 'd' * 64
    restaged = stage_revision(repo, owner='preserve', identity=answer.request_sha256,
        baseline=baseline, raw_bytes=b'{"value":2}', preview=preview, reason=note['reason'],
        applies_to_request_sha256=next_identity)
    assert restaged.applies_to_request_sha256 == next_identity
    assert restaged.candidate_sha256 == record.candidate_sha256
    assert adopted_patch(repo, 'preserve', current, lambda w: []) is None
    repo.write(record.product_rel, data, owners=('preserve',),
               refs=(ref._replace(request_sha256=next_identity),))
    activate_node_revisions(repo, record.node)
    assert len(transport.calls) == 1


def test_patch_evidence_survives_external_file_and_binds_current_product(tmp_path):
    from tests.str06_facilities import synthetic_ref
    repo, _session, _transport, answer, baseline = _baseline(tmp_path)
    preview = preview_owner_patch(owner='preserve', identity=answer.request_sha256,
        candidate={'value': 2}, baseline=baseline, validate=lambda w: [],
        dry_run=lambda w: {'status': 'ran', 'errors': []})
    original = b'{ "value": 2 }\r\n'
    record = stage_revision(repo, owner='preserve', identity=answer.request_sha256, baseline=baseline,
                            raw_bytes=original, preview=preview, reason='修复承载')
    assert repo.path(record.evidence_rel).read_bytes() == original
    assert revision_states_at(repo.root) == [], '未写正式产物的 staged 不开启级联'
    repo.context.enter_node('str06.stage4.preserve')
    ref = synthetic_ref('preserve')._replace(request_sha256=answer.request_sha256,
                                         patch={'candidate_sha256': record.candidate_sha256})
    repo.write(record.product_rel, {'value': 2}, owners=('preserve',), refs=(ref,))
    # 输出已封存但激活尚未来得及落盘：重启可核验同一补丁并免费恢复。
    assert adopted_patch(repo, 'preserve', answer.request_sha256, lambda w: [])[0] == {'value': 2}
    activate_node_revisions(repo, record.node)
    states = revision_states_at(repo.root)
    assert states[0].accepted_product_sha256 == states[0].current_product_sha256
    assert adopted_patch(repo, 'preserve', answer.request_sha256, lambda w: [])[0] == {'value': 2}
    activate_node_revisions(repo, record.node)
    resolve_node_revisions(repo, 'str06.stage4.compile')
    assert revision_states_at(repo.root) == []
    # 修订后再次手改不得被补丁重放自动覆盖。
    repo.path(record.product_rel).write_text('{"value": 3}', encoding='utf-8')
    with pytest.raises(ValueError, match='再次变化'):
        adopted_patch(repo, 'preserve', answer.request_sha256, lambda w: [])


def test_staged_patch_does_not_overwrite_unregistered_intervening_edit(tmp_path):
    repo, _session, _transport, answer, baseline = _baseline(tmp_path)
    preview = preview_owner_patch(owner='preserve', identity=answer.request_sha256,
        candidate={'value': 2}, baseline=baseline, validate=lambda w: [], dry_run=lambda w: {})
    record = stage_revision(repo, owner='preserve', identity=answer.request_sha256, baseline=baseline,
                            raw_bytes=b'{"value":2}', preview=preview, reason='修复承载')
    product = repo.path(record.product_rel)
    product.parent.mkdir(parents=True, exist_ok=True)
    product.write_text('{"value":3}', encoding='utf-8')
    with pytest.raises(ValueError, match='再次变化'):
        adopted_patch(repo, 'preserve', answer.request_sha256, lambda w: [])
    assert json.loads(product.read_text(encoding='utf-8')) == {'value': 3}


def _publish_patch(repo, answer, baseline, value):
    from tests.str06_facilities import synthetic_ref
    candidate = {'value': value}
    preview = preview_owner_patch(owner='preserve', identity=answer.request_sha256,
        candidate=candidate, baseline=baseline, validate=lambda w: [], dry_run=lambda w: {})
    raw = json.dumps(candidate).encode('utf-8')
    record = stage_revision(repo, owner='preserve', identity=answer.request_sha256,
                            baseline=baseline, raw_bytes=raw, preview=preview,
                            reason=f'第 {value} 次核读修订')
    repo.context.enter_node(record.node)
    ref = synthetic_ref('preserve')._replace(request_sha256=answer.request_sha256,
                                         patch={'candidate_sha256': record.candidate_sha256})
    repo.write(record.product_rel, candidate, owners=('preserve',), refs=(ref,))
    activate_node_revisions(repo, record.node)
    return record, raw


@pytest.mark.parametrize('resolved', [False, True])
def test_successive_explicit_patches_keep_history_and_cost_without_new_purchase(tmp_path, resolved):
    repo, session, transport, answer, baseline = _baseline(tmp_path)
    first, first_raw = _publish_patch(repo, answer, baseline, 2)
    if resolved:
        resolve_node_revisions(repo, 'str06.stage4.compile')
    repo.write('runtime/revisions/preserve.json', {'sha256': first.candidate_sha256})
    candidate_path = tmp_path / 'second_wire.json'
    candidate_path.write_text('{"value":3}', encoding='utf-8')
    session.adopt_roots = (repo.root,)
    with pytest.raises(ValueError, match='不一致'):
        session.bind_wire_adoption('preserve', answer.request_sha256, candidate_path)
    from novel.stages.revisions import verified_previous_revision
    previous = verified_previous_revision(repo, 'preserve')
    bound = session.bind_wire_adoption('preserve', answer.request_sha256,
                                       candidate_path, allow_patch=True,
                                       previous_patch_sha256=previous.candidate_sha256)
    assert bound['owner_patch'] and bound['attempt_id'] == answer.attempt_id
    second, _raw = _publish_patch(repo, answer, baseline, 3)
    assert adopted_patch(repo, 'preserve', answer.request_sha256, lambda w: [])[0] == {'value': 3}
    history = list((repo.root / 'runtime/revisions/history/preserve').glob('*.json'))
    assert len(history) == 1
    assert repo.read_external(history[0])['candidate_sha256'] == first.candidate_sha256
    assert repo.path(first.evidence_rel).read_bytes() == first_raw
    assert second.baseline_request_sha256 == first.baseline_request_sha256
    assert len(transport.calls) == 1, '连续免费修订不购买也不重置额度'


@pytest.mark.parametrize('resolved', [False, True])
def test_new_patch_refuses_unregistered_product_change_before_replacing_history(tmp_path, resolved):
    repo, _session, transport, answer, baseline = _baseline(tmp_path)
    first, _raw = _publish_patch(repo, answer, baseline, 2)
    if resolved:
        resolve_node_revisions(repo, 'str06.stage4.compile')
    active = repo.path('runtime/revisions/active/preserve.json').read_bytes()
    repo.path(first.product_rel).write_text('{"value":99}', encoding='utf-8')
    with pytest.raises(ValueError, match='再次变化'):
        _publish_patch(repo, answer, baseline, 3)
    assert repo.path('runtime/revisions/active/preserve.json').read_bytes() == active
    assert json.loads(repo.path(first.product_rel).read_bytes()) == {'value': 99}
    assert len(transport.calls) == 1


def test_resample_does_not_reset_work_unit_or_book_budget(tmp_path):
    from novel.llm.paid_requests import ExecutionError
    from novel.work_units import WorkUnitBudget, read_book_budget
    repo, session, transport, _answer, _baseline_row = _baseline(tmp_path)
    session.book_call_ceiling = 2
    request, _selection = sampling_request(repo, session, 'preserve', _request(), explicit=True, reason='第一次重采')
    transport.contents.append('{"value":2}')
    ask_json(session, 'preserve', request, lambda w: [])
    next_request, selection = sampling_request(repo, session, 'preserve', _request(), explicit=True, reason='第二次重采')
    assert selection.sequence == 2
    with pytest.raises(ExecutionError) as stopped:
        ask_json(session, 'preserve', next_request, lambda w: [])
    assert stopped.value.code == 'book_call_ceiling'
    assert len(transport.calls) == read_book_budget(repo.root.parent, repo.root.name).used == 2
    unit = WorkUnitBudget(repo.root.parent, repo.root.name, 'str06.stage4.preserve', 'executor', 'book', read_only=True)
    assert len(unit._read().turns) == 2


def test_obligation_review_keeps_unselected_material_and_cross_chapter_carriers():
    from tools.str06_review import obligation_review
    from tests.test_str06_target import _assemble
    graph, binding, _plan, _ir = _green_book_ir()
    _chars, blueprint, _checkpoint = _assemble()
    entry = binding.entries[0].model_copy(update={'scene_id': 'sc02', 'beat_ref': 'sc02#b01',
                                                 'supporting_beat_refs': ('sc01#b01',)})
    binding = binding.model_copy(update={'entries': (entry,)})
    class ReadOnlyInputs:
        def read_model(self, rel):
            return {'stage4/grounding/semantic_binding.json': binding,
                    'stage4/design/presentation_graph.json': graph,
                    'stage2/blueprints/chapter_blueprints.json': blueprint}[rel]
    report = obligation_review(ReadOnlyInputs())
    assert report['obligations'][0]['target_chapters'] == [1, 2]
    assert report['obligations'][0]['cross_chapter'] is True
    selected = set(binding.obligations[0].lineage.blueprint_refs)
    assert {row['beat']['beat_id'] for row in report['unselected_source_beats']} == {
        b.beat_id for c in blueprint.chapters for b in c.beats} - selected
    assert report['source_expression']['items'] and report['expression_decisions']


def test_resample_has_new_identity_and_restart_reuses_selected_sample(tmp_path):
    repo, session, transport, _answer, _baseline_row = _baseline(tmp_path)
    request, selection = sampling_request(repo, session, 'preserve', _request(), explicit=True, reason='换一个承载')
    assert selection.sequence == 1
    transport.contents.append('{"value": 2}')
    assert ask_json(session, 'preserve', request, lambda w: [])[0] == {'value': 2}
    restored, same = sampling_request(repo, session, 'preserve', _request(), explicit=False, reason='')
    assert same == selection
    assert ask_json(session, 'preserve', restored, lambda w: [])[0] == {'value': 2}
    assert len(transport.calls) == 2
    # 原回答仍可按原请求读取；新采样不抹掉原已购证据。
    assert ask_json(session, 'preserve', _request(), lambda w: [])[0] == {'value': 1}
    changed, selected = sampling_request(repo, session, 'preserve', _request('新基线'), explicit=False, reason='')
    assert not changed.sampling_nonce and selected is None
    session.yes = False
    with pytest.raises(ValueError, match='许可'):
        sampling_request(repo, session, 'preserve', _request(), explicit=True, reason='再采样')


def test_counting_excludes_all_unicode_whitespace_but_includes_punctuation():
    from novel.core.text import count_non_whitespace_chars
    from novel.stages.stage5 import count_body_chars
    from novel.stages.chain import derive_total_length_budget
    text = '甲。 \t\r\n乙\u3000\u00a0丙！'
    assert count_non_whitespace_chars(text) == count_body_chars(text) == 5
    assert derive_total_length_budget([{'chapter_ordinal': 1, 'text': '甲 ' * 1001}]) == 2000


def test_missing_and_null_are_distinct_in_patch_diff():
    from novel.stages.revisions import wire_diff
    changes = wire_diff({}, {'holder': None})
    assert changes[0]['before_present'] is False and changes[0]['after_present'] is True
    assert changes[0]['category'] == 'id'


def test_selected_sampling_nonce_is_closed_to_baseline_and_sequence(tmp_path):
    repo, session, _transport, _answer, _baseline_row = _baseline(tmp_path)
    _request_row, selected = sampling_request(repo, session, 'preserve', _request(), explicit=True, reason='显式重采')
    rel = f'runtime/revisions/sampling/preserve/{selected.baseline_request_sha256}.json'
    repo.write(rel, selected.model_copy(update={'nonce': 'f' * 64}))
    from novel.llm.paid_requests import ExecutionError
    with pytest.raises(ExecutionError, match='序号损坏'):
        sampling_request(repo, session, 'preserve', _request(), explicit=False, reason='')


def test_source_expression_refs_are_checked_and_brief_is_source_blind():
    from copy import deepcopy
    from tests.test_str06_stage2_protocol import _book_wire, SEGMENTS
    from novel.stages.stage2 import compile_book_observation
    from tests.test_str06_target import _assemble
    from novel.stages.stage4 import project_brief
    wire = _book_wire()
    wire['characters']['fu_jun']['surfaces'] = [
        s for s in wire['characters']['fu_jun']['surfaces'] if s['surface'] != '他']
    wire['expression']['mechanisms'] = [{'kind': 'contrast', 'statement': '对外守礼，对内直白。',
                                         'source_refs': ['e01_01']}]
    book, errors = compile_book_observation(wire, evidence=SEGMENTS)
    assert not errors and book.expression.mechanisms[0].statement == '对外守礼，对内直白。'
    bad = deepcopy(wire)
    bad['expression']['mechanisms'][0]['source_refs'] = ['e99_99']
    assert compile_book_observation(bad, evidence=SEGMENTS)[1]
    chars, blueprint, checkpoint = _assemble()
    brief, _identities = project_brief(checkpoint, chars, blueprint)
    text = brief.model_dump_json()
    assert brief.expression and 'source_refs' not in text and 'e01_01' not in text


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown', 'reason'])
def test_expression_decisions_require_exact_coverage_and_explicit_change_reason(mutation):
    from tests.test_str06_target import _design_wire
    from tests.str06_expression_inputs import sample_narration, sample_requirements
    from novel.stages.stage4 import assemble_body_design
    wire = _design_wire()
    assert assemble_body_design(wire, known_char_ids={'yun', 'fu_jun'},
        expression_requirements=sample_requirements(), source_narration=sample_narration())[1] == []
    if mutation == 'missing':
        wire['expression_decisions'] = []
    elif mutation == 'duplicate':
        wire['expression_decisions'] *= 2
    elif mutation == 'unknown':
        wire['expression_decisions'][0]['ex_id'] = 'ex_99'
    else:
        wire['expression_decisions'][0]['reason'] = None
    body, errors = assemble_body_design(wire, known_char_ids={'yun', 'fu_jun'},
        expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is None and errors


def test_anchor_is_earlier_and_immediate_continuation_uses_previous_last_beat():
    from contracts.schema.design import BodyDesign
    from pydantic import ValidationError
    body, binding, plan, _ir = _green_book_ir()
    def anchored(anchor, transition='flashback'):
        entry = body.chapters[1].entry.model_copy(update={'anchor': anchor, 'transition': transition})
        updated = body.model_copy(update={'chapters': (body.chapters[0], body.chapters[1].model_copy(update={'entry': entry}))})
        return BodyDesign.model_validate(updated.model_dump())
    for bad in ('sc99#b01', 'sc02#b01'):
        with pytest.raises(ValidationError):
            anchored(bad)
    with pytest.raises(ValidationError):
        anchored('sc01#b01', 'immediate_continuation')
    from novel.stages.stage4 import compile_writer_book_ir
    from tests.test_str06_hook import _characters
    chars = _characters()
    ir = compile_writer_book_ir(graph=anchored('sc01#b01'), plan=plan, binding=binding,
        total_budget=1000, length_band=(0.6, 1.3), projection=chars.name_projection(), structure=chars.name_structure)
    from novel.stages.common import render_chapter_packet
    rendered = render_chapter_packet(project_chapter_packet(ir, 2))
    assert body.beat('sc01', 'b01').event in rendered and '倒叙' in rendered
    assert 'sc01#b01' not in rendered


def test_generated_design_schema_rejects_the_same_local_branch_errors():
    import jsonschema
    from copy import deepcopy
    from tests.test_str06_target import _design_wire
    from contracts.schema.design import DesignWireV5
    from pydantic import ValidationError
    validator = jsonschema.Draft202012Validator(DesignWireV5.model_json_schema())
    wire = _design_wire()
    assert not list(validator.iter_errors(wire))
    bads = []
    bad = deepcopy(wire)
    bad['expression_decisions'][0]['reason'] = None
    bads.append(bad)
    bad = deepcopy(wire)
    bad['scenes'][0]['beats'][0]['threads'] = [{'op': 'open', 'tag': 'q', 'kind': 'question',
                                              'subject': 'ch1', 'text': '谁来了', 'progress': '违规进展'}]
    bads.append(bad)
    bad = deepcopy(wire)
    bad['scenes'][0]['beats'][0]['target_delta'] = [{'domain': 'knowledge', 'subjects': [],
        'key': 'q', 'holder': None, 'op': 'set', 'value': '答案', 'scope': 'book', 'knowledge_mode': 'known'}]
    bads.append(bad)
    # reader known 的 confirms 分支在嵌套 schema 中也必须与模型验证一致。
    confirmation = {'domain': 'knowledge', 'subjects': [], 'key': 'q',
                    'holder': 'reader', 'op': 'set', 'value': '答案',
                    'scope': 'book', 'knowledge_mode': 'known',
                    'confirms': {'domain': 'world', 'key': 'answer',
                                 'subjects': [], 'holder': None}}
    good = deepcopy(wire)
    good['scenes'][0]['beats'][0]['target_delta'] = [confirmation]
    assert not list(validator.iter_errors(good))
    DesignWireV5.model_validate(good)
    for address in (None, {'domain': 'knowledge', 'key': 'q',
                           'subjects': [], 'holder': 'reader'}):
        bad = deepcopy(good)
        bad['scenes'][0]['beats'][0]['target_delta'][0]['confirms'] = address
        bads.append(bad)
    for bad in bads:
        assert list(validator.iter_errors(bad))
        with pytest.raises(ValidationError):
            DesignWireV5.model_validate(bad)


def test_family_release_is_shared_and_stage7_replaces_actual_forms():
    from tests.test_str06_hook import _characters
    from novel.core.name_materials import expand_surface_releases, withheld_name_literals
    from novel.stages.stage7 import compile_name_map, compile_bound_replacements, apply_bound_names
    chars = _characters()
    releases = expand_surface_releases({'fu_jun': ('夫君',)}, chars.name_structure)
    assert '陆家' in releases['fu_jun']
    assert '陆家' not in withheld_name_literals(chars.name_projection(), releases['fu_jun'], chars.name_structure)
    from tests.test_str06_hook import _full_chain_wires
    names, errors = compile_name_map(_full_chain_wires()['naming'], chars)
    assert not errors
    replacements, errors = compile_bound_replacements(chars, names)
    from novel.core.name_text import resolve_name_text
    text = resolve_name_text('[[name:fu_jun|陆家]]还有[[name:fu_jun|陆兄]]。')
    assert not errors and apply_bound_names(text.text, text.mentions, replacements)[0] == '顾家还有顾兄。'


def test_narration_reaches_writer_and_rejects_scene_pov_drift():
    from contracts.schema.design import BodyDesign
    from pydantic import ValidationError
    from novel.stages.stage4 import compile_execution_plan, compile_writer_book_ir
    from tests.test_str06_hook import _characters
    from novel.stages.common import render_chapter_packet
    body, binding, _plan, _ir = _green_book_ir()
    first = body.model_copy(update={'narration': body.narration.model_copy(update={
        'person': 'first', 'focalizers': ('yun_niang',)})})
    first = BodyDesign.model_validate(first.model_dump())
    drift = first.model_copy(update={'scenes': (first.scenes[0].model_copy(update={
        'delivery': first.scenes[0].delivery.model_copy(update={'pov': 'fu_jun'})}), *first.scenes[1:])})
    with pytest.raises(ValidationError, match='POV'):
        BodyDesign.model_validate(drift.model_dump())
    chars = _characters()
    plan, errors = compile_execution_plan(first, binding)
    assert not errors
    ir = compile_writer_book_ir(graph=first, plan=plan, binding=binding, total_budget=2000,
        length_band=(0.6, 1.3), projection=chars.name_projection(), structure=chars.name_structure)
    for n in (1, 2):
        text = render_chapter_packet(project_chapter_packet(ir, n))
        assert '第一人称' in text and '对白中的自称不改变正文叙述人称' in text
    # 不以对白中的“我”机械判断第三人称违约；自由文本人称由 canary 核读。
    from novel.stages.stage5 import validate_chapter_prose
    assert not validate_chapter_prose('她说：“我去。”', target_chars=8, band_low=5, band_high=10,
                                      withheld_literals=(), released_surfaces=())


def test_inherited_expression_leak_points_to_explicit_wire_override():
    from contracts.schema.expression import ExpressionDecision
    from contracts.schema.source import NameProjection
    from novel.stages.stage4.materials import validate_design_materials
    from copy import deepcopy

    body, _binding, _plan, _ir = _green_book_ir()
    leaking = ExpressionDecision(ex_id='ex_04', action='keep', reason=None,
                                  statement='沈鹤汀表面黏人，内心克制，形成反差。')
    body = body.model_copy(update={'expression_decisions': (leaking,)})
    before = deepcopy(body.model_dump())
    projection = NameProjection(proper_literals=('沈鹤汀',), family_literals=(), by_person=())
    errors = validate_design_materials(body, projection=projection, known_char_ids={'fu_jun', 'yun_niang'})
    assert errors and all('expression_overrides' in e for e in errors)
    assert all(e.startswith('positive_material_leak[') for e in errors)
    assert body.model_dump() == before
    repaired = body.model_copy(update={'expression_decisions': (
        ExpressionDecision(ex_id='ex_04', action='change', reason='按当前释放范围表达反差。',
                           statement='表面黏人，内心克制，形成反差。'),)})
    assert not validate_design_materials(repaired, projection=projection, known_char_ids={'fu_jun', 'yun_niang'})
