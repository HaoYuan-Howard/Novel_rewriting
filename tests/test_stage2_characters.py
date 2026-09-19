# -*- coding: utf-8 -*-
"""人物 v3 producer：Extract→Resolve 直连生成、typed 编辑与免费复检烟测。

CharactersResponse 生产臂已退役；本文件不再断言 v2 wire / compile_response。
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING
from contracts.schema.characters import CharactersEdit
from novel.errors import ProvenanceRefused, TerminalState
from novel.stages.stage2 import characters
from novel.store import ArtifactStore
from tests.stage2_fixtures import character_response
from tests.test_stage2_global_agent import (
    _ctx as _global_ctx, _free_ctx,
)


def _ctx(base):
    """共享盘面夹具：保留 executor_profile 供同文件族的 global/plot agent 测试。"""
    ctx = _global_ctx(base, with_characters=False)
    ctx.node = characters.NODE
    return ctx


def _dctx(ctx):
    """直连生成投影：剥掉 agent profile；盘上账同路，内存刻度全新。"""
    from novel.llm.ledger import CostLedger
    from novel.run import RunContext
    return RunContext(
        novel_dir=ctx.novel_dir, node=ctx.node, paid=ctx.paid, code=ctx.code,
        cache=ctx.cache,
        ledger=CostLedger(path=ctx.ledger.path, run_id=ctx.run_id,
                          parent_run_id=ctx.parent_run_id),
        run_id=ctx.run_id, parent_run_id=ctx.parent_run_id)


def _v3_wires(*, source=None, view=None):
    """确定性 Extract + Resolve fake wire（与 stage2_fixtures.generate_characters 同源）。"""
    from tests.test_stage2 import main_text

    rows = main_text() if source is None else source
    legacy = character_response(source=rows, view=view)
    surfaces = sorted({
        alias['surface']
        for row in legacy['characters']
        for alias in row['aliases']
    })
    extract_wire = {
        'chapters': [
            {'chapter_ordinal': int(section['chapter_ordinal']), 'surfaces': surfaces}
            for section in rows
        ],
    }
    seeds = []
    bindings = []
    seen: set[str] = set()
    for row in legacy['characters']:
        seeds.append({
            'character_ref': row['character_ref'],
            'canonical_surface': row['canonical'],
            'status': 'confirmed',
        })
        for alias in row['aliases']:
            surface = alias['surface']
            if surface in seen:
                continue
            seen.add(surface)
            kind = 'proper_name' if surface == row['canonical'] else 'short_name'
            bindings.append({
                'surface': surface,
                'disposition': 'bound',
                'kind': kind,
                'character_ref': row['character_ref'],
                'identity_revealed_from': alias['identity_revealed_from'],
            })
    return extract_wire, {'characters': seeds, 'surface_bindings': bindings}


def _generate(ctx, script=None, *, expect_blocked=False):
    """直连 v3 生成；默认剧本为 Extract→Resolve 两步。

    返回 (artifact, transport)。
    """
    from tests.test_stage2_generation import _client, _ok

    if script is None:
        script = list(_v3_wires())

    def respond(_request, index):
        item = script[index] if index < len(script) else script[-1]
        return _ok(item)

    run_ctx = _dctx(ctx)
    client, transport = _client(run_ctx, respond)
    if expect_blocked:
        from novel.errors import GateBlocked
        with pytest.raises(GateBlocked):
            characters.run(run_ctx, client)
        return None, transport
    return characters.run(run_ctx, client), transport


def test_independent_characters_generate_through_real_producer_and_store(tmp_path):
    ctx = _ctx(tmp_path)
    artifact, transport = _generate(ctx)
    assert len(transport.calls) == 2  # extract + resolve
    store = ArtifactStore(ctx)
    assert store.exists(characters.REL)
    assert not store.exists(STAGE2_GLOBAL_GROUNDING)
    assert store.freshness(characters.REL).state == 'fresh'
    assert set(artifact.provenance.inputs) == {STAGE1_MAIN_TEXT}
    assert all(hasattr(row, 'canonical_surface') for row in artifact.characters.values())
    assert all(hasattr(row, 'first_mention_chapter') for row in artifact.characters.values())
    assert artifact.mentions


def test_characters_edit_replace_preserves_provenance_and_is_idempotent(tmp_path):
    ctx = _ctx(tmp_path)
    previous, _transport = _generate(ctx)
    free = _free_ctx(ctx)
    template = characters.edit_template(free)
    row = copy.deepcopy(template['operations'][0])
    row['canonical_surface'] = row['canonical_surface'] + '改'
    edit = CharactersEdit.model_validate({**template, 'operations': [row]})
    updated = characters.apply_edit(free, edit, actor='human-test', reason='核对静态身份')
    assert updated.characters[row['char_id']].canonical_surface != (
        previous.characters[row['char_id']].canonical_surface)
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


def test_character_edit_invalid_target_preserves_formal_projection_and_cost(tmp_path):
    ctx = _ctx(tmp_path)
    _generate(ctx)
    store = ArtifactStore(ctx)
    before = {rel: store.path(rel).read_bytes()
              for rel in (characters.REL, characters.PROJECTION)}
    ledger = ctx.ledger.path.read_bytes()
    template = characters.edit_template(_free_ctx(ctx))
    row = {'kind': 'replace', 'char_id': 'char_999',
           'canonical_surface': '不存在', 'status': 'confirmed'}
    with pytest.raises(TerminalState, match='不存在'):
        characters.apply_edit(
            _free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [row]}),
            actor='test:human', reason='验证目标存在性')
    assert {rel: store.path(rel).read_bytes() for rel in before} == before
    assert ctx.ledger.path.read_bytes() == ledger
    assert not store.exists(characters.INVALID)


def test_character_add_and_delete_smoke(tmp_path):
    ctx = _ctx(tmp_path)
    previous, _transport = _generate(ctx)
    store = ArtifactStore(ctx)
    ledger = ctx.ledger.path.read_bytes()
    template = characters.edit_template(_free_ctx(ctx))
    retired_id = template['operations'][-1]['char_id']
    characters.apply_edit(_free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [
        {'kind': 'delete', 'char_id': retired_id, 'reason': '验证退休身份不会再分配'}]}),
        actor='test:human', reason='独立删除')
    # seed_surface 必须字面出现在正文，否则无 bound mention，checks 会硬拦
    request = CharactersEdit.model_validate({
        'source_sha256': characters.edit_template(_free_ctx(ctx))['source_sha256'],
        'operations': [{
            'kind': 'add', 'character_ref': 'c7',
            'canonical_surface': '父亲', 'status': 'confirmed',
            'seed_surface': '父亲', 'seed_kind': 'relational_address',
        }],
    })
    result = characters.apply_edit(
        _free_ctx(ctx), request, actor='test:human', reason='按原文新增，程序分配身份')
    assert retired_id not in result.characters
    assert any(row.canonical_surface == '父亲' for row in result.characters.values())
    assert len(result.characters) >= len(previous.characters)
    # 幂等重放不增账
    characters.apply_edit(_free_ctx(ctx), request, actor='test:human', reason='按原文新增，程序分配身份')
    assert ctx.ledger.path.read_bytes() == ledger
    assert store.exists(characters.REL)


def test_character_add_local_handles_cannot_repeat():
    operation = {
        'kind': 'add', 'character_ref': 'c1',
        'canonical_surface': '甲', 'status': 'confirmed',
    }
    with pytest.raises(ValidationError, match='局部引用不得重复'):
        CharactersEdit.model_validate({'source_sha256': 'a' * 64, 'operations': [operation, operation]})


def test_identity_reveal_does_not_ban_a_previously_visible_surface():
    from novel.stages.stage2.shared.character_refs import visible_character_refs
    inventory = {'char_001': {
        'canonical_surface': '裴书远',
        'first_mention_chapter': 10,
        'name_usable_from': 11,
        'surfaces': [
            {'surface': '新邻居', 'kind': 'social_address', 'identity_revealed_from': 11},
            {'surface': '裴书远', 'kind': 'proper_name', 'identity_revealed_from': 11},
        ],
    }}
    # first_mention 10：第 10 章「新邻居」字面进闭集；正式名许可自 11
    inventory['char_001']['first_mention_chapter'] = 10
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
    row = copy.deepcopy(template['operations'][0])
    row['canonical_surface'] = row['canonical_surface'] + '预检'
    edit = CharactersEdit.model_validate({**template, 'operations': [row]})
    candidate = characters.preflight_edit(
        free, edit, actor='test', reason='独立预检', at='2026-09-12T00:00:00Z')
    assert store.path(characters.REL).read_bytes() == before
    assert not store.exists(characters.STAGE2_CHARACTERS_EDIT)
    landed = characters.commit_prepared_edit(
        _free_ctx(ctx), edit, candidate,
        expected_source_sha256=edit.source_sha256, actor='test', reason='独立预检')
    assert landed == candidate
    assert characters.commit_prepared_edit(
        _free_ctx(ctx), edit, candidate,
        expected_source_sha256=edit.source_sha256, actor='test', reason='独立预检') == candidate


def test_frozen_character_baseline_is_read_from_exact_archived_bytes(tmp_path):
    ctx = _ctx(tmp_path)
    previous, _transport = _generate(ctx)
    store = ArtifactStore(ctx)
    old_sha = store.full_sha256_or_none(characters.REL)
    template = characters.edit_template(_free_ctx(ctx))
    row = copy.deepcopy(template['operations'][0])
    row['canonical_surface'] = row['canonical_surface'] + '基线'
    characters.apply_edit(
        _free_ctx(ctx), CharactersEdit.model_validate({**template, 'operations': [row]}),
        actor='test', reason='制造历史基线')
    assert store.read_own_by_sha256(type(previous), characters.REL, old_sha) == previous
    with pytest.raises(ProvenanceRefused, match='没有完整 SHA'):
        store.read_own_by_sha256(type(previous), characters.REL, 'f' * 64)


def test_explicit_backend_runs_the_same_v3_contract(tmp_path):
    from tests.test_stage2_generation import _client, _ok
    ctx = _dctx(_ctx(tmp_path))
    script = list(_v3_wires())
    queue = iter(script)

    def respond(_request, _index):
        return _ok(next(queue))

    client, transport = _client(ctx, respond)
    artifact = characters.run(ctx, client)
    assert len(transport.calls) == 2
    assert artifact.provenance.model == 'test-model'
    assert ArtifactStore(ctx).freshness(characters.REL).state == 'fresh'
    assert not ArtifactStore(ctx).exists(STAGE2_GLOBAL_GROUNDING)
