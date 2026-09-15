"""独立 Prepare 的人工裁决面：CLI 工作包/模板、待审免费恢复与 durable 决定重放。

【角色边界】受托 Commander reviewer 已退役（2026-09-15）：prepare 的 typed
决定改由人工经 CLI/HTTP 提交（``--review-decisions`` / ``--apply-edit``）。
旧 reviewer wire 的纯传输层反例（immutable workpack 信封绑定、native schema
组装与 rewrap revalidation，载体是 ``stage6_prepare_wire``/``reviewer_tasks``
的 build_task/load_action）随 commander 角色一并删除，不再有生产消费入口；
本文件只保留现役人工链：免费模板与 Stage5 门序、提交中断的免费恢复与预算，
以及 canonical 决定文档的原样重放。
"""
from __future__ import annotations

import json

import pytest

from contracts.artifacts import STAGE6_PREPARE_REVIEW_DECISIONS
from contracts.schema import decode_artifact, encode_artifact
from novel.cli import main as cli
from novel.orchestrator import approvals, nodes, queue
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from tests.test_stage6_prepare import decision_for, seed_prepare
from tests.test_stage2_global_agent import _free_ctx


def test_actual_cli_templates_are_free_and_canonical_prepare_requires_stage5(tmp_path, monkeypatch, capsys):
    ctx, driver, _ = seed_prepare(tmp_path)
    capsys.readouterr()  # 上游真实 E 的 @observe 不属于待验证的 CLI JSON stdout。
    args = ['run', prepare.NODE, ctx.sid, '--base', str(tmp_path)]
    rc = cli.main([*args, '--emit-review-workpack'])
    output = capsys.readouterr()
    assert rc == 0, output.err
    assert output.out.strip(), f'CLI 工作包缺 JSON stdout：{output.err}'
    pack = json.loads(output.out)
    assert pack['state']['state'] == 'pending'
    rc = cli.main([*args, '--emit-review-template'])
    output = capsys.readouterr()
    assert rc == 0, output.err
    decoded = decode_artifact(prepare.DECISIONS, json.loads(output.out))
    assert decoded.payload['source_sha256'] == pack['state']['subject_sha256']
    # 本例验证 CLI→Stage5 的门序；Stage2 语义审阅已退役，这里只剩 Stage5
    # require 和 dispatch 哨兵保持真实。
    monkeypatch.setattr(cli, '_dispatch', lambda *a, **k: pytest.fail('未闭Stage5不得进入driver/producer'))
    rc = cli.main([*args, '--yes'])
    failure = capsys.readouterr()
    assert rc == 2, failure.err
    assert 'stage5' in (failure.out + failure.err).lower()
    assert len(driver.calls) == 1


def test_pending_decision_exposes_exact_free_recovery_and_preserves_budget(tmp_path, monkeypatch):
    ctx, driver, _ = seed_prepare(tmp_path)
    free = _free_ctx(ctx)
    with monkeypatch.context() as interrupted:
        interrupted.setattr(prepare, '_finish_journal', lambda *a: (_ for _ in ()).throw(OSError('提交中断')))
        with pytest.raises(OSError, match='提交中断'):
            prepare.submit_review(free, decision_for(free))
    before = ctx.ledger.path.read_bytes()
    item = queue.prepare_edit_recovery_item(tmp_path, ctx.sid, code=ctx.code)
    assert item.waiting_on == queue.WAITING_ON_STAGE6_PREPARE_COMMIT
    assert item.flags == ['--recover-edit'] and not nodes.is_paid(item.node, item.flags)
    view = approvals.get(tmp_path, ctx.sid)
    assert view.prepare.pending_commit
    assert any(action.kind == prepare.NODE and action.extra == ['--recover-edit'] for action in view.spawns['s5'])
    prepare.run_recover_edit(_free_ctx(ctx))
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'accepted'
    assert queue.prepare_edit_recovery_item(tmp_path, ctx.sid, code=ctx.code) is None
    assert ctx.ledger.path.read_bytes() == before and len(driver.calls) == 1


def test_canonical_durable_prepare_decision_replays_and_uses_same_service_input(tmp_path):
    """canonical 决定文档（emit-review-template 的 envelope）原样重放：
    幂等提交同一服务输入，只读采用证明仍然成立，不再购买任何模型调用。"""
    ctx, driver, _ = seed_prepare(tmp_path)
    free = _free_ctx(ctx)
    decision = decision_for(_free_ctx(ctx))
    document = encode_artifact(
        STAGE6_PREPARE_REVIEW_DECISIONS, decision.model_dump(mode='json'))
    path = tmp_path / 'prepare-decisions.json'
    path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')

    prepare.run_review_decisions(free, str(path))
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'accepted'

    # 同一份文档重放：submit_review 以请求摘要幂等短路，不产生第二份 journal。
    prepare.run_review_decisions(_free_ctx(ctx), str(path))
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'accepted'
    assert prepare.submitted_decision_is_current(
        tmp_path, ctx.sid, document, code=ctx.code)
    assert len(driver.calls) == 1
