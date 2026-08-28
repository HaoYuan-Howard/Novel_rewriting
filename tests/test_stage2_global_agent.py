#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global executor agent 的候选—校验—resume—落盘纵切。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema.evaluation import GlobalReview
from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    GlobalAgentTurnResponse, GlobalGrounding, GlobalGroundingResponse,
)
from novel import agent_session_audit, prov
from novel.agents import AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage
from novel.agents.session_client import agent_json_schema
from novel.errors import GateBlocked
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import AUDIT_REL, CostLedger, ledger_path
from novel.node_agents import Stage2GlobalAgent
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import _response_global


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path) -> RunContext:
    book = base / 'agent纵切_2042'
    book.mkdir()
    clean = book / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True)
    clean.write_text('synthetic stage0 root\n', encoding='utf-8', newline='')
    ctx = RunContext(
        novel_dir=book, node='stage2.global', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)), executor_profile=_profile())
    ArtifactStore(ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    return ctx


def _bad_global() -> dict:
    bad = copy.deepcopy(_response_global())
    bad['entities']['characters']['char_002']['canonical'] = \
        bad['entities']['characters']['char_001']['canonical']
    return bad


def _full(candidate: dict) -> str:
    return json.dumps({
        'payload': {'kind': 'full_candidate', 'candidate': candidate},
    }, ensure_ascii=False)


def _bad_evidence_global() -> dict:
    bad = copy.deepcopy(_response_global())
    bad['entities']['relations'][0]['predicate_evidence']['locator'] = \
        '全书没有这句关系证据'
    return bad


def _evidence_patch(
    candidate: dict,
    *,
    candidate_sha256: str | None = None,
    claim_path: str = 'entities.relations.rel_01.predicate',
    chapter: int = 2,
    locator: str = '回来时看见母亲在门口等她。',
) -> str:
    sha = candidate_sha256 or extract.global_response_sha256(
        GlobalGroundingResponse.model_validate(candidate))
    return json.dumps({
        'payload': {
            'kind': 'evidence_patch',
            'candidate_sha256': sha,
            'changes': [{
                'claim_path': claim_path,
                'evidence': {
                    'chapter_ordinal': chapter,
                    'locator': locator,
                },
            }],
        },
    }, ensure_ascii=False)


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, responses: list[str], book: Path) -> None:
        self.responses = list(responses)
        self.book = book
        self.calls: list[tuple[str, AgentRequest]] = []

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver=self.driver, provider=self.provider, executable='fake-codex',
            available=True, version='0.test')

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id='stage2-global-session', content=self.responses.pop(0),
            model='gpt-5.6-sol', actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=100, completion_tokens=20, reasoning_tokens=10,
                total_tokens=120),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', request))
        return self._turn()

    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        assert session_id == 'stage2-global-session'
        # 最关键的中间态边界：首轮 HARD 只在内存，resume 发起前盘上没有问题稿、
        # 正式稿或 review，也没有一轮伪历史。
        assert not (self.book / extract.GLOBAL_REL).exists()
        assert not (self.book / extract._GLOBAL_INVALID).exists()
        assert not (self.book / extract.GLOBAL_REVIEW_REL).exists()
        assert not list((self.book / 'stage2/_rounds').glob(
            'global_grounding*.json'))
        self.calls.append(('resume', request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


def _rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_hard_candidate_resumes_same_session_then_lands_once(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(_bad_global()),
        _full(_response_global()),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=3).run()

    assert result.turns == 2
    assert [kind for kind, _request in driver.calls] == ['start', 'resume']
    first, resumed = (call[1] for call in driver.calls)
    assert first.system is not None and resumed.system is None
    assert first.cwd == resumed.cwd
    assert resumed.execution_policy == 'executor-isolated'
    assert 'code_validation_failed' in resumed.prompt
    assert 'canonical_unique' in resumed.prompt
    assert 'full_candidate' in resumed.prompt
    assert first.output_schema == resumed.output_schema
    assert main_text()[0]['text'][:30] in first.prompt
    assert main_text()[0]['text'][:30] not in resumed.prompt

    store = ArtifactStore(ctx)
    landed = store.read(GlobalGrounding, extract.GLOBAL_REL)
    review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
    assert landed == result.artifact
    assert landed.provenance.usage is not None
    assert landed.provenance.usage.calls_made == 2
    assert review.items and not store.exists(extract._GLOBAL_INVALID)

    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert len(record.turns) == 2
    assert record.process_state == 'ok'
    assert record.delivery_state == 'delivered'
    ledger = _rows(ledger_path(tmp_path))
    assert {row['attempt_id'] for row in ledger} == {
        turn.attempt_id for turn in record.turns}

    llm_audit = _rows(tmp_path.joinpath(*AUDIT_REL))
    decisions = [row for row in llm_audit if row['kind'] == 'gate_decision']
    assert [row['decision'] for row in decisions] == [
        'accepted', 'rejected', 'accepted']
    adoption, = [row for row in llm_audit if row['kind'] == 'artifact_adoption']
    assert adoption['attempt_id'] == record.turns[-1].attempt_id
    assert not [row for row in llm_audit
                if row['kind'] == 'candidate_patch_applied']


def test_evidence_hard_resumes_with_hash_bound_path_restricted_patch(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _bad_evidence_global()
    driver = _Driver([
        _full(bad),
        _evidence_patch(bad),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=3).run()

    assert result.turns == 2
    first, resumed = (request for _kind, request in driver.calls)
    assert first.output_schema == resumed.output_schema
    assert first.output_schema == agent_json_schema(GlobalAgentTurnResponse)
    assert 'evidence_patch' in resumed.prompt
    assert 'entities.relations.rel_01.predicate' in resumed.prompt
    assert extract.global_response_sha256(
        GlobalGroundingResponse.model_validate(bad)) in resumed.prompt
    feedback = json.loads(resumed.prompt)
    contract = feedback['failures'][0]['repair_contract']
    assert contract['allowed_paths'] == [
        'entities.relations.rel_01.predicate']
    assert set(contract) == {
        'kind', 'candidate_sha256', 'allowed_paths', 'failures', 'instruction'}

    relation = result.artifact.entities.relations[0]
    assert relation.predicate == '母女'
    assert relation.predicate_evidence.chapter_ordinal == 2
    assert relation.predicate_evidence.source is not None
    assert relation.predicate_evidence.source.text == '回来时看见母亲在门口等她。'

    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    audit_rows = _rows(tmp_path.joinpath(*AUDIT_REL))
    adoption, = [row for row in audit_rows
                 if row['kind'] == 'artifact_adoption']
    assert adoption['attempt_id'] == record.turns[-1].attempt_id
    patch_event, = [row for row in audit_rows
                    if row['kind'] == 'candidate_patch_applied']
    assert patch_event['attempt_id'] == record.turns[-1].attempt_id
    assert patch_event['base_candidate_sha256'] == \
        extract.global_response_sha256(
            GlobalGroundingResponse.model_validate(bad))
    assert patch_event['result_candidate_sha256'] == \
        extract.global_response_sha256(
            GlobalGroundingResponse.model_validate(_response_global()))
    assert patch_event['changed_path_count'] == 1
    assert patch_event['sid'] == ctx.sid
    assert patch_event['node'] == patch_event['purpose'] == ctx.node
    assert 'path' not in patch_event and 'locator' not in patch_event
    assert '回来时看见母亲在门口等她。' not in json.dumps(
        patch_event, ensure_ascii=False)


def test_evidence_patch_keeps_progress_and_rebinds_only_new_failures(tmp_path):
    ctx = _ctx(tmp_path)
    good = _response_global()
    bad = copy.deepcopy(good)
    bad['entities']['relations'][0]['predicate_evidence']['locator'] = \
        '全书没有这句关系证据'
    bad['canon']['secrets'][0]['fact_evidence']['locator'] = \
        '全书没有这句秘密证据'

    intermediate = copy.deepcopy(bad)
    intermediate['entities']['relations'][0]['predicate_evidence'] = \
        copy.deepcopy(good['entities']['relations'][0]['predicate_evidence'])
    intermediate['canon']['secrets'][0]['fact_evidence'] = {
        'chapter_ordinal': 1,
        'locator': '这句秘密证据仍然不存在',
    }

    def patch(candidate: dict, changes: list[dict]) -> str:
        return json.dumps({
            'payload': {
                'kind': 'evidence_patch',
                'candidate_sha256': extract.global_response_sha256(
                    GlobalGroundingResponse.model_validate(candidate)),
                'changes': changes,
            },
        }, ensure_ascii=False)

    first_patch = patch(bad, [{
        'claim_path': 'entities.relations.rel_01.predicate',
        'evidence': good['entities']['relations'][0]['predicate_evidence'],
    }, {
        'claim_path': 'canon.secrets.secret_01.fact',
        'evidence': intermediate['canon']['secrets'][0]['fact_evidence'],
    }])
    second_patch = patch(intermediate, [{
        'claim_path': 'canon.secrets.secret_01.fact',
        'evidence': good['canon']['secrets'][0]['fact_evidence'],
    }])
    driver = _Driver([
        _full(bad), first_patch, second_patch,
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=3).run()

    assert result.turns == 3
    assert result.artifact.entities.relations[0].predicate_evidence.source is not None
    assert result.artifact.canon.secrets[0].fact_evidence.source is not None
    second_resume = json.loads(driver.calls[2][1].prompt)
    contract = second_resume['failures'][0]['repair_contract']
    assert contract['candidate_sha256'] == extract.global_response_sha256(
        GlobalGroundingResponse.model_validate(intermediate))
    assert contract['allowed_paths'] == ['canon.secrets.secret_01.fact']

    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    rows = _rows(tmp_path.joinpath(*AUDIT_REL))
    patches = [row for row in rows if row['kind'] == 'candidate_patch_applied']
    assert len(patches) == 2
    assert [row['changed_path_count'] for row in patches] == [2, 1]
    adoption, = [row for row in rows if row['kind'] == 'artifact_adoption']
    assert adoption['attempt_id'] == record.turns[-1].attempt_id


def test_multi_evidence_patch_rejects_a_missing_allowed_path(tmp_path):
    ctx = _ctx(tmp_path)
    good = _response_global()
    bad = copy.deepcopy(good)
    bad['entities']['relations'][0]['predicate_evidence']['locator'] = \
        '不存在的关系证据'
    bad['canon']['secrets'][0]['fact_evidence']['locator'] = \
        '不存在的秘密证据'
    candidate_sha = extract.global_response_sha256(
        GlobalGroundingResponse.model_validate(bad))

    def payload(changes: list[dict]) -> str:
        return json.dumps({
            'payload': {
                'kind': 'evidence_patch',
                'candidate_sha256': candidate_sha,
                'changes': changes,
            },
        }, ensure_ascii=False)

    relation_change = {
        'claim_path': 'entities.relations.rel_01.predicate',
        'evidence': good['entities']['relations'][0]['predicate_evidence'],
    }
    secret_change = {
        'claim_path': 'canon.secrets.secret_01.fact',
        'evidence': good['canon']['secrets'][0]['fact_evidence'],
    }
    driver = _Driver([
        _full(bad),
        payload([relation_change]),
        payload([relation_change, secret_change]),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=3).run()

    assert result.passed and result.turns == 3
    validation_feedback = json.loads(driver.calls[2][1].prompt)
    detail = validation_feedback['validator_feedback']
    assert 'missing=' in detail
    assert 'canon.secrets.secret_01.fact' in detail
    patch_events = [
        row for row in _rows(tmp_path.joinpath(*AUDIT_REL))
        if row['kind'] == 'candidate_patch_applied']
    assert [row['changed_path_count'] for row in patch_events] == [2]


@pytest.mark.parametrize('bad_patch', ['hash', 'path', 'noop'])
def test_bad_evidence_patch_is_rejected_without_mutating_candidate(
    tmp_path, bad_patch: str,
):
    ctx = _ctx(tmp_path)
    bad = _bad_evidence_global()
    if bad_patch == 'hash':
        rejected = _evidence_patch(bad, candidate_sha256='0' * 64)
    elif bad_patch == 'path':
        rejected = _evidence_patch(
            bad, claim_path='canon.setting.时代背景',
            chapter=1, locator='春风吹过老宅的院墙。')
    else:
        rejected = _evidence_patch(
            bad, chapter=2, locator='全书没有这句关系证据')
    driver = _Driver([
        _full(bad), rejected, _evidence_patch(bad),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=3).run()

    assert result.turns == 3
    assert [kind for kind, _request in driver.calls] == [
        'start', 'resume', 'resume']
    assert len({json.dumps(request.output_schema, sort_keys=True)
                for _kind, request in driver.calls}) == 1
    assert result.artifact.entities.relations[0].predicate == '母女'
    assert result.artifact.entities.relations[0] \
        .predicate_evidence.chapter_ordinal == 2
    patch_events = [
        row for row in _rows(tmp_path.joinpath(*AUDIT_REL))
        if row['kind'] == 'candidate_patch_applied']
    assert len(patch_events) == 1
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert patch_events[0]['attempt_id'] == record.turns[-1].attempt_id


def test_mixed_evidence_and_other_hard_requires_full_candidate(tmp_path):
    ctx = _ctx(tmp_path)
    mixed = _bad_evidence_global()
    mixed['entities']['characters']['char_002']['canonical'] = \
        mixed['entities']['characters']['char_001']['canonical']
    driver = _Driver([
        _full(mixed),
        _evidence_patch(mixed),
        _full(_response_global()),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=3).run()

    assert result.passed and result.turns == 3
    first_feedback = json.loads(driver.calls[1][1].prompt)
    assert {row['rule'] for row in first_feedback['failures']} >= {
        'canonical_unique', 'global_evidence_unlocated'}
    assert all(row['repair_contract']['kind'] == 'full_candidate'
               for row in first_feedback['failures'])
    second_feedback = json.loads(driver.calls[2][1].prompt)
    assert '本轮只接受 full_candidate' in \
        second_feedback['validator_feedback']
    rows = _rows(tmp_path.joinpath(*AUDIT_REL))
    assert not [row for row in rows if row['kind'] == 'candidate_patch_applied']


def test_global_agent_envelope_rejects_duplicate_or_extra_patch_fields():
    change = {
        'claim_path': 'entities.relations.rel_01.predicate',
        'evidence': {'chapter_ordinal': 2, 'locator': '母亲在门口等她'},
    }
    with pytest.raises(ValidationError, match='claim_path 不能重复'):
        GlobalAgentTurnResponse.model_validate({
            'payload': {
                'kind': 'evidence_patch',
                'candidate_sha256': 'a' * 64,
                'changes': [change, copy.deepcopy(change)],
            },
        })
    with pytest.raises(ValidationError, match='extra_forbidden'):
        GlobalAgentTurnResponse.model_validate({
            'payload': {
                'kind': 'evidence_patch',
                'candidate_sha256': 'a' * 64,
                'changes': [change],
                'candidate': _response_global(),
            },
        })


def test_turn_limit_lands_only_the_last_typed_invalid(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _full(_bad_global())
    driver = _Driver([bad, bad], ctx.novel_dir)

    with pytest.raises(GateBlocked, match='canonical_unique'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    store = ArtifactStore(ctx)
    assert len(driver.calls) == 2
    assert store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    assert not list((ctx.novel_dir / 'stage2/_rounds').glob(
        'global_grounding.INVALID.r*.json'))
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'blocked'


def test_schema_exhaustion_after_business_reject_preserves_recovery_candidate(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(_bad_global()),
        'not-json',
    ], ctx.novel_dir)

    with pytest.raises(GateBlocked, match='canonical_unique'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    invalid = ArtifactStore(ctx).read(
        GlobalGrounding, extract._GLOBAL_INVALID)
    assert invalid.provenance.usage is not None
    assert invalid.provenance.usage.calls_made == 2
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'blocked'


def test_all_schema_bad_has_no_artifact_and_is_not_called_delivered(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver(['not-json', 'still-not-json'], ctx.novel_dir)

    with pytest.raises(LlmFailed, match='已用尽 2 轮'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    store = ArtifactStore(ctx)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'failed'
