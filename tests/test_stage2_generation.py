#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 生成侧的零成本闭环测试。"""
from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from novel.errors import GateBlocked
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import blueprints, global_grounding, main_text


def _response_global() -> dict:
    data = copy.deepcopy(global_grounding())
    data.pop('_provenance')
    return data


def _response_blueprints() -> dict[int, dict]:
    out: dict[int, dict] = {}
    for blueprint in copy.deepcopy(blueprints())['blueprints']:
        for beat in blueprint['beats']:
            beat.pop('present_char_ids')
            for hinge in beat['hinges']:
                hinge.pop('text')
        for narration in blueprint['narration']:
            narration.pop('narrator_char_id')
            narration.pop('focalizer_char_id')
        out[blueprint['chapter_ordinal']] = blueprint
    return out


def _ctx(book: Path, node: str, *, paid: bool = True) -> RunContext:
    return RunContext(
        novel_dir=book,
        node=node,
        paid=paid,
        code='test-stage2-generation',
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _client(ctx: RunContext, responder) -> tuple[LlmClient, FakeTransport]:
    transport = FakeTransport(responder=responder)
    return (
        LlmClient(
            ctx,
            transport,
            cache=ResponseCache(),
            model='test-model',
            max_retries=0,
            concurrency=3,
        ),
        transport,
    )


def _ok(data: dict) -> ChatResponse:
    return ChatResponse(
        content=json.dumps(data, ensure_ascii=False),
        finish_reason='stop',
        usage={
            'prompt_tokens': 10,
            'completion_tokens': 20,
            'reasoning_tokens': 5,
        },
        model='test-model',
    )


def test_stage2_generation_reaches_a_reloadable_jointly_checked_artifact():
    root = Path(tempfile.mkdtemp())
    book = root / '生成测试_123'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))

        global_artifact = extract.run_global(global_ctx, global_client)
        assert isinstance(global_artifact, GlobalGrounding)
        assert len(global_transport.calls) == 1
        assert ArtifactStore(global_ctx).exists(extract.GLOBAL_REL)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            ordinals = re.findall(r'阅读序第(\d+)章', req.user)
            assert ordinals, '单章 user prompt 没有任务范围'
            ordinal = int(ordinals[-1])
            return _ok({'blueprints': [by_ordinal[ordinal]]})

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, blueprint_transport = _client(
            blueprint_ctx, respond)
        report = extract.run_blueprint(blueprint_ctx, blueprint_client)

        assert report.passed
        assert len(blueprint_transport.calls) == len(main_text())
        stored = ArtifactStore(blueprint_ctx).read(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        assert [bp.chapter_ordinal for bp in stored.blueprints] == [1, 2, 3]
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 3
        assert not ArtifactStore(blueprint_ctx).exists(extract._INVALID)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_global_invalid_can_be_revalidated_without_another_llm_call():
    root = Path(tempfile.mkdtemp())
    book = root / '提升测试_456'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        response = _response_global()
        original = response['entities']['characters']['char_002']['canonical']
        response['entities']['characters']['char_002']['canonical'] = \
            response['entities']['characters']['char_001']['canonical']
        client, transport = _client(ctx, lambda _req, _index: _ok(response))

        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        assert len(transport.calls) == 1

        invalid = book / extract._GLOBAL_INVALID
        raw = json.loads(invalid.read_text(encoding='utf-8'))
        raw['entities']['characters']['char_002']['canonical'] = original
        invalid.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

        promoted, report = extract.run_revalidate(
            _ctx(book, 'stage2.global', paid=False))
        assert promoted
        assert len(transport.calls) == 1
        assert (book / extract.GLOBAL_REL).is_file()
        assert not invalid.exists()
        stored = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
        assert stored.provenance.revalidated is True
        assert next(r for r in report.rules
                    if r.rule == 'canonical_unique').status == 'ok'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_global_migrate_promotes_deterministically_pruned_invalid():
    root = Path(tempfile.mkdtemp())
    book = root / '全局迁移测试_457'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        response = _response_global()
        secret = response['canon']['secrets'][0]
        reveal = secret['reader_reveal_events'][0]['chapter']
        secret['setup_chapters'] = [reveal]
        client, transport = _client(ctx, lambda _req, _index: _ok(response))

        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        assert len(transport.calls) == 1
        assert (book / extract._GLOBAL_INVALID).is_file()

        report = extract.run_migrate(
            _ctx(book, 'stage2.global', paid=False))

        assert report.passed
        assert len(transport.calls) == 1
        assert (book / extract.GLOBAL_REL).is_file()
        assert not (book / extract._GLOBAL_INVALID).exists()
        stored = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
        assert stored.canon.secrets[0].setup_chapters == []
        assert stored.provenance.setup_chapters_pruned == []
        assert stored.provenance.partial_rewrite == '--migrate'
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_blueprint_migrate_rebuilds_code_derived_character_refs():
    root = Path(tempfile.mkdtemp())
    book = root / '蓝图迁移测试_458'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
            return _ok({'blueprints': [by_ordinal[ordinal]]})

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, blueprint_transport = _client(
            blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed
        calls_before = len(blueprint_transport.calls)

        path = book / extract.BLUEPRINT_REL
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['blueprints'][0]['beats'][0]['present_char_ids'] = ['char_999']
        path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

        report = extract.run_migrate(
            _ctx(book, 'stage2.blueprint', paid=False))

        assert report.passed
        assert len(blueprint_transport.calls) == calls_before
        stored = ArtifactStore(blueprint_ctx).read(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        assert 'char_999' not in stored.blueprints[0].beats[0].present_char_ids
        assert stored.provenance.partial_rewrite == '--migrate'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_targeted_regeneration_refreshes_one_chapter_and_merges_book():
    root = Path(tempfile.mkdtemp())
    book = root / '定向重生成测试_459'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
            return _ok({'blueprints': [by_ordinal[ordinal]]})

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, _blueprint_transport = _client(
            blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed

        store = ArtifactStore(blueprint_ctx)
        previous, raw = store.read_own(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        broken = copy.deepcopy(raw)
        hinge = broken['blueprints'][0]['beats'][0]['hinges'][0]
        hinge['starts_with'] = '并不存在的原文锚点'
        hinge['ends_with'] = '并不存在的原文锚点'
        hinge['text'] = None
        broken['_provenance']['verbatim_failures'] = [
            ['b01_01', 'hinge', '锚点不在本章原文']]
        store.write_invalid(
            extract.BLUEPRINT_REL,
            ChapterBlueprints.model_validate(broken),
            gates_skipped=[],
        )

        repair_ctx = _ctx(book, 'stage2.blueprint')
        repair_ctx.cache = CachePolicy.refresh()
        repair_client, repair_transport = _client(repair_ctx, respond)
        report = extract.run_blueprint(
            repair_ctx, repair_client, chapters=(1,))

        assert report.passed
        assert len(repair_transport.calls) == 1
        assert '上一版结构基线' in repair_transport.calls[0].user
        assert '上一版必须修正的问题' in repair_transport.calls[0].user
        assert 'b01_01' in repair_transport.calls[0].user
        assert len(_blueprint_transport.calls) == len(main_text())
        stored = store.read(ChapterBlueprints, extract.BLUEPRINT_REL)
        assert [bp.chapter_ordinal for bp in stored.blueprints] == [1, 2, 3]
        assert stored.provenance.partial_regen_chapters == [1]
        assert stored.provenance.repairs is not None
        assert stored.provenance.repairs[-1].cache_policy == 'refresh-write'
        assert stored.provenance.repairs[-1].failure_items == [
            'hinge_anchor_void', 'verbatim_failed']
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 4
        assert not (book / extract._INVALID).exists()
        assert previous.provenance.usage is not None
    finally:
        shutil.rmtree(root, ignore_errors=True)
