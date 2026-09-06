#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    APPROVAL_GATES, APPROVAL_SEEN, BLIND_ALIAS_ALGORITHM,
    DESIGN_REVIEW_CONTRACT, FROZEN_V6_DESIGN_REVIEW_CONTRACT,
)
from contracts.schema.api import (
    ApprovalDoc, DesignChapterSubject, DesignPreflightFailure,
    DesignPreflightSummary, DesignRepairConstraint, DesignReviewHistoryEntry,
    DesignReviewRequest, DesignReviewTemplate,
    DesignStrategySubject, DesignStructureEvidence, StructureApprovalRequest,
    StructureApprovalView,
)
from contracts.schema.stage4 import BlindCandidateChapter, BlindChapterEditRequest
from novel.errors import ApprovalRequired, HumanEditProtected, UpstreamMissing
from novel.orchestrator import approvals
from novel.stages.stage4.design_evolution import (
    chapter_strategy_sha256, strategy_epoch_sha256,
)


SID = '123'
FACT = 'a_' + '1' * 16
PERSON = 'a_' + '2' * 16


def test_design_history_summary_constructs_and_roundtrips_without_workpack_copy():
    entry = DesignReviewHistoryEntry(
        actor='reviewer', reviewer_kind='human', review_run_id='run-1',
        general_reason='已完成逐章与跨章复核',
        review_token='s4dr1_' + 'a' * 64,
        strategy_decision='accept', chapter_decisions={1: 'accept'},
        decision_sha256='b' * 64, decided_at='2026-09-01T00:00:00+00:00',
    )

    restored = DesignReviewHistoryEntry.model_validate_json(
        entry.model_dump_json())

    assert restored == entry
    assert set(restored.model_dump()) == {
        'actor', 'reviewer_kind', 'review_run_id', 'general_reason',
        'review_token', 'strategy_decision', 'chapter_decisions',
        'decision_sha256', 'decided_at',
    }


def _pass_preflight(
    *, status: str = 'pass',
    failures: list[DesignPreflightFailure] | None = None,
) -> DesignPreflightSummary:
    """合成夹具的预检投影：默认 pass，可显式注入 blocked。"""
    return DesignPreflightSummary(
        status=status,  # type: ignore[arg-type]
        candidate_sha256='c' * 64,
        blueprint_sha256='e' * 64,
        review_contract=DESIGN_REVIEW_CONTRACT,
        code_version='test',
        blocked_owners=[] if status == 'pass'
        else sorted({f.repair_owner for f in (failures or [])}),
        failures=failures or [],
        infeasibility=[],
    )


