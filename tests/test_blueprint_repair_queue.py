"""实际蓝图的失配只打开免费 owner 订正，不把旧正式文件变成付费重抽许可。"""
from __future__ import annotations

from contracts.schema.plot import ActorRef
from contracts.schema.stage2 import ChapterBlueprints
from novel.orchestrator import queue
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_plot_producer import generate_plot, plot_contract  # noqa: F401


def prepare_complete_book(tmp_path):
    """贯通 fixture 从真实摄入/批准/分章产生来源链，不跳过 Stage0/1 闸门。"""
    from novel.stages.stage0.removal import run_source, run_approve, _context
    from novel.stages.stage1.partition import run_partition
    from tests.test_stage2 import main_text
    from tests.test_stage2_generation import _ctx, _client, _ok, _response_global, _accept_fixture_global
    from tests.stage2_fixtures import generate_characters

    source = tmp_path / 'FQ_完整剧情测试_123.txt'
    body = '李宝珠收到父亲已经回城的来信。\n\n' + '\n\n'.join(
        f"{row['chapter_ordinal']}\n\n{row['text']}" for row in main_text())
    source.write_text(body, encoding='utf-8', newline='\n')
    book, _, _ = run_source(source, tmp_path / 'output')
    _, report = run_approve(_context(book, 'stage0.approve'),
                            actor='test:源文审阅者', reason='已核对合成全书来源与保留范围')
    assert report.passed
    _, report = run_partition(_context(book, 'stage1.partition'))
    assert report.passed
    generate_characters(_ctx(book, 'stage2.characters'))
    ctx = _ctx(book, 'stage2.global')
    client, _ = _client(ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(ctx, client)
    _accept_fixture_global(ctx)
    return book


def test_current_blueprint_with_missing_presentation_blocks_paid_queue(tmp_path):
    book = prepare_complete_book(tmp_path)
    ctx, transport = generate_plot(book)
    store = ArtifactStore(ctx)
    bp, _ = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    changed = bp.model_copy(deep=True)
    changed.blueprints[0].beats[0].participants[0] = ActorRef(
        char_id='char_001', presentation_ref='ap01_99')
    store.write(extract.BLUEPRINT_REL, changed)
    before = store.full_sha256_or_none(extract.BLUEPRINT_REL)
    todo, blocked = queue.plan_book(book.parent, ctx.sid, until_stage='stage4')
    repair = next(row for row in blocked if row.node == 'stage2.blueprint')
    assert repair.waiting_on == queue.WAITING_ON_INBOX
    assert '免费' in repair.why and not repair.steps
    assert not any(step.paid for row in [*todo, *blocked] for step in row.steps)
    assert store.full_sha256_or_none(extract.BLUEPRINT_REL) == before
    assert len(transport.calls) == 3
