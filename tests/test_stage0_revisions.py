#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage0 v3：单次机械 transform 的身份、物化与幂等重跑（STR-03-S0）。

旧 revision 链 / pending / 候选 / 人工稿用例随 v3 下线；本文件钉住：
① transform_id 自证；② clean 物化可自愈、手改被拦；③ 同输入幂等、算法变则替换。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from contracts.schema.stage0 import ExternalFileRef, Stage0Source
from novel import prov
from novel.errors import ArtifactUnreadable, HumanEditProtected, SourceConflict
from novel.stages.stage0 import checks as stage0_checks
from novel.stages.stage0 import removal
from novel.stages.stage0 import revisions as rev
from novel.stages.stage0.removal import CLEAN_REL, _context, run_source


def _source(tmp_path: Path, sid: str = '900') -> Path:
    path = tmp_path / f'FQ_rev_{sid}.txt'
    path.write_text('开篇文案。\n\n1\n\n甲乙丙。\n', encoding='utf-8', newline='')
    return path


def _machine_learns_new_extra(monkeypatch, marker: str = '甲乙丙。') -> None:
    """模拟「机器剔除算法更新」：扫描器学会把 `marker` 那行认成番外。"""
    from novel.core import ingest
    real = ingest.nonbody_kind

    def fake(content: str):
        if content.strip() == marker:
            return '番外'
        return real(content)

    monkeypatch.setattr(ingest, 'nonbody_kind', fake)


def _transform(novel_dir: Path):
    return rev.read(removal.ArtifactStore(_context(novel_dir, 'stage0.remove')))


def _assert_mechanical_admission(admission) -> None:
    assert admission.status == 'passed'
    assert admission.scope == 'mechanical'
    assert admission.by == 'system:stage0-auto'
    assert admission.at


# ── ① transform 身份 ────────────────────────────────────────────────────────


def test_first_run_writes_one_machine_transform_and_materializes_clean(tmp_path):
    source = _source(tmp_path, '910')
    novel_dir, result, check = run_source(source, tmp_path / 'Output')

    transform = _transform(novel_dir)
    assert transform is not None
    _assert_mechanical_admission(transform.admission)
    assert transform.transform_id == rev.transform_digest(
        source=transform.source,
        removed=list(transform.removed),
        clean_sha1=transform.clean_sha1,
    )

    assert (novel_dir / CLEAN_REL).read_text(encoding='utf-8') == result.clean_text
    assert result.report == rev.snapshot_of(transform)
    assert result.report.revision_id == transform.transform_id
    assert result.report.revision_origin == 'machine'
    assert result.report.admission.status == 'passed'
    assert check is not None
    assert next(line for line in check.rules if line.rule == 's0_approval').status == 'ok'
    assert next(
        line for line in check.rules if line.rule == 's0_revision_chain').status == 'ok'


def test_transform_id_recomputes_from_its_preimage_and_tampering_is_refused(tmp_path):
    novel_dir, _result, _check = run_source(
        _source(tmp_path, '911'), tmp_path / 'Output')
    store = removal.ArtifactStore(_context(novel_dir, 'stage0.remove'))
    transform = rev.read(store)
    assert transform is not None
    assert transform.transform_id == rev.transform_digest(
        source=transform.source,
        removed=list(transform.removed),
        clean_sha1=transform.clean_sha1,
    )

    path = novel_dir / rev.REVISIONS_REL
    raw = json.loads(path.read_text(encoding='utf-8'))
    forged = '被改过的正文。'
    raw['clean_sha1'] = prov.content_sha1(forged.encode('utf-8'))
    raw['body_code_points'] = len(forged)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8', newline='')

    with pytest.raises(ArtifactUnreadable, match='transform_id|对不上'):
        rev.read(store)


def test_digest_golden_vector_pins_the_canonical_preimage():
    """`_digest` 的前像序列化口径是契约，不是实现细节。"""
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


def test_transform_id_ignores_time_and_code(tmp_path):
    """前像不含 at / code：同 source/removed/clean_sha1 必得同一 transform_id。"""
    novel_dir, result, _ = run_source(
        _source(tmp_path, '912'), tmp_path / 'Output')
    transform = _transform(novel_dir)
    assert transform is not None
    first = rev.make_transform(
        source=transform.source, code='code-a',
        clean_text=result.clean_text, removed=list(transform.removed),
        at='2020-01-01T00:00:00+00:00')
    later = rev.make_transform(
        source=transform.source, code='code-b',
        clean_text=result.clean_text, removed=list(transform.removed),
        at='2099-12-31T00:00:00+00:00')
    assert first.transform_id == later.transform_id == transform.transform_id