def _subjects(
    *, brief: str = '把事实放进一个新场景',
    binding_infeasibility: dict | None = None,
    repair_capability: dict | None = None,
    preflight: DesignPreflightSummary | None = 'default',
    review_contract: str = DESIGN_REVIEW_CONTRACT,
    legacy_frozen_v6: bool = False,
) -> approvals._DesignSubjects:
    strategy = DesignStrategySubject(
        basis={
            'review_contract': review_contract,
            'root_clean_sha1': 'a' * 40,
            'truth_sha256': 'b' * 64,
            'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        },
        reconstruction_strategy='按事实义务重新组织场景，不沿用原文段落。',
        chapter_weights=[{'chapter_ordinal': 1, 'weight': 'CH_EVEN'}],
    )
    strategy_sha = approvals._decision_sha256({
        'kind': 'stage4_design_strategy_subject',
        'subject': strategy.model_dump(mode='json'),
    })
    strategy_epoch = strategy_epoch_sha256(
        strategy.basis.model_dump(mode='json'),
        strategy.reconstruction_strategy,
    )
    chapter = BlindCandidateChapter.model_validate({
        'output_ordinal': 1,
        'structure_decision': {
            'mode': 'retain', 'reason': '单章夹具保持直接推进。',
            'declared_change_kinds': [],
        },
        'chapter_goal': {'intent': 'G_ADVANCE', 'fact_aliases': [FACT]},
        'open_hook_goal': {'intent': 'G_OPEN', 'fact_aliases': [FACT]},
        'end_hook_goal': {'intent': 'G_CLOSE', 'fact_aliases': [FACT]},
        'weight': 'CH_EVEN',
        'chapter_rewrite_brief': brief,
        'scenes': [{
            'candidate_scene_id': 'c01s01',
            'weight': 'W_MID', 'purpose': '以新场景落下事实',
            'role': 'R_SETUP', 'delivery': 'D_SCENE',
            'transition': 'X_START',
            'land_fact_aliases': [FACT], 'context_fact_aliases': [],
            'turn_fact_alias': FACT,
            'entry_state_aliases': [], 'exit_state_aliases': [],
        }],
    })
    subject = DesignChapterSubject(
        strategy_epoch_sha256=strategy_epoch,
        chapter_strategy_sha256=chapter_strategy_sha256(
            strategy_epoch, 1, chapter.weight),
        blind_truth={
            'chapter_ordinal': 1,
            'chapter_function': '建立冲突',
            'facts': [{
                'fact_alias': FACT, 'claim': '她发现门已经打开。',
                'kind': 'event', 'obligation': 'land',
                'participant_aliases': [PERSON],
            }],
            'causal_edges': [],
            'entry_state_aliases': [], 'ending_state_aliases': [],
        },
        chapter=chapter,
        structure_evidence=DesignStructureEvidence(
            state='ok', declared_change_kinds=[], computed_change_kinds=[],
            realized_declared=[], unrealized_declared=[], undeclared_actual=[],
            readings={'mapping_state': 'ok', 'land_facts': 1},
        ),
    )
    chapter_sha = approvals._decision_sha256({
        'kind': 'stage4_design_chapter_subject',
        'subject': subject.model_dump(mode='json'),
    })
    capability = repair_capability or {
        'agent_repairable': True,
        'agent_reason_code': 'same_strategy_epoch_ready',
        'full_chapter_editable': True,
        'full_chapter_edit_reason_code': 'current_typed_decision',
        'chapters': [{
            'output_ordinal': 1,
            'committed_rounds': 0,
            'agent_repair_available': True,
            'full_chapter_edit_available': True,
        }],
    }
    return approvals._DesignSubjects(
        legacy_frozen_v6=legacy_frozen_v6,
        candidate_sha256='c' * 64,
        aggregate_subject_sha256=approvals._decision_sha256({
            'strategy': strategy_sha, 'chapter': chapter_sha,
            'binding_infeasibility': binding_infeasibility,
            'repair_capability': capability,
        }),
        binding_infeasibility=binding_infeasibility,
        preflight=(
            _pass_preflight() if preflight == 'default' else preflight),
        repair_capability=capability,
        strategy=strategy,
        strategy_subject_sha256=strategy_sha,
        strategy_epoch_sha256=strategy_epoch,
        chapters=[subject],
        chapter_subject_sha256={1: chapter_sha},
    )


def _frozen_subjects() -> approvals._DesignSubjects:
    """frozen v6 只有零 wire 章替换能力，不伪造 agent lane。"""
    return _subjects(
        review_contract=FROZEN_V6_DESIGN_REVIEW_CONTRACT,
        legacy_frozen_v6=True,
        preflight=None,
        repair_capability={
            'agent_repairable': False,
            'agent_reason_code': 'legacy_frozen_v6_human_edit_only',
            'full_chapter_editable': True,
            'full_chapter_edit_reason_code': 'current_typed_decision',
            'chapters': [{
                'output_ordinal': 1,
                'committed_rounds': 2,
                'agent_repair_available': False,
                'full_chapter_edit_available': True,
            }],
        },
    )


def _mixed_repair_subjects() -> approvals._DesignSubjects:
    one = _subjects()
    strategy = DesignStrategySubject.model_validate({
        **one.strategy.model_dump(mode='json'),
        'chapter_weights': [
            {'chapter_ordinal': 1, 'weight': 'CH_EVEN'},
            {'chapter_ordinal': 2, 'weight': 'CH_EVEN'},
        ],
    })
    strategy_sha = approvals._decision_sha256({
        'kind': 'stage4_design_strategy_subject',
        'subject': strategy.model_dump(mode='json'),
    })
    strategy_epoch = strategy_epoch_sha256(
        strategy.basis.model_dump(mode='json'),
        strategy.reconstruction_strategy,
    )
    first = one.chapters[0].model_copy(update={
        'strategy_epoch_sha256': strategy_epoch,
        'chapter_strategy_sha256': chapter_strategy_sha256(
            strategy_epoch, 1, 'CH_EVEN'),
    })
    second_chapter = first.chapter.model_copy(update={
        'output_ordinal': 2,
        'scenes': [first.chapter.scenes[0].model_copy(update={
            'candidate_scene_id': 'c02s01',
        })],
    })
    second = first.model_copy(update={
        'chapter_strategy_sha256': chapter_strategy_sha256(
            strategy_epoch, 2, 'CH_EVEN'),
        'blind_truth': first.blind_truth.model_copy(update={
            'chapter_ordinal': 2,
        }),
        'chapter': second_chapter,
    })
    chapter_hashes = {
        ordinal: approvals._decision_sha256({
            'kind': 'stage4_design_chapter_subject',
            'subject': subject.model_dump(mode='json'),
        })
        for ordinal, subject in ((1, first), (2, second))
    }
    capability = {
        'agent_repairable': True,
        'agent_reason_code': 'same_strategy_epoch_ready',
        'full_chapter_editable': True,
        'full_chapter_edit_reason_code': 'current_typed_decision',
        'chapters': [
            {'output_ordinal': 1, 'committed_rounds': 1,
             'agent_repair_available': True,
             'full_chapter_edit_available': True},
            {'output_ordinal': 2, 'committed_rounds': 2,
             'agent_repair_available': False,
             'full_chapter_edit_available': True},
        ],
    }
    return approvals._DesignSubjects(
        candidate_sha256=one.candidate_sha256,
        aggregate_subject_sha256=approvals._decision_sha256({
            'strategy': strategy_sha,
            'chapters': chapter_hashes,
            'repair_capability': capability,
        }),
        binding_infeasibility=None,
        preflight=_pass_preflight(),
        repair_capability=capability,
        strategy=strategy,
        strategy_subject_sha256=strategy_sha,
        strategy_epoch_sha256=strategy_epoch,
        chapters=[first, second],
        chapter_subject_sha256=chapter_hashes,
    )


def _proof(decision) -> approvals._ApprovalProof:
    return approvals._ApprovalProof(
        root_clean_sha1='a' * 40,
        artifacts={'stage4/blind_candidate.json': 'd' * 64},
        decision_sha256=approvals._decision_sha256(
            approvals._design_decision_payload(decision)),
    )


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    book = tmp_path / f'design_{SID}'
    book.mkdir()
    base_record = approvals._fresh_record(SID)
    gates = dict(base_record.gate)
    gate_proofs = dict(base_record.gate_proofs)
    gates['s2'] = True
    gate_proofs['s2'] = approvals._ApprovalProof(
        root_clean_sha1='a' * 40,
        artifacts={'stage2/chapter_blueprints.json': 'e' * 64},
        decision_sha256='f' * 64,
    )
    state = {'record': base_record.model_copy(update={
        'gate': gates, 'gate_proofs': gate_proofs,
    }), 'subjects': _subjects(), 'candidate_proof_sha256': 'd' * 64}
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: book)
    monkeypatch.setattr(
        approvals, '_read_current', lambda _base, _sid: state['record'])
    monkeypatch.setattr(
        approvals, '_read_state',
        lambda _base, _sid: (state['record'], state['record']))
    monkeypatch.setattr(
        approvals, '_current_design_subjects', lambda _novel_dir: state['subjects'])
    monkeypatch.setattr(approvals, '_proof_for_design', lambda _dir, decision: _proof(decision))
    monkeypatch.setattr(
        approvals, '_make_proof',
        lambda _dir, _artifacts, payload: approvals._ApprovalProof(
            root_clean_sha1='a' * 40,
            artifacts={
                'stage4/blind_candidate.json': state['candidate_proof_sha256'],
            },
            decision_sha256=approvals._decision_sha256(payload),
        ),
    )
    monkeypatch.setattr(
        approvals, '_proof_for_structure',
        lambda _dir, decision: approvals._ApprovalProof(
            root_clean_sha1='a' * 40,
            artifacts={
                'stage4/plan_chapters.json': 'f' * 64,
                'stage4/evidence_binding.json': '1' * 64,
            },
            decision_sha256=approvals._decision_sha256(
                approvals._structure_decision_payload(decision)),
        ),
    )
    monkeypatch.setattr(approvals, '_spawns', lambda _base, _sid: {})
    monkeypatch.setattr(
        approvals, '_current_structure_subject_and_chapters',
        lambda _dir: (_ for _ in ()).throw(UpstreamMissing('no plan')))
    monkeypatch.setattr(approvals, '_store_for', lambda _dir: object())
    monkeypatch.setattr(approvals, 'current_infeasible_binding', lambda _store: None)
    monkeypatch.setattr(approvals, '_now', lambda: '2026-08-28T00:00:00+00:00')

    def write(base, record):
        state['record'] = approvals._ApprovalRecordV7.model_validate(
            record.model_dump(mode='json'))
        return approvals._view(base, state['record'])

    monkeypatch.setattr(approvals, '_write', write)
    return tmp_path, state


