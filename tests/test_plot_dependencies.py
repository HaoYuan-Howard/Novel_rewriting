"""真实材料闭包的依赖反例：未消费的远端正文不扩大接受失效。"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from contracts.schema.knowledge_links import KnowledgeLinks, KnowledgeLinksProvenance
from novel.stages.stage2 import grounding, knowledge_links
from novel.stages.stage2.plot_compile import source_digest
from novel.stages.stage4.plot_materials import (
    chapter_materials, chapter_materials_identity, material_identity,
)
from tests.stage2_fixtures import plot_blueprints
from tests.test_stage2 import main_text
from tests.test_stage2_grounding import character_preparation, pair  # noqa: F401
from tests.test_stage2_knowledge_links import _scope, _history


def _review_pack(pair, bp, source, global_g=None):
    from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING, STAGE2_CHARACTERS
    from contracts.schema.plot import PlotBlueprint
    from novel.stages.stage2.plot_review import build_workpack
    from novel.stages.stage2.plot_review_dependencies import selections
    registry, knowledge = pair
    data = {STAGE1_MAIN_TEXT: source, STAGE2_CHAPTER_BLUEPRINTS: bp,
            STAGE2_CHARACTERS: registry.model_dump(mode='json', by_alias=True),
            STAGE2_GLOBAL_GROUNDING: global_g or grounding.compose(registry, knowledge).model_dump(
                mode='json', by_alias=True)}
    scope = _scope(knowledge, bp, source=source)
    snapshot = SimpleNamespace(inputs=data, ordinals=[1, 2, 3],
        blueprints=SimpleNamespace(blueprints=[PlotBlueprint.model_validate(row) for row in bp['blueprints']]),
        knowledge_scope=lambda _scope: scope.model_copy(update={
            'obligations': [r for r in scope.obligations if r.chapter_ordinal == 3]}))
    return build_workpack('chapter:3', data, additional_selections=selections(snapshot, 'chapter:3'))


def test_review_material_and_manifest_share_precise_foreign_evidence_dependencies(pair):
    bp, source = plot_blueprints(), main_text()
    before = _review_pack(pair, bp, source)
    assert any(isinstance(r, dict) and 'source_ranges' in r for r in before.material['dependencies'])
    source[0]['text'] += '\n\n院外微风吹过，无人经过。'
    bp['blueprints'][0]['source_sha256'] = source_digest(source[0]['text'])
    after = _review_pack(pair, bp, source)
    assert after.material == before.material
    assert after.manifest == before.manifest
    assert after.subject_sha256 == before.subject_sha256
    registry, knowledge = pair
    changed = grounding.compose(registry, knowledge).model_dump(mode='json', by_alias=True)
    changed['canon']['secrets'][0]['fact'] += '命题被修改'
    assert _review_pack(pair, bp, source, changed).subject_sha256 != before.subject_sha256


def test_issue_can_cite_visible_foreign_global_anchor_but_not_unoffered_source(pair):
    from tests.test_plot_scope_review import issue_response, submit
    pack = _review_pack(pair, plot_blueprints(), main_text())
    global_row = next(row for dep in pack.material['dependencies'] if isinstance(dep, list)
                      for row in dep if row.get('kind') == 'secret')
    anchor = global_row['evidence'][0]
    assert anchor['chapter_ordinal'] != 3
    value = issue_response()
    value['new_issues'][0].update(
        subjects=[{'kind': 'secret', 'ref': global_row['ref']}],
        evidence_refs=[{'chapter_ordinal': anchor['chapter_ordinal'],
                        'evidence_ref': anchor['evidence_ref']}])
    assert len(submit(pack, value).issues) == 1
    value['new_issues'][0]['evidence_refs'][0]['evidence_ref'] = 'p:99:99'
    with pytest.raises(ValueError, match='越出本轮'):
        submit(pack, value)


def _inputs(pair):
    registry, knowledge = pair
    bp = plot_blueprints()
    scope = _scope(knowledge, bp)
    links = KnowledgeLinks(scope=scope, links=knowledge_links.require_complete(scope, _history(scope)),
        _provenance=KnowledgeLinksProvenance(code='fixture', root_clean_sha1='0' * 40,
                                            inputs={}, decision_sha256='d' * 64))
    return bp, grounding.compose(registry, knowledge).model_dump(mode='json', by_alias=True), \
        links.model_dump(mode='json', by_alias=True), main_text()


def test_foreign_unconsumed_source_does_not_expire_chapter_materials(pair):
    bp, global_g, links, source = _inputs(pair)
    actual = chapter_materials(3, bp, global_g, links, source, [])
    before = chapter_materials_identity(3, bp, global_g, links, source, [])
    assert material_identity(actual) == before
    foreign = next(row for row in actual.source_materials if row.chapter_ordinal == 2)
    assert len(foreign.ranges) == 1
    assert foreign.ranges[0].text == '回来时看见母亲在门口等她。'
    changed_source = copy.deepcopy(source)
    changed_source[1]['text'] += '\n\n院门外吹来一阵微风。'
    changed_bp = copy.deepcopy(bp)
    changed_bp['blueprints'][1]['source_sha256'] = source_digest(changed_source[1]['text'])
    after = chapter_materials_identity(3, changed_bp, global_g, links, changed_source, [])
    assert after == before
    changed_actual = chapter_materials(3, changed_bp, global_g, links, changed_source, [])
    assert changed_actual == actual
    assert material_identity(changed_actual) == after


def test_consumed_foreign_evidence_and_membership_still_invalidate(pair):
    bp, global_g, links, source = _inputs(pair)
    before = chapter_materials_identity(3, bp, global_g, links, source, [])
    changed = copy.deepcopy(global_g)
    evidence = changed['entities']['characters']['char_002']['role_evidence']['source']
    evidence['start'] += 1
    evidence['text'] = evidence['text'][1:]
    assert chapter_materials_identity(3, bp, changed, links, source, []) != before
    wider = [{'kind': 'world_rule', 'ref': 'wr_01'}]
    assert chapter_materials_identity(3, bp, global_g, links, source, wider) != before
