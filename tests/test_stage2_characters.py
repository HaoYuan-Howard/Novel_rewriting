# -*- coding: utf-8 -*-
"""人物独立 producer 的真实 session、持久提交与人工恢复边界。"""
from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING
from contracts.schema.characters import CharactersEdit, CharactersResponse
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.llm import agent_runs
from novel.orchestrator.node_agents import Stage2CharactersAgent, build_node_agent
from novel.stages.stage2 import characters
from novel.store import ArtifactStore
from tests.stage2_fixtures import character_response
from tests.test_stage2_global_agent import (
    _ctx as _global_ctx, _Driver, _free_ctx, _new_run_ctx, _resume_ctx,
)


def _ctx(base):
    ctx = _global_ctx(base, with_characters=False)
    ctx.node = characters.NODE
    return ctx


def _response():
    return character_response()


def _wire(value=None):
    return json.dumps(_response() if value is None else value, ensure_ascii=False)


def _generate(ctx):
    driver = _Driver([_wire()], ctx.novel_dir, session_id=f'characters-{ctx.run_id}')
    result = build_node_agent(ctx, driver=driver).run()
    return result.artifact, driver


def test_independent_characters_generate_through_real_agent_and_store(tmp_path):
    ctx = _ctx(tmp_path)
    artifact, driver = _generate(ctx)
    assert len(driver.calls) == 1
    store = ArtifactStore(ctx)
    assert store.exists(characters.REL)
    assert not store.exists(STAGE2_GLOBAL_GROUNDING)
    assert store.freshness(characters.REL).state == 'fresh'
    assert set(artifact.provenance.inputs) == {STAGE1_MAIN_TEXT}
    assert all(not hasattr(row, 'fate') for row in artifact.characters.values())
    assert agent_runs.commit_state(ctx, characters.commit_spec())['state'] == 'committed'
    assert 'source_blurb' not in characters.SYSTEM


