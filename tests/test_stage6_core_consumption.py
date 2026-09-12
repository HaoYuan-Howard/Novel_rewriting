"""Prepare、开篇 judged 与 Writer9 公开目录的真实消费边界。"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE6_PREPARE
from novel.consumption import prepare_projection, projection_for, stage6_context
from novel.errors import TerminalState
from novel.stages.stage5.measure import load_drafts
from novel.stages.stage6 import prompts
from novel.stages.stage6.measure import lead_drafts, lead_upto


def _accepted_prepare_outcomes():
    """隔离 Hook 重试/运输样例的明确前置；真实 Prepare 核验见本文件 stage 反例。"""
    from novel.report.findings import ran, unverifiable
    return [ran('prepare_evidence_valid', scanned='合成已接受准备前置'),
            unverifiable('prepare_semantic_review', '合成业务前置已由独立决定接受')]


def _preparation():
    return {'source_blurb': {
        'facts': [
            {'kind': 'hook_supported', 'claim_id': 'hook_001', 'claim': '她已活过两世。',
             'hook_evidence': {'source': {'text': '钩子证据', 'start': 0, 'end': 4}}},
            {'kind': 'unresolved', 'claim_id': 'hook_002', 'claim': '他已经归来。',
             'reason': '正文与原钩子说法冲突，尚无确定归属'},
        ],
        'opening': {'kind': 'proposed', 'through_chapter': 1, 'rationale': '连续开篇',
                    'boundary_evidence': {'source': {'text': '范围证据'}}},
    }, 'main_text_sha256': 'a' * 64, 'hook_copy_sha256': 'b' * 64}


def test_prepare_semantics_and_evidence_have_separate_consumer_edges():
    original = _preparation()
    changed = copy.deepcopy(original)
    changed['source_blurb']['facts'][0]['hook_evidence']['source']['start'] = 5
    changed['source_blurb']['opening']['rationale'] = '审阅说明更正'
    for node in ('stage6.hook', 'stage6.semantic'):
        project = projection_for(node, STAGE6_PREPARE)
        assert project is prepare_projection
        assert project(original) == project(changed)
    changed['source_blurb']['facts'][0]['kind'] = 'unresolved'
    changed['source_blurb']['facts'][0]['reason'] = '存在正文反证'
    assert prepare_projection(original) != prepare_projection(changed)
    changed = copy.deepcopy(original)
    changed['source_blurb']['opening']['through_chapter'] = 2
    assert prepare_projection(original) != prepare_projection(changed)


def test_hook_material_carries_independent_truth_and_public_actor_directories_once():
    actor = {'actor_ref': 'actor_opaque_a', 'display_name': '新邻居', 'surfaces': ['新邻居'],
             'surface_allowed_slots': {'新邻居': ['s01']},
             'role': '隔壁住客', 'role_allowed_slots': ['s01'], 'allowed_slots': ['s01']}
    chapter = {
        'output_ordinal': 1, 'characters': [actor],
        'beats': [{'beat_ref': 'b01_01', 'content': '新邻居递来一封信。',
                   'participants': ['actor_opaque_a']}],
        'background_beats': [{'beat_ref': 'b00_01', 'content': '去年搬来。',
                              'participants': ['actor_opaque_a']}],
        'states': [], 'constraints': [], 'knowledge_events': [],
        'global_subjects': [{'subject_ref': {'kind': 'relation', 'ref': 'subject_opaque'},
                             'value': {'subject': 'actor_opaque_a', 'object': 'actor_opaque_b'}}],
        'narration': [{'narrator': 'actor_opaque_a', 'focalizer': 'actor_opaque_a'}],
        'restricted_subject_refs': [{'kind': 'secret', 'ref': 'secret_opaque'}],
        'scenes': [{'slot': 's01', 'deliveries': [{'beat_ref': 'b01_01'}]}],
    }
    registry = {'char_001': {'canonical': '登记身份', 'aliases': [{'surface': '新邻居'}]}}
    grounding = {'setting': {}, 'relations': [], 'world_rules': [], 'arc_secrets': []}
    preparation = prepare_projection(_preparation())
    material = prompts.build_material(
        title='书名', ctx={'chapters': [chapter]}, hook_mechanical={},
        grounding=grounding, characters=registry, preparation=preparation,
        source_hook='原钩子只提供一次。', lead=[{'output_ordinal': 1, 'text': '当前开篇。'}],
        missing=[], upto=1)
    packet = json.loads(material.blocks[1])
    assert packet['story_truth'] == {'characters': registry, 'global': grounding, 'prepare': preparation}
    view = packet['whole_story_map'][0]
    for key in ('characters', 'background_beats', 'global_subjects', 'narration', 'restricted_subject_refs'):
        assert view[key] == chapter[key]
    assert list(material.story_map) == stage6_context({'chapters': [chapter]})
    assert 'char_001' not in json.dumps(view), '不能给opaque表面身份添加内部反向映射'
    assert material.given.count('原钩子只提供一次。') == 1
    assert material.given.count('当前开篇。') == 1
    assert '钩子证据' not in material.given and '范围证据' not in material.given
    assert 'unresolved' in prompts.hook_system() and '正文反证优先' in prompts.hook_system()


def test_unresolved_opening_cannot_default_to_first_chapter():
    with pytest.raises(TerminalState, match='Prepare'):
        lead_upto({'kind': 'unresolved', 'reason': '开篇边界尚未确认'})


class _DraftStore:
    def __init__(self, root):
        self.root = root
        self.reads = []

    def path(self, rel):
        return self.root / rel

    def read_judged(self, _model, rel, *, project, require_current):
        self.reads.append(rel)
        ordinal = int(rel.rsplit('ch', 1)[1].split('.')[0])
        text = f'第{ordinal}章成稿'
        data = {'output_ordinal': ordinal, 'text': text,
                '_provenance': {'round': 1}}
        return SimpleNamespace(output_ordinal=ordinal, text=text,
            provenance=SimpleNamespace(round=1), model_dump=lambda **_: data), 'a' * 10, 'b' * 10


def test_lead_loader_reads_only_opening_but_default_loader_retains_fullbook(tmp_path):
    (tmp_path / 'stage5').mkdir()
    for rel in ('ch01.json', 'ch02.json', 'ch03.INVALID.json'):
        (tmp_path / 'stage5' / rel).write_text('{}', encoding='utf-8')
    store = _DraftStore(tmp_path)
    lead, missing, judged = lead_drafts(store, 1)
    assert store.reads == ['stage5/ch01.json']
    assert [row['output_ordinal'] for row in lead] == [1] and not missing
    assert set(judged) == {'stage5/ch01.json'}
    store.reads.clear()
    rows, invalids, all_judged = load_drafts(store)
    assert store.reads == ['stage5/ch01.json', 'stage5/ch02.json']
    assert set(rows) == {1, 2} and set(all_judged) == set(store.reads)
    assert invalids == ['stage5/ch03.INVALID.json']


@pytest.mark.parametrize('accepted', [False, True])
def test_prepare_recheck_has_exact_owner_scope_and_reads_independent_decision(tmp_path, capsys, accepted):
    from novel.cli import main as cli
    from novel.orchestrator import approvals
    from novel.stages.stage6 import prepare
    from novel.store import ArtifactStore
    from tests.test_stage6_prepare import seed_prepare, decision_for, _free_ctx

    ctx, driver, _ = seed_prepare(tmp_path)
    if accepted:
        prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx)))
    free = _free_ctx(ctx)
    store = ArtifactStore(free)
    before = {rel: store.full_sha256_or_none(rel)
              for rel in (prepare.REL, prepare.REVIEW, prepare.COMMIT)}
    report = prepare.run_check_only(free)
    assert report.node == 'stage6.prepare' and report.stage == 'stage6'
    assert {row.rule for row in report.rules} == {
        'prepare_evidence_valid', 'prepare_semantic_review'}
    assert report.passed and report.badge == 'amber' and report.counts.skipped == 0
    mechanical, = [row for row in report.rules if row.rule == 'prepare_evidence_valid']
    assert mechanical.status == 'ok' and mechanical.scanned == '4'
    semantic, = [row for row in report.rules if row.rule == 'prepare_semantic_review']
    assert semantic.status == 'unver'
    assert ('当前 Prepare 已经独立接受' if accepted else '未独立接受（pending）') in semantic.why
    assert {rel: store.full_sha256_or_none(rel) for rel in before} == before
    assert store.exists(prepare.REVIEW) is accepted
    assert not approvals.get(tmp_path, ctx.sid).gate['s6']
    assert not store.exists('stage6/hook.json') and len(driver.calls) == 1
    capsys.readouterr()
    assert cli.main(['run', prepare.NODE, ctx.sid, '--base', str(tmp_path), '--revalidate']) == 0
    output = capsys.readouterr().out
    assert 'prepare_evidence_valid' in output and 'prepare_semantic_review' in output
    assert 'hook_empty' not in output, '免费重验的退出码只对应 Prepare 自身登记范围'
    assert {rel: store.full_sha256_or_none(rel) for rel in before} == before
    assert not approvals.get(tmp_path, ctx.sid).gate['s6'] and len(driver.calls) == 1


def test_prepare_recheck_rejects_changed_source_and_keeps_prior_acceptance_distinct(tmp_path):
    from contracts.artifacts import STAGE1_MAIN_TEXT
    from contracts.schema.stage1 import MainText
    from novel.errors import ProvenanceRefused
    from novel.stages.stage6 import prepare
    from novel.store import ArtifactStore
    from tests.test_stage2 import main_text
    from tests.test_stage6_prepare import seed_prepare, decision_for, _free_ctx

    ctx, driver, _ = seed_prepare(tmp_path)
    prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx)))
    store = ArtifactStore(ctx)
    original = {rel: store.full_sha256_or_none(rel)
                for rel in (prepare.REL, prepare.REVIEW, prepare.COMMIT)}
    changed = copy.deepcopy(main_text())
    changed[0]['text'] = '新正文已经移除原来支持来信的内容。'
    store.write(STAGE1_MAIN_TEXT, MainText.model_validate(changed))
    free = _free_ctx(ctx)
    report = prepare.run_check_only(free)
    assert report.node == 'stage6.prepare' and not report.passed
    mechanical, = [row for row in report.rules if row.rule == 'prepare_evidence_valid']
    assert mechanical.status == 'hit' and mechanical.findings
    assert any('实际正文或源钩子材料已改变' in item.detail for item in mechanical.findings)
    semantic, = [row for row in report.rules if row.rule == 'prepare_semantic_review']
    assert semantic.status == 'unver' and '未独立接受（stale）' in semantic.why
    with pytest.raises(ProvenanceRefused, match='Prepare 未接受'):
        prepare.require_accepted(ArtifactStore(free))
    assert {rel: store.full_sha256_or_none(rel) for rel in original} == original
    assert len(driver.calls) == 1


def test_hook_scope_cannot_omit_prepare_checks_or_put_them_in_text_identity_grid():
    from contracts.judges.stage6 import PREPARE_RULES
    from contracts.rules import REGISTRY
    from novel.errors import RefusedByStructure
    from novel.prov import FreshnessReport
    from novel.report import build
    from novel.report.findings import ran
    from novel.stages.stage6 import hook

    prepare_ids = {rule.rule for rule in PREPARE_RULES}
    for node in ('stage6.hook', 'stage6.semantic', 'stage6.approve'):
        report = build(sid='123', node=node, freshness=FreshnessReport('fresh', 'match'))
        assert {row.rule for row in report.rules} == {row.rule for row in REGISTRY.of_stage('stage6')}
        assert all(row.status == 'skipped' for row in report.rules if row.rule in prepare_ids)
        assert not report.passed
    assert prepare_ids.isdisjoint(row.rule for row in hook._judgeable_rules())
    with pytest.raises(RefusedByStructure, match='hook_empty'):
        build(sid='123', node='stage6.prepare', freshness=FreshnessReport('fresh', 'match'),
              outcomes=[ran('hook_empty')])
