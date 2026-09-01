#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 v7 叙事关系：产地、人审写回与 Stage4 盲投影缝。

全部用合成三章底座和 fake transport，不发任何真实 LLM wire。
这里特别守两个容易“各自全绿”的缝：

* producer response schema 与动态闸都只接受 ``review``；
* Commander 看完整 review/hard，Executor 只看 HARD，两面都只能看 opaque alias。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE2_BLUEPRINT_RELATION_EDIT
from contracts.invariants import BLUEPRINT_RELATION_REVIEW_CONTRACT, NODES
from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.api import BlueprintRelationEditPreview
from contracts.schema.stage2 import (
    Blueprint,
    BlueprintRelationEdit,
    ChapterBlueprints,
    ChapterBlueprintsResponseV31,
)
from novel.api.routes import edits as edit_routes
from novel.errors import TerminalState
from novel.orchestrator import nodes, uploads
from novel.stages.stage2 import extract, relation_edit
from novel.stages.stage4.blind import (
    build_blind_executor_projection,
    build_blind_projection,
)
from novel.store import ArtifactStore
from novel.views import inbox as inbox_view
from tests.test_stage2 import blueprints as blueprint_payload
from tests.test_stage2_generation import _response_blueprints
from tests.test_stage2_secret_edit import _ctx, _seed
from tests.test_stage4_blind import ROOT, _blueprints as blind_blueprints


def _chapter(ordinal: int = 1) -> dict:
    return copy.deepcopy(blueprint_payload()['blueprints'][ordinal - 1])


def _relation(
    kind: str,
    first: str,
    second: str,
    *,
    enforcement: str = 'review',
    observer: str = 'char_001',
) -> dict:
    common = {'kind': kind, 'enforcement': enforcement}
    if kind == 'story_before':
        return {**common, 'before_fact_ref': first, 'after_fact_ref': second}
    if kind == 'response_to':
        return {**common, 'stimulus_fact_ref': first, 'response_fact_ref': second}
    if kind == 'same_scene':
        return {**common, 'left_fact_ref': first, 'right_fact_ref': second}
    if kind == 'observes':
        return {
            **common,
            'observer_char_id': observer,
            'observed_fact_ref': first,
            'observation_fact_ref': second,
        }
    raise AssertionError(kind)


def test_formal_relation_schema_closes_refs_cycles_observer_and_canonical_pairs():
    base = _chapter(1)
    facts = [row['fact_id'] for row in base['fact_atoms']]
    assert len(facts) >= 2
    first, second = facts[:2]

    hard = copy.deepcopy(base)
    hard['narrative_relations'] = [
        _relation('response_to', first, second, enforcement='hard')]
    assert Blueprint.model_validate(hard).narrative_relations[0].enforcement == 'hard'

    missing = copy.deepcopy(base)
    missing['narrative_relations'] = [
        _relation('response_to', first, 'f01_999')]
    with pytest.raises(ValidationError, match='章级语义引用不存在'):
        Blueprint.model_validate(missing)

    self_loop = copy.deepcopy(base)
    self_loop['narrative_relations'] = [
        _relation('same_scene', first, first)]
    with pytest.raises(ValidationError, match='严格升序|不能自环'):
        Blueprint.model_validate(self_loop)

    noncanonical = copy.deepcopy(base)
    noncanonical['narrative_relations'] = [
        _relation('same_scene', second, first)]
    with pytest.raises(ValidationError, match='严格升序'):
        Blueprint.model_validate(noncanonical)

    cycle = copy.deepcopy(base)
    cycle['narrative_relations'] = [
        _relation('story_before', first, second),
        _relation('story_before', second, first),
    ]
    with pytest.raises(ValidationError, match='时序环'):
        Blueprint.model_validate(cycle)

    duplicate_by_enforcement = copy.deepcopy(base)
    duplicate_by_enforcement['narrative_relations'] = [
        _relation('response_to', first, second, enforcement='hard'),
        _relation('response_to', first, second, enforcement='review'),
    ]
    with pytest.raises(ValidationError, match='不能重复'):
        Blueprint.model_validate(duplicate_by_enforcement)

    invalid_observer = copy.deepcopy(base)
    invalid_observer['narrative_relations'] = [
        _relation('observes', first, second, observer='char_999')]
    with pytest.raises(ValidationError, match='observer 不在本章人物闭集'):
        Blueprint.model_validate(invalid_observer)

    # ch2 的 char_002 在章人物闭集里，但第一条 fact 没把它列为观察者。
    observer_not_on_fact = _chapter(2)
    observed, observation = [
        row['fact_id'] for row in observer_not_on_fact['fact_atoms'][:2]]
    observer_not_on_fact['narrative_relations'] = [
        _relation(
            'observes', observed, observation,
            observer='char_002',
        )]
    # 如果 fixture 的 observation 刚好带 char_002，就交换两个端点。
    observation_row = next(
        row for row in observer_not_on_fact['fact_atoms']
        if row['fact_id'] == observation)
    if 'char_002' in observation_row['participant_char_ids']:
        observer_not_on_fact['narrative_relations'] = [
            _relation(
                'observes', observation, observed,
                observer='char_002',
            )]
    with pytest.raises(ValidationError, match='observation fact'):
        Blueprint.model_validate(observer_not_on_fact)


