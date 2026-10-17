#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""invocation-scoped operator 快照：一次指纹、一次完成度、每层一次复检。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator import inspection as I
from novel.stages import checkable_stages


def test_operator_snapshot_freezes_one_code_and_runs_each_check_once(
        monkeypatch, tmp_path):
    code_calls: list[None] = []
    overview_calls: list[tuple] = []
    check_calls: list[tuple] = []
    overview = SimpleNamespace(sid='book-1', stages=[])

    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: tmp_path / 'book')
    monkeypatch.setattr(
        I.prov, 'code_version', lambda: code_calls.append(None) or 'code-v1')
    monkeypatch.setattr(
        I, 'overview_at',
        lambda novel_dir, code: overview_calls.append((novel_dir, code)) or overview)
    monkeypatch.setattr(
        I, 'readonly_ctx', lambda novel_dir, node, code: (novel_dir, node, code))
    monkeypatch.setattr(I, 'check_node', lambda stage: f'{stage}.canonical-check')


    def check_only(stage):
        def run(ctx):
            check_calls.append((stage, ctx))
            return SimpleNamespace(node=f'{stage}.canonical-check')
        return run

    monkeypatch.setattr(I, 'check_only', check_only)

    snapshot = I.capture_operator_snapshot(tmp_path, 'book-1')

    assert len(code_calls) == 1
    assert overview_calls == [(tmp_path / 'book', 'code-v1')]
    # F8c：快照只采集有层级复检器的层（stage0/1）；新链 Stage2/4/5/7 的复检是
    # 节点级 `--check-only`，不进这份快照——`check(stage)` 对它们是「未采集」，
    # 由消费者显示「校验未知」，不是绿灯。
    assert sorted(stage for stage, _ctx in check_calls) == sorted(checkable_stages())
    assert all(ctx[2] == 'code-v1' for _stage, ctx in check_calls)
    assert snapshot.overview is overview
    assert set(snapshot.checks) == set(checkable_stages())
    assert all(snapshot.check(stage).report is not None
               for stage in checkable_stages())
    # 没采集的层（新链 Stage2/4/5/7 走节点级 --check-only）给「未采集」观测：
    # for_view() 为 None ⇒ 界面显示「校验未知」，不是绿灯。
    assert snapshot.check('stage2').for_view() is None
    assert snapshot.check('stage2').report is None
    with pytest.raises(KeyError):
        snapshot.check('stage3')  # 不在 STAGES 的层名是拼写错误，必须当场炸
    with pytest.raises(TypeError):
        snapshot.checks['stage0'] = I.CheckObservation()  # type: ignore[index]


def test_operator_snapshot_accepts_prevalidated_epoch_code_without_rehashing_repo(
        monkeypatch, tmp_path):
    overview = SimpleNamespace(sid='book-1', stages=[])
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: tmp_path / 'book')
    monkeypatch.setattr(
        I.prov, 'code_version',
        lambda: pytest.fail('Supervisor 快照不得重算整仓 code fingerprint'))
    monkeypatch.setattr(
        I, 'read_surface_seal',
        lambda _base, _sid, *, code: f'seal:{code}')
    monkeypatch.setattr(
        I, 'overview_at',
        lambda _novel_dir, code: overview if code == 'epoch-code' else None)
    monkeypatch.setattr(I, 'readonly_ctx', lambda *_args: object())
    monkeypatch.setattr(I, 'check_node', lambda stage: f'{stage}.check')
    monkeypatch.setattr(
        I, 'check_only',
        lambda stage: lambda _ctx: SimpleNamespace(node=f'{stage}.check'))

    snapshot = I.capture_operator_snapshot(
        tmp_path, 'book-1', code='epoch-code')

    assert snapshot.code == 'epoch-code'
    assert snapshot.surface_seal == 'seal:epoch-code'


def test_operator_snapshot_rejects_a_write_crossing_its_checks(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: tmp_path / 'book')
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        I, 'overview_at', lambda _novel_dir, _code: SimpleNamespace(sid='book-1'))
    monkeypatch.setattr(I, 'readonly_ctx', lambda *_args: object())
    monkeypatch.setattr(I, 'check_node', lambda stage: f'{stage}.check')
    monkeypatch.setattr(
        I, 'check_only',
        lambda _stage: lambda _ctx: SimpleNamespace(node='synthetic'))
    seals = iter(('before', 'after'))
    monkeypatch.setattr(
        I, 'read_surface_seal',
        lambda *_args, **_kwargs: next(seals))

    with pytest.raises(ArtifactUnreadable, match='混合时点'):
        I.capture_operator_snapshot(tmp_path, 'book-1')


