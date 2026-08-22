#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage0 的不可变 revision 链与原子发布（2026-08-22 阶段 C）。

本文件取代 `tests/test_stage0_merge.py`：码点级 diff3、typed 冲突、WAL 崩溃重放矩阵
与 `_rounds/` 逐轮归档一起下线之后，那些用例守的洞由这里的三组接管——
① 链自证（逐条复算 `revision_id`）；② 物化投影可自愈、且**手改会被拦住**；
③ 三选一（保留当前 / 采纳候选 / 采纳后再编辑）与它们的乐观锁、幂等重放。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.api import Stage0CandidateDecisionRequest
from contracts.schema.stage0 import (
    ApprovedApproval, Stage0CandidatePlan, Stage0Current, Stage0Revisions,
)
from novel.api.routes import ingest as ingest_routes
from novel.errors import (
    ArtifactUnreadable, HumanEditProtected, ProvenanceRefused, SourceConflict,
    UpstreamMissing,
)
from novel.orchestrator import uploads
from novel.stages.stage0 import checks as stage0_checks
from novel.stages.stage0 import removal
from novel.stages.stage0 import revisions as rev
from novel.stages.stage0.removal import (
    CLEAN_REL, REPORT_REL, _context, run_edit, run_source,
)


def _source(tmp_path: Path, sid: str = '900') -> Path:
    path = tmp_path / f'FQ_rev_{sid}.txt'
    path.write_text('开篇文案。\n\n1\n\n甲乙丙。\n', encoding='utf-8', newline='')
    return path


def _machine_learns_new_extra(monkeypatch, marker: str = '甲乙丙。') -> None:
    """模拟「机器剔除算法更新」：扫描器学会把 `marker` 那行认成番外。

    这是「同一原文、机器结果变化」的**真实世界形状**——机器结果只会随代码/
    扫描器变化（原文变了走 `source_changed` 分支）。单点 patch `ingest.nonbody_kind`：
    `scan_text` 在调用时经 ingest 模块全局取它，产地与视图两条路看到同一份新结果。
    """
    from novel.core import ingest
    real = ingest.nonbody_kind

    def fake(content: str):
        if content.strip() == marker:
            return '番外'
        return real(content)

    monkeypatch.setattr(ingest, 'nonbody_kind', fake)


def _log(novel_dir: Path) -> Stage0Revisions:
    return rev.read(removal.ArtifactStore(_context(novel_dir, 'stage0.remove')))


def _approve(novel_dir: Path, *, at: str = '2026-08-22T00:00:00+00:00'):
    return removal.run_approve(
        _context(novel_dir, 'stage0.approve'),
        actor='reviewer', reason='看过了', approved_at=at)[0]


def _edit(novel_dir: Path, tmp_path: Path, text: str):
    path = tmp_path / 'human.txt'
    path.write_text(text, encoding='utf-8', newline='')
    return run_edit(
        _context(novel_dir, 'stage0.edit'), path,
        actor='annotator-a', reason='人工校正正文')[0]


def _candidate_plan(novel_dir: Path, tmp_path: Path, action: str, *,
                    actor: str = 'reviewer', reason: str = '三选一') -> Path:
    log = _log(novel_dir)
    plan = Stage0CandidatePlan(
        action=action,
        candidate_revision_id=log.candidate_revision_id,
        expected_revision_id=log.current.revision_id,
        actor=actor, reason=reason,
    )
    path = tmp_path / f'{action}-plan.json'
    path.write_text(plan.model_dump_json(), encoding='utf-8', newline='')
    return path


def _pending_candidate(tmp_path: Path, monkeypatch, sid: str = '900'):
    """机器重跑与**已批准**的当前稿不同 ⇒ 挂候选，正式稿一个字不动。"""
    source = _source(tmp_path, sid)
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    _approve(novel_dir)
    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')
    return novel_dir, source


# ── ① 链自证 ────────────────────────────────────────────────────────────────