def test_producer_can_only_propose_review_and_prompt_says_so():
    raw = copy.deepcopy(_response_blueprints()[1])
    atoms = [
        atom['atom_key']
        for beat in raw['beats']
        for atom in beat['fact_atoms']
    ]
    assert len(atoms) >= 2
    raw['narrative_relations'] = [
        _relation('response_to', atoms[0], atoms[1], enforcement='hard')]
    with pytest.raises(ValidationError, match='review'):
        ChapterBlueprintsResponseV31.model_validate({'blueprints': [raw]})

    raw['narrative_relations'][0]['enforcement'] = 'review'
    typed_review = ChapterBlueprintsResponseV31.model_validate(
        {'blueprints': [raw]}).blueprints[0]
    allowed = {
        str(char_id)
        for beat in typed_review.beats
        for char_id in beat.present_char_ids
    } | {
        str(char_id)
        for beat in typed_review.beats
        for atom in beat.fact_atoms
        for char_id in atom.participant_char_ids
    }
    # 即使内部调用者绕过 Pydantic 构造一份脏对象，生产动态闸仍关闭式拒绝。
    rogue_relation = typed_review.narrative_relations[0].model_copy(
        update={'enforcement': 'hard'})
    rogue = typed_review.model_copy(
        update={'narrative_relations': [rogue_relation]})
    error = extract._fact_contract_error(1, rogue, allowed)
    assert error and 'producer 只能提出 enforcement=review' in error

    assert extract._fact_contract_error(1, typed_review, allowed) is None

    from novel.stages.stage2 import prompts

    for system in (prompts.BLUEPRINT_SYS_V2_REFS, prompts.BLUEPRINT_SYS_V31):
        assert 'enforcement="review"' in system
        assert '只有后续 typed 人审' in system
        assert '关系晋升为 hard' in system
        assert '同一 beat' in system and '不要立关系' in system


