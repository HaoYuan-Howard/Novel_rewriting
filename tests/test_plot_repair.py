"""定向返修使用真实请求/提交日志，模型传输替身不冒充语义质量验收。"""
from __future__ import annotations

import copy
import json
import pytest

from contracts.schema.stage2 import ChapterBlueprints
from novel.llm import stage2_runs
from novel.llm.cache import CachePolicy
from novel.stages.stage2 import extract, plot_repair
from novel.store import ArtifactStore
from tests.test_plot_producer import prepare_book, generate_plot, plot_contract
from tests.test_stage2_generation import _ctx, _client, _ok, _disk_blueprint_ctx


def revision_answer(req, _index):
    metadata = json.loads(req.user.split('\n', 1)[0])
    row = copy.deepcopy(metadata['repair_baseline'])
    row['beats'][0]['content'] = '昨夜的来信说，李宝珠的父亲已经回城。'
    row['identities'] = metadata['baseline_identities']
    return _ok({'blueprints': [row]})


@pytest.mark.parametrize('stop', ['prepared', 'formal'])
def test_targeted_revision_and_both_commit_windows_resume_without_rebuy(tmp_path, monkeypatch, stop):
    book = prepare_book(tmp_path)
    generate_plot(book)
    ctx = _ctx(book, 'stage2.blueprint')
    ctx.cache = CachePolicy.refresh()
    before = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    preview = extract.build_prompt(ctx, chapters=(1,))
    client, transport = _client(ctx, revision_answer)
    with monkeypatch.context() as patch:
        if stop == 'prepared':
            original = stage2_runs.prepare_commit
            def interrupt(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('提交候选封存后中断')
            patch.setattr(stage2_runs, 'prepare_commit', interrupt)
        else:
            original = extract._write_formal
            def interrupt(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('正式稿写回后中断')
            patch.setattr(extract, '_write_formal', interrupt)
        with pytest.raises(RuntimeError, match='中断'):
            extract.run_blueprint(ctx, client, chapters=(1,))
    assert len(transport.calls) == 1
    assert (transport.calls[0].system, transport.calls[0].user) == preview
    resumed = _disk_blueprint_ctx(book, ctx.ledger.path, paid=True, run_id=ctx.run_id)
    def forbidden(_req, _index):
        pytest.fail('已购修订在重启提交时不能再次调用模型')
    recovered_client, recovered_transport = _client(resumed, forbidden)
    report = extract.run_blueprint(resumed, recovered_client, chapters=(1,))
    assert report.passed and recovered_transport.calls == []
    landed = ArtifactStore(resumed).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert landed.blueprints[1:] == before.blueprints[1:]
    assert landed.blueprints[0].beats[0].beat_id == before.blueprints[0].beats[0].beat_id
    assert landed.blueprints[0].beats[0].content != before.blueprints[0].beats[0].content
    assert len(landed.provenance.plot_revisions) == 1
    assert stage2_runs.committed(resumed) is not None
    assert landed.provenance.usage != before.provenance.usage


def test_model_split_uses_new_ids_and_preserves_old_tombstones(tmp_path):
    book = prepare_book(tmp_path)
    generate_plot(book)
    ctx = _ctx(book, 'stage2.blueprint')
    ctx.cache = CachePolicy.refresh()
    def split(req, _index):
        metadata = json.loads(req.user.split('\n', 1)[0])
        row = metadata['repair_baseline']
        row['identities'] = metadata['baseline_identities']
        original = row['beats'][1]
        first, second = copy.deepcopy(original), copy.deepcopy(original)
        first.update(beat_key='b10', content='李宝珠站在春日老宅廊下看落花，回想昨夜的来信。')
        second.update(beat_key='b11', content='李宝珠折信收袖，听见远处梆声。')
        row['beats'][1:] = [first, second]
        row['identities'][1] = dict(collection='beats', kind='replace',
                                    old_refs=['b01_02'], new_keys=['b10', 'b11'])
        return _ok({'blueprints': [row]})
    client, transport = _client(ctx, split)
    assert extract.run_blueprint(ctx, client, chapters=(1,)).passed
    landed = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert len(transport.calls) == 1
    tombstone = landed.provenance.plot_revisions[0].tombstones[0]
    assert tombstone.node_id == 'b01_02'
    assert tombstone.superseded_by == [row.beat_id for row in landed.blueprints[0].beats][1:]
    assert 'b01_02' not in tombstone.superseded_by


def test_paid_revision_schema_repair_reuses_verified_checkpoint_and_candidate(tmp_path):
    from pathlib import Path
    from novel.errors import ResponseRepairBatchRequired
    from novel.llm import response_repair
    book = prepare_book(tmp_path)
    generate_plot(book)
    first = _ctx(book, 'stage2.blueprint')
    first.cache = CachePolicy.refresh()
    first.repair_rejected_responses = True
    candidates = {}
    def answer(req, _index):
        metadata = json.loads(req.user.split('\n', 1)[0])
        row = metadata['repair_baseline']
        row['identities'] = metadata['baseline_identities']
        row['chapter_function'] += '；明确本章剧情作用'
        candidates[metadata['chapter_ordinal']] = copy.deepcopy(row)
        if metadata['chapter_ordinal'] == 2:
            row.pop('identities')  # 已购完整剧情只缺结构字段，等待明确格式恢复。
        return _ok({'blueprints': [row]})
    client, transport = _client(first, answer)
    with pytest.raises(ResponseRepairBatchRequired) as stopped:
        extract.run_blueprint(first, client, chapters=(1, 2))
    assert len(transport.calls) == 2 and len(stopped.value.attempts) == 1
    attempt = stopped.value.attempts[0]
    record = response_repair.find_attempt(book, attempt)
    response_repair.candidate_path(record).write_text(
        json.dumps({'blueprints': [candidates[2]]}, ensure_ascii=False), encoding='utf-8')
    decisions = tmp_path / 'revision-response-decisions.json'
    decisions.write_text(json.dumps({'decisions': [{
        'attempt_id': attempt, 'action': 'submit',
        'reason': '保留已购剧情正文，按原编辑基线补齐一对一身份字段',
    }]}, ensure_ascii=False), encoding='utf-8')
    stage2_runs.submit_batch(book, Path(stopped.value.manifest_path), decisions, actor='test:恢复者')
    resumed = _disk_blueprint_ctx(book, first.ledger.path, paid=True, run_id=first.run_id)
    resumed.cache = CachePolicy.refresh()
    resumed.repair_rejected_responses = True
    def forbidden(_request, _index):
        pytest.fail('已购修订的格式恢复不能另买章节')
    recovered, wire = _client(resumed, forbidden)
    assert extract.run_blueprint(resumed, recovered, chapters=(1, 2)).passed
    assert wire.calls == []
    formal = ArtifactStore(resumed).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert len(formal.provenance.plot_revisions) == 1
    assert [row.chapter_ordinal for row in formal.blueprints] == [1, 2, 3]