def test_check_observation_only_downgrades_expected_unavailable_errors():
    assert I.CheckObservation(
        error=UpstreamMissing('这一层还没跑到')).for_view() is None

    bug = RuntimeError('判据实现坏了')
    with pytest.raises(RuntimeError, match='判据实现坏了'):
        I.CheckObservation(error=bug).for_view()


def test_read_surface_seal_ignores_unregistered_documents(
        monkeypatch, tmp_path):
    novel_dir = tmp_path / 'FQ_测试书_book-1'
    (novel_dir / 'stage4' / 'naming').mkdir(parents=True)
    artifact = novel_dir / 'stage4' / 'naming' / 'name_map.json'
    artifact.write_text('v1', encoding='utf-8')
    review = novel_dir / '测试书_review.md'
    review.write_text('draft v1', encoding='utf-8')
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v1')

    before = I.read_surface_seal(tmp_path, 'book-1')
    review.write_text('draft v2', encoding='utf-8')
    assert I.read_surface_seal(tmp_path, 'book-1') == before

    artifact.write_text('artifact changed', encoding='utf-8')
    assert I.read_surface_seal(tmp_path, 'book-1') != before
def test_surface_seal_ignores_legacy_report_files_but_keeps_active_artifact(
        monkeypatch, tmp_path):
    novel_dir = tmp_path / 'FQ_测试书_book-1'
    novel_dir.mkdir()
    canonical = novel_dir / '测试书_review.md'
    canonical.write_text('canonical evidence', encoding='utf-8')
    custom = novel_dir / '人工终审草稿.md'
    custom.write_text('draft', encoding='utf-8')
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v1')

    before = I.read_surface_seal(
        tmp_path, 'book-1')
    custom.write_text('edited draft', encoding='utf-8')
    custom.with_name(custom.name + '.lock').write_bytes(b'\0')
    custom.with_name(
        f'.{custom.name}.operator-sync.abc.tmp').write_text(
            'pending', encoding='utf-8')
    assert I.read_surface_seal(
        tmp_path, 'book-1') == before

    canonical.write_text('changed evidence', encoding='utf-8')
    assert I.read_surface_seal(
        tmp_path, 'book-1') == before

    active = novel_dir / 'stage4' / 'naming' / 'name_map.json'
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text('{}', encoding='utf-8')
    assert I.read_surface_seal(
        tmp_path, 'book-1') != before


def test_surface_seal_ignores_rounds_scratch_and_untracked_approval(
        monkeypatch, tmp_path):
    """seal 忽略 rounds/scratch/一次性请求；历史审批文件按**原始字节**参与。

    2026-10-04 裁决后审批账不再解码：seal 对 `_service/approvals/<sid>.json`
    只以 `_legacy_approval_bytes` 的原始字节快照进入摘要——文件任何字节变化
    都会改变 seal，不再有「改了历史不改 seal」的第二解释面。
    """
    novel_dir = tmp_path / 'FQ_测试书_book-1'
    active = novel_dir / 'stage4' / 'design' / 'body_design.json'
    active.parent.mkdir(parents=True)
    active.write_text('{"active":1}', encoding='utf-8')
    rounds = novel_dir / 'stage4' / '_rounds' / 'plan_chapters.r1.json'
    rounds.parent.mkdir()
    rounds.write_text('{"history":1}', encoding='utf-8')
    scratch = novel_dir / 'stage4' / '_executor' / 'attempt.json'
    scratch.parent.mkdir()
    scratch.write_text('{"attempt":1}', encoding='utf-8')
    external_request = novel_dir / 'stage4' / 'plan_edit.json'
    external_request.write_text('{"request":1}', encoding='utf-8')
    chapter_edit_request = novel_dir / 'stage4' / 'blind_chapter_edit.json'
    chapter_edit_request.write_text('{"request":1}', encoding='utf-8')
    approval = tmp_path / '_service' / 'approvals' / 'book-1.json'
    approval.parent.mkdir(parents=True)
    approval.write_bytes(b'{"v":9,"history":"old"}')

    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v1')

    before = I.read_surface_seal(tmp_path, 'book-1')
    rounds.write_text('{"history":2}', encoding='utf-8')
    scratch.write_text('{"attempt":2}', encoding='utf-8')
    external_request.write_text('{"request":2}', encoding='utf-8')
    chapter_edit_request.write_text('{"request":2}', encoding='utf-8')
    assert I.read_surface_seal(tmp_path, 'book-1') == before

    approval.write_bytes(b'{"v":9,"history":"old","appended":1}')
    assert I.read_surface_seal(tmp_path, 'book-1') != before