def test_commander_truth_keeps_review_but_executor_gets_only_hard_opaque_aliases():
    source = blind_blueprints()
    source['blueprints'][0]['narrative_relations'] = [
        _relation('response_to', 'f01_001', 'f01_002', enforcement='review'),
        _relation('same_scene', 'f01_002', 'f01_003', enforcement='hard'),
    ]
    truth, _mapping = build_blind_projection(source, root_clean_sha1=ROOT)
    executor = build_blind_executor_projection(truth)

    commander_relations = truth['chapters'][0]['narrative_relations']
    executor_relations = executor['chapters'][0]['narrative_relations']
    assert {row['enforcement'] for row in commander_relations} == {'hard', 'review'}
    assert [row['enforcement'] for row in executor_relations] == ['hard']

    for payload in (truth, executor):
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for formal in ('f01_001', 'f01_002', 'f01_003', 'char_001'):
            assert formal not in rendered
        for row in payload['chapters'][0]['narrative_relations']:
            endpoint_values = [
                value for key, value in row.items() if key.endswith('_alias')]
            assert endpoint_values and all(
                isinstance(value, str) and value.startswith('a_')
                for value in endpoint_values)


def _request(
    artifact,
    *,
    relation: dict,
    actor: str = '测试审阅者',
    reason: str = '确认该回应必须保留真实指向',
) -> BlueprintRelationEdit:
    return BlueprintRelationEdit.model_validate({
        'actor': actor,
        'reason': reason,
        'review_token': relation_edit.review_token(artifact),
        'review_contract_version': BLUEPRINT_RELATION_REVIEW_CONTRACT,
        'chapters': [{
            'chapter_ordinal': 1,
            'narrative_relations': [relation],
        }],
    })


def _write_request(book: Path, request: BlueprintRelationEdit) -> Path:
    path = book / STAGE2_BLUEPRINT_RELATION_EDIT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            encode_artifact(
                STAGE2_BLUEPRINT_RELATION_EDIT,
                request.model_dump(mode='json', by_alias=True),
            ),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + '\n',
        encoding='utf-8',
        newline='\n',
    )
    return path