def _template(base: Path) -> DesignReviewTemplate:
    return approvals.design_template(
        base, SID, actor='reviewer', reviewer_kind='human',
        review_run_id='review-1', reason='逐项核对当前 A 候选')


def _compact_request(template: DesignReviewTemplate) -> DesignReviewRequest:
    """只投影公网 POST 真正接受的 token + typed rulings。"""
    return DesignReviewRequest.model_validate({
        'actor': template.actor,
        'reviewer_kind': template.reviewer_kind,
        'review_run_id': template.review_run_id,
        'general_reason': template.general_reason,
        'review_token': template.review_token,
        'strategy': {
            'decision': template.strategy.decision,
            'reason': template.strategy.reason,
            'replan_constraints': [
                {
                    'kind': row.kind,
                    'chapter_ordinals': row.chapter_ordinals,
                }
                for row in template.strategy.replan_constraints
            ],
        },
        'chapters': [
            {
                'output_ordinal': row.output_ordinal,
                'decision': row.decision,
                'reason': row.reason,
                'constraints': [
                    item.model_dump(mode='json') for item in row.constraints
                ],
            }
            for row in template.chapters
        ],
    })


def _accept_all(template: DesignReviewTemplate) -> DesignReviewRequest:
    updated = template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'accept', 'reason': '整书策略可进入 B',
            'replan_constraints': [],
            'source_decision_sha256': None,
        }),
        'chapters': [row.model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'accept', 'reason': '本章设计可进入 B',
            'constraints': [], 'source_decision_sha256': None,
        }) for row in template.chapters],
    })
    return _compact_request(updated)


def test_design_apply_decided_view_and_plan_authorization_are_current(isolated):
    base, state = isolated
    template = _template(base)
    assert template.strategy.decision == 'request_replan'
    assert template.chapters[0].decision == 'request_repair'

    doc = approvals.approve_design(base, SID, _accept_all(template))

    assert doc.design_review.state == 'accepted'
    assert doc.design_review.current_decision is not None
    assert not doc.gate['s4'], 'A 审阅绝不打开最终 structure gate'
    assert approvals.authorize_plan(base, SID) == doc.design_review.current_decision
    # 回归：decided 分支必须实际 return 一个 view，不能掉进后续 helper 定义。
    assert approvals._view(base, state['record']).design_review.state == 'accepted'


def test_same_design_subject_only_accepts_exact_replay(isolated):
    base, state = isolated
    template = _template(base)
    accepted = _accept_all(template)
    first = approvals.approve_design(base, SID, accepted)
    second = approvals.approve_design(base, SID, accepted)
    assert second == first
    assert len(state['record'].design_history) == 1

    changed = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'accept', 'reason': '策略不变但尝试改票',
            'replan_constraints': [], 'source_decision_sha256': None,
        }),
        'chapters': [template.chapters[0].model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_repair', 'reason': '同主体追加不同决定',
            'constraints': [DesignRepairConstraint(
                code='scene_goal', scene_refs=['c01s01'],
                opaque_aliases=[FACT])],
            'source_decision_sha256': None,
        })],
    }))
    with pytest.raises(HumanEditProtected, match='只接受 exact replay'):
        approvals.approve_design(base, SID, changed)
    assert len(state['record'].design_history) == 1


