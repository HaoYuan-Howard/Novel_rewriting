#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 blueprint 的候选评估、单次终态与交付终验边界。"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

from pathlib import Path

import pytest


from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    ChapterBlueprints, ChapterBlueprintsResponseV31, GlobalGrounding,
)
from novel import paths, prov
from novel.errors import GateBlocked, ProvenanceRefused
from novel.llm import stage2_runs
from novel.llm.transport import ChatRequest, ChatResponse
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import (
    _accept_fixture_global, _client, _ctx, _ok, _respond_blueprints, _response_blueprints,
    _response_global,
)


@pytest.fixture(autouse=True)
def _workflow_epoch(tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)


@pytest.fixture(autouse=True)
def _stable_contract_and_code(monkeypatch):
    monkeypatch.setenv(
        'NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage2-blueprint-agent')


def _land_global(book: Path) -> None:
    ctx = _ctx(book, 'stage2.global')
    ArtifactStore(ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    client, _transport = _client(
        ctx, lambda _request, _index: _ok(_response_global()))
    extract.run_global(ctx, client)
    _accept_fixture_global(ctx)


def _blueprint_client(ctx, by_ordinal):
    def respond(request: ChatRequest, _index: int) -> ChatResponse:
        return _respond_blueprints(request, by_ordinal)

    return _client(ctx, respond)


def test_chapter_request_surface_supports_one_private_client_per_chapter():
    """producer 只按 progress 路由；节点层可让每章持有独立 session/driver。"""
    by_ordinal = _response_blueprints()

    class PrivateClient:
        def __init__(self, ordinal: int) -> None:
            self.ordinal = ordinal
            self.calls = 0

        def ask_json(self, _system, _user, **kwargs):
            assert kwargs['progress'].chapter == self.ordinal
            self.calls += 1
            return ChapterBlueprintsResponseV31.model_validate({
                'blueprints': [by_ordinal[self.ordinal]],
            })

    private = {ordinal: PrivateClient(ordinal) for ordinal in by_ordinal}

    class Pool:
        concurrency = len(private)

        @staticmethod
        def ask_json(system, user, **kwargs):
            return private[kwargs['progress'].chapter].ask_json(
                system, user, **kwargs)

    requests = [
        (ordinal, 'SYSTEM', f'USER-{ordinal}')
        for ordinal in sorted(by_ordinal)
    ]
    raw, violations, errors = extract._run_chapter_requests(
        type('Ctx', (), {
            'node': 'stage2.blueprint',
            'observe': staticmethod(lambda *_a, **_kw: None),
        })(),
        Pool(), requests, model_cls=ChapterBlueprintsResponseV31,
        checkpoints=False,
    )

    assert [row['chapter_ordinal'] for row in raw] == sorted(by_ordinal)
    assert violations == [] and errors == []
    assert {ordinal: client.calls for ordinal, client in private.items()} == {
        ordinal: 1 for ordinal in by_ordinal
    }


def test_blueprint_hard_candidate_stays_in_memory_until_terminal(tmp_path):
    """联合 HARD 先成为结构化反馈；只有显式 finalize 才落一份 INVALID。"""
    book = tmp_path / '蓝图候选内存拒稿_501'
    book.mkdir()
    _land_global(book)
    by_ordinal = _response_blueprints()
    broken = min(by_ordinal)
    for beat in by_ordinal[broken]['beats']:
        beat['hinges'] = []

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _blueprint_client(ctx, by_ordinal)
    batch = extract.request_blueprints(ctx, client)
    first = extract.evaluate_blueprint_candidate(ctx, client, batch)
    second = extract.evaluate_blueprint_candidate(ctx, client, batch)

    assert not first.passed
    assert first.hard_hits == second.hard_hits
    assert first.feedback_rows(broken)
    assert all({'rule', 'item', 'requirement', 'chapters', 'findings'}
               <= set(row) for row in first.feedback_rows())
    assert len(transport.calls) == len(main_text())
    assert not (book / extract.BLUEPRINT_REL).exists()
    assert not (book / extract._INVALID).exists()
    assert not list((book / 'stage2/_rounds').glob(
        'chapter_blueprints*.json'))
    assert all(paths.stage2_blueprint_checkpoint_path(
        book, ctx.run_id, 'blueprint', chapter).is_file()
        for chapter in by_ordinal), \
        '候选边界继续复用受管 _service/runs 逐章 checkpoint，不另造恢复账'

    with pytest.raises(GateBlocked):
        extract.finalize_blueprint_candidate(ctx, client, batch, second)

    invalid = ArtifactStore(ctx).read(ChapterBlueprints, extract._INVALID)
    assert invalid.provenance.usage is not None
    assert invalid.provenance.usage.calls_made == len(main_text())
    assert not (book / extract.BLUEPRINT_REL).exists()
    assert not list((book / 'stage2/_rounds').glob(
        'chapter_blueprints.INVALID.r*.json'))


def test_blueprint_candidate_finalizes_through_existing_commit_journal(tmp_path):
    """通过候选只在终态落正式稿，并沿用 checkpoint/adoption/committed 闭环。"""
    book = tmp_path / '蓝图候选正式提交_502'
    book.mkdir()
    _land_global(book)
    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _blueprint_client(ctx, _response_blueprints())
    batch = extract.request_blueprints(ctx, client)

    evaluation = extract.evaluate_blueprint_candidate(ctx, client, batch)
    assert evaluation.passed
    assert len(evaluation.accepted_attempts) == len(main_text())
    assert all(attempt for _purpose, attempt in evaluation.accepted_attempts)
    assert not (book / extract.BLUEPRINT_REL).exists()

    report = extract.finalize_blueprint_candidate(
        ctx, client, batch, evaluation)

    assert report.passed
    assert len(transport.calls) == len(main_text())
    landed = ArtifactStore(ctx).read(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    assert landed == evaluation.candidate
    assert landed.provenance.usage is not None
    assert landed.provenance.usage.calls_made == len(main_text())
    committed = stage2_runs.committed(ctx)
    assert committed is not None
    assert committed['attempts'] == sorted(
        attempt for _purpose, attempt in evaluation.accepted_attempts)
    assert extract.verify_blueprint_delivery(ctx, landed).passed


def test_blueprint_full_rebuild_can_replace_stale_formal(tmp_path):
    """上游称名投影变化后，完整 producer 必须能覆盖自己的 stale 旧稿。"""
    book = tmp_path / '蓝图过期正式稿重建_505'
    book.mkdir()
    _land_global(book)

    initial_ctx = _ctx(book, 'stage2.blueprint')
    initial_client, _initial_transport = _blueprint_client(
        initial_ctx, _response_blueprints())
    initial_batch = extract.request_blueprints(initial_ctx, initial_client)
    initial_evaluation = extract.evaluate_blueprint_candidate(
        initial_ctx, initial_client, initial_batch)
    extract.finalize_blueprint_candidate(
        initial_ctx, initial_client, initial_batch, initial_evaluation)

    global_ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(global_ctx)
    from tests.test_stage2_generation import _edit_static_character
    _edit_static_character(book, 'char_001', canonical='管家')
    extract.run_renormalize(_ctx(book, 'stage2.global', paid=False))
    assert ArtifactStore(_ctx(book, 'stage2.blueprint')).freshness(
        extract.BLUEPRINT_REL).state == 'stale'
    _accept_fixture_global(global_ctx)

    rerun_ctx = _ctx(book, 'stage2.blueprint')
    rerun_client, rerun_transport = _blueprint_client(
        rerun_ctx, _response_blueprints())
    batch = extract.request_blueprints(rerun_ctx, rerun_client)
    evaluation = extract.evaluate_blueprint_candidate(
        rerun_ctx, rerun_client, batch)

    report = extract.finalize_blueprint_candidate(
        rerun_ctx, rerun_client, batch, evaluation)

    assert report.passed
    assert len(rerun_transport.calls) == len(main_text())
    assert ArtifactStore(rerun_ctx).freshness(
        extract.BLUEPRINT_REL).state == 'fresh'
    assert list((book / 'stage2' / '_rounds').glob(
        'chapter_blueprints.r*.json'))


def test_blueprint_candidate_rejects_naming_projection_drift_after_wire(tmp_path):
    """P1 prompt 的响应不得在并发 Global 称名变成 P2 后按 P2 编译/记账。"""
    book = tmp_path / '蓝图请求后称名漂移_504'
    book.mkdir()
    _land_global(book)
    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _blueprint_client(ctx, _response_blueprints())
    batch = extract.request_blueprints(ctx, client)
    request_projection = batch['character_projection']

    global_ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(global_ctx)
    from tests.test_stage2_generation import _edit_static_character
    # 使用第三章真实字面，由人物 producer 发布新的称名投影。
    _edit_static_character(book, 'char_001', canonical='管家')
    _projection, current_projection = \
        extract._require_blueprint_character_projection(store)
    assert current_projection != request_projection, '反例没有真正改变称名投影'

    with pytest.raises(ProvenanceRefused, match='请求后称名投影发生漂移'):
        extract.evaluate_blueprint_candidate(ctx, client, batch)
    assert len(transport.calls) == len(main_text()), '拒绝发生在已有 wire 之后，不追加请求'
    assert not (book / extract.BLUEPRINT_REL).exists()
    assert not (book / extract._INVALID).exists()


def test_blueprint_code_drift_is_rejected_before_settle_or_write(
        tmp_path, monkeypatch):
    """并行章节 session 运行期间代码漂移，不得先写 formal 再由会话尾账报错。"""
    book = tmp_path / '蓝图候选代码漂移_503'
    book.mkdir()
    _land_global(book)
    ctx = _ctx(book, 'stage2.blueprint')
    client, _transport = _blueprint_client(ctx, _response_blueprints())
    batch = extract.request_blueprints(ctx, client)
    evaluation = extract.evaluate_blueprint_candidate(ctx, client, batch)
    original = extract.prov.code_version

    monkeypatch.setattr(
        extract.prov, 'code_version', lambda: 'changed-during-chapter-sessions')
    with pytest.raises(ProvenanceRefused, match='代码版本.*漂移'):
        extract.finalize_blueprint_candidate(
            ctx, client, batch, evaluation)

    assert not (book / extract.BLUEPRINT_REL).exists()
    assert not (book / extract._INVALID).exists()
    assert not list((book / 'stage2/_rounds').glob(
        'chapter_blueprints*.json'))

    monkeypatch.setattr(extract.prov, 'code_version', original)
    report = extract.finalize_blueprint_candidate(
        ctx, client, batch, evaluation)
    assert report.passed
    landed = ArtifactStore(ctx).read(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    assert landed.provenance.usage is not None
    assert landed.provenance.usage.calls_made == len(main_text())