def _business_chapters(artifact) -> dict[int, bytes]:
    return {
        row.chapter_ordinal: json.dumps(
            row.model_dump(mode='json'),
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
        for row in artifact.blueprints
    }


def test_relation_preview_and_apply_share_candidate_and_only_replace_target_chapter(
    tmp_path: Path,
):
    book = tmp_path / '平台_关系修订_1234567890123456789'
    book.mkdir()
    _seed(book, with_blueprints=True)
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    store = ArtifactStore(ctx)
    before, _raw = store.read_own(
        ChapterBlueprints,
        extract.BLUEPRINT_REL,
        require_current=True,
    )
    before_business = _business_chapters(before)
    facts = [row.fact_id for row in before.blueprints[0].fact_atoms]
    request = _request(
        before,
        relation=_relation(
            'response_to', facts[0], facts[1], enforcement='hard'),
    )
    request_path = _write_request(book, request)

    bytes_before_preview = {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in book.rglob('*') if path.is_file()
    }
    preview = relation_edit.validate_edit(ctx, str(request_path))
    bytes_after_preview = {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in book.rglob('*') if path.is_file()
    }
    assert bytes_after_preview == bytes_before_preview
    assert preview == BlueprintRelationEditPreview.model_validate(
        preview.model_dump(mode='json'))
    assert preview.would_apply and preview.hard_hits == []
    assert preview.changed_chapters == [1]
    assert (preview.relations_total, preview.relations_hard,
            preview.relations_review) == (1, 1, 0)

    applied, report = relation_edit.apply_edit(ctx, str(request_path))
    assert report.node == 'stage2.blueprint'
    assert relation_edit.review_subject_sha256(applied) == \
        preview.result_subject_sha256
    assert applied.provenance.origin == 'human'
    assert applied.provenance.partial_rewrite == '--apply-edit'
    assert applied.provenance.prompts == before.provenance.prompts
    assert applied.provenance.model == before.provenance.model
    assert applied.provenance.usage == before.provenance.usage
    assert applied.blueprints[0].narrative_relations[0].enforcement == 'hard'

    after_business = _business_chapters(applied)
    assert after_business[1] != before_business[1]
    assert {
        ordinal: payload for ordinal, payload in after_business.items()
        if ordinal != 1
    } == {
        ordinal: payload for ordinal, payload in before_business.items()
        if ordinal != 1
    }
    assert (ctx.ledger.calls_made, ctx.ledger.calls_cached) == (0, 0)
    audit = [
        row for row in ctx.ledger.audit_events
        if row.get('kind') == 'human_typed_edit'
    ]
    assert len(audit) == 1
    assert audit[0]['chapters'] == [1]
    assert 'claim' not in audit[0] and 'narrative_relations' not in audit[0]
    assert audit[0]['subject_sha256'] == request.review_token.removeprefix('brt1_')


def test_stale_noop_or_schema_invalid_relation_request_never_changes_formal(
    tmp_path: Path,
):
    book = tmp_path / '平台_关系拒绝_1234567890123456789'
    book.mkdir()
    _seed(book, with_blueprints=True)
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    store = ArtifactStore(ctx)
    current, _raw = store.read_own(
        ChapterBlueprints,
        extract.BLUEPRINT_REL,
        require_current=True,
    )
    formal_before = store.path(extract.BLUEPRINT_REL).read_bytes()
    facts = [row.fact_id for row in current.blueprints[0].fact_atoms]

    stale = _request(
        current,
        relation=_relation('response_to', facts[0], facts[1]),
    ).model_copy(update={'review_token': 'brt1_' + '0' * 64})
    with pytest.raises(TerminalState, match='已过期'):
        relation_edit.prepare_edit(ctx, stale)
    assert store.path(extract.BLUEPRINT_REL).read_bytes() == formal_before

    no_op = BlueprintRelationEdit.model_validate({
        'actor': '审阅者', 'reason': '确认保持当前集合',
        'review_token': relation_edit.review_token(current),
        'review_contract_version': BLUEPRINT_RELATION_REVIEW_CONTRACT,
        'chapters': [{
            'chapter_ordinal': 1,
            'narrative_relations': [],
        }],
    })
    with pytest.raises(TerminalState, match='没有改变'):
        relation_edit.prepare_edit(ctx, no_op)
    assert store.path(extract.BLUEPRINT_REL).read_bytes() == formal_before

    cyclic = BlueprintRelationEdit.model_validate({
        'actor': '审阅者', 'reason': '故意构造时序冲突的反例',
        'review_token': relation_edit.review_token(current),
        'review_contract_version': BLUEPRINT_RELATION_REVIEW_CONTRACT,
        'chapters': [{
            'chapter_ordinal': 1,
            'narrative_relations': [
                _relation('story_before', facts[0], facts[1]),
                _relation('story_before', facts[1], facts[0]),
            ],
        }],
    })
    with pytest.raises(TerminalState, match='时序环'):
        relation_edit.prepare_edit(ctx, cyclic)
    assert store.path(extract.BLUEPRINT_REL).read_bytes() == formal_before
    assert not store.exists(extract._INVALID)


def test_stage2_relation_cli_policy_is_free_preview_then_write_apply():
    assert {'--emit-edit-template', '--validate-edit', '--apply-edit'} <= set(
        NODES['stage2.blueprint'].flags)
    assert nodes.is_paid('stage2.blueprint', ['--validate-edit', 'edit.json']) is False
    assert nodes.is_paid('stage2.blueprint', ['--apply-edit', 'edit.json']) is False
    assert nodes.would_write(
        'stage2.blueprint', ['--validate-edit', 'edit.json']) is False
    assert nodes.would_write(
        'stage2.blueprint', ['--apply-edit', 'edit.json']) is True
    assert '--validate-edit' in nodes.build_argv(
        'stage2.blueprint', '1234567890123456789',
        ['--validate-edit', 'edit.json'], base='Output')
    human_how = inbox_view._human_how('stage2')
    assert '`stage2.blueprint --apply-edit`' in human_how
    assert '`stage2.global --apply-edit`' in human_how


def test_stage2_relation_http_preview_and_apply_reach_the_shared_producer(
    monkeypatch,
    tmp_path: Path,
):
    request = BlueprintRelationEdit.model_validate({
        'actor': '审阅者',
        'reason': '结构化人审决定',
        'review_token': 'brt1_' + '1' * 64,
        'review_contract_version': BLUEPRINT_RELATION_REVIEW_CONTRACT,
        'chapters': [{
            'chapter_ordinal': 1,
            'narrative_relations': [
                _relation('response_to', 'f01_001', 'f01_002')],
        }],
    })
    preview = BlueprintRelationEditPreview(
        review_token=request.review_token,
        review_contract_version=BLUEPRINT_RELATION_REVIEW_CONTRACT,
        result_subject_sha256='2' * 64,
        changed_chapters=[1],
        relations_total=1,
        relations_hard=0,
        relations_review=1,
        hard_hits=[],
        would_apply=True,
    )
    ctx = SimpleNamespace(marker='ctx')
    prepared = SimpleNamespace(preview=lambda: preview)
    opened: list[tuple] = []
    monkeypatch.setattr(
        edit_routes.RunContext,
        'open',
        lambda *args, **kwargs: opened.append((args, kwargs)) or ctx,
    )
    monkeypatch.setattr(
        edit_routes.relation_edit,
        'prepare_edit',
        lambda actual_ctx, actual_request: (
            prepared
            if actual_ctx is ctx and actual_request is request
            else pytest.fail('HTTP preview 没有原样转交 typed request')
        ),
    )
    assert edit_routes.preview_blueprint_relations(
        request, sid='1234567890123456789', base=str(tmp_path)) == preview
    assert opened[0][0][2] == 'stage2.blueprint'
    assert opened[0][1] == {'paid': False, 'write_ledger': False}

    one_shot = tmp_path / '_service/edits/1234567890123456789/request.json'
    one_shot.parent.mkdir(parents=True)
    one_shot.write_text('{}', encoding='utf-8')
    marker = SimpleNamespace(state='ok')
    calls: list[tuple] = []
    monkeypatch.setattr(
        uploads,
        'save_blueprint_relation_edit',
        lambda base, sid, body: one_shot,
    )
    monkeypatch.setattr(
        edit_routes.jobs,
        'run',
        lambda *args, **kwargs: calls.append((args, kwargs)) or marker,
    )
    discarded: list[Path] = []
    monkeypatch.setattr(
        uploads,
        'discard_edit_plan',
        lambda base, path: discarded.append(Path(path)),
    )
    assert edit_routes.apply_blueprint_relations(
        request, sid='1234567890123456789', base=str(tmp_path)) is marker
    assert calls == [((
        str(tmp_path), 'stage2.blueprint', '1234567890123456789',
        ['--apply-edit', str(one_shot)],
    ), {'follow_check': False})]
    assert discarded == [one_shot]


def test_http_relation_request_is_a_registered_one_shot_service_input(
    tmp_path: Path,
):
    request = BlueprintRelationEdit.model_validate({
        'actor': '审阅者',
        'reason': '完整关系集合已人工核对',
        'review_token': 'brt1_' + '3' * 64,
        'review_contract_version': BLUEPRINT_RELATION_REVIEW_CONTRACT,
        'chapters': [{
            'chapter_ordinal': 1,
            'narrative_relations': [
                _relation('response_to', 'f01_001', 'f01_002')],
        }],
    })
    path = uploads.save_blueprint_relation_edit(
        tmp_path, '1234567890123456789', request)

    assert path.is_relative_to(
        (tmp_path / '_service/edits/1234567890123456789').resolve())
    envelope = json.loads(path.read_text(encoding='utf-8'))
    decoded = decode_artifact(STAGE2_BLUEPRINT_RELATION_EDIT, envelope)
    assert BlueprintRelationEdit.model_validate(decoded.payload) == request
    assert not list(tmp_path.glob('*_1234567890123456789/stage2/*.json'))
