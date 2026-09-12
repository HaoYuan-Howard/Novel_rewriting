"""旧 Blueprint 版本在真实生产消费边界关闭；历史根只配旧代码回滚。

合成 Stage2 与 Stage4A 由现役测试 producer 生成；只改变测试信封版本，
检验读取、状态、审阅、队列及交付均不会因为已有下游或旧决定放行。
本文件不读真实语料，也不调用真实模型。
"""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE4_BLIND_CANDIDATE
from contracts.schema import ArtifactVersionError
from contracts.schema.orchestration import ArtifactState
from contracts.schema.stage2 import ChapterBlueprints
from contracts.schema.stage1 import MainText
from novel.api.app import create_app
from novel.errors import ArtifactUnreadable, NovelError
from novel.orchestrator import approvals, delivery, queue, state
from novel.stages.stage7 import assemble
from novel.store import ArtifactStore
from tests.test_stage4_v3_integration import CODE, _ctx, designed  # noqa: F401


@pytest.fixture(params=[6, 7, 8, 10], ids=['retired-v6', 'retired-v7', 'retired-v8', 'future-v10'])
def incompatible(designed, request):
    book, _blueprints, design = designed
    ctx, _client, transport, _candidate, _truth, _mapping = design
    store = ArtifactStore(ctx)
    path = book / STAGE2_CHAPTER_BLUEPRINTS
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['_schema_version'] == 9
    raw['_schema_version'] = request.param
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8', newline='')
    before = {str(p.relative_to(book)): p.read_bytes()
              for p in book.rglob('*') if p.is_file()}
    return SimpleNamespace(
        book=book, base=book.parent, sid=ctx.sid, ctx=ctx, store=store,
        transport=transport, calls=len(transport.calls), before=before,
    )


def _unchanged(box):
    assert {str(p.relative_to(box.book)): p.read_bytes()
            for p in box.book.rglob('*') if p.is_file()} == box.before
    assert len(box.transport.calls) == box.calls


def test_current_store_rejects_each_noncurrent_version(incompatible):
    box = incompatible
    with pytest.raises(ArtifactUnreadable) as caught:
        box.store.read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)
    assert isinstance(caught.value.__cause__, ArtifactVersionError)
    _unchanged(box)


def test_existing_design_does_not_promote_an_unreadable_blueprint(incompatible):
    box = incompatible
    assert box.store.exists(STAGE4_BLIND_CANDIDATE)
    overview = state.overview_at(box.book, CODE)
    stage2 = next(row for row in overview.stages if row.stage == 'stage2')
    blueprint = next(row for row in stage2.artifacts if row.rel == STAGE2_CHAPTER_BLUEPRINTS)
    assert blueprint.exists and not blueprint.readable
    assert blueprint.condition == 'unreadable'
    assert stage2.done is not True
    assert not state.stage2_blueprint_extraction_current(overview)
    assert state.resume_verdict(overview, 'stage2.blueprint')[0] != state.RESUME_SKIP
    with pytest.raises(ValidationError):
        ArtifactState.model_validate({
            **blueprint.model_dump(), 'legacy_frozen': True, 'legacy_contract': 'v6',
        })
    _unchanged(box)


def test_old_version_keeps_reading_source_but_cannot_publish_review(incompatible):
    box = incompatible
    with TestClient(create_app(base=box.base)) as client:
        reading = client.get(f'/api/novels/{box.sid}/reading/1')
        assert reading.status_code == 200, reading.text
        payload = reading.json()
        assert payload['blueprint'] is None
        assert payload['blueprint_state']['condition'] == 'unreadable'
        assert payload['_provenance'] is None
        assert payload['text_chars'] == len(payload['text']) > 0
        assert payload['text'] == box.store.read_own(MainText, STAGE1_MAIN_TEXT)[0].root[0].text
        response = client.get(f'/api/novels/{box.sid}/approvals/design/workpack')
        assert response.status_code >= 400, response.text
    _unchanged(box)


def test_queue_does_not_skip_old_blueprint_or_publish_downstream_steps(incompatible, monkeypatch):
    box = incompatible
    # 只限制本测试的节点分母，不替换 producer、freshness 或审批判定。
    monkeypatch.setattr(queue, 'nodes_up_to', lambda _until: ['stage2.blueprint', 'stage4.design'])
    todo, blocked = queue.plan_book_from_overview(
        box.base, box.sid, state.overview_at(box.book, CODE),
        until_stage='stage4', follow_check=False)
    assert not todo
    assert blocked and all(not row.steps for row in blocked)
    _unchanged(box)


def test_service_cli_and_download_share_closed_stage2_guard(incompatible, monkeypatch):
    box = incompatible
    ctx = _ctx(box.book, 'stage7.assemble', paid=False)
    for guard in (
        lambda: approvals.require_stage2_review(box.base, box.sid, 'stage7.assemble'),
        lambda: assemble.require_stage2_delivery_approved(ctx),
        lambda: assemble.read_current_book(ctx),
    ):
        with pytest.raises(NovelError):
            guard()
    cli = importlib.import_module('novel.cli.main')
    monkeypatch.setattr(cli, 'RunContext', SimpleNamespace(open=lambda *_a, **_kw: ctx))
    dispatched = []
    monkeypatch.setattr(cli, '_dispatch', lambda *args: dispatched.append(args) or 0)
    code = cli.main(['run', 'stage7.assemble', box.sid, '--base', str(box.base)])
    assert code != 0 and not dispatched
    _unchanged(box)


def test_final_delivery_cannot_use_existing_design_to_bypass_current_decoder(incompatible, monkeypatch):
    box = incompatible
    monkeypatch.setattr(delivery.prov, 'code_version', lambda: CODE)
    outcome = delivery.assess(box.base, box.sid, start_code_version=CODE)
    assert outcome.state == 'stopped'
    assert 'ArtifactUnreadable' in outcome.reason
    _unchanged(box)