def test_same_design_business_subject_rebinds_only_the_exact_payload_after_proof_drift(
        isolated):
    base, state = isolated
    accepted = _accept_all(_template(base))
    approvals.approve_design(base, SID, accepted)
    original = state['record'].design_decision
    assert original is not None

    # 模拟确定性重写只更新 candidate provenance：人可见 A 业务主体、预检、
    # capability 与外部 token 均不变，完整文件 SHA 则必须重新落 proof。
    state['subjects'] = state['subjects'].model_copy(update={
        'candidate_sha256': 'e' * 64,
    })
    state['candidate_proof_sha256'] = 'e' * 64
    assert approvals._view(base, state['record']).design_review.state == 'expired'

    changed = accepted.model_copy(update={'general_reason': '借旧 token 改写决定'})
    with pytest.raises(HumanEditProtected, match='不能用旧 token 改票'):
        approvals.approve_design(base, SID, changed)
    assert len(state['record'].design_history) == 1

    rebound = approvals.approve_design(base, SID, accepted)
    assert rebound.design_review.state == 'accepted'
    assert len(state['record'].design_history) == 2
    assert state['record'].design_history[0] == original
    latest = state['record'].design_decision
    assert latest is not None and latest != original
    assert latest.candidate_sha256 == 'e' * 64
    assert latest.proof.artifacts['stage4/blind_candidate.json'] == 'e' * 64

    with pytest.raises(HumanEditProtected, match='只接受 exact replay'):
        approvals.approve_design(base, SID, changed)
    assert len(state['record'].design_history) == 2

def test_v3_split_capability_decision_decodes_but_is_expired_under_v4(isolated):
    base, state = isolated
    state['subjects'] = _subjects(review_contract='stage4-design-review-v3')
    approvals.approve_design(base, SID, _accept_all(_template(base)))
    assert state['record'].design_decision is not None

    state['subjects'] = _subjects()
    view = approvals._view(base, state['record']).design_review
    assert view.state == 'expired'
    assert view.current_decision is None
    assert view.action_review_token is None
    assert len(view.history) == 1


def test_current_b_replan_allows_one_non_accept_design_resolution_only(
        isolated, monkeypatch: pytest.MonkeyPatch):
    base, state = isolated
    accepted = _accept_all(_template(base))
    approvals.approve_design(base, SID, accepted)

    structure_subject = '9' * 64
    chapter_subject = '8' * 64
    monkeypatch.setattr(
        approvals, '_current_structure_subject_and_chapters',
        lambda _novel_dir: (structure_subject, [1]))
    monkeypatch.setattr(
        approvals, '_current_structure_chapter_subject_sha256',
        lambda _novel_dir: {1: chapter_subject})
    monkeypatch.setattr(
        approvals, '_current_binding_concerns', lambda _novel_dir: [])
    approvals.approve_structure(base, SID, StructureApprovalRequest(
        actor='structure-reviewer', reason='本章需要结构重规划',
        review_token=f's4sr1_{structure_subject}',
        chapters=[{
            'output_ordinal': 1, 'decision': 'request_replan',
            'reason': '当前结构语义未闭合',
        }],
    ))

    replayed = approvals.approve_design(base, SID, accepted)
    assert replayed.design_review.state == 'accepted'
    assert replayed.structure.state == 'replan_requested'
    assert len(state['record'].design_history) == 1

    # B 已拒绝时，A 的 provenance-only proof 漂移也只能重绑最新 accept；
    # 这一步不得覆盖 B、打开 s4 或直接跳过 resolution blocker。
    state['subjects'] = state['subjects'].model_copy(update={
        'candidate_sha256': 'e' * 64,
    })
    state['candidate_proof_sha256'] = 'e' * 64
    assert approvals._view(base, state['record']).design_review.state == 'expired'
    rebound = approvals.approve_design(base, SID, accepted)
    assert rebound.design_review.state == 'accepted'
    assert rebound.structure.state == 'replan_requested'
    assert not rebound.gate['s4']
    assert len(state['record'].design_history) == 2

    changed_accept = _compact_request(_template(base).model_copy(update={
        'strategy': _template(base).strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'accept', 'reason': '改写理由伪装成新的 all-accept',
            'replan_constraints': [], 'source_decision_sha256': None,
        }),
        'chapters': [_template(base).chapters[0].model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'accept', 'reason': '改写章理由伪装成新的 all-accept',
            'constraints': [], 'source_decision_sha256': None,
        })],
    }))
    with pytest.raises(HumanEditProtected, match='direct all-accept'):
        approvals.approve_design(base, SID, changed_accept)

    template = _template(base)
    resolution = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'accept', 'reason': '策略保留，定向修订本章',
            'replan_constraints': [], 'source_decision_sha256': None,
        }),
        'chapters': [template.chapters[0].model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_repair',
            'reason': '按最终结构裁决修正场景目标',
            'constraints': [DesignRepairConstraint(
                code='scene_goal', scene_refs=['c01s01'],
                opaque_aliases=[FACT])],
            'source_decision_sha256': None,
        })],
    }))
    decided = approvals.approve_design(base, SID, resolution)
    assert decided.design_review.state == 'repair_requested'
    assert len(state['record'].design_history) == 3

    # resolution 落下后，同一 A 主体重新回到 exact-replay-only。
    replayed = approvals.approve_design(base, SID, resolution)
    assert replayed == decided
    assert len(state['record'].design_history) == 3


def test_frozen_v6_raw_all_accept_is_suspended_without_any_action_token(
    isolated,
) -> None:
    base, state = isolated
    state['subjects'] = _frozen_subjects()
    template = _template(base)

    replan = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'request_replan',
            'reason': '尝试重规划冻结书',
            'source_decision_sha256': None,
        }),
        'chapters': [row.model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'request_repair',
            'reason': '即使各章另有真实修订，冻结策略也不得付费重规划',
            'constraints': [DesignRepairConstraint(
                code='scene_goal', scene_refs=['c01s01'],
                opaque_aliases=[FACT])],
            'source_decision_sha256': None,
        }) for row in template.chapters],
    }))
    with pytest.raises(HumanEditProtected, match='不开放付费 strategy replan'):
        approvals.approve_design(base, SID, replan)
    assert state['record'].design_decision is None

    doc = approvals.approve_design(base, SID, _accept_all(template))

    assert state['record'].design_decision is not None, 'raw typed 决定必须落盘'
    assert len(state['record'].design_history) == 1
    assert doc.design_review.state == 'suspended'
    assert doc.design_review.current_decision is not None
    assert doc.design_review.action_review_token is None
    assert doc.spawns.get('s4', []) == []
    assert approvals.design_review_is_current(base, SID) is False
    with pytest.raises(ApprovalRequired, match='state=suspended'):
        approvals.authorize_plan(base, SID)

    workpack = approvals.design_workpack(base, SID)
    assert workpack.disposition == 'suspended'
    assert workpack.current_decision_sha256 \
        == doc.design_review.current_decision.decision_sha256
    assert all(
        item.review_token is None
        for group in workpack.groups for item in group.items
    ), 'raw all-accept 不得签发 s4er1 完整章替换票'


