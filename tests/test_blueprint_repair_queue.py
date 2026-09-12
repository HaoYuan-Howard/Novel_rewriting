"""实际蓝图的失配只打开免费 owner 订正，不把旧正式文件变成付费重抽许可。"""
from contracts.schema.plot import ActorRef
from contracts.schema.stage2 import ChapterBlueprints
from novel.orchestrator import queue
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_plot_review_runtime import prepare_complete_book
from tests.test_plot_producer import generate_plot, plot_contract  # noqa: F401


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
