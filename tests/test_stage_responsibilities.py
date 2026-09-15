"""补齐 Stage5～Stage7 真实生成、消费与失效/恢复；源书完全合成。

上游经原 producer 生成一次后只保存不可变字节；每例恢复自己的独立盘面。
被测视图、校验、交付读取及来源判断不替换，也不复用准备期的通过结论。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel import paths, prov
from novel.core.measure import measure
from novel.errors import ProvenanceRefused, TerminalState
from novel.stages.stage7 import assemble
from novel.views.stage6 import stage6_view
from tests.frozen_tree import FrozenTree
from tests.test_plot_pipeline import complete_stage5, complete_stage6, complete_stage7, grounded_global, stage5_writer
from tests.test_plot_design import produce_through_first_chapter
from tests.test_stage2_generation import _ctx


@pytest.fixture(scope='module')
def baseline(tmp_path_factory, request):
    root = tmp_path_factory.mktemp('stage-consumers')
    needed = {marker.args[0] for item in request.session.items if item.path == Path(__file__)
              for marker in item.iter_markers('stage_scope')}
    last = max(needed or {'stage7'})
    inputs = {}
    with pytest.MonkeyPatch.context() as patch:
        from tests.conftest import _TEST_CODE_VERSION
        patch.setattr(prov, 'code_version', lambda: _TEST_CODE_VERSION)
        book = produce_through_first_chapter(root, patch, distributed=True, grounding=grounded_global(),
                                             real_code_identity=True)
        for stage, finish in [('stage5', complete_stage5), ('stage6', complete_stage6), ('stage7', complete_stage7)]:
            inputs[stage] = FrozenTree.capture(root)
            finish(book)
            if stage == last:
                break
        return FrozenTree.capture(root), book.relative_to(root), inputs


@pytest.fixture
def produced_book(tmp_path, baseline):
    tree, relative, _inputs = baseline
    tree.restore(tmp_path)
    return tmp_path / relative


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


@pytest.mark.stage_scope('stage5')
def test_stage5_real_delivery_tail_refuses_foreign_bytes_and_recovers_without_model(tmp_path, baseline, monkeypatch):
    from novel.stages.stage5 import write, review
    from novel.work_units import budget_for
    from tests.test_stage2_generation import _client
    _tree, relative, inputs = baseline
    inputs['stage5'].restore(tmp_path)
    book = tmp_path / relative
    ctx, client, wire, requested = stage5_writer(book)
    original_record = write._record_legacy_chapter_delivery

    def interrupted(*args, **kwargs):
        if kwargs['ordinal'] == 3:
            raise OSError('合成提交故障：正式章已写，采纳账未闭合')
        return original_record(*args, **kwargs)

    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', interrupted)
    with pytest.raises(OSError, match='合成提交故障'):
        write.run_write(ctx, client, chapters='2,3')
    assert sorted(requested) == [2, 3] and len(wire.calls) == 2
    formal = book / 'stage5' / 'ch03.json'
    frozen = formal.read_bytes()
    budget = budget_for(ctx, scope='chapter:3')
    assert budget.latest_pending_response() is not None
    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', original_record)

    def forbid_wire(*_args):
        pytest.fail('免费恢复不应再次请求模型')

    formal.write_bytes(frozen + b'\n')
    refused_ctx = _ctx(book, 'stage5.write')
    refused_client, refused_wire = _client(refused_ctx, forbid_wire)
    with pytest.raises(ProvenanceRefused, match='第三方状态'):
        write.run_write(refused_ctx, refused_client)
    assert refused_wire.calls == [] and budget.latest_pending_response() is not None

    formal.write_bytes(frozen)
    ledger = paths.service_dir(book.parent) / 'ledger.jsonl'
    ledger_before = ledger.read_bytes()
    resumed_ctx = _ctx(book, 'stage5.write')
    resumed_client, resumed_wire = _client(resumed_ctx, forbid_wire)
    with pytest.raises(TerminalState, match='没有要写的章'):
        write.run_write(resumed_ctx, resumed_client)
    assert resumed_wire.calls == [] and formal.read_bytes() == frozen
    assert budget.latest_pending_response() is None and budget.used == 1
    assert ledger.read_bytes() == ledger_before
    assert review.run_review(_ctx(book, 'stage5.review', paid=False))[1].passed


@pytest.mark.stage_scope('stage6')
def test_stage6_real_candidates_and_approval_survive_view_reload_without_writes(produced_book):
    book = produced_book
    before = _files(book.parent)
    view = stage6_view(book.parent, _ctx(book, 'stage6.hook', paid=False).sid)
    assert [row.index for row in view.candidates] == [1, 2, 3]
    assert all(row.text and row.chars == measure(row.text).chars for row in view.candidates)
    assert view.approved is not None
    assert [row.approach for row in view.candidates if row.approved] == [view.approved.approach]
    # 判官退役（2026-09-15）后候选行只剩机械身份：subject_sha256 逐条绑定字节。
    assert all(len(row.subject_sha256) == 64 for row in view.candidates)
    again = stage6_view(book.parent, _ctx(book, 'stage6.hook', paid=False).sid)
    assert again.model_dump() == view.model_dump()
    assert _files(book.parent) == before


@pytest.mark.stage_scope('stage7')
def test_stage7_upstream_tampering_revokes_delivery_and_exact_restore_recovers_without_model(produced_book):
    book = produced_book
    ctx = _ctx(book, 'stage7.assemble', paid=False)
    expected = assemble.read_current_book(ctx)
    ledger = paths.service_dir(book.parent) / 'ledger.jsonl'
    ledger_before = ledger.read_bytes()
    chapter = book / 'stage5' / 'ch01.json'
    original = chapter.read_bytes()
    raw = json.loads(original)
    # 改正文载体的原始字节，保留合法 JSON 以触发实际来源/交付失效。
    chapter.write_bytes(original.replace('李宝珠'.encode(), '王宝珠'.encode()))
    assert chapter.read_bytes() != original and json.loads(chapter.read_bytes()) != raw
    with pytest.raises(TerminalState):
        assemble.read_current_book(_ctx(book, 'stage7.assemble', paid=False))
    chapter.write_bytes(original)
    assert assemble.read_current_book(_ctx(book, 'stage7.assemble', paid=False)) == expected
    assert ledger.read_bytes() == ledger_before


@pytest.mark.parametrize('target_stage', [
    pytest.param('stage5', marks=pytest.mark.stage_scope('stage5')),
    pytest.param('stage6', marks=pytest.mark.stage_scope('stage6')),
    pytest.param('stage7', marks=pytest.mark.stage_scope('stage7')),
])
def test_stage_generation_from_frozen_upstream_reaches_real_consumer(target_stage, tmp_path, baseline):
    from contracts.schema.stage5 import Chapter
    from novel.stages.stage5 import review
    from novel.store import ArtifactStore
    _tree, relative, inputs = baseline
    inputs[target_stage].restore(tmp_path)
    book = tmp_path / relative
    {'stage5': complete_stage5, 'stage6': complete_stage6, 'stage7': complete_stage7}[target_stage](book)
    if target_stage == 'stage5':
        store = ArtifactStore(_ctx(book, 'stage5.review', paid=False))
        assert [store.read_own(Chapter, f'stage5/ch{n:02}.json', require_current=True)[0].output_ordinal
                for n in (1, 2, 3)] == [1, 2, 3]
        assert review.run_review(_ctx(book, 'stage5.review', paid=False))[1].passed
    elif target_stage == 'stage6':
        view = stage6_view(book.parent, _ctx(book, 'stage6.hook', paid=False).sid)
        assert len(view.candidates) == 3 and view.approved is not None
    else:
        assert '顾清棠' in assemble.read_current_book(_ctx(book, 'stage7.assemble', paid=False))


@pytest.mark.stage_scope('stage7')
def test_golden_capture_reads_real_source_delivery_and_exposes_missing_run_identity(produced_book, tmp_path):
    from novel.stages.stage0 import revisions
    from novel.store import ArtifactStore
    from tests import golden as G
    from tests.test_agent import _PROFILES
    book = produced_book
    chain = revisions.read(ArtifactStore(_ctx(book, 'stage0.remove', paid=False)))
    source = Path(chain.source.source_ref.absolute_path)
    source_root = tmp_path / 'frozen_sources'
    source_root.mkdir()
    (source_root / source.name).write_bytes(source.read_bytes())
    manifest = G.Manifest(source_root=str(source_root), output_root=str(book.parent),
                          code_version=prov.code_version(), profiles=_PROFILES,
                          books=G.inventory(source_root), criteria=G.CRITERIA)
    snapshot = G.collect(manifest)
    row = snapshot['books']['123']
    assert row['error'] is None and row['source_current'] and len(row['surface_sha256']) == 64
    assert row['output_sha256'] == G._sha(assemble.read_current_book(_ctx(book, 'stage7.assemble', paid=False)).encode())
    assert row['chapter_ordinals'] == [1, 2, 3]
    # 此 fixture 证明正式 producer 消费链；没有伪造整书 Supervisor 身份和真实质量结论。
    assert row['profiles_current'] is False and snapshot['engineering_state'] == 'fail'