def test_frozen_v6_real_repair_still_mints_only_zero_wire_edit_token(
    isolated,
) -> None:
    base, state = isolated
    state['subjects'] = _frozen_subjects()
    template = _template(base)
    request = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'accept',
            'reason': '整书策略可保留，仅修当前章',
            'replan_constraints': [],
            'source_decision_sha256': None,
        }),
        'chapters': [template.chapters[0].model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'request_repair',
            'reason': '当前场景目标与事实锚点不一致',
            'constraints': [DesignRepairConstraint(
                code='scene_goal', scene_refs=['c01s01'],
                opaque_aliases=[FACT])],
            'source_decision_sha256': None,
        })],
    }))

    doc = approvals.approve_design(base, SID, request)

    assert doc.design_review.state == 'repair_requested'
    assert doc.design_review.current_decision is not None
    assert doc.design_review.action_review_token is None
    assert doc.spawns.get('s4', []) == []
    workpack = approvals.design_workpack(base, SID)
    repair_item = next(
        item for item in workpack.groups[0].items
        if item.code == 'design_repair_requested:ch1')
    assert repair_item.review_token is not None
    assert repair_item.review_token.startswith('s4er1_')

    chapter = template.chapters[0].subject.chapter.model_dump(mode='json')
    for scene in chapter['scenes']:
        scene.pop('candidate_scene_id')
    edit = BlindChapterEditRequest.model_validate({
        'actor': 'human:reviewer',
        'reason': '按 typed constraint 提交完整章响应',
        'review_token': repair_item.review_token,
        'output_ordinal': 1,
        'chapter': chapter,
    })
    authorization = approvals.resolve_design_edit_action(base, SID, edit)
    assert authorization.review_token == repair_item.review_token
    with pytest.raises(HumanEditProtected, match='只开放零 wire 完整章替换'):
        approvals.resolve_design_action(
            base, SID, doc.design_review.action_review_token,
            expected='repair')


def test_design_template_cannot_be_applied_while_placeholders_remain(isolated):
    base, _state = isolated
    template = _template(base)

    assert template.strategy.placeholder
    assert template.strategy.template_token is not None
    assert all(row.placeholder for row in template.chapters)
    assert all(row.template_token is not None for row in template.chapters)
    with pytest.raises(ValidationError, match='Extra inputs are not permitted'):
        DesignReviewRequest.model_validate(template.model_dump(mode='json'))


def test_clearing_template_markers_alone_cannot_submit_default_sentinels(isolated):
    base, _state = isolated
    template = _template(base)
    copied = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
        }),
        'chapters': [row.model_copy(update={
            'placeholder': False, 'template_token': None,
        }) for row in template.chapters],
    }))

    with pytest.raises(HumanEditProtected, match='template sentinel'):
        approvals.approve_design(base, SID, copied)


def test_fresh_b_infeasibility_is_visible_bound_and_disables_accept_reuse(isolated):
    base, state = isolated
    first = approvals.approve_design(base, SID, _accept_all(_template(base)))
    first_decision = first.design_review.current_decision
    assert first_decision is not None
    evidence = {
        'candidate_sha256': '9' * 64,
        'binding_concerns': [{
            'constraint_code': 'insufficient_evidence',
            'chapter_ordinal': 1,
            'scenes': ['c01s01'],
            'reason': '当前封闭目录无法完成绑定。',
            'closed_refs': [FACT],
        }],
        'infeasibility_proof': [{
            'chapter_ordinal': 1,
            'scenes': ['c01s01'],
            'constraint_code': 'missing_chapter_catalog',
            'closed_refs': [FACT],
            'proof_basis': '8' * 64,
        }],
    }
    state['subjects'] = _subjects(binding_infeasibility=evidence)

    template = _template(base)
    assert template.binding_infeasibility is not None
    assert template.strategy.placeholder
    assert template.strategy.source_decision_sha256 is None
    assert all(row.placeholder for row in template.chapters)
    assert approvals._view(base, state['record']).design_review.state == 'expired'
    assert (
        approvals._view(base, state['record'])
        .design_review.binding_infeasibility.infeasibility_proof[0].proof_basis
        == '8' * 64
    )

    reviewed = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'reason': '代码证明 B 不可行，整书策略必须重建',
        }),
        'chapters': [row.model_copy(update={
            'placeholder': False, 'template_token': None,
            'reason': '本章随整书策略重建后重新审阅',
        }) for row in template.chapters],
    }))
    decided = approvals.approve_design(base, SID, reviewed)
    current = decided.design_review.current_decision
    assert current is not None and current.binding_infeasibility is not None
    scope = approvals.resolve_design_action(
        base, SID, approvals._design_action_review_token(
            current.decision_sha256), expected='replan')
    assert scope.binding_infeasibility == current.binding_infeasibility