def test_first_run_writes_one_machine_root_and_materializes_the_live_pair(tmp_path):
    source = _source(tmp_path, '910')
    novel_dir, result, check = run_source(source, tmp_path / 'Output')

    log = _log(novel_dir)
    assert len(log.revisions) == 1
    root = log.revisions[0]
    assert root.parent_revision_id is None
    assert (root.origin, root.actor, root.reason) == ('machine', None, None)
    assert log.current.revision_id == root.revision_id
    assert log.current.approval.status == 'pending'
    assert log.candidate_revision_id is None and log.dismissed == []

    # 两份 live 产物就是 current 的物化投影，逐字。
    assert (novel_dir / CLEAN_REL).read_text(encoding='utf-8') == root.content.clean_text
    assert result.report == rev.report_of(log)
    assert result.report.revision_id == root.revision_id
    assert result.report.revision_origin == 'machine'
    # 零命中首跑照样停在人工闸前（2026-08-21 停产自动批）。
    assert check is not None
    assert next(line for line in check.rules if line.rule == 's0_approval').status == 'hit'
    assert next(
        line for line in check.rules if line.rule == 's0_revision_chain').status == 'ok'


def test_revision_id_recomputes_from_its_preimage_and_tampering_is_refused(tmp_path):
    novel_dir, _result, _check = run_source(
        _source(tmp_path, '911'), tmp_path / 'Output')
    store = removal.ArtifactStore(_context(novel_dir, 'stage0.remove'))
    log = rev.read(store)
    original = log.revisions[0]
    assert original.revision_id == rev.revision_digest(
        parent_revision_id=original.parent_revision_id, origin=original.origin,
        actor=original.actor, reason=original.reason, content=original.content)

    # 手改盘上的字节：正文换掉、两格派生数跟着改到自洽 ⇒ **模型校验一条都拦不住**，
    # 唯一还对不上的是 `revision_id`。这正是审计链下线之后由前像接管的那个洞。
    path = novel_dir / rev.REVISIONS_REL
    raw = json.loads(path.read_text(encoding='utf-8'))
    forged = '被改过的正文。'
    content = raw['revisions'][0]['content']
    content['clean_text'] = forged
    content['clean_sha1'] = hashlib.sha1(forged.encode('utf-8')).hexdigest()
    content['body_code_points'] = len(forged)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8', newline='')

    with pytest.raises(ArtifactUnreadable, match='id 与内容对不上'):
        rev.read(store)


def test_digest_golden_vector_pins_the_canonical_preimage():
    """`_digest` 的前像是契约（stage0.md §2.5 前像定义表），不是实现细节。

    覆盖：中文 · 非 BMP（UTF-16 代理对）· `None` · bool · list · tuple→JSON array ·
    键序无关。锚值固化在此——任何人改了序列化口径（separators / sort_keys /
    ensure_ascii），这条当场红，而不是文档静默失效。
    ⚠ 锚值与 2026-08-21 的 `snapshot_digest` golden vector **逐字相同**：
    阶段 C 换掉的是「谁被哈希」，不是「怎么哈希」。
    """
    base = {
        '中文': '番外体的内容。',
        '非BMP': '𠀀🎹',
        '空值': None,
        '布尔': False,
        '列表': [1, '二', None],
        '元组': (7, 8),
    }
    shuffled = {
        '元组': (7, 8),
        '布尔': False,
        '列表': [1, '二', None],
        '空值': None,
        '非BMP': '𠀀🎹',
        '中文': '番外体的内容。',
    }
    anchor = '94cf05ad21bff20136f08153ec5778a39c5b5fd55de103e779a58a18ec95d136'
    assert rev._digest(base) == rev._digest(shuffled) == anchor
    assert rev._digest({**base, '中文': '另一个值'}) != anchor


def test_revision_id_ignores_time_and_code_so_a_dismissed_candidate_stays_dismissed(
        tmp_path):
    """前像不含 `at` / `code`：同内容同 parent 必得同一个 id。

    ⚠ 这不是省事——`dismissed` 认的就是这个 id。它若随时间/代码版本变化，
    人否掉的候选每跑一次机器就复活一次。
    """
    content = rev.read(removal.ArtifactStore(_context(
        run_source(_source(tmp_path, '912'), tmp_path / 'Output')[0],
        'stage0.remove'))).revisions[0].content
    first = rev.make_revision(
        parent_revision_id=None, origin='machine', actor=None, reason=None,
        code='code-a', content=content, at='2020-01-01T00:00:00+00:00')
    later = rev.make_revision(
        parent_revision_id=None, origin='machine', actor=None, reason=None,
        code='code-b', content=content, at='2099-12-31T00:00:00+00:00')
    assert first.revision_id == later.revision_id


