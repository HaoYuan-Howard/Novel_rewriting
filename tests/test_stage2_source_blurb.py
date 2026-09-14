# -*- coding: utf-8 -*-
"""钩子独立证据的实际 producer、审阅材料、免费修订和文案消费链。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.artifacts import STAGE1_HOOK_COPY, STAGE2_GLOBAL_GROUNDING
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage1 import HookCopy
from contracts.schema.stage2 import GlobalEdit
from novel.core.evidence import without_global_evidence
from novel.orchestrator.node_agents import Stage2GlobalAgent
from novel.stages.stage2 import extract, grounding
from novel.stages.stage2.source_blurb import (
    hook_evidence_problems, manual_hook_evidence, normalize_hook_evidence,
)
from novel.store import ArtifactStore
from tests.test_stage2_global_agent import _Driver, _free_ctx, _resume_ctx
from tests.test_stage2_grounding import _knowledge_response
from tests.test_stage2_knowledge_flow import _prepared


HOOK = '新婚夜，我收到一封远方来信。'


def _hook_fact(locator=HOOK):
    return {'kind': 'hook_supported', 'claim_id': 'hook_001',
            'claim': '新婚夜收到远方来信', 'hook_evidence': {'locator': locator}}


def _producer(tmp_path):
    ctx, _registry = _prepared(tmp_path)
    ArtifactStore(ctx).write(STAGE1_HOOK_COPY, HookCopy(text=HOOK))
    candidate = _knowledge_response().model_dump(mode='json')
    candidate['source_blurb']['facts'] = [_hook_fact()]
    wire = json.dumps({'payload': {'kind': 'full_candidate', 'candidate': candidate}}, ensure_ascii=False)
    driver = _Driver([wire], ctx.novel_dir)
    return ctx, driver


def test_hook_only_fact_reaches_real_producer_review_api_and_hook_material(tmp_path):
    from fastapi.testclient import TestClient
    from novel.api.app import create_app
    from novel.stages.stage6.prompts import build_material
    ctx, driver = _producer(tmp_path)
    assert Stage2GlobalAgent(ctx, driver=driver).run().passed
    store = ArtifactStore(ctx)
    knowledge = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    fact = knowledge.source_blurb.facts[0]
    assert fact.kind == 'hook_supported'
    assert fact.hook_evidence.source.text == HOOK
    assert fact.hook_evidence.source.start == 0 and fact.hook_evidence.source.end == len(HOOK)
    projected = without_global_evidence(grounding.read_own(store)[0].model_dump(by_alias=True))
    assert projected['source_blurb']['facts'][0] == {
        'kind': 'hook_supported', 'claim_id': 'hook_001', 'claim': '新婚夜收到远方来信'}
    material = build_material(title='合成测试', ctx={'chapters': []}, hook_mechanical={},
                              grounding=projected, source_hook=HOOK, lead=[], missing=[], upto=1)
    assert 'hook_supported' in material.given
    assert material.story_truth['source_blurb'] == projected['source_blurb']
    for _ in range(2):
        with TestClient(create_app(tmp_path)) as client:
            response = client.get(f'/api/novels/{ctx.sid}/stage2/global')
            assert response.status_code == 200, response.text
            assert response.json()['source_blurb']['facts'][0]['hook_evidence']['source']['text'] == HOOK
    assert len(driver.calls) == 1


def test_hook_only_evidence_and_real_drafts_reach_final_approval_http_after_restart(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from contracts.artifacts import STAGE6_HOOK, artifact_rel
    from novel.api.app import create_app
    from novel import prov
    from novel.errors import UpstreamMissing
    from novel.stages.stage6 import hook
    from tests.test_final_approval_workpacks import _stage6_snapshot_env
    from tests.test_stage5_generation import _stage5_chapter

    ctx, driver = _producer(tmp_path)
    assert Stage2GlobalAgent(ctx, driver=driver).run().passed
    store = ArtifactStore(ctx)
    upstream = SimpleNamespace(
        source_hook=HOOK, gg=grounding.read_own(store)[0],
        wc=SimpleNamespace(chapters=[1, 2]),
    )
    # 合成章只替代与本例无关的上游生产；正文仍经正式 read_judged 装载和版本校验。
    monkeypatch.setattr(ArtifactStore, 'freshness', lambda *_args: prov.FreshnessReport('fresh', 'match'))
    body = ['天亮后，她走出院门。', '第二天，她已经抵达城外。']
    for ordinal, text in enumerate(body, 1):
        draft = _stage5_chapter(text=text, output_ordinal=ordinal)
        draft = draft.model_copy(update={'provenance': draft.provenance.model_copy(update={
            'root_clean_sha1': ctx.root_clean_sha1,
        })})
        store.write(artifact_rel('stage5.chapter', nn=ordinal), draft)
    real_materials = hook._approval_materials
    _snapshot_store, art = _stage6_snapshot_env(monkeypatch)
    monkeypatch.setattr(hook, '_approval_materials', real_materials)
    monkeypatch.setattr(hook, '_Upstreams', lambda *_args: upstream)
    art = art.model_copy(update={'provenance': art.provenance.model_copy(update={
        'root_clean_sha1': ctx.root_clean_sha1, 'inputs': ctx.edges,
    })})
    store.write(STAGE6_HOOK, art)
    snapshots = []
    for _ in range(2):
        with TestClient(create_app(tmp_path)) as client:
            response = client.get(f'/api/novels/{ctx.sid}/approvals/stage6/workpack')
            assert response.status_code == 200, response.text
            pack = response.json()
            snapshots.append(pack)
            assert pack['materials']['source_hook'] == HOOK
            assert pack['materials']['source_blurb']['facts'][0]['hook_evidence']['source']['text'] == HOOK
            assert [row['text'] for row in pack['materials']['rewritten_chapters']] == body
    assert snapshots[0] == snapshots[1]
    assert len(driver.calls) == 1
    store.path(artifact_rel('stage5.chapter', nn=2)).unlink()
    with pytest.raises(UpstreamMissing, match='第 \\[2\\] 章'):
        real_materials(store, upstream)


@pytest.mark.parametrize('locator,source', [('不存在的证据', HOOK), ('重复', '重复，重复。')])
def test_hook_locator_failure_retains_unknown_and_can_be_repaired_for_free(locator, source):
    data = {'source_blurb': {'facts': [_hook_fact(locator)]}}
    normalize_hook_evidence(data, source)
    evidence = data['source_blurb']['facts'][0]['hook_evidence']
    assert evidence == {'source': None, 'unresolved_locator': locator}
    assert hook_evidence_problems(data, source)
    assert manual_hook_evidence(evidence, source) == {'locator': locator}
    data['source_blurb']['facts'][0]['hook_evidence'] = {'locator': source}
    normalize_hook_evidence(data, source)
    assert hook_evidence_problems(data, source) == {}
    assert hook_evidence_problems(data, None)
    assert hook_evidence_problems(data, '已改变的源文案')


def test_hook_fact_edit_uses_same_normalizer_and_preserves_other_subjects(tmp_path):
    ctx, driver = _producer(tmp_path)
    Stage2GlobalAgent(ctx, driver=driver).run()
    store = ArtifactStore(ctx)
    before = grounding.read_own(store)[0]
    raw = before.model_dump(mode='json', by_alias=True)
    main = extract._read_main_text(store)
    replacement = extract._manualize_evidence(
        raw['source_blurb'], {row['chapter_ordinal']: row['text'] for row in main},
        subject='source_blurb', source_hook=HOOK)
    replacement['facts'][0]['claim'] = '我在新婚夜收到远方寄来的信'
    edit = GlobalEdit.model_validate({'source_blurb_replace': replacement})
    candidate = extract._build_global_edit_candidate(before, raw, edit, main, source_hook=HOOK)
    assert candidate.data['source_blurb']['facts'][0]['hook_evidence']['source']['text'] == HOOK
    assert candidate.data['entities'] == raw['entities']
    assert candidate.data['canon'] == raw['canon']
    assert raw == before.model_dump(mode='json', by_alias=True)
    free = _free_ctx(ctx)
    ArtifactStore(free).write_sparse_input(extract.GLOBAL_EDIT, edit)
    changed, _report = extract.run_apply_edit(free, extract.GLOBAL_EDIT)
    assert changed.source_blurb.facts[0].claim == replacement['facts'][0]['claim']
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    final = changed.model_dump(mode='json', by_alias=True)
    broken = copy.deepcopy(final)
    broken['source_blurb']['facts'][0]['hook_evidence']['source']['text'] = '伪造证据'
    assert 'global_evidence_unlocated' in extract._hard_hits(
        extract._judge_candidate(broken, None, main, source_hook=HOOK))


def test_hook_edit_template_does_not_mistake_overlapping_hits_for_unique():
    source = '哈哈哈哈'
    original = {'source': {'text': '哈哈哈', 'start': 0, 'end': 3}}
    locator = manual_hook_evidence(original, source)
    assert locator == {'locator': source}
    data = {'source_blurb': {'facts': [_hook_fact(locator['locator'])]}}
    normalize_hook_evidence(data, source)
    assert hook_evidence_problems(data, source) == {}


def test_saved_hook_fact_candidate_recovers_without_another_model_call(tmp_path, monkeypatch):
    from novel.llm import stage2_global_runs
    ctx, driver = _producer(tmp_path)

    def crash_after_formal(*args, **kwargs):
        raise OSError('模拟新文案事实已提交后的中断')

    with monkeypatch.context() as crash:
        crash.setattr(stage2_global_runs, 'mark_committed', crash_after_formal)
        with pytest.raises(OSError):
            Stage2GlobalAgent(ctx, driver=driver).run()
    resumed = _resume_ctx(ctx)
    no_wire = _Driver([], ctx.novel_dir)
    result = Stage2GlobalAgent(resumed, driver=no_wire).run()
    assert result.passed and no_wire.calls == []
    assert len(driver.calls) == 1
