"""稀疏约束替代旧事实关系：封闭端点、真实编辑、盲投影与拒绝不写盘。"""
from __future__ import annotations
import copy
import json
import pytest
from pydantic import ValidationError
from contracts.schema.plot import PlotChapterResponse
from contracts.schema.plot_edit import PlotEdit
from novel.stages.stage2.plot_compile import compile_chapter
from novel.stages.stage2 import plot_edit
from novel.stages.stage4.plot_design import build_blind_projection
from novel.errors import ProvenanceRefused, TerminalState
from tests.test_plot_blueprint import chapter_response, SOURCE, CHARS
from tests.test_plot_producer import prepare_book, generate_plot, plot_contract
from tests.test_plot_edit import request_for, save_request
from tests.test_stage2_generation import _ctx


def _relation(kind='response_to', before='b1', after='b2'):
    return {'constraint_key': 'c1', 'kind': kind, 'before_beat_ref': before,
            'after_beat_ref': after, 'observer_char_id': 'char_001' if kind == 'observes' else None,
            'evidence': [{'start_para': 1, 'end_para': 2}]}


def _compile(relations):
    raw = chapter_response()
    raw['constraints'] = relations
    return compile_chapter(PlotChapterResponse.model_validate(raw), SOURCE, allowed_char_ids=CHARS)


@pytest.mark.parametrize('kind', ['cause', 'story_before', 'response_to', 'same_scene', 'observes'])
def test_constraints_keep_explicit_semantic_endpoints_without_fact_database(kind):
    chapter = _compile([_relation(kind)])
    row = chapter.constraints[0]
    assert (row.before_beat_ref, row.after_beat_ref) == ('b01_01', 'b01_02')
    assert row.observer_char_id == ('char_001' if kind == 'observes' else None)
    assert 'enforcement' not in row.model_dump()


@pytest.mark.parametrize('damage', ['foreign', 'self_loop', 'observer_missing', 'observer_wrong_kind', 'cycle'])
def test_constraints_refuse_dangling_self_cycle_or_wrong_observer(damage):
    row = _relation()
    relations = [row]
    if damage == 'foreign':
        row['after_beat_ref'] = 'b99'
    elif damage == 'self_loop':
        row['after_beat_ref'] = 'b1'
    elif damage == 'observer_missing':
        row['kind'] = 'observes'
    elif damage == 'observer_wrong_kind':
        row['observer_char_id'] = 'char_001'
    else:
        row['kind'] = 'cause'
        relations.append({**_relation('cause', 'b2', 'b1'), 'constraint_key': 'c2'})
    with pytest.raises(ValueError):
        _compile(relations)


@pytest.mark.parametrize('enforcement', ['review', 'hard'])
def test_retired_dual_enforcement_cannot_enter_current_response(enforcement):
    row = {**_relation(), 'enforcement': enforcement}
    with pytest.raises(ValidationError):
        _compile([row])


def test_planner_projection_preserves_constraint_semantics_without_source_ids():
    chapter = _compile([_relation('observes')])
    from tests.test_plot_design import planner_characters
    view, mapping = build_blind_projection({'blueprints': [chapter.model_dump(mode='json')]},
        root_clean_sha1='2' * 40, global_g=planner_characters())
    wire = json.dumps(view, ensure_ascii=False)
    observer = next(alias for alias, ref in mapping['subject_aliases'].items()
                    if ref == {'kind': 'character', 'ref': 'char_001'})
    assert 'observes' in wire and observer in wire
    assert not any(forbidden in wire for forbidden in ('b01_', 'e01_', 'start_para', 'source_sha256'))


def test_constraint_edit_previews_and_applies_same_current_candidate(tmp_path):
    book = prepare_book(tmp_path)
    _, transport = generate_plot(book)
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    raw = request_for(ctx)
    edit = raw['chapters'][0]
    edit['chapter']['constraints'] = [_relation('story_before')]
    edit['identities'].append({'collection': 'constraints', 'kind': 'add',
                               'old_refs': [], 'new_keys': ['c1']})
    candidate = plot_edit.prepare_edit(ctx, PlotEdit.model_validate(raw))
    before = (book / plot_edit.REL).read_bytes()
    assert candidate.preview().changed_chapters == [1]
    assert (book / plot_edit.REL).read_bytes() == before
    landed, report = plot_edit.apply_edit(ctx, save_request(ctx, raw))
    assert report.passed
    assert plot_edit.subject(landed) == candidate.record.result_subject_sha256
    assert len(transport.calls) == 3
    assert len(landed.blueprints[0].constraints) == 1
    assert landed.blueprints[1:] == candidate.artifact.blueprints[1:]


@pytest.mark.parametrize('damage', ['stale', 'no_op', 'cycle'])
def test_bad_edit_cannot_modify_formal_artifact(tmp_path, damage):
    book = prepare_book(tmp_path)
    generate_plot(book)
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    raw = request_for(ctx)
    if damage == 'stale':
        raw['review_token'] = 'bpe1_' + 'f' * 64
    elif damage == 'cycle':
        raw['chapters'][0]['chapter']['constraints'] = [
            _relation('cause'), {**_relation('cause', 'b2', 'b1'), 'constraint_key': 'c2'}]
        raw['chapters'][0]['identities'].append({'collection': 'constraints', 'kind': 'add',
                                                'old_refs': [], 'new_keys': ['c1', 'c2']})
    before = (book / plot_edit.REL).read_bytes()
    with pytest.raises((ValueError, TerminalState, ProvenanceRefused)):
        plot_edit.apply_edit(ctx, save_request(ctx, raw))
    assert (book / plot_edit.REL).read_bytes() == before
