#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage0 的不可变 revision 链与原子发布（2026-08-22 阶段 C）。

本文件取代 `tests/test_stage0_merge.py`：码点级 diff3、typed 冲突、WAL 崩溃重放矩阵
与 `_rounds/` 逐轮归档一起下线之后，那些用例守的洞由这里的三组接管——
① 链自证（逐条复算 `revision_id`）；② 物化投影可自愈、且**手改会被拦住**；
③ 机器重跑：机械校验通过即自动接受（2026-09-15 全自动裁决，人工 approve/edit/
候选三选一已退役），存量人工稿关闭式拒绝重跑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.schema.stage0 import (
    PendingApproval, Stage0Current, Stage0RevisionContent, Stage0Revisions,
)
from novel.errors import (
    ArtifactUnreadable, SourceConflict, TerminalState,
)
from novel.stages.stage0 import checks as stage0_checks
from novel.stages.stage0 import removal
from novel.stages.stage0 import revisions as rev
from novel.stages.stage0.removal import CLEAN_REL, _context, run_source


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


def _store(novel_dir: Path):
    return removal.ArtifactStore(_context(novel_dir, 'stage0.remove'))


def _assert_auto_approval(approval) -> None:
    """机械接受记录的形状（`removal._auto_approval` 唯一落点）。"""
    assert approval.status == 'approved'
    assert approval.by == 'system:stage0-auto'
    assert approval.reason
    assert approval.at


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
    # 首跑即机械接受（2026-09-15 全自动裁决）：不再停在人工闸前。
    _assert_auto_approval(log.current.approval)
    assert log.candidate_revision_id is None and log.dismissed == []

    # 两份 live 产物就是 current 的物化投影，逐字。
    assert (novel_dir / CLEAN_REL).read_text(encoding='utf-8') == root.content.clean_text
    assert result.report == rev.snapshot_of(log)
    assert result.report.revision_id == root.revision_id
    assert result.report.revision_origin == 'machine'
    assert check is not None
    assert next(line for line in check.rules if line.rule == 's0_approval').status == 'ok'
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


def test_stale_materialization_heals_on_the_next_write_path(tmp_path, monkeypatch):
    """正文投影停在链上旧版 ⇒ 下一条写路径按 current 重新物化。"""
    source = _source(tmp_path, '920')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    stale_clean = (novel_dir / CLEAN_REL).read_bytes()

    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')          # 当前稿是机器稿 ⇒ 直接替换
    fresh_clean = (novel_dir / CLEAN_REL).read_bytes()
    assert fresh_clean != stale_clean

    # 手工把盘面退回「物化只完成一半」的样子：live 是**链上另一版**的正文。
    (novel_dir / CLEAN_REL).write_bytes(stale_clean)
    drifted = stage0_checks.run_check_only(_context(novel_dir, 'stage0.remove'))
    chain = next(line for line in drifted.rules if line.rule == 's0_revision_chain')
    assert chain.status == 'hit'

    run_source(source, tmp_path / 'Output')
    assert (novel_dir / CLEAN_REL).read_bytes() == fresh_clean


# ── ③ 机器重跑：机械通过即自动接受 ──────────────────────────────────────────


def test_same_machine_result_changes_nothing_on_disk(tmp_path):
    source = _source(tmp_path, '930')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    before = {rel: (novel_dir / rel).read_bytes()
              for rel in (CLEAN_REL, rev.REVISIONS_REL)}

    run_source(source, tmp_path / 'Output')

    assert {rel: (novel_dir / rel).read_bytes() for rel in before} == before