def test_static_hard_failure_resumes_same_session_without_global_write(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _response()
    bad['characters'][1]['role'] = '最终遇害'
    driver = _Driver([_wire(bad), _wire()], ctx.novel_dir)
    result = Stage2CharactersAgent(ctx, driver=driver).run()
    assert result.passed
    assert [kind for kind, _ in driver.calls] == ['start', 'resume']
    assert result.artifact.provenance.usage.calls_made == 2


@pytest.mark.parametrize('window', ['before_commit', 'after_formal'])
def test_saved_character_answer_and_formal_recover_without_new_wire(tmp_path, monkeypatch, window):
    ctx = _ctx(tmp_path)
    if window == 'before_commit':
        target, name = characters, '_commit'
    else:
        target, name = agent_runs, 'mark_committed'
    original = getattr(target, name)

    def interrupted(*args, **kwargs):
        raise ProvenanceRefused('模拟中断')

    monkeypatch.setattr(target, name, interrupted)
    first = _Driver([_wire()], ctx.novel_dir)
    with pytest.raises(ProvenanceRefused, match='模拟中断'):
        Stage2CharactersAgent(ctx, driver=first).run()
    monkeypatch.setattr(target, name, original)
    resumed = _resume_ctx(ctx)
    no_calls = _Driver([], ctx.novel_dir)
    result = Stage2CharactersAgent(resumed, driver=no_calls).run()
    assert result.passed
    assert not no_calls.calls
    assert result.artifact.provenance.usage.calls_made == 1
    assert agent_runs.commit_state(resumed, characters.commit_spec())['state'] == 'committed'
    assert not list((ctx.novel_dir / 'stage2/_rounds').glob('characters.r*'))


def test_characters_changed_source_refuses_saved_answer_without_repurchase(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    original = characters._commit
    monkeypatch.setattr(characters, '_commit', lambda *a, **k: (_ for _ in ()).throw(
        ProvenanceRefused('中断')))
    with pytest.raises(ProvenanceRefused):
        _generate(ctx)
    monkeypatch.setattr(characters, '_commit', original)
    source = ArtifactStore(ctx).path(STAGE1_MAIN_TEXT)
    source.write_text(source.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    no_calls = _Driver([], ctx.novel_dir)
    with pytest.raises(ProvenanceRefused):
        Stage2CharactersAgent(_resume_ctx(ctx), driver=no_calls).run()
    assert not no_calls.calls


def test_characters_edit_preserves_evidence_and_is_idempotent(tmp_path):
    ctx = _ctx(tmp_path)
    previous, _ = _generate(ctx)
    free = _free_ctx(ctx)
    template = characters.edit_template(free)
    row = template['operations'][0]
    row['character']['role'] += '，为家事操持'
    edit = CharactersEdit.model_validate({**template, 'operations': [row]})
    updated = characters.apply_edit(free, edit, actor='human-test', reason='核对静态身份')
    assert updated.characters[row['char_id']].role != previous.characters[row['char_id']].role
    assert updated.provenance.model == previous.provenance.model
    assert updated.provenance.characters_edit.edit_sha256
    assert set(updated.provenance.inputs) == {STAGE1_MAIN_TEXT}
    assert characters.apply_edit(_free_ctx(ctx), edit, actor='human-test', reason='核对静态身份') == updated
    with pytest.raises(ProvenanceRefused, match='actor/reason'):
        characters.apply_edit(_free_ctx(ctx), edit, actor='someone', reason='核对静态身份')


def test_characters_noop_edit_does_not_land_a_round(tmp_path):
    ctx = _ctx(tmp_path)
    _generate(ctx)
    free = _free_ctx(ctx)
    edit = CharactersEdit.model_validate(characters.edit_template(free))
    with pytest.raises(TerminalState, match='未改变事实'):
        characters.apply_edit(free, edit, actor='human-test', reason='原样模板')
    assert not ArtifactStore(ctx).exists(characters.STAGE2_CHARACTERS_EDIT)


def test_character_static_contract_rejects_fate_and_outside_evidence(tmp_path):
    bad = _response()
    bad['characters'][0]['fate'] = '后成为大臣'
    with pytest.raises(ValueError):
        CharactersResponse.model_validate(bad)
    ctx = _ctx(tmp_path)
    prepared = characters.prepare(ctx)
    outside = _response()
    outside['characters'][0]['role_evidence_refs'] = ['e999999']
    with pytest.raises(ValueError, match='闭集'):
        characters.compile_response(CharactersResponse.model_validate(outside), prepared.table)


def test_exhausted_character_candidate_stays_invalid_and_revalidation_cannot_bless(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _response()
    bad['characters'][0]['role'] = '最终遇害'
    driver = _Driver([_wire(bad)] * 4, ctx.novel_dir)
    with pytest.raises(GateBlocked):
        Stage2CharactersAgent(ctx, driver=driver).run()
    assert len(driver.calls) == 4
    store = ArtifactStore(ctx)
    assert store.exists(characters.INVALID) and not store.exists(characters.REL)
    passed, _ = characters.revalidate(_free_ctx(ctx), actor='human-test', reason='按原文复检')
    assert not passed
    assert not store.exists(characters.REL)
    template = characters.edit_template(_free_ctx(ctx))
    first = template['operations'][0]
    first['character']['role'] = _response()['characters'][0]['role']
    fixed = characters.apply_edit(
        _free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [first]}),
        actor='human-test', reason='把动态经历移出静态身份')
    assert fixed.characters[first['char_id']].role == first['character']['role']
    assert store.exists(characters.REL) and not store.exists(characters.INVALID)


def test_bad_candidate_then_parse_exhaustion_keeps_all_paid_attempts(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _response()
    bad['characters'][0]['role'] = '最终遇害'
    driver = _Driver([_wire(bad), '{', '{', '{'], ctx.novel_dir)
    with pytest.raises(GateBlocked):
        Stage2CharactersAgent(ctx, driver=driver).run()
    from contracts.schema.characters import CharacterRegistry
    invalid = ArtifactStore(ctx).read_own(CharacterRegistry, characters.INVALID)[0]
    assert invalid.provenance.usage.calls_made == 4
    assert len(driver.calls) == 4


def test_old_saved_answer_cannot_overwrite_a_later_human_edit(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _generate(ctx)
    second = _new_run_ctx(ctx)
    original = characters._commit

    def interrupted(*_args, **_kwargs):
        raise ProvenanceRefused('尚未 prepared')

    monkeypatch.setattr(characters, '_commit', interrupted)
    with pytest.raises(ProvenanceRefused, match='尚未 prepared'):
        _generate(second)
    monkeypatch.setattr(characters, '_commit', original)
    free = _free_ctx(ctx)
    template = characters.edit_template(free)
    row = template['operations'][0]
    row['character']['role'] += '，为家事操持'
    current = characters.apply_edit(
        free, CharactersEdit.model_validate({**template, 'operations': [row]}),
        actor='human-test', reason='补齐静态身份')
    no_calls = _Driver([], ctx.novel_dir)
    with pytest.raises(ProvenanceRefused, match='其他写口修改'):
        Stage2CharactersAgent(_resume_ctx(second), driver=no_calls).run()
    assert not no_calls.calls
    assert ArtifactStore(ctx).read_own(type(current), characters.REL)[0] == current


def test_raw_character_answer_before_checkpoint_is_recovered_without_wire(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    original = agent_runs.write_checkpoint

    def interrupted(*_args, **_kwargs):
        raise ProvenanceRefused('回答已落盘，checkpoint 尚未写入')

    monkeypatch.setattr(agent_runs, 'write_checkpoint', interrupted)
    with pytest.raises(ProvenanceRefused, match='checkpoint 尚未'):
        _generate(ctx)
    monkeypatch.setattr(agent_runs, 'write_checkpoint', original)
    no_calls = _Driver([], ctx.novel_dir)
    result = Stage2CharactersAgent(_resume_ctx(ctx), driver=no_calls).run()
    assert result.passed and not no_calls.calls
    assert result.artifact.provenance.usage.calls_made == 1


def test_repeated_blocked_checkpoint_does_not_create_another_invalid_round(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _response()
    bad['characters'][0]['role'] = '最终遇害'
    with pytest.raises(GateBlocked):
        Stage2CharactersAgent(ctx, driver=_Driver([_wire(bad)] * 4, ctx.novel_dir)).run()
    before = ArtifactStore(ctx).path(characters.INVALID).read_bytes()
    no_calls = _Driver([], ctx.novel_dir)
    with pytest.raises(GateBlocked):
        Stage2CharactersAgent(_resume_ctx(ctx), driver=no_calls).run()
    assert ArtifactStore(ctx).path(characters.INVALID).read_bytes() == before
    assert not no_calls.calls
    assert not list((ctx.novel_dir / 'stage2/_rounds').glob('characters.INVALID.r*'))


def test_explicit_legacy_backend_runs_the_same_static_contract(tmp_path):
    from tests.test_stage2_generation import _client, _ok
    ctx = _ctx(tmp_path)
    ctx.executor_backend = None
    ctx.executor_profile = None
    client, transport = _client(ctx, lambda _request, _index: _ok(_response()))
    artifact = characters.run_legacy(ctx, client)
    assert len(transport.calls) == 1
    assert artifact.provenance.model == 'test-model'
    assert ArtifactStore(ctx).freshness(characters.REL).state == 'fresh'
    assert not ArtifactStore(ctx).exists(STAGE2_GLOBAL_GROUNDING)


@pytest.mark.parametrize('kind', ['add', 'replace', 'delete'])
def test_character_edit_invalid_target_preserves_formal_projection_and_cost(tmp_path, kind):
    ctx = _ctx(tmp_path)
    _generate(ctx)
    store = ArtifactStore(ctx)
    before = {rel: store.path(rel).read_bytes()
              for rel in (characters.REL, characters.PROJECTION)}
    ledger = ctx.ledger.path.read_bytes()
    template = characters.edit_template(_free_ctx(ctx))
    row = copy.deepcopy(template['operations'][0])
    row['kind'] = kind
    if kind != 'add':
        row['char_id'] = 'char_999'
    if kind == 'delete':
        row = {'kind': kind, 'char_id': row['char_id'], 'reason': '核对不存在主体'}
    with pytest.raises(ValidationError if kind == 'add' else TerminalState, match={
            'add': 'character_ref|char_id', 'replace': '不存在', 'delete': '不存在'}[kind]):
        characters.apply_edit(
            _free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [row]}),
            actor='test:human', reason='验证目标存在性')
    assert {rel: store.path(rel).read_bytes() for rel in before} == before
    assert ctx.ledger.path.read_bytes() == ledger
    assert not store.exists(characters.INVALID)


def test_character_add_compiles_stable_ids_avoids_retired_and_replays_without_cost(tmp_path):
    ctx = _ctx(tmp_path)
    previous, _ = _generate(ctx)
    store = ArtifactStore(ctx)
    ledger = ctx.ledger.path.read_bytes()
    template = characters.edit_template(_free_ctx(ctx))
    row = copy.deepcopy(template['operations'][-1])
    retired_id = row['char_id']
    characters.apply_edit(_free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [
        {'kind': 'delete', 'char_id': retired_id, 'reason': '验证退休身份不会再分配'}]}),
        actor='test:human', reason='独立删除')
    from novel.stages.stage2.core_evidence import evidence_slots, evidence_snippet
    from tests.test_stage2 import main_text
    for _path, owner, field in evidence_slots(row['character']):
        owner[field] = [{'chapter_ordinal': previous.evidence_anchors[ref].chapter_ordinal,
                         'locator': evidence_snippet(previous.evidence_anchors[ref].model_dump(), main_text())}
                        for ref in owner[field]]
    request = CharactersEdit.model_validate({
        'source_sha256': characters.edit_template(_free_ctx(ctx))['source_sha256'],
        'operations': [{'kind': 'add', 'character_ref': 'c7', 'character': row['character']}],
    })
    preview = characters.preflight_edit(_free_ctx(ctx), request, actor='test:human',
                                        reason='按原文新增，程序分配身份')
    assert preview.provenance.characters_edit.added_character_ids == {'c7': 'char_003'}
    assert retired_id not in preview.characters
    result = characters.apply_edit(_free_ctx(ctx), request, actor='test:human',
                                   reason='按原文新增，程序分配身份', candidate=preview)
    frozen = store.path(characters.REL).read_bytes()
    assert characters.apply_edit(_free_ctx(ctx), request, actor='test:human',
                                  reason='按原文新增，程序分配身份') == result
    assert store.path(characters.REL).read_bytes() == frozen
    assert ctx.ledger.path.read_bytes() == ledger


def test_character_add_local_handles_cannot_repeat_or_supply_persistent_ids():
    from tests.stage2_fixtures import character_response
    character = character_response()['characters'][0].copy()
    del character['character_ref']
    # schema 层采用合法证据 ID；本例只验证身份权责，不跳过真实 producer 的证据检查。
    from novel.stages.stage2.core_evidence import evidence_slots
    for _path, owner, field in evidence_slots(character):
        owner[field] = ['ev_' + 'a' * 24]
    operation = {'kind': 'add', 'character_ref': 'c1', 'character': character}
    with pytest.raises(ValidationError, match='局部引用不得重复'):
        CharactersEdit.model_validate({'source_sha256': 'a' * 64, 'operations': [operation, operation]})
    with pytest.raises(ValidationError, match='char_id'):
        CharactersEdit.model_validate({'source_sha256': 'a' * 64, 'operations': [
            {**operation, 'char_id': 'char_001'}]})


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('kind', ['replace', 'delete'])
def test_character_edit_cannot_guess_and_modify_same_batch_new_id(tmp_path, reverse, kind):
    ctx = _ctx(tmp_path)
    _generate(ctx)
    store = ArtifactStore(ctx)
    original = store.path(characters.REL).read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    template = characters.edit_template(_free_ctx(ctx))
    row = copy.deepcopy(template['operations'][0]['character'])
    guessed = ({'kind': kind, 'char_id': 'char_003', 'character': row} if kind == 'replace'
               else {'kind': kind, 'char_id': 'char_003', 'reason': '试图删除本批新增身份'})
    operations = [{'kind': 'add', 'character_ref': 'c9', 'character': row}, guessed]
    if reverse:
        operations.reverse()
    request = CharactersEdit.model_validate({**template, 'operations': operations})
    with pytest.raises(TerminalState, match='原基线不存在'):
        characters.apply_edit(_free_ctx(ctx), request, actor='test', reason='不能猜新增正式 ID')
    assert store.path(characters.REL).read_bytes() == original
    assert ctx.ledger.path.read_bytes() == ledger


@pytest.mark.parametrize('field,value,rule', [
    ('identity_revealed_from', 99, 'address_from_out_of_range'),
    ('surface', '不存在的称谓', 'address_unlocatable'),
])
def test_character_address_hard_gate_and_free_repair_share_the_real_producer(
        tmp_path, field, value, rule):
    ctx = _ctx(tmp_path)
    _generate(ctx)
    store = ArtifactStore(ctx)
    formal = store.path(characters.REL).read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    template = characters.edit_template(_free_ctx(ctx))
    row = copy.deepcopy(template['operations'][0])
    # 添加第二称谓，保留 canonical 恰好一次这一结构约束。
    row['character']['aliases'].append(copy.deepcopy(row['character']['aliases'][0]))
    row['character']['aliases'][-1]['surface'] = '宝珠'
    original = row['character']['aliases'][-1][field]
    row['character']['aliases'][-1][field] = value
    with pytest.raises(GateBlocked):
        characters.apply_edit(
            _free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [row]}),
            actor='test:human', reason='验证称谓引用硬闸')
    assert store.path(characters.REL).read_bytes() == formal
    assert store.exists(characters.INVALID)
    passed, report = characters.revalidate(
        _free_ctx(ctx), actor='test:human', reason='失败稿不能免费洗白')
    assert not passed
    assert rule in {hit.rule for hit in report.rules
                    if hit.sev == 'hard' and hit.status in {'hit', 'skipped'}}
    repair = characters.edit_template(_free_ctx(ctx))
    fixed = next(op for op in repair['operations'] if op['char_id'] == row['char_id'])
    fixed['character']['aliases'][-1][field] = original
    characters.apply_edit(
        _free_ctx(ctx), CharactersEdit.model_validate({**repair, 'operations': [fixed]}),
        actor='test:human', reason='按原文修复称谓引用')
    assert not store.exists(characters.INVALID)
    assert store.freshness(characters.REL).state == 'fresh'
    assert ctx.ledger.path.read_bytes() == ledger