# ── ② 物化投影：可自愈，但手改会被拦住 ──────────────────────────────────────


def test_interrupted_materialization_heals_on_the_next_write_path(tmp_path, monkeypatch):
    """崩在两次 write 之间 ⇒ 下一条写路径按链重新物化。**这就是删掉 WAL 的依据。**"""
    source = _source(tmp_path, '920')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    stale_clean = (novel_dir / CLEAN_REL).read_bytes()
    stale_report = (novel_dir / REPORT_REL).read_bytes()

    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')          # 当前稿是机器且 pending ⇒ 直接替换
    fresh_clean = (novel_dir / CLEAN_REL).read_bytes()
    fresh_report = (novel_dir / REPORT_REL).read_bytes()
    assert fresh_clean != stale_clean

    # 手工把盘面退回「物化只完成一半」的样子：live 是**链上另一版**的正文。
    (novel_dir / CLEAN_REL).write_bytes(stale_clean)
    (novel_dir / REPORT_REL).write_bytes(stale_report)
    drifted = stage0_checks.run_check_only(_context(novel_dir, 'stage0.remove'))
    chain = next(line for line in drifted.rules if line.rule == 's0_revision_chain')
    assert chain.status == 'hit'

    run_source(source, tmp_path / 'Output')
    assert (novel_dir / CLEAN_REL).read_bytes() == fresh_clean
    assert (novel_dir / REPORT_REL).read_bytes() == fresh_report


def test_bypassing_the_entry_point_to_edit_clean_text_is_refused_not_overwritten(
        tmp_path):
    """live 正文是投影，但**投影不是可以随便冲掉的东西**。

    中断留下的一定是链上某一版的正文；手改留下的字节链上一版都对不上。
    ⇒ 对不上就拒绝并指路，绝不「补一次物化」把人的字悄悄冲掉——剔除是破坏性的，
    产物目录里没有第二份副本。
    """
    novel_dir, _first, _ = run_source(_source(tmp_path, '921'), tmp_path / 'Output')
    forged = '绕过入口直接改盘。\n'
    (novel_dir / CLEAN_REL).write_text(forged, encoding='utf-8', newline='')

    with pytest.raises(HumanEditProtected, match='绕过'):
        _approve(novel_dir)

    assert (novel_dir / CLEAN_REL).read_text(encoding='utf-8') == forged


# ── ③ 机器重跑：换掉 / 挂候选，两条路各有明确条件 ──────────────────────────


def test_same_machine_result_changes_nothing_on_disk(tmp_path):
    source = _source(tmp_path, '930')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    before = {rel: (novel_dir / rel).read_bytes()
              for rel in (CLEAN_REL, REPORT_REL, rev.REVISIONS_REL)}

    run_source(source, tmp_path / 'Output')

    assert {rel: (novel_dir / rel).read_bytes() for rel in before} == before


def test_machine_rerun_replaces_an_unapproved_machine_draft_without_a_candidate(
        tmp_path, monkeypatch):
    """当前稿是机器算的、还没人看过 ⇒ 换成新结果**不丢任何东西**。"""
    source = _source(tmp_path, '931')
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    _machine_learns_new_extra(monkeypatch)

    _dir, second, _check = run_source(source, tmp_path / 'Output')

    log = _log(novel_dir)
    assert log.candidate_revision_id is None
    assert len(log.revisions) == 2
    assert log.current.revision_id != first.report.revision_id
    assert rev.current_revision(log).parent_revision_id == first.report.revision_id
    assert log.current.approval.status == 'pending'
    assert second.report.revision_origin == 'machine'
    assert second.clean_text != first.clean_text