def test_machine_rerun_replaces_the_machine_draft_and_stays_auto_approved(
        tmp_path, monkeypatch):
    """当前稿是机器算的 ⇒ 换成新结果不丢任何东西，且**保持机械接受**。

    2026-09-15 全自动裁决前这里要求人工重批（换稿退回 pending）；现在
    `stage0.remove` 的机械判据就是唯一放行条件，替换稿直接带 auto 批准。
    """
    source = _source(tmp_path, '931')
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    _machine_learns_new_extra(monkeypatch)

    _dir, second, _check = run_source(source, tmp_path / 'Output')

    log = _log(novel_dir)
    assert log.candidate_revision_id is None
    assert len(log.revisions) == 2
    assert log.current.revision_id != first.report.revision_id
    assert rev.current_revision(log).parent_revision_id == first.report.revision_id
    _assert_auto_approval(log.current.approval)
    assert second.report.revision_origin == 'machine'
    assert second.clean_text != first.clean_text
    # 替换后执行资格仍然成立：下游 stage1 无需任何人工动作。
    from novel.stages.stage0.eligibility import is_stage0_executable
    assert is_stage0_executable(second.report)


def test_a_changed_source_is_refused_before_anything_is_written(tmp_path):
    source = _source(tmp_path, '934')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    before = (novel_dir / rev.REVISIONS_REL).read_bytes()
    source.write_text('另一本书的正文。\n\n1\n\n甲。\n', encoding='utf-8', newline='')

    with pytest.raises(SourceConflict, match='stage0_source_changed'):
        run_source(source, tmp_path / 'Output')

    assert (novel_dir / rev.REVISIONS_REL).read_bytes() == before


def test_human_origin_legacy_draft_refuses_the_machine_rerun(tmp_path):
    """存量书含人工剔除内容（旧 stage0.edit 产物）⇒ 关闭式拒绝重跑。

    机器重算会覆盖人工内容，而人工编辑入口已随全自动裁决退役——所以不是
    「挂候选等裁决」，而是指引显式废弃书目录后重跑（R-0.14），绝不静默覆盖。
    """
    source = _source(tmp_path, '935')
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')

    # 复刻 B1 之前的存量盘面：链上最新一版是人工编辑稿（origin=human_edit）。
    store = _store(novel_dir)
    log = rev.read(store)
    current = rev.current_revision(log)
    human = rev.make_revision(
        parent_revision_id=current.revision_id, origin='human_edit',
        actor='annotator', reason='存量人工校正', code='legacy',
        content=Stage0RevisionContent(
            clean_text=current.content.clean_text + '人工补记一行。\n',
            removed=first.report.removed))
    rev.commit(store, rev.published(log, human, candidate_revision_id=None))
    before = (novel_dir / rev.REVISIONS_REL).read_bytes()

    with pytest.raises(TerminalState, match='origin=human|人工剔除'):
        run_source(source, tmp_path / 'Output')

    assert (novel_dir / rev.REVISIONS_REL).read_bytes() == before
    assert (novel_dir / CLEAN_REL).read_text(encoding='utf-8').endswith('人工补记一行。\n')


def test_pending_machine_draft_keeps_stage1_blocked_until_stage0_accepts(tmp_path):
    """存量书停在 pending（B1 之前中途留下的机器稿）⇒ stage1 拒绝并指路重跑。

    「先重跑 stage0.remove 完成机械接受」是现役唯一恢复口径；执行资格的
    唯一落点仍是 `is_stage0_executable`，views 与 producer 都不得复述条件。
    """
    from novel.errors import ApprovalRequired
    from novel.stages.stage0.eligibility import is_stage0_executable
    from novel.stages.stage1.partition import run_partition

    source = _source(tmp_path, '936')
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    assert is_stage0_executable(first.report)

    # 把盘面退回 B1 之前的形状：同一版机器稿、批准格还是 pending。
    store = _store(novel_dir)
    log = rev.read(store)
    rev.commit(store, log.model_copy(update={'current': Stage0Current(
        revision_id=log.current.revision_id,
        approval=PendingApproval(status='pending'))}))

    snapshot = rev.snapshot_of(rev.read(store))
    assert not is_stage0_executable(snapshot)
    with pytest.raises(ApprovalRequired, match='先重跑 stage0.remove'):
        run_partition(_context(novel_dir, 'stage1.partition'))
    assert not (novel_dir / 'stage1').exists()

    from novel.views import ingest as ingest_views
    with pytest.raises(ApprovalRequired, match='先重跑 stage0.remove'):
        ingest_views.stage1_view(novel_dir.parent, novel_dir.name)
