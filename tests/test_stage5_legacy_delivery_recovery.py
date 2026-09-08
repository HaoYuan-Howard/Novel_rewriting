"""真实 legacy writer 回答、正式章与采纳账之间的零模型补尾。"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_WRITER_CONTEXT
from contracts.schema import encode_artifact
from contracts.schema.stage4 import CtxProvenance, WriterContext
from novel.errors import ProvenanceRefused, TerminalState
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.transport import ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage5 import write
from novel.store import ArtifactStore
from novel.work_units import budget_for, require_workflow
from tests import factories


def _ctx(base, *, refresh=False):
    require_workflow(base)
    book = base / '提交尾窗_855'
    clean = book / 'stage0' / 'clean_text.txt'
    if not clean.exists():
        clean.parent.mkdir(parents=True)
        clean.write_text('测试原始输入。', encoding='utf-8')
    return RunContext(
        novel_dir=book, node='stage5.write', paid=True, code='test-tail',
        cache=CachePolicy.refresh() if refresh else CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)), observer=lambda _event: None)


def _client(ctx, text='完整测试章节。'):
    wire = FakeTransport.always(ChatResponse(
        content=json.dumps({'segments': [{'slot': 's01', 'text': text}]}, ensure_ascii=False),
        finish_reason='stop', model='test-model',
        usage={'prompt_tokens': 10, 'completion_tokens': 6}))
    return LlmClient(ctx, wire, model='test-model', max_retries=0,
                     cache=ResponseCache(ctx.novel_dir.parent / 'cache'),
                     budget_checker=lambda *_a, **_kw: None), wire


def _rig(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    seed = factories.stage5_book()
    entry = copy.deepcopy(seed.chapters[1])
    entry['budget_chars'] = 1000
    entry['scenes'][0]['budget_chars'] = 1000
    store = ArtifactStore(ctx)
    artifact = WriterContext(
        chapters=[entry], _provenance=ctx.free_provenance(CtxProvenance))
    payload = artifact.model_dump(by_alias=True, mode='json')
    payload['_provenance'] = artifact.provenance.to_disk()
    path = store.path(STAGE4_WRITER_CONTEXT)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(encode_artifact(STAGE4_WRITER_CONTEXT, payload),
                               ensure_ascii=False), encoding='utf-8')
    rows = [row.model_dump(by_alias=True, mode='json') for row in artifact.chapters]
    book = write.M.Book(title=seed.title, ctx={'chapters': rows}, measurable=seed.measurable,
                        source_text=seed.source_text, secrets=seed.secrets, characters=seed.characters)

    def load(_ctx, current_store):
        current_store.read(WriterContext, STAGE4_WRITER_CONTEXT)
        return book

    monkeypatch.setattr(write.review, 'load_book', load)
    monkeypatch.setattr(write, '_judge', lambda *_a, **_kw: ([], [], []))
    monkeypatch.setattr(write, '_report', lambda *_a, **_kw: SimpleNamespace(passed=True))
    return ctx, book


def test_formal_before_adoption_recovers_default_incremental_and_next_rewrite(
        tmp_path, monkeypatch):
    ctx, _book = _rig(tmp_path, monkeypatch)
    client, wire = _client(ctx)
    original = write._record_legacy_chapter_delivery

    def crash(*_args, **_kwargs):
        raise OSError('正式章已写完，采纳账尚未写')

    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', crash)
    with pytest.raises(OSError, match='采纳账尚未写'):
        write.run_write(ctx, client)
    formal = ctx.novel_dir / write._rel(1)
    frozen = formal.read_bytes()
    assert len(wire.calls) == 1
    assert budget_for(ctx, scope='chapter:1').latest_pending_response() is not None

    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', original)
    resumed = _ctx(tmp_path)
    resumed_client, resumed_wire = _client(resumed)
    with pytest.raises(TerminalState, match='没有要写的章'):
        write.run_write(resumed, resumed_client)
    assert resumed_wire.calls == [] and formal.read_bytes() == frozen
    budget = budget_for(resumed, scope='chapter:1')
    assert budget.used == 1 and budget.latest_pending_response() is None
    rows = [json.loads(line) for line in ledger_path(tmp_path).read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 1 and rows[0]['run_id'] == ctx.run_id

    revised = _ctx(tmp_path, refresh=True)
    revised_client, revised_wire = _client(revised, text='完成明确返工后的新章节。')
    assert write.run_write(revised, revised_client, chapters='1').passed
    assert len(revised_wire.calls) == 1 and budget.used == 2
    assert formal.read_bytes() != frozen


def test_formal_tail_refuses_third_party_bytes_without_new_wire(tmp_path, monkeypatch):
    ctx, _book = _rig(tmp_path, monkeypatch)
    client, _wire = _client(ctx)

    def crash(*_args, **_kwargs):
        raise OSError('提交后硬杀')

    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', crash)
    with pytest.raises(OSError, match='硬杀'):
        write.run_write(ctx, client)
    formal = ctx.novel_dir / write._rel(1)
    formal.write_bytes(formal.read_bytes() + b'\n')
    resumed = _ctx(tmp_path)
    resumed_client, wire = _client(resumed)
    with pytest.raises(ProvenanceRefused, match='第三方状态'):
        write.run_write(resumed, resumed_client)
    assert wire.calls == [] and budget_for(resumed, scope='chapter:1').used == 1
