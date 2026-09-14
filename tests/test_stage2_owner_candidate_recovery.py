# -*- coding: utf-8 -*-
"""已退役窄修的保障由现役人物完整候选承担；累计额度和断点身份保持不变。"""
from __future__ import annotations

import json

import pytest

from contracts.artifacts import STAGE2_GLOBAL_GROUNDING
from contracts.schema.characters import CharactersResponse
from contracts.schema.knowledge import KnowledgeAgentTurnResponse
from novel.errors import ProvenanceRefused
from novel.llm import agent_runs
from novel.orchestrator.node_agents import Stage2CharactersAgent
from novel.stages.stage2 import characters
from novel.store import ArtifactStore
from tests.test_stage2_characters import _ctx, _response, _wire
from tests.test_stage2_global_agent import _Driver, _resume_ctx


def _bad_wire(index: int) -> str:
    raw = _response()
    raw['characters'][0]['role'] = f'账房，最终第{index}次离开老宅'
    return _wire(raw)


@pytest.mark.parametrize('prior_turns', [1, 2, 3])
def test_current_character_full_candidates_share_one_four_turn_budget(tmp_path, prior_turns):
    ctx = _ctx(tmp_path)
    driver = _Driver([*[_bad_wire(i) for i in range(1, prior_turns + 1)], _wire()],
                     ctx.novel_dir)
    result = Stage2CharactersAgent(ctx, driver=driver, max_turns=4).run()
    assert result.passed and result.turns == prior_turns + 1
    assert [kind for kind, _ in driver.calls] == ['start'] + ['resume'] * prior_turns
    assert result.artifact.provenance.usage.calls_made == prior_turns + 1
    for _, request in driver.calls[1:]:
        assert 'role_leaks_fate' in request.prompt
        assert 'role_fate_patch' not in request.prompt
    assert not ArtifactStore(ctx).exists(STAGE2_GLOBAL_GROUNDING)


@pytest.mark.parametrize('prior_turns', [1, 2, 3])
def test_durable_red_character_checkpoint_resumes_only_remaining_turns(
        tmp_path, monkeypatch, prior_turns):
    ctx = _ctx(tmp_path)
    original = agent_runs.write_checkpoint
    written = 0

    def crash_after_checkpoint(*args, **kwargs):
        nonlocal written
        result = original(*args, **kwargs)
        written += 1
        if written == prior_turns:
            raise ProvenanceRefused('模拟坏候选 checkpoint 已保存后退出')
        return result

    monkeypatch.setattr(agent_runs, 'write_checkpoint', crash_after_checkpoint)
    first = _Driver([_bad_wire(i) for i in range(1, prior_turns + 1)], ctx.novel_dir)
    with pytest.raises(ProvenanceRefused, match='checkpoint 已保存'):
        Stage2CharactersAgent(ctx, driver=first, max_turns=4).run()
    before = ctx.ledger.path.read_bytes()
    assert len(first.calls) == prior_turns
    assert not ArtifactStore(ctx).exists(characters.REL)
    monkeypatch.setattr(agent_runs, 'write_checkpoint', original)
    resumed = _resume_ctx(ctx)
    remaining = _Driver([_wire()], ctx.novel_dir, session_id='characters-recovered')
    result = Stage2CharactersAgent(resumed, driver=remaining, max_turns=4).run()
    assert result.passed and result.turns == prior_turns + 1
    assert len(remaining.calls) == 1
    assert result.artifact.provenance.usage.calls_made == prior_turns + 1
    assert ctx.ledger.path.read_bytes().startswith(before)
    assert agent_runs.commit_state(resumed, characters.commit_spec())['state'] == 'committed'
    assert not ArtifactStore(ctx).exists(STAGE2_GLOBAL_GROUNDING)
    committed_ledger = ctx.ledger.path.read_bytes()
    no_calls = _Driver([], ctx.novel_dir)
    replay = Stage2CharactersAgent(_resume_ctx(ctx), driver=no_calls, max_turns=4).run()
    assert replay.passed and not no_calls.calls
    assert replay.artifact == result.artifact
    assert ctx.ledger.path.read_bytes() == committed_ledger


@pytest.mark.parametrize('owner', ['stage2.characters', 'stage2.global'])
@pytest.mark.parametrize('kind', ['role_fate_patch', 'evidence_patch'])
def test_current_owner_wire_rejects_retired_narrow_patch_even_with_valid_hashes(owner, kind):
    payload = {'kind': kind, 'candidate_sha256': 'a' * 64, 'evidence_table_sha256': 'b' * 64,
               'changes': [{'char_id': 'char_001', 'role': '负责核账',
                            'role_evidence': {'evidence_candidate_id': 'e000001'},
                            'fate': '后来离开', 'fate_evidence': {'evidence_candidate_id': 'e000002'}}]}
    model = CharactersResponse if owner == 'stage2.characters' else KnowledgeAgentTurnResponse
    with pytest.raises(ValueError):
        model.model_validate({'payload': payload})
    schema = json.dumps(model.model_json_schema())
    assert '"const": "role_fate_patch"' not in schema
    assert '"const": "evidence_patch"' not in schema