@pytest.mark.parametrize('guard', ['approved', 'human_edit'])
def test_machine_rerun_only_proposes_a_candidate_when_something_could_be_lost(
        tmp_path, monkeypatch, guard):
    source = _source(tmp_path, '932' if guard == 'approved' else '933')
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    if guard == 'approved':
        _approve(novel_dir)
    else:
        _edit(novel_dir, tmp_path, first.clean_text + '人工补记一行。\n')
    before_clean = (novel_dir / CLEAN_REL).read_bytes()
    before_report = (novel_dir / REPORT_REL).read_bytes()

    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')

    log = _log(novel_dir)
    assert log.candidate_revision_id is not None
    candidate = rev.by_id(log, log.candidate_revision_id)
    assert candidate.origin == 'machine'
    # 正式稿一个字不动：候选只是链上一条还没被指向的 revision。
    assert (novel_dir / CLEAN_REL).read_bytes() == before_clean
    assert (novel_dir / REPORT_REL).read_bytes() == before_report


def test_a_changed_source_is_refused_before_anything_is_written(tmp_path):
    source = _source(tmp_path, '934')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    before = (novel_dir / rev.REVISIONS_REL).read_bytes()
    source.write_text('另一本书的正文。\n\n1\n\n甲。\n', encoding='utf-8', newline='')

    with pytest.raises(SourceConflict, match='stage0_source_changed'):
        run_source(source, tmp_path / 'Output')

    assert (novel_dir / rev.REVISIONS_REL).read_bytes() == before


# ── ④ 三选一 ────────────────────────────────────────────────────────────────


def test_adopting_a_candidate_records_who_swapped_the_text_and_reopens_the_gate(
        tmp_path, monkeypatch):
    novel_dir, _source = _pending_candidate(tmp_path, monkeypatch, '940')
    log = _log(novel_dir)
    candidate = rev.by_id(log, log.candidate_revision_id)

    removal.run_candidate_plan(
        _context(novel_dir, 'stage0.edit'),
        _candidate_plan(novel_dir, tmp_path, 'adopt',
                        actor='reviewer', reason='机器新结果更准'))

    after = _log(novel_dir)
    current = rev.current_revision(after)
    assert current.origin == 'human_adopt'
    assert (current.actor, current.reason) == ('reviewer', '机器新结果更准')
    assert current.content == candidate.content          # 内容逐字取候选……
    assert current.revision_id != candidate.revision_id  # ……但它是另一次状态转移
    assert current.parent_revision_id == log.current.revision_id
    assert after.candidate_revision_id is None
    # ⚠ 换稿必须重新批准：原来那一版是**已批准**的，采纳之后闸门重新关上。
    assert after.current.approval.status == 'pending'
    assert (novel_dir / CLEAN_REL).read_text(encoding='utf-8') == candidate.content.clean_text


def test_dismissing_a_candidate_keeps_the_current_text_and_never_resurrects_it(
        tmp_path, monkeypatch):
    novel_dir, source = _pending_candidate(tmp_path, monkeypatch, '941')
    before_clean = (novel_dir / CLEAN_REL).read_bytes()
    log = _log(novel_dir)
    dismissed_id = log.candidate_revision_id

    removal.run_candidate_plan(
        _context(novel_dir, 'stage0.edit'),
        _candidate_plan(novel_dir, tmp_path, 'dismiss',
                        actor='reviewer', reason='机器认错了，保留当前稿'))

    after = _log(novel_dir)
    assert after.candidate_revision_id is None
    assert after.current.revision_id == log.current.revision_id
    assert after.current.approval.status == 'approved'   # 当前稿没动 ⇒ 批准也没动
    assert [item.revision_id for item in after.dismissed] == [dismissed_id]
    assert (novel_dir / CLEAN_REL).read_bytes() == before_clean

    # 机器再跑一次仍然算出同一份内容 ⇒ 同一个 id ⇒ **不复活**。
    run_source(source, tmp_path / 'Output')
    assert _log(novel_dir).candidate_revision_id is None


def test_editing_on_top_of_an_adopted_candidate_is_just_adopt_then_edit(
        tmp_path, monkeypatch):
    """三选一的第三支不是第三个动作：adopt 之后照常走 `stage0.edit`。"""
    novel_dir, _source = _pending_candidate(tmp_path, monkeypatch, '942')
    removal.run_candidate_plan(
        _context(novel_dir, 'stage0.edit'),
        _candidate_plan(novel_dir, tmp_path, 'adopt'))
    adopted = rev.current_revision(_log(novel_dir))

    edited = _edit(novel_dir, tmp_path, adopted.content.clean_text + '再补一句。\n')

    log = _log(novel_dir)
    assert [item.origin for item in log.revisions] == [
        'machine', 'machine', 'human_adopt', 'human_edit']
    assert edited.revision_origin == 'human_edit'
    assert rev.current_revision(log).parent_revision_id == adopted.revision_id
    assert edited.approval.status == 'pending'


