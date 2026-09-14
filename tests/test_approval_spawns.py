#!/usr/bin/env python3
from __future__ import annotations

from novel.orchestrator import approvals


def test_gate_spawns_follow_the_same_disk_resume_verdict(monkeypatch, tmp_path):
    """FC-05: a fresh downstream artifact must not be offered for paid overwrite."""
    novel_dir = tmp_path / 'book_123'
    novel_dir.mkdir()
    sentinel = object()
    verdicts = {
        'stage4.design': approvals.state.RESUME_SKIP,
        'stage4.plan': approvals.state.RESUME_SKIP,
    }

    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(approvals, '_planned', lambda _base, _sid: [1, 2, 3])
    monkeypatch.setattr(approvals.state, 'overview', lambda _base, _sid: sentinel)
    monkeypatch.setattr(
        approvals.state, 'resume_verdict',
        lambda overview, node: (verdicts.get(node, approvals.state.RESUME_RUN), node))
    design_review = {'accepted': False}
    monkeypatch.setattr(
        approvals, 'design_review_is_current',
        lambda _base, _sid: design_review['accepted'])

    fresh = approvals.get(tmp_path, '123')
    assert [action.kind for action in fresh.spawns['s1']] == ['stage2.global']
    assert fresh.spawns['s4'] == [], 'fresh Stage4 A/B must not be spawned again'
    assert not (tmp_path / '_service').exists(), 'the dynamic GET remains read-only'

    # Stage2 复核闸退役：blueprint 的下一步由统一机械准入发布。
    from novel.orchestrator import mechanical_admission
    admission_state = {'ready': False}
    monkeypatch.setattr(mechanical_admission, 'blueprint_generation_ready',
                        lambda *_args: mechanical_admission.AdmissionCheck(
                            'stage2.blueprint', ok=admission_state['ready']))
    monkeypatch.setattr(mechanical_admission, 'stage4_ready',
                        lambda *_args: mechanical_admission.AdmissionCheck('stage4', ok=True))
    verdicts['stage2.global'] = approvals.state.RESUME_SKIP
    global_done = approvals.get(tmp_path, '123')
    assert global_done.spawns['s1'] == [], '机械准入未过不下发 Blueprint'
    admission_state['ready'] = True
    accepted = approvals.get(tmp_path, '123')
    assert [action.kind for action in accepted.spawns['s1']] == ['stage2.blueprint']

    verdicts['stage2.blueprint'] = approvals.state.RESUME_SKIP
    stage2_done = approvals.get(tmp_path, '123')
    assert stage2_done.spawns['s1'] == []

    verdicts['stage4.plan'] = approvals.state.RESUME_RUN
    missing = approvals.get(tmp_path, '123')
    assert missing.spawns['s4'] == [], 'A 候选落盘不等于 A 人审已通过'

    design_review['accepted'] = True
    missing = approvals.get(tmp_path, '123')
    assert [action.kind for action in missing.spawns['s4']] == ['stage4.plan']

    verdicts['stage4.design'] = approvals.state.RESUME_RUN
    both = approvals.get(tmp_path, '123')
    assert [action.kind for action in both.spawns['s4']] == ['stage4.design'], (
        'A/B 必须各自确认；A 未完成时不能把两次付费调用塞进一个 GateBar 动作')


def test_default_approval_value_is_not_mistaken_for_a_saved_human_choice(
        monkeypatch, tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    novel_dir = tmp_path / 'book_123'
    novel_dir.mkdir()
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(approvals, '_planned', lambda _base, _sid: [1, 2, 3])
    monkeypatch.setattr(approvals.state, 'overview', lambda _base, _sid: object())
    monkeypatch.setattr(
        approvals.state, 'resume_verdict',
        lambda _overview, node: (approvals.state.RESUME_RUN, node))
    from novel.orchestrator import mechanical_admission
    monkeypatch.setattr(mechanical_admission, 'blueprint_generation_ready',
                        lambda *_args: mechanical_admission.AdmissionCheck(
                            'stage2.blueprint', ok=False))
    monkeypatch.setattr(mechanical_admission, 'stage4_ready',
                        lambda *_args: mechanical_admission.AdmissionCheck('stage4', ok=False))

    doc = approvals.get(tmp_path, '123')
    assert doc.v == 9 and not doc.writing
    assert 'deep' not in type(doc).model_fields, '公开账不得重新暴露已退役的双臂决定'
    assert not approvals.has_record(tmp_path, '123')

    # 盘上即使已有一份当前记录，writing 没有当前 subject proof 仍不是花钱授权。
    approvals._write(tmp_path, approvals._fresh_record('123', [1, 2, 3]))
    assert not approvals.has_record(tmp_path, '123')
