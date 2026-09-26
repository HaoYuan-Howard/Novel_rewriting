#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 审批落账随人工闸整层退役；存量回执只读解码的不变式仍在（F8c）。

原文件测 `approvals.stage4_review_payload_landed`：一份已提交回执是否由
**正式历史尾部**精确证明（同一 actor/reason/token/逐章决定/候选 proof），
坏证据保持 unknown、缺文件不得回落到 fresh 记录。2026-09-15 全自动裁决后
design/structure typed 回执没有现役写面，`stage4_review_payload_landed`
连同 Stage4 约束冻结面一并删除（`contracts/schema/retirement.py`
登记）；新链 Stage4 的落账面是候选产物与 compile 节点，不读这份人工账。

仍然真实的是**存量书的只读解码**：`_read_raw`／`approval_head_bytes` 按
原字节解回 v9 记录，记录模型自己守着四条结构不变式（gate 覆盖、gate 与
proof 一致、history 尾部即 current、s4 gate 只能由逐章全 accept 的结构
裁决打开）。本文件改写为：退役守卫 + 这四条不变式 + 坏字节拒读。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
)
from contracts.schema.api import (
    DesignReviewRepairCapability, DesignReviewRequest,
    StructureApprovalRequest,
)
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.orchestrator import approvals

SID = '123'
AGGREGATE = 'a' * 64


def _root() -> str:
    return 'c' * 40


def _proof(artifacts: dict[str, str], digest: str) -> approvals._ApprovalProof:
    return approvals._ApprovalProof(
        root_clean_sha1=_root(), artifacts=artifacts,
        decision_sha256=digest)


def _design_request(**overrides) -> DesignReviewRequest:
    request = DesignReviewRequest(
        actor='存量审阅者', reviewer_kind='human', review_run_id='legacy-review-1',
        review_token=f's4dr1_{AGGREGATE}',
        general_reason='已完成逐章与跨章复核',
        strategy={'decision': 'accept', 'reason': '策略与逐章复核一致'},
        chapters=[{'output_ordinal': 1, 'decision': 'accept',
                   'reason': '第 1 章已逐项核对'}],
    )
    data = request.model_dump(mode='json')
    data.update(overrides)
    return DesignReviewRequest.model_validate(data)


def _design_capability() -> DesignReviewRepairCapability:
    return DesignReviewRepairCapability(
        agent_repairable=True, agent_reason_code='same_strategy_epoch_ready',
        full_chapter_editable=True,
        full_chapter_edit_reason_code='current_typed_decision',
        chapters=[{'output_ordinal': 1, 'committed_rounds': 0,
                   'agent_repair_available': True,
                   'full_chapter_edit_available': True}],
    )


def _design_stored(request: DesignReviewRequest, aggregate: str, candidate: str):
    """slim 历史行：typed 决定 + 内部身份快照 + 精确候选 proof。"""
    return approvals._StoredDesignDecisionSlim(
        request=request,
        candidate_sha256=candidate,
        aggregate_subject_sha256=aggregate,
        strategy_subject_sha256='b' * 64,
        strategy_epoch_sha256='d' * 64,
        chapter_subject_sha256={1: 'e' * 64},
        repair_capability=_design_capability(),
        binding_infeasibility=None,
        decision_sha256='f' * 64, decided_at='2026-09-15T00:00:00+00:00',
        proof=_proof({STAGE4_BLIND_CANDIDATE: candidate}, '1' * 64))


def _structure_request(subject: str = 'b' * 64, decision: str = 'accept',
                       chapters=(1, 2)) -> StructureApprovalRequest:
    return StructureApprovalRequest(
        actor='存量结构审阅者', reason='逐章核对计划与证据绑定',
        review_token=f's4sr1_{subject}',
        chapters=[{'output_ordinal': ordinal, 'decision': decision,
                   'reason': f'第 {ordinal} 章结构已逐项核对'}
                  for ordinal in chapters])


def _structure_stored(request: StructureApprovalRequest):
    return approvals._StoredStructureDecision(
        actor=request.actor, reason=request.reason,
        subject_sha256=request.review_token.removeprefix('s4sr1_'),
        chapters=request.chapters, decided_at='2026-09-15T00:00:00+00:00',
        proof=_proof({STAGE4_BLIND_CANDIDATE: 'c' * 64,
                      STAGE4_EVIDENCE_BINDING: 'd' * 64}, '2' * 64),
        chapter_subject_sha256={})


@pytest.fixture(params=['design_slim', 'structure'])
def legacy_ledger(request, tmp_path):
    """一份合法 v9 存量账：design slim 与 structure 两族 typed 历史。"""
    (tmp_path / 'book_123').mkdir()
    design = _design_stored(_design_request(), AGGREGATE, 'e' * 64)
    structure = _structure_stored(_structure_request())
    record = approvals._fresh_record(SID).model_copy(update={
        'design_history': [design], 'design_decision': design,
        'structure_history': [structure], 'structure_decision': structure,
    })
    target = approvals._path(tmp_path, SID)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(record.model_dump_json() + '\n', encoding='utf-8')
    return SimpleNamespace(base=tmp_path, path=target, design=design,
                           structure=structure)


