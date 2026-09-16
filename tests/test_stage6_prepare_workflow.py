"""独立 Prepare：批次 B4 后 CLI 审阅写面退役；保留恢复与 auto accept。

机械校验通过即 ``system:stage6-prepare-auto``；``--emit-review-*`` /
``--review-decisions`` / ``--apply-edit`` 已退役。本文件钉住退役面、免费恢复与
正式生成后的自动接受。
"""
from __future__ import annotations

import pytest

from novel.cli import main as cli
from novel.orchestrator import approvals, nodes, queue
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from tests.test_stage6_prepare import decision_for, seed_prepare
from tests.test_stage2_global_agent import _free_ctx


def test_prepare_review_cli_surfaces_are_retired(tmp_path, monkeypatch, capsys):
    ctx, driver, _ = seed_prepare(tmp_path)
    capsys.readouterr()
    args = ['run', prepare.NODE, ctx.sid, '--base', str(tmp_path)]
    # 退役 flag：argparse 未知参数 SystemExit(2)，或节点表显式终态返回 2。
    for flag in ('--emit-review-workpack', '--emit-review-template',
                 '--review-decisions', '--apply-edit'):
        argv = [*args, flag, 'x.json'] if flag in {
            '--review-decisions', '--apply-edit'} else [*args, flag]
        try:
            rc = cli.main(argv)
        except SystemExit as exited:
            assert exited.code == 2, flag
            out = capsys.readouterr()
            assert 'unrecognized arguments' in out.err or flag in out.err
            continue
        out = capsys.readouterr()
        assert rc == 2, (flag, out.err or out.out)
        blob = (out.err + out.out)
        assert '不接受' in blob or '已退役' in blob, blob
    # Stage5 门序仍真实：未闭 Stage5 不得进入付费正式生成。
    monkeypatch.setattr(
        cli, '_dispatch',
        lambda *a, **k: pytest.fail('未闭Stage5不得进入driver/producer'))
    rc = cli.main([*args, '--yes'])
    failure = capsys.readouterr()
    assert rc == 2, failure.err
    assert 'stage5' in (failure.out + failure.err).lower()
    assert len(driver.calls) == 1


def test_pending_decision_exposes_exact_free_recovery_and_preserves_budget(
        tmp_path, monkeypatch):
    ctx, driver, _ = seed_prepare(tmp_path)
    free = _free_ctx(ctx)
    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            prepare, '_finish_journal',
            lambda *a: (_ for _ in ()).throw(OSError('提交中断')))
        with pytest.raises(OSError, match='提交中断'):
            prepare.submit_review(free, decision_for(free))
    before = ctx.ledger.path.read_bytes()
    item = queue.prepare_edit_recovery_item(tmp_path, ctx.sid, code=ctx.code)
    assert item.waiting_on == queue.WAITING_ON_STAGE6_PREPARE_COMMIT
    assert item.flags == ['--recover-edit'] and not nodes.is_paid(item.node, item.flags)
    view = approvals.get(tmp_path, ctx.sid)
    assert view.prepare.pending_commit
    assert any(
        action.kind == prepare.NODE and action.extra == ['--recover-edit']
        for action in view.spawns['s5'])
    prepare.run_recover_edit(_free_ctx(ctx))
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'accepted'
    assert queue.prepare_edit_recovery_item(tmp_path, ctx.sid, code=ctx.code) is None
    assert ctx.ledger.path.read_bytes() == before and len(driver.calls) == 1


def test_prepare_auto_accepts_after_mechanical_pass(tmp_path):
    """正式生成机械通过后 producer 以 system:stage6-prepare-auto 自动接受。"""
    ctx, driver, _ = seed_prepare(tmp_path)
    # seed_prepare 已跑完正式 E；B4 下应已 auto accept。
    state = prepare.view_state(ArtifactStore(_free_ctx(ctx)))
    assert state.state == 'accepted'
    assert state.review is not None
    assert state.review.actor == 'system:stage6-prepare-auto'
    assert len(driver.calls) == 1
