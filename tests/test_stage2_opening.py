"""源文案正式输入、未知裁决、typed 修改与已购回答恢复的消费回归。"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE1_HOOK_COPY
from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import HookCopy
from contracts.schema.stage2 import GlobalEdit, GlobalGrounding
from novel import paths
from novel.errors import HumanEditProtected, ProvenanceRefused, TerminalState, UpstreamMissing
from novel.node_agents import Stage2GlobalAgent
from novel.quality.global_review import build_review, decide, generation_safety
from novel.stages.stage2 import extract
from novel.stages.stage6.measure import lead_upto
from novel.store import ArtifactStore
from novel.work_units import initialize_workflow
from tests.test_stage2 import global_grounding
from tests.test_stage2_generation import _ctx, _response_global
from tests.test_stage2_global_agent import _Driver, _ctx as agent_ctx, _full, _resume_ctx
from tests.test_stage2_secret_edit import _apply, _seed, _write_edit


def _replacement(through: int = 2) -> dict:
    return {
        'facts': [{'kind': 'supported', 'claim_id': 'hook_001', 'claim': '父亲已经回城',
                   'evidence': {'chapter_ordinal': 1, 'locator': '信上说父亲已经回城。'}}],
        'opening': {'kind': 'proposed', 'through_chapter': through,
                    'rationale': '连续提供来信与次日母亲的反应',
                    'boundary_evidence': {'chapter_ordinal': 2,
                                          'locator': '其实母亲早就知道那封信的事。'}},
    }


def _book(tmp_path):
    initialize_workflow(tmp_path)
    book = tmp_path / '源文案范围_7201'
    book.mkdir()
    _seed(book)
    return book


def test_global_request_and_provenance_bind_source_hook(tmp_path):
    book = _book(tmp_path)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    artifact = grounding_io.read_own(store, extract.GLOBAL_REL)[0]
    assert STAGE1_HOOK_COPY in artifact.provenance.inputs
    _system, user = extract.build_prompt(ctx)
    source = store.read_own(HookCopy, STAGE1_HOOK_COPY)[0].text
    assert user.count(source) == 1
    assert 'source_blurb' in _system
    assert 'conflict_stages' not in _system
    assert 'arc' not in artifact.model_dump()


def test_missing_source_hook_stops_before_wire(tmp_path):
    ctx = agent_ctx(tmp_path)
    (ctx.novel_dir / STAGE1_HOOK_COPY).unlink()
    driver = _Driver([], ctx.novel_dir)
    with pytest.raises(UpstreamMissing, match='hook_copy'):
        Stage2GlobalAgent(ctx, driver=driver).run()
    assert driver.calls == []
    assert not ArtifactStore(ctx).exists(extract.GLOBAL_REL)


def test_source_hook_drift_refuses_saved_answer_without_repurchase(tmp_path, monkeypatch):
    real_evaluate = extract.evaluate_global_candidate
    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt('已保存回答后中断')
    monkeypatch.setattr(extract, 'evaluate_global_candidate', crash)
    ctx = agent_ctx(tmp_path)
    driver = _Driver([_full(_response_global())], ctx.novel_dir)
    with pytest.raises(KeyboardInterrupt, match='已保存回答'):
        Stage2GlobalAgent(ctx, driver=driver).run()
    checkpoints = list(paths.stage2_global_checkpoint_dir(
        ctx.novel_dir, ctx.run_id).glob('rev-*.json'))
    assert len(checkpoints) == 1
    before = checkpoints[0].read_bytes()
    ArtifactStore(ctx).write(STAGE1_HOOK_COPY, HookCopy(text='修改后的源文案要求核对另一件事。'))
    monkeypatch.setattr(extract, 'evaluate_global_candidate', real_evaluate)
    resumed = _resume_ctx(ctx)
    no_wire = _Driver([], resumed.novel_dir)
    with pytest.raises(ProvenanceRefused):
        Stage2GlobalAgent(resumed, driver=no_wire).run()
    assert no_wire.calls == []
    assert checkpoints[0].read_bytes() == before
    assert not ArtifactStore(ctx).exists(extract.GLOBAL_REL)


@pytest.mark.parametrize('target', ['opening', 'fact'])
def test_unresolved_subject_cannot_be_accepted_or_count_as_safe(target):
    raw = global_grounding()
    if target == 'opening':
        raw['source_blurb']['opening'] = {'kind': 'unresolved', 'reason': '终点不明'}
    else:
        raw['source_blurb']['facts'][0] = {
            'kind': 'unresolved', 'claim_id': 'hook_001', 'claim': '父亲回城',
            'reason': '无法确定源文案所指事件'}
    review = build_review(raw)
    item = next(row for row in review.items if row.resolution_required)
    with pytest.raises(HumanEditProtected, match='typed'):
        decide(review, item_id=item.item_id, expected_subject_sha256=item.subject_sha256,
               state='PASS', actor='测试审阅者', reason='不能以接受绕过未知')
    assert not generation_safety(raw, review).safe


def test_opening_typed_edit_updates_real_artifact_and_reopens_review(tmp_path):
    book = _book(tmp_path)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    old_review = store.read_own(GlobalReview, extract.GLOBAL_REVIEW_REL)[0]
    old = next(row for row in old_review.items if row.kind == 'opening_scope')
    accepted = decide(old_review, item_id=old.item_id,
                      expected_subject_sha256=old.subject_sha256, state='PASS',
                      actor='测试审阅者', reason='读过原开篇范围')
    store.write(extract.GLOBAL_REVIEW_REL, accepted)
    formal_before = (book / extract.GLOBAL_REL).read_bytes()
    _write_edit(book, {'source_blurb_replace': _replacement()})
    artifact, report = _apply(book)
    assert report.passed
    assert artifact.provenance.partial_rewrite == '--apply-edit'
    assert extract.GLOBAL_EDIT in artifact.provenance.inputs
    assert STAGE1_HOOK_COPY in artifact.provenance.inputs
    reloaded = grounding_io.read_own(store, extract.GLOBAL_REL)[0]
    assert lead_upto(reloaded.source_blurb.opening.model_dump()) == 2
    new_review = store.read_own(GlobalReview, extract.GLOBAL_REVIEW_REL)[0]
    new = next(row for row in new_review.items if row.kind == 'opening_scope')
    assert new.subject_sha256 != old.subject_sha256
    assert new.state == 'pending' and new.actor is None
    assert (book / 'stage2/_rounds/global_grounding.r1.json').read_bytes() == formal_before


@pytest.mark.parametrize('through', [1, 99])
def test_invalid_opening_edit_preserves_formal_and_review(tmp_path, through):
    book = _book(tmp_path)
    _write_edit(book, {'source_blurb_replace': _replacement(through)})
    before = {rel: (book / rel).read_bytes()
              for rel in (extract.GLOBAL_REL, extract.GLOBAL_REVIEW_REL)}
    with pytest.raises(extract.GlobalEditCandidateRejected, match='invalid_opening_scope'):
        _apply(book)
    assert before == {rel: (book / rel).read_bytes() for rel in before}
    assert not (book / 'stage2/global_grounding.INVALID.json').exists()


@pytest.mark.parametrize('opening', [None, {}, {'kind': 'unresolved', 'reason': '未知'},
                                    {'kind': 'proposed', 'through_chapter': True}])
def test_stage6_has_no_default_opening_range(opening):
    with pytest.raises(TerminalState):
        lead_upto(opening)


def test_global_edit_new_schema_rejects_retired_arc_and_roundtrips_opening():
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'arc_replace': {'ending': '结束'}})
    request = {'source_blurb_replace': _replacement()}
    encoded = encode_artifact(extract.GLOBAL_EDIT, request)
    assert encoded['_schema_version'] == 5
    decoded = GlobalEdit.model_validate(decode_artifact(extract.GLOBAL_EDIT, encoded).payload)
    assert decoded.source_blurb_replace.opening.through_chapter == 2