def test_stage4_landing_entries_stay_deleted():
    """Stage4 审批落账与约束冻结写面不复活（S-05）。"""
    for name in ('stage4_review_payload_landed', 'reviewer_approval_payload_landed',
                 'freeze_design_constraint_history', 'cast_infeasible_replan_scope',
                 'approve_gate', 'backfill_from_disk'):
        assert not hasattr(approvals, name), f'已退役的 Stage4 落账入口仍在：{name}'


def test_legacy_v9_record_with_both_typed_histories_decodes_exactly(legacy_ledger):
    """存量 design slim 与 structure 历史按原字节解回，不迁移、不补写。"""
    decoded = approvals._read_raw(legacy_ledger.base, SID)
    assert decoded.sid == SID and decoded.v == 9
    assert decoded.design_decision == legacy_ledger.design
    assert decoded.structure_decision == legacy_ledger.structure
    assert decoded.design_decision.candidate_sha256 == 'e' * 64
    assert decoded.structure_decision.chapters[0].decision == 'accept'
    # head 序列化只带 current 决定，历史长度不进 seal。
    head = approvals.approval_head_bytes(legacy_ledger.base, SID)
    decoded_head = json.loads(head.decode('utf-8'))
    assert 'design_history' not in decoded_head
    assert 'structure_history' not in decoded_head
    assert decoded_head['design_decision']['candidate_sha256'] == 'e' * 64


@pytest.mark.parametrize('damage', ['gate_extra', 'gate_proof_mismatch',
                                    'structure_not_tail', 's4_without_all_accept'])
def test_record_model_refuses_structurally_impossible_ledgers(legacy_ledger, damage):
    """记录模型的结构不变式仍然硬：坏账一个字节都不能落成「已落账」。"""
    raw = json.loads(legacy_ledger.path.read_text('utf-8'))
    if damage == 'gate_extra':
        raw['gate']['s3'] = False
        raw['gate_proofs']['s3'] = None
    elif damage == 'gate_proof_mismatch':
        raw['gate']['s4'] = True
        raw['gate_proofs']['s4'] = None
    elif damage == 'structure_not_tail':
        # current 决定停在历史中段：尾部追加了一条不同的结构结论。
        newer = json.loads(json.dumps(raw['structure_history'][0]))
        newer['reason'] = '修后重新提交的结构结论'
        raw['structure_history'].append(newer)
    else:
        raw['gate']['s4'] = True
        raw['gate_proofs']['s4'] = raw['structure_decision']['proof']
        chapters = raw['structure_decision']['chapters']
        chapters[0]['decision'] = 'request_replan'
    legacy_ledger.path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    with pytest.raises((HumanEditProtected, ArtifactUnreadable)):
        approvals._read_raw(legacy_ledger.base, SID)
    with pytest.raises(ArtifactUnreadable):
        approvals.approval_head_bytes(legacy_ledger.base, SID)


@pytest.mark.parametrize('damage', ['json', 'wrong_sid', 'foreign_version'])
def test_corrupt_or_foreign_ledgers_stay_refused(legacy_ledger, damage):
    """损坏、归属不符与旧版本一律显式拒绝；缺文件不是空账序列化。"""
    if damage == 'json':
        legacy_ledger.path.write_text('{broken', encoding='utf-8')
        with pytest.raises(HumanEditProtected, match='损坏'):
            approvals._read_raw(legacy_ledger.base, SID)
    elif damage == 'wrong_sid':
        raw = json.loads(legacy_ledger.path.read_text('utf-8'))
        raw['sid'] = '456'
        legacy_ledger.path.write_text(
            json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        with pytest.raises(HumanEditProtected, match='belongs to'):
            approvals._read_raw(legacy_ledger.base, SID)
    else:
        raw = json.loads(legacy_ledger.path.read_text('utf-8'))
        raw['v'] = 8
        legacy_ledger.path.write_text(
            json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        with pytest.raises(HumanEditProtected, match='均不迁移'):
            approvals._read_raw(legacy_ledger.base, SID)


def test_absent_approval_file_is_not_a_landed_decision(legacy_ledger):
    """缺正式审批文件：读数是「没有这份历史账」，不是任何落账结论。"""
    legacy_ledger.path.unlink()
    assert approvals.approval_head_bytes(legacy_ledger.base, SID) is None
    assert approvals.has_record(legacy_ledger.base, SID) is False


def test_ledger_bytes_are_never_rewritten_by_reads(legacy_ledger):
    """只读解码不改写盘面：旧账一个字节都不动。"""
    before = legacy_ledger.path.read_bytes()
    approvals._read_raw(legacy_ledger.base, SID)
    approvals.approval_head_bytes(legacy_ledger.base, SID)
    approvals.has_record(legacy_ledger.base, SID)
    assert legacy_ledger.path.read_bytes() == before