# ── ② 物化投影 ──────────────────────────────────────────────────────────────


def test_missing_materialization_heals_on_the_next_write_path(tmp_path, monkeypatch):
    """缺 clean 投影 ⇒ 下一条写路径按当前 transform 重新物化。

    v3 无 revision 链：把 live 写成「另一版正文」会被当成手改拒绝；
    可自愈的中断形状是投影缺失。
    """
    source = _source(tmp_path, '920')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    fresh_clean = (novel_dir / CLEAN_REL).read_bytes()

    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')
    updated_clean = (novel_dir / CLEAN_REL).read_bytes()
    assert updated_clean != fresh_clean

    (novel_dir / CLEAN_REL).unlink()
    run_source(source, tmp_path / 'Output')
    assert (novel_dir / CLEAN_REL).read_bytes() == updated_clean


def test_wrong_live_clean_is_detected_by_check(tmp_path, monkeypatch):
    """live clean 与当前 transform 不一致 ⇒ s0_revision_chain 命中。"""
    source = _source(tmp_path, '922')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    stale_clean = (novel_dir / CLEAN_REL).read_bytes()
    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')
    (novel_dir / CLEAN_REL).write_bytes(stale_clean)
    drifted = stage0_checks.run_check_only(_context(novel_dir, 'stage0.remove'))
    chain = next(line for line in drifted.rules if line.rule == 's0_revision_chain')
    assert chain.status == 'hit'


def test_hand_edited_clean_is_refused_by_ensure_materialized(tmp_path):
    source = _source(tmp_path, '921')
    novel_dir, _result, _ = run_source(source, tmp_path / 'Output')
    store = removal.ArtifactStore(_context(novel_dir, 'stage0.remove'))
    (novel_dir / CLEAN_REL).write_text('被人改过的正文。\n', encoding='utf-8', newline='')
    with pytest.raises(HumanEditProtected, match='人手改了正文|半成品损坏'):
        rev.ensure_materialized(store)


# ── ③ 幂等与替换 ────────────────────────────────────────────────────────────


def test_same_machine_result_changes_nothing_on_disk(tmp_path):
    source = _source(tmp_path, '930')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    before = {rel: (novel_dir / rel).read_bytes()
              for rel in (CLEAN_REL, rev.REVISIONS_REL)}

    run_source(source, tmp_path / 'Output')

    assert {rel: (novel_dir / rel).read_bytes() for rel in before} == before


def test_machine_rerun_replaces_transform_when_algorithm_changes(
        tmp_path, monkeypatch):
    """算法结论变化 ⇒ 整份 transform 替换，仍带机械 admission。"""
    source = _source(tmp_path, '931')
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    _machine_learns_new_extra(monkeypatch)

    _dir, second, _check = run_source(source, tmp_path / 'Output')

    transform = _transform(novel_dir)
    assert transform is not None
    assert transform.transform_id != first.report.revision_id
    _assert_mechanical_admission(transform.admission)
    assert second.report.revision_origin == 'machine'
    assert second.clean_text != first.clean_text
    from novel.stages.stage0.eligibility import is_stage0_executable
    assert is_stage0_executable(second.report)


def test_a_changed_source_is_refused_before_anything_is_written(tmp_path):
    source = _source(tmp_path, '934')
    novel_dir, _first, _ = run_source(source, tmp_path / 'Output')
    before = (novel_dir / rev.REVISIONS_REL).read_bytes()
    source.write_text('另一本书的正文。\n\n1\n\n甲。\n', encoding='utf-8', newline='')

    # ensure_materialized 先核对记录路径上的原文字节；漂移在 SourceConflict 之前关闭。
    with pytest.raises((SourceConflict, ArtifactUnreadable), match='stage0_source_changed|原始字节身份漂移'):
        run_source(source, tmp_path / 'Output')

    assert (novel_dir / rev.REVISIONS_REL).read_bytes() == before


def test_make_transform_builds_stage0_source_fields():
    text = '正文。\n'
    raw = text.encode('utf-8')
    source = Stage0Source(
        source_ref=ExternalFileRef(kind='external_file', absolute_path='/tmp/x.txt'),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        encoding='utf-8',
        normalized_source_sha1=prov.content_sha1(raw),
        total_code_points=len(text),
    )
    transform = rev.make_transform(
        source=source, code='test', clean_text=text, removed=[])
    assert transform.body_code_points == len(text)
    assert transform.admission.scope == 'mechanical'