def test_a_stale_page_cannot_decide_a_candidate(tmp_path, monkeypatch):
    novel_dir, _source = _pending_candidate(tmp_path, monkeypatch, '943')
    plan_path = _candidate_plan(novel_dir, tmp_path, 'adopt')
    # 页面还开着，另一条路先把当前稿改了。
    _edit(novel_dir, tmp_path, '另一位审阅者先改了正文。\n')

    with pytest.raises(HumanEditProtected):
        removal.run_candidate_plan(_context(novel_dir, 'stage0.edit'), plan_path)


@pytest.mark.parametrize('action', ['adopt', 'dismiss'])
def test_replaying_the_same_decision_after_a_crash_is_idempotent(
        tmp_path, monkeypatch, action):
    novel_dir, _source = _pending_candidate(
        tmp_path, monkeypatch, '944' if action == 'adopt' else '945')
    plan_path = _candidate_plan(novel_dir, tmp_path, action)

    first, _ = removal.run_candidate_plan(_context(novel_dir, 'stage0.edit'), plan_path)
    landed = (novel_dir / rev.REVISIONS_REL).read_bytes()

    # 进程崩在 CLI 返回之前 ⇒ 同一份决定再跑一次，只认账不翻案。
    again, _ = removal.run_candidate_plan(_context(novel_dir, 'stage0.edit'), plan_path)

    assert again == first
    assert (novel_dir / rev.REVISIONS_REL).read_bytes() == landed


def test_reading_a_candidate_when_there_is_none_says_so(tmp_path):
    novel_dir, _first, _ = run_source(_source(tmp_path, '946'), tmp_path / 'Output')
    with pytest.raises(UpstreamMissing, match='没有待裁决'):
        removal.read_candidate(_context(novel_dir, 'stage0.remove'))


# ── ⑤ 审批 ──────────────────────────────────────────────────────────────────


def test_approval_rolls_back_to_pending_when_the_source_moves_mid_commit(
        tmp_path, monkeypatch):
    """提交与后置条件之间原文被换 ⇒ 回滚。**回滚 = 再原子写一次那一份文件。**"""
    source = _source(tmp_path, '950')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    before = (novel_dir / rev.REVISIONS_REL).read_bytes()
    before_report = (novel_dir / REPORT_REL).read_bytes()
    original_write = removal.ArtifactStore.write

    def write_then_move_source(self, rel, artifact):
        path = original_write(self, rel, artifact)
        if rel == rev.REVISIONS_REL:
            source.write_bytes(source.read_bytes() + b'\nsource changed during commit\n')
        return path

    monkeypatch.setattr(removal.ArtifactStore, 'write', write_then_move_source)

    with pytest.raises(ProvenanceRefused, match='not current'):
        _approve(novel_dir)

    assert (novel_dir / rev.REVISIONS_REL).read_bytes() == before
    assert (novel_dir / REPORT_REL).read_bytes() == before_report


def _legacy_auto_pair(tmp_path: Path, sid: str = '960'):
    """手工造一份**历史自动批**的链：`auto_zero_hit` 已于 2026-08-21 停产。"""
    novel_dir, _first, _ = run_source(_source(tmp_path, sid), tmp_path / 'Output')
    store = removal.ArtifactStore(_context(novel_dir, 'stage0.remove'))
    log = rev.read(store)
    rev.commit(store, Stage0Revisions(
        revisions=log.revisions,
        current=Stage0Current(
            revision_id=log.current.revision_id,
            approval=ApprovedApproval(
                status='approved', origin='auto_zero_hit',
                at='2026-08-01T00:00:00+00:00', by=None, reason=None)),
        candidate_revision_id=None,
    ))
    return novel_dir, f'rev_{sid}'


