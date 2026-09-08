"""Hook 连续修槽走真实 LlmClient、工作单元账和正式 producer 提交恢复。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from contracts.invariants import HOOK_RECIPE, HOOK_SLOTS
from contracts.schema.report import finding
from contracts.schema.stage6 import HookCandidateRetryResponse, Mech
from novel.llm.cache import CachePolicy, cache_key
from novel.llm.client import LlmClient
from novel.llm.ledger import audit_path, ledger_path, read_jsonl_rows
from novel.llm.transport import ChatResponse
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage6 import hook, prompts
from novel.store import ArtifactStore
from novel.work_units import WorkUnitBudgetExhausted, budget_for
from tests.test_stage2_generation import _ctx
from tests.test_stage6_legacy_delivery_recovery import (
    _crash_after_first_adoption, _response, _rig,
)


_REPAIR_MARKER = '── 单候选定向修复 ──\n'


def _sequence_rig(tmp_path, monkeypatch, hard_counts: tuple[int, ...], *,
                  cached_initial: bool = False, malformed_first: bool = False,
                  repeated_last: bool = False):
    """只控制合成回答和 HARD 证据；生产、解析、成本、预算、来源和盘面均用真链。"""
    ctx, source, wire, _make_client, _up = _rig(tmp_path, monkeypatch)
    texts = [f'首槽第{index}版候选。' for index in range(len(hard_counts))]
    rationales = [f'整组说明采用首槽第{index}版。' for index in range(len(hard_counts))]
    if repeated_last:
        assert hard_counts[-1] == hard_counts[-2]
        texts[-1], rationales[-1] = texts[-2], rationales[-2]
    counts = dict(zip(texts, hard_counts))

    def judge(text, _up):
        # 同一注册 HARD 可报告多段独立证据；不改严重度或用假 PASS 洗掉问题。
        findings = [finding(
            'hook_truncated', detail=f'合成片段{index}尚未完整',
            evidence=[f'{text} 未完成片段{index}'])
            for index in range(counts.get(text, 0))]
        return text, Mech(punct_fixed=0, quotes_fixed=0, paras_fixed=0), findings

    monkeypatch.setattr(hook, '_normalize_and_judge', judge)
    initial = {
        'candidates': [
            {'approach': approach, 'text': texts[0] if index == 0 else f'完整旁槽{index}。'}
            for index, approach in enumerate(HOOK_RECIPE)],
        'rationale': rationales[0],
    }
    repair_requests: list[dict[str, Any]] = []
    next_version = 1
    malformed_sent = False

    def respond(request, _index):
        nonlocal next_version, malformed_sent
        if _REPAIR_MARKER not in request.user:
            return _response(initial)
        # 格式纠错可能在原请求后追加诊断；只解码原单槽工作包。
        repair, _end = json.JSONDecoder().raw_decode(request.user.split(_REPAIR_MARKER, 1)[1])
        assert repair['target_slot'] == HOOK_SLOTS[0]
        assert repair['target_approach'] == HOOK_RECIPE[0]
        repair_requests.append(repair)
        if malformed_first and not malformed_sent:
            malformed_sent = True
            return ChatResponse(
                content='这份回答不是 JSON。', finish_reason='stop', model='test-model',
                usage={'prompt_tokens': 10, 'completion_tokens': 20})
        assert next_version < len(texts), '没有进展或预算耗尽后不应再购买一份回答'
        response = _response({
            'approach': repair['target_approach'], 'text': texts[next_version],
            'rationale': rationales[next_version],
        })
        next_version += 1
        return response

    wire.responder = respond
    if cached_initial:
        prepared = hook.prepare_hook_candidate(ctx)
        request = source._chat_request(
            purpose=ctx.node, model=source.model, system=prepared.system, user=prepared.user,
            reasoning_effort=source._rungs[0][0], thinking=source._rungs[0][1],
            json_mode=True, max_tokens=hook.MAX_TOKENS)
        source.cache.put(cache_key(request), _response(initial))
    return ctx, source, wire, texts, rationales, repair_requests


def _adoptions(base: Path) -> list[dict[str, Any]]:
    return [row for row in read_jsonl_rows(audit_path(base))
            if row.get('kind') == 'artifact_adoption' and row.get('artifact') == hook.REL]


def _proof(ctx) -> dict[str, Any]:
    saved = budget_for(ctx, scope='book').producer_recoveries()
    assert len(saved) == 1
    return saved[0][1]


def test_same_slot_repairs_two_to_one_to_zero_with_current_feedback(tmp_path, monkeypatch):
    ctx, source, wire, texts, rationales, requests = _sequence_rig(
        tmp_path, monkeypatch, (2, 1, 0))
    result = hook.run_hook(ctx, source)
    assert len(wire.calls) == 3
    assert budget_for(ctx, scope='book').used == 3
    assert [(row.slot, row.attempt, row.before_hard, row.after_hard, row.status)
            for row in result.provenance.candidate_retries] == [
                (HOOK_SLOTS[0], 1, 2, 1, 'accepted'),
                (HOOK_SLOTS[0], 2, 1, 0, 'accepted')]
    assert requests[1]['current_candidates'][0]['text'] == texts[1]
    assert requests[1]['current_rationale'] == rationales[1]
    assert any(role == 'assistant' and texts[1] in content
               for role, content in wire.calls[-1].history)
    assert result.candidates[0].text == texts[-1] and not result.candidates[0].findings
    assert result.rationale == rationales[-1] and result.approved is None
    costs = read_jsonl_rows(ledger_path(tmp_path))
    assert len(costs) == 3 and all(row['cached'] is False for row in costs)
    assert {row['attempt_id'] for row in _adoptions(tmp_path)} == {
        costs[0]['attempt_id'], costs[-1]['attempt_id']}


@pytest.mark.parametrize('cached_initial', [False, True])
def test_continuous_improvement_uses_four_book_turns_and_fifth_never_reaches_wire(
        tmp_path, monkeypatch, cached_initial):
    hard_counts = (5, 4, 3, 2, 1) if cached_initial else (4, 3, 2, 1)
    ctx, source, wire, texts, rationales, _requests = _sequence_rig(
        tmp_path, monkeypatch, hard_counts, cached_initial=cached_initial)
    result = hook.run_hook(ctx, source)
    assert len(wire.calls) == 4
    budget = budget_for(ctx, scope='book')
    assert budget.used == 4 and budget.remaining == 0
    accepted = [row for row in result.provenance.candidate_retries if row.accepted]
    expected = ([(1, 5, 4), (2, 4, 3), (3, 3, 2), (4, 2, 1)] if cached_initial
                else [(1, 4, 3), (2, 3, 2), (3, 2, 1)])
    assert [(row.attempt, row.before_hard, row.after_hard) for row in accepted] == expected
    costs = read_jsonl_rows(ledger_path(tmp_path))
    assert sum(row['cached'] is False for row in costs) == 4
    assert sum(row['cached'] is True for row in costs) == int(cached_initial)
    assert result.candidates[0].text == texts[-1] and len(result.candidates[0].findings) == 1
    assert result.rationale == rationales[-1] and result.approved is None
    # 另一次实际内容调用仍经原 LlmClient 准入，不直接操作预算模拟拒绝。
    frozen = ledger_path(tmp_path).read_bytes()
    with pytest.raises(WorkUnitBudgetExhausted, match='4/4'):
        source.ask_json(
            prompts.hook_retry_system(), '请求第五次修订。',
            model_cls=HookCandidateRetryResponse, purpose=f'{ctx.node}:retry:{HOOK_SLOTS[0]}',
            max_tokens=hook.CANDIDATE_RETRY_MAX_TOKENS,
            rungs=hook.CANDIDATE_RETRY_RUNGS, cache_policy=CachePolicy.refresh())
    assert len(wire.calls) == 4 and ledger_path(tmp_path).read_bytes() == frozen
    assert budget_for(ctx, scope='book').used == 4


def test_identical_nonprogress_answer_stops_before_four_turn_limit(tmp_path, monkeypatch):
    ctx, source, wire, texts, rationales, _requests = _sequence_rig(
        tmp_path, monkeypatch, (2, 1, 1), repeated_last=True)
    result = hook.run_hook(ctx, source)
    assert len(wire.calls) == 3 and budget_for(ctx, scope='book').remaining == 1
    assert [row.status for row in result.provenance.candidate_retries] == [
        'accepted', 'hard_not_reduced']
    assert result.candidates[0].text == texts[1] and result.rationale == rationales[1]
    assert len(result.candidates[0].findings) == 1


def test_format_correction_and_content_repairs_share_the_same_four_turn_account(
        tmp_path, monkeypatch):
    ctx, source, wire, texts, _rationales, _requests = _sequence_rig(
        tmp_path, monkeypatch, (2, 1, 0), malformed_first=True)
    result = hook.run_hook(ctx, source)
    assert len(wire.calls) == 4 and budget_for(ctx, scope='book').remaining == 0
    assert result.candidates[0].text == texts[-1] and not result.candidates[0].findings
    assert [(row.before_hard, row.after_hard) for row in result.provenance.candidate_retries
            if row.accepted] == [(2, 1), (1, 0)]
    costs = read_jsonl_rows(ledger_path(tmp_path))
    assert len(costs) == 4 and all(row['cached'] is False for row in costs)
    assert len({row['purpose'] for row in costs}) == 2
    rejected = [row for row in read_jsonl_rows(audit_path(tmp_path))
                if row.get('kind') == 'gate_decision' and row.get('gate') == 'parse'
                and row.get('decision') == 'rejected']
    assert len(rejected) == 1 and rejected[0]['attempt_id'] == costs[1]['attempt_id']
    assert costs[1]['attempt_id'] not in {row['attempt_id'] for row in _adoptions(tmp_path)}


def test_last_valid_json_without_improvement_keeps_previous_content_and_receipt(
        tmp_path, monkeypatch):
    ctx, source, wire, texts, rationales, _requests = _sequence_rig(
        tmp_path, monkeypatch, (2, 1, 1))
    result = hook.run_hook(ctx, source)
    assert len(wire.calls) == 3
    assert result.candidates[0].text == texts[1] and result.rationale == rationales[1]
    costs = read_jsonl_rows(ledger_path(tmp_path))
    initial, retained, unused = [row['attempt_id'] for row in costs]
    assert source.accepted_attempt(f'{ctx.node}:retry:{HOOK_SLOTS[0]}') == unused
    assert {row['attempt_id'] for row in _adoptions(tmp_path)} == {initial, retained}
    proof = _proof(ctx)
    assert {attempt for _purpose, attempt in proof['batch']['adoptions']} == {initial, retained}
    assert {row['attempt_id'] for row in proof['costs']} == {initial, retained, unused}
    receipts = {row['checkpoint']['attempt_id']: row for row in proof['receipts']}
    assert retained in receipts and texts[1] in receipts[retained]['checkpoint']['accepted_text']
    assert unused in receipts and texts[2] in receipts[unused]['checkpoint']['accepted_text']


@pytest.mark.parametrize('cached_initial', [False, True])
@pytest.mark.parametrize('window', ['before_write', 'after_first_adoption'])
def test_multiturn_partial_delivery_recovers_retained_source_after_cache_removal(
        tmp_path, monkeypatch, cached_initial, window):
    ctx, source, wire, texts, rationales, _requests = _sequence_rig(
        tmp_path, monkeypatch, (2, 1, 1), cached_initial=cached_initial)
    with monkeypatch.context() as fault:
        if window == 'after_first_adoption':
            _crash_after_first_adoption(fault, ctx)
        else:
            original_write = ArtifactStore.write

            def before_write(self, rel, artifact):
                if rel == hook.REL:
                    raise OSError('多轮候选正式首写前中断')
                return original_write(self, rel, artifact)

            fault.setattr(ArtifactStore, 'write', before_write)
        with pytest.raises(OSError, match='中断'):
            hook.run_hook(ctx, source)
    assert len(wire.calls) == 3 - int(cached_initial)
    assert len(_adoptions(tmp_path)) == int(window == 'after_first_adoption')
    proof = _proof(ctx)
    formal = ctx.novel_dir / hook.REL
    expected_bytes = formal.read_bytes() if formal.exists() else None
    costs = read_jsonl_rows(ledger_path(tmp_path))
    initial, retained, unused = [row['attempt_id'] for row in costs]
    frozen_costs = ledger_path(tmp_path).read_bytes()
    for path in (tmp_path / 'cache').rglob('*.json'):
        path.unlink()
    resumed = _ctx(ctx.novel_dir, ctx.node, paid=False)
    monkeypatch.setattr(LlmClient, '__init__',
                        lambda *_a, **_k: pytest.fail('免费恢复不能构造模型 client'))
    query = readonly_ctx(ctx.novel_dir, ctx.node, ctx.code)
    before_read = {path.relative_to(tmp_path): path.read_bytes()
                   for path in tmp_path.rglob('*') if path.is_file()}
    projection = hook.inspect_prepared_recovery(query)
    assert projection.state == 'recoverable', projection.reason
    assert projection.source_run_id == ctx.run_id
    assert {path.relative_to(tmp_path): path.read_bytes()
            for path in tmp_path.rglob('*') if path.is_file()} == before_read
    recovered = hook.recover_prepared_hook(
        resumed, source_run_id=projection.source_run_id, proof_sha256=projection.proof_sha256)
    assert recovered.model_dump(by_alias=True, mode='json') == proof['target_payload']
    assert recovered.candidates[0].text == texts[1] and recovered.rationale == rationales[1]
    if expected_bytes is not None:
        assert formal.read_bytes() == expected_bytes
    assert ArtifactStore(resumed).full_sha256_or_none(hook.REL) == proof['target_sha256']
    assert ledger_path(tmp_path).read_bytes() == frozen_costs
    assert budget_for(resumed, scope='book').used == 3 - int(cached_initial)
    adoptions = _adoptions(tmp_path)
    assert len(adoptions) == 2 and {row['attempt_id'] for row in adoptions} == {initial, retained}
    assert unused not in {row['attempt_id'] for row in adoptions}
    assert {row['run_id'] for row in adoptions} == {ctx.run_id}
    assert hook.inspect_prepared_recovery(query).state == 'complete'
    assert not budget_for(resumed, scope='book').unfinished_content_ids()
