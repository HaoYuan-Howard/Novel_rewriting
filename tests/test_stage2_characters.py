# -*- coding: utf-8 -*-
"""人物独立 producer 的真实 session、持久提交与人工恢复边界。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING
from contracts.schema.characters import CharactersEdit, CharactersResponse
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.llm import agent_runs
from novel.node_agents import Stage2CharactersAgent, build_node_agent
from novel.stages.stage2 import characters
from novel.store import ArtifactStore
from tests.stage2_fixtures import candidate_view as _response_global
from tests.test_stage2_global_agent import (
    _ctx as _global_ctx, _Driver, _free_ctx, _new_run_ctx, _resume_ctx,
)


def _ctx(base):
    ctx = _global_ctx(base, with_characters=False)
    ctx.node = characters.NODE
    return ctx


def _response():
    rows = _response_global()['entities']['characters']
    return {'characters': [
        {'char_id': cid, **{key: value for key, value in row.items()
                           if key not in {'fate', 'fate_evidence'}}}
        for cid, row in rows.items()
    ]}


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
    bad['characters'][1]['canonical'] = bad['characters'][0]['canonical']
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
    outside['characters'][0]['role_evidence']['evidence_candidate_id'] = 'e999999'
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
    with pytest.raises(TerminalState, match={
            'add': '已经登记', 'replace': '不存在', 'delete': '不存在'}[kind]):
        characters.apply_edit(
            _free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [row]}),
            actor='test:human', reason='验证目标存在性')
    assert {rel: store.path(rel).read_bytes() for rel in before} == before
    assert ctx.ledger.path.read_bytes() == ledger
    assert not store.exists(characters.INVALID)


@pytest.mark.parametrize('field,value,rule', [
    ('by', 'char_999', 'address_by_unresolved'),
    ('from_chapter', 99, 'address_from_out_of_range'),
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
    original = row['character']['address'][0][field]
    row['character']['address'][0][field] = value
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
    fixed['character']['address'][0][field] = original
    characters.apply_edit(
        _free_ctx(ctx), CharactersEdit.model_validate({**repair, 'operations': [fixed]}),
        actor='test:human', reason='按原文修复称谓引用')
    assert not store.exists(characters.INVALID)
    assert store.freshness(characters.REL).state == 'fresh'
    assert ctx.ledger.path.read_bytes() == ledger