def test_mixed_repair_capability_resolves_only_agent_available_chapter(
        isolated):
    base, state = isolated
    state['subjects'] = _mixed_repair_subjects()
    template = _template(base)

    assert [row.agent_repair_available
            for row in template.repair_capability.chapters] == [True, False]
    assert [row.full_chapter_edit_available
            for row in template.repair_capability.chapters] == [True, True]
    assert [row.decision for row in template.chapters] == [
        'request_repair', 'request_repair']
    reviewed = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'accept', 'reason': '策略本身可执行，仅第一章需定向返修',
            'replan_constraints': [],
        }),
        'chapters': [
            template.chapters[0].model_copy(update={
                'placeholder': False, 'template_token': None,
                'reason': '第一章场景目标仍需按当前证据重做',
                'constraints': [DesignRepairConstraint(
                    code='scene_goal', scene_refs=['c01s01'],
                    opaque_aliases=[FACT],
                )],
            }),
            template.chapters[1].model_copy(update={
                'placeholder': False, 'template_token': None,
                'reason': '第二章只走零 wire 完整章替换，不得误发 agent',
            }),
        ],
    }))
    doc = approvals.approve_design(base, SID, reviewed)
    decision = doc.design_review.current_decision
    assert decision is not None
    scope = approvals.resolve_design_action(
        base, SID, approvals._design_action_review_token(
            decision.decision_sha256), expected='repair')
    assert scope.target_chapters == [1]
    assert scope.repair_capability.chapters[1].committed_rounds == 2


@pytest.mark.parametrize(('capability', 'reason_code'), [
    ({
        'agent_repairable': False,
        'agent_reason_code': 'legacy_or_untracked_candidate',
        'full_chapter_editable': True,
        'full_chapter_edit_reason_code': 'current_typed_decision',
        'chapters': [{
            'output_ordinal': 1, 'committed_rounds': 0,
            'agent_repair_available': False,
            'full_chapter_edit_available': True,
        }],
    }, 'legacy_or_untracked_candidate'),
    ({
        'agent_repairable': False,
        'agent_reason_code': 'agent_epoch_unavailable',
        'full_chapter_editable': True,
        'full_chapter_edit_reason_code': 'current_typed_decision',
        'chapters': [{
            'output_ordinal': 1, 'committed_rounds': 2,
            'agent_repair_available': False,
            'full_chapter_edit_available': True,
        }],
    }, 'agent_epoch_unavailable'),
])
def test_full_edit_only_chapter_cannot_be_sent_to_agent(
        isolated, capability, reason_code):
    base, state = isolated
    state['subjects'] = _subjects(repair_capability=capability)
    template = _template(base)
    assert template.repair_capability.agent_reason_code == reason_code
    assert template.chapters[0].decision == 'request_repair'
    attempted = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'accept', 'reason': '策略可保留',
            'replan_constraints': [],
        }),
        'chapters': [template.chapters[0].model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_repair', 'reason': '尝试不可执行的返修',
            'constraints': [DesignRepairConstraint(
                code='scene_goal', scene_refs=['c01s01'],
                opaque_aliases=[FACT],
            )],
        })],
    }))

    doc = approvals.approve_design(base, SID, attempted)
    decision = doc.design_review.current_decision
    assert decision is not None
    with pytest.raises(approvals.ApprovalRequired, match='零 wire 完整章替换'):
        approvals.resolve_design_action(
            base, SID, approvals._design_action_review_token(
                decision.decision_sha256), expected='repair')


def test_authorize_design_initial_delegates_to_core_slot_capability(
        isolated, monkeypatch):
    base, _state = isolated
    from novel.stages.stage4 import plan
    seen = []
    monkeypatch.setattr(
        plan, 'require_initial_design_slot', lambda store: seen.append(store))

    assert approvals.authorize_design_initial(base, SID) is None
    assert len(seen) == 1


def test_repair_resolver_requires_exact_current_sha_and_returns_only_constraints(isolated):
    base, _state = isolated
    template = _template(base)
    request = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'accept', 'reason': '策略可保留',
            'replan_constraints': [],
        }),
        'chapters': [template.chapters[0].model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'request_repair', 'reason': '场景目标需更清晰',
            'constraints': [DesignRepairConstraint(
                code='scene_goal', scene_refs=['c01s01'],
                opaque_aliases=[FACT],
            )],
        })],
    }))
    doc = approvals.approve_design(base, SID, request)
    decision = doc.design_review.current_decision
    assert decision is not None
    assert doc.design_review.action_review_token == \
        approvals._design_action_review_token(decision.decision_sha256)
    with pytest.raises(HumanEditProtected, match='不等于当前'):
        approvals.resolve_design_action(
            base, SID, 's4rr1_' + '0' * 64, expected='repair')
    scope = approvals.resolve_design_action(
        base, SID, approvals._design_action_review_token(
            decision.decision_sha256), expected='repair')
    assert scope.target_chapters == [1]
    assert scope.chapters[0].constraints == []
    assert scope.chapters[0].new_constraints == request.chapters[0].constraints
    assert scope.chapters[0].accumulated_constraints \
        == request.chapters[0].constraints
    assert scope.chapters[0].constraint_set_sha256 is not None
    assert 'reason' not in scope.model_dump(mode='json')