def test_legacy_auto_approval_decodes_but_is_not_executable(tmp_path):
    from novel.errors import ApprovalRequired
    from novel.orchestrator.queue import _human_gate_satisfied
    from novel.stages.stage0.eligibility import (
        LEGACY_AUTO_APPROVAL, block_reason, is_stage0_executable,
    )
    from novel.stages.stage1.partition import run_partition

    novel_dir, sid = _legacy_auto_pair(tmp_path)
    store = removal.ArtifactStore(_context(novel_dir, 'stage0.remove'))
    report, _ = store.read_own(removal.RemovalReport, REPORT_REL)

    assert not is_stage0_executable(report)
    assert block_reason(report) == LEGACY_AUTO_APPROVAL
    assert not _human_gate_satisfied(tmp_path / 'Output', sid, 'stage0.approve')
    with pytest.raises(ApprovalRequired, match=LEGACY_AUTO_APPROVAL):
        run_partition(_context(novel_dir, 'stage1.partition'))


def test_legacy_auto_approval_regains_execution_after_a_human_reapproves(tmp_path):
    from novel.stages.stage0.eligibility import is_stage0_executable

    novel_dir, _sid = _legacy_auto_pair(tmp_path, '961')
    before_clean = (novel_dir / CLEAN_REL).read_bytes()
    before = _log(novel_dir).current.revision_id

    reapproved = _approve(novel_dir)

    # 人工重批不产生新 revision：正文没动，动的只是闸门那一格。
    assert reapproved.approval.origin == 'human'
    assert reapproved.approval.by == 'reviewer' and reapproved.approval.reason == '看过了'
    assert reapproved.revision_id == before
    assert (novel_dir / CLEAN_REL).read_bytes() == before_clean
    assert is_stage0_executable(reapproved)


# ── ⑥ HTTP：一次性决定的留存与清理 ──────────────────────────────────────────


def _decision_request() -> Stage0CandidateDecisionRequest:
    return Stage0CandidateDecisionRequest(
        candidate_revision_id='a' * 64,
        expected_revision_id='b' * 64,
        actor='api-reviewer', reason='retain until terminal',
    )


def test_api_candidate_plan_is_content_addressed_and_cleaned_only_after_terminal(
    tmp_path, monkeypatch,
):
    request = _decision_request()
    plan = Stage0CandidatePlan(action='adopt', **request.model_dump())
    first = uploads.save_candidate_plan(tmp_path, '901', plan)
    second = uploads.save_candidate_plan(tmp_path, '901', plan)
    assert first == second and first.is_file()

    monkeypatch.setattr(ingest_routes.uploads, 'save_candidate_plan', lambda *_a: first)
    discarded: list[Path] = []
    real_discard = uploads.discard_edit_plan

    def discard(base, path):
        discarded.append(Path(path))
        real_discard(base, path)

    monkeypatch.setattr(ingest_routes.uploads, 'discard_edit_plan', discard)
    monkeypatch.setattr(
        ingest_routes.jobs, 'run', lambda *_a, **_k: SimpleNamespace(state='failed'))
    failed = ingest_routes._run_candidate_decision(
        str(tmp_path), '901', request, 'adopt')
    assert failed.state == 'failed' and discarded == [] and first.is_file()

    monkeypatch.setattr(
        ingest_routes.jobs, 'run', lambda *_a, **_k: SimpleNamespace(state='landed_red'))
    landed = ingest_routes._run_candidate_decision(
        str(tmp_path), '901', request, 'adopt')
    assert landed.state == 'landed_red' and discarded == [first] and not first.exists()


def test_api_keeps_the_candidate_plan_when_the_job_runner_crashes(tmp_path, monkeypatch):
    request = _decision_request()
    plan_path = tmp_path / 'durable-candidate-plan.json'
    plan_path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(
        ingest_routes.uploads, 'save_candidate_plan', lambda *_a: plan_path)
    discarded: list[Path] = []
    monkeypatch.setattr(
        ingest_routes.uploads, 'discard_edit_plan',
        lambda _base, path: discarded.append(Path(path)))

    def crash(*_args, **_kwargs):
        raise RuntimeError('spawn crashed')

    monkeypatch.setattr(ingest_routes.jobs, 'run', crash)
    with pytest.raises(RuntimeError, match='spawn crashed'):
        ingest_routes._run_candidate_decision(str(tmp_path), '901', request, 'adopt')
    assert discarded == [] and plan_path.is_file()