def test_local_character_ids_are_frozen_and_evidence_deduplicates():
    from tests.test_stage2 import main_text
    from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable
    table = GlobalEvidenceCandidateTable.build(main_text())
    response = CharactersResponse.model_validate(_response())
    compiled = characters.compile_response(response, table, identity_map={'c1': 'char_017'},
                                           reserved_ids=('char_099',))
    assert compiled.character_ids == {'c1': 'char_017', 'c2': 'char_100'}
    first = compiled.characters['char_017']
    assert first['aliases'][0]['evidence_refs'] == first['first_appearance_evidence_refs']
    assert all(set(anchor) == {'chapter_ordinal', 'start', 'end', 'text_sha256'}
               for anchor in compiled.evidence_anchors.values())
    reversed_response = response.model_copy(update={'characters': list(reversed(response.characters))})
    assert characters.compile_response(reversed_response, table,
        identity_map={'c1': 'char_017'}, reserved_ids=('char_099',)) == compiled


def test_same_surface_multiple_entities_is_allowed_but_each_binding_needs_ownership_evidence():
    raw = _response()
    second = copy.deepcopy(raw['characters'][0])
    second['character_ref'] = 'c3'
    raw['characters'].append(second)
    assert len(CharactersResponse.model_validate(raw).characters) == 3
    raw['characters'][0]['aliases'].append(copy.deepcopy(raw['characters'][0]['aliases'][0]))
    with pytest.raises(ValueError, match='唯一称谓'):
        CharactersResponse.model_validate(raw)