def test_repair_constraints_accumulate_and_complete_edit_uses_one_opaque_token(
        isolated):
    base, state = isolated
    first_template = _template(base)
    first_request = _compact_request(
        first_template.model_copy(update={
            'strategy': first_template.strategy.model_copy(update={
                'placeholder': False, 'template_token': None,
                'decision': 'accept', 'reason': '宏观策略保持不变',
                'replan_constraints': [],
            }),
            'chapters': [first_template.chapters[0].model_copy(update={
                'placeholder': False, 'template_token': None,
                'reason': '先收紧场景目标',
                'constraints': [DesignRepairConstraint(
                    code='scene_goal', scene_refs=['c01s01'],
                    opaque_aliases=[FACT])],
            })],
        }))
    approvals.approve_design(base, SID, first_request)

    # 真实第二轮发生在首次 repair 已产出新候选之后；策略 epoch 不变，章业务
    # subject 改变。v4 禁止在同一未变化主体上靠重复 POST 累加决定。
    state['subjects'] = _subjects(brief='首次返修后的新候选业务字节')
    second_template = _template(base)
    second_request = _compact_request(
        second_template.model_copy(update={
            'chapters': [second_template.chapters[0].model_copy(update={
                'placeholder': False, 'template_token': None,
                'reason': '第二轮补充事实组合，第一轮约束仍未关闭',
                'constraints': [DesignRepairConstraint(
                    code='fact_grouping', scene_refs=[],
                    opaque_aliases=[FACT, PERSON])],
            })],
        }))
    doc = approvals.approve_design(base, SID, second_request)
    decision = doc.design_review.current_decision
    assert decision is not None
    resolved = approvals.resolve_design_action(
        base, SID, approvals._design_action_review_token(
            decision.decision_sha256), expected='repair')
    chapter_scope = resolved.chapters[0]
    assert chapter_scope.new_constraints == second_request.chapters[0].constraints
    assert {row.code for row in chapter_scope.accumulated_constraints} == {
        'scene_goal', 'fact_grouping'}

    chapter = second_template.chapters[0].subject.chapter.model_dump(mode='json')
    for scene in chapter['scenes']:
        scene.pop('candidate_scene_id')
    review_token = approvals._design_chapter_edit_review_token(
        decision, decision.chapters[0], chapter_scope)
    workpack = approvals.design_workpack(base, SID)
    repair_item = next(
        item for item in workpack.groups[0].items
        if item.code == 'design_repair_requested:ch1')
    assert repair_item.review_token == review_token
    assert decision.decision_sha256 not in repair_item.detail
    assert chapter_scope.constraint_set_sha256 not in repair_item.detail
    edit = BlindChapterEditRequest.model_validate({
        'actor': 'human:reviewer', 'reason': '提交完整章 typed response',
        'review_token': review_token,
        'output_ordinal': 1,
        'chapter': chapter,
    })
    authorization = approvals.resolve_design_edit_action(base, SID, edit)
    assert authorization.scope == chapter_scope
    assert authorization.review_token == review_token
    assert authorization.candidate_sha256 == decision.candidate_sha256
    stale = edit.model_copy(update={'review_token': 's4er1_' + '0' * 64})
    with pytest.raises(HumanEditProtected, match='opaque review_token'):
        approvals.resolve_design_edit_action(base, SID, stale)


def test_strategy_replan_scope_has_no_chapters(isolated):
    base, _state = isolated
    template = _template(base)
    reviewed = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'reason': '整书策略需重新组织后再审',
        }),
        'chapters': [
            row.model_copy(update={
                'placeholder': False, 'template_token': None,
                'reason': '本章随整书策略重规划后再审',
            })
            for row in template.chapters
        ],
    }))
    doc = approvals.approve_design(base, SID, reviewed)
    decision = doc.design_review.current_decision
    assert decision is not None
    scope = approvals.resolve_design_action(
        base, SID, approvals._design_action_review_token(
            decision.decision_sha256), expected='replan')
    assert doc.design_review.state == 'strategy_replan_requested'
    assert scope.strategy_replan and scope.target_chapters == [] and scope.chapters == []


def test_template_reuses_only_unchanged_accept_subjects(isolated):
    base, state = isolated
    accepted = approvals.approve_design(base, SID, _accept_all(_template(base)))
    decision = accepted.design_review.current_decision
    assert decision is not None
    state['subjects'] = _subjects(brief='这次只改变本章候选业务字节')

    refreshed = _template(base)

    assert refreshed.strategy.decision == 'accept'
    assert not refreshed.strategy.placeholder
    assert refreshed.strategy.template_token is None
    assert refreshed.strategy.source_decision_sha256 == decision.decision_sha256
    assert refreshed.chapters[0].decision == 'request_repair'
    assert refreshed.chapters[0].source_decision_sha256 is None


def test_constraint_refs_must_belong_to_the_reviewed_chapter(isolated):
    base, _state = isolated
    template = _template(base)
    request = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False,
            'template_token': None,
            'decision': 'accept', 'reason': '策略可保留',
            'replan_constraints': [],
        }),
        'chapters': [template.chapters[0].model_copy(update={
            'placeholder': False,
            'template_token': None,
            'reason': '场景目标引用需修订',
            'constraints': [DesignRepairConstraint(
                code='scene_goal', scene_refs=['c99s99'],
                opaque_aliases=[FACT],
            )],
        })],
    }))
    with pytest.raises(HumanEditProtected, match='constraint 越界'):
        approvals.approve_design(base, SID, request)


def test_explicit_v5_to_v6_migration_never_fabricates_design_history(
        monkeypatch, tmp_path):
    payload = approvals._fresh_record(SID).model_dump(
        mode='json',
        exclude={'design_history', 'design_decision',
                 'closure_history', 'closure_decision', 'seen_subjects'})
    payload['v'] = 5
    legacy = approvals._ApprovalRecordV5Legacy.model_validate(payload)
    book = tmp_path / f'legacy_{SID}'
    book.mkdir()
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: book)

    migrated, backfilled = approvals._migrate_legacy_v5(tmp_path, SID, legacy)

    assert migrated.v == 7 and backfilled == []
    assert migrated.design_history == [] and migrated.design_decision is None
    assert not migrated.writing


def test_design_http_routes_keep_template_read_only_and_apply_typed(
        monkeypatch, tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    from novel.api.routes import approvals as route

    template = _template_for_http()
    request = _accept_all(template)
    calls = []
    monkeypatch.setattr(
        route.approvals, 'design_template',
        lambda base, sid, **kwargs: calls.append(('get', sid, kwargs)) or template)
    response = ApprovalDoc(
        sid=SID,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        spawns={},
        structure=StructureApprovalView(
            state='unavailable', current_subject_sha256=None),
    )
    monkeypatch.setattr(
        route.approvals, 'approve_design',
        lambda base, sid, body: calls.append(('post', sid, body)) or response)
    with TestClient(create_app(tmp_path)) as client:
        got = client.get(
            f'/api/novels/{SID}/approvals/design/template',
            headers={UI_HEADER: '1'},
            params={
                'actor': 'reviewer', 'reviewer_kind': 'human',
                'review_run_id': 'review-1', 'reason': '完整审阅',
            })
        posted = client.post(
            f'/api/novels/{SID}/approvals/design',
            headers={UI_HEADER: '1'}, json=request.model_dump(mode='json'))
    assert got.status_code == 200 and posted.status_code == 200
    assert calls[0][0] == 'get' and calls[1][0] == 'post'


def _template_for_http() -> DesignReviewTemplate:
    current = _subjects()
    subject = current.chapters[0]
    return DesignReviewTemplate(
        actor='reviewer', reviewer_kind='human', review_run_id='review-1',
        general_reason='完整审阅', review_token='s4dr1_' + 'f' * 64,
        candidate_sha256=current.candidate_sha256,
        aggregate_subject_sha256=current.aggregate_subject_sha256,
        repair_capability=current.repair_capability,
        strategy={
            'subject': current.strategy,
            'subject_sha256': current.strategy_subject_sha256,
            'placeholder': True,
            'template_token': 'd' * 64,
            'decision': 'request_replan', 'reason': '关闭式模板',
            'replan_constraints': [{
                'kind': 'replace_reconstruction_strategy',
                'subject_sha256': current.strategy_subject_sha256,
            }],
        },
        chapters=[{
            'output_ordinal': 1, 'subject': subject,
            'subject_sha256': current.chapter_subject_sha256[1],
            'placeholder': True,
            'template_token': 'e' * 64,
            'decision': 'request_repair', 'reason': '关闭式模板',
            'constraints': [{
                'code': 'scene_goal',
                'scene_refs': [subject.chapter.scenes[0].candidate_scene_id],
                'opaque_aliases': [subject.blind_truth.facts[0].fact_alias],
            }],
        }],
    )


def _blocked_preflight() -> DesignPreflightSummary:
    return _pass_preflight(status='blocked', failures=[DesignPreflightFailure(
        code='fact_coverage', rule='fact_coverage', chapter_ordinal=1,
        refs=['f_1'], repair_owner='stage4.design',
        detail='land 事实未恰好落一次（测试注入）')])


def test_blocked_preflight_refuses_accept_but_allows_repair(isolated):
    base, state = isolated
    state['subjects'] = _subjects(preflight=_blocked_preflight())
    template = _template(base)
    with pytest.raises(HumanEditProtected, match='预检 blocked'):
        approvals.approve_design(base, SID, _accept_all(template))
    # remedy 不受闸：request_repair 仍可提交。
    repair_request = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_replan', 'reason': '预检 blocked，整书重规划',
            'source_decision_sha256': None}),
        'chapters': [row.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_repair',
            'reason': '按预检 witness 修订本章',
            'constraints': [DesignRepairConstraint(
                code='fact_grouping',
                scene_refs=[row.subject.chapter.scenes[0].candidate_scene_id],
                opaque_aliases=[
                    row.subject.blind_truth.facts[0].fact_alias,
                    # arity (0,2)：fact_grouping 至少要两个 opaque alias。
                    row.subject.blind_truth.facts[0].participant_aliases[0],
                ])],
            'source_decision_sha256': None}) for row in template.chapters],
    }))
    doc = approvals.approve_design(base, SID, repair_request)
    # 策略 replan + 章 repair 的组合态由 view 状态机裁定（replan 优先）。
    assert doc.design_review.state == 'strategy_replan_requested'


def test_preflight_missing_fails_closed_for_accept(isolated):
    base, state = isolated
    state['subjects'] = _subjects(preflight=None)
    template = _template(base)
    with pytest.raises(HumanEditProtected, match='预检未能产出'):
        approvals.approve_design(base, SID, _accept_all(template))


def test_retired_repair_code_refused_on_new_decision(isolated):
    base, state = isolated
    template = _template(base)
    request = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_replan', 'reason': '泛化重排不再可提交',
            'source_decision_sha256': None}),
        'chapters': [row.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_repair', 'reason': '退役码必须被拒',
            'constraints': [DesignRepairConstraint(
                code='structure_delta', scene_refs=[], opaque_aliases=[])],
            'source_decision_sha256': None}) for row in template.chapters],
    }))
    with pytest.raises(HumanEditProtected, match='退役修复码'):
        approvals.approve_design(base, SID, request)


def test_binding_constraint_unsat_requires_mechanical_witness(isolated):
    base, state = isolated
    template = _template(base)
    request = _compact_request(template.model_copy(update={
        'strategy': template.strategy.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_replan', 'reason': '无 witness 的不可满足主张',
            'source_decision_sha256': None}),
        'chapters': [row.model_copy(update={
            'placeholder': False, 'template_token': None,
            'decision': 'request_repair', 'reason': '把 B 失败升级为不可满足',
            'constraints': [DesignRepairConstraint(
                code='binding_constraint_unsat',
                scene_refs=[row.subject.chapter.scenes[0].candidate_scene_id],
                opaque_aliases=[])],
            'source_decision_sha256': None}) for row in template.chapters],
    }))
    with pytest.raises(HumanEditProtected, match='缺机械'):
        approvals.approve_design(base, SID, request)