def test_identity_reveal_does_not_ban_a_previously_visible_surface():
    from novel.stages.stage2.character_refs import visible_character_refs
    inventory = {'char_001': {'canonical': '裴书远', 'aliases': [
        {'surface': '新邻居', 'identity_revealed_from': 11},
        {'surface': '裴书远', 'identity_revealed_from': 11}]}}
    visible = visible_character_refs(inventory, [
        {'chapter_ordinal': 10, 'text': '新邻居搬来。'},
        {'chapter_ordinal': 11, 'text': '新邻居原来是裴书远。'}])
    assert visible[10] == {'char_001': ['新邻居']}
    assert visible[11] == {'char_001': ['新邻居', '裴书远']}


def test_character_edit_preflight_is_readonly_and_exact_candidate_commits(tmp_path):
    ctx = _ctx(tmp_path)
    _generate(ctx)
    store = ArtifactStore(ctx)
    before = store.path(characters.REL).read_bytes()
    free = _free_ctx(ctx)
    template = characters.edit_template(free)
    row = template['operations'][0]
    row['character']['role'] += '，负责家事'
    edit = CharactersEdit.model_validate({**template, 'operations': [row]})
    candidate = characters.preflight_edit(free, edit, actor='test', reason='独立预检', at='2026-09-12T00:00:00Z')
    assert store.path(characters.REL).read_bytes() == before
    assert not store.exists(characters.STAGE2_CHARACTERS_EDIT)
    landed = characters.commit_prepared_edit(_free_ctx(ctx), edit, candidate,
        expected_source_sha256=edit.source_sha256, actor='test', reason='独立预检')
    assert landed == candidate
    assert characters.commit_prepared_edit(_free_ctx(ctx), edit, candidate,
        expected_source_sha256=edit.source_sha256, actor='test', reason='独立预检') == candidate


def test_frozen_character_baseline_is_read_from_exact_archived_bytes(tmp_path):
    ctx = _ctx(tmp_path)
    previous, _ = _generate(ctx)
    store = ArtifactStore(ctx)
    old_sha = store.full_sha256_or_none(characters.REL)
    template = characters.edit_template(_free_ctx(ctx))
    row = template['operations'][0]
    row['character']['role'] += '，负责家事'
    characters.apply_edit(_free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [row]}),
                          actor='test', reason='制造历史基线')
    assert store.read_own_by_sha256(type(previous), characters.REL, old_sha) == previous
    with pytest.raises(ProvenanceRefused, match='没有完整 SHA'):
        store.read_own_by_sha256(type(previous), characters.REL, 'f' * 64)
