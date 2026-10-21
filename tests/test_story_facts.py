"""离线人工事实评测：源冻结、分集、防泄漏、实际SourceStory对齐及未知分母。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from novel.stages.stage2 import story as source_engine
from tests.story_facilities import _inputs, _book_wire
from tools import story_facts as F


def _case():
    index, chapters = _inputs()
    book = source_engine.compile_book(_book_wire(index, chapters), index=index, chapters=chapters)
    people = {}
    for key, entity in book.story.entities.items():
        literals = [surface.literal for surface in entity.surfaces]
        people[key] = {'family_group': None, 'family': None, 'given': None,
            'aliases': ['盛儿'] if '盛儿' in literals else [],
            'pure_titles': [literal for literal in literals if literal != '盛儿']}
    names = source_engine.compile_names({'contract_version': 'story-v1', 'people': people}, book=book)
    story = source_engine.compile_source_story(index=index, chapters=chapters, book=book, names=names)
    pack = F.freeze(index, sample='baby', split='dev', sid='42', source_relative='样书_42.txt',
                    source_raw_sha256='a' * 64, evidence_refs=list(index.evidence))
    annotation = F.blank_annotation(pack).model_dump(mode='json')
    evidence = list(index.chapter(1))[2]
    annotation['coverage']['fact'] = {'status': 'annotated', 'note': '重点核对误信纠正', 'evidence_refs': [evidence]}
    annotation['expectations'] = {'invitation': {'category': 'fact', 'statement': '受邀只是猜测，后文证伪',
        'presence': 'present', 'evidence_refs': [evidence],
        'checks': [{'path': ['world_status'], 'op': 'equals', 'value': 'refuted'}]}}
    return index, book, story, pack, F.Annotation.model_validate(annotation)


def _review(report, path):
    value = deepcopy(report['review_template'])
    value['reviewer'] = '离线人工评审'
    value['findings']['invitation'] = {'verdict': 'supported', 'candidate_paths': [list(path)],
                                     'note': '原文先猜测、后明确否定；实际命题保留refuted'}
    return F.Review.model_validate(value)


@pytest.mark.parametrize('refs', ([], ['not_a_segment'], ['e001_00001', 'e001_00001']))
def test_freeze_refuses_unknown_empty_or_duplicate_evidence_scope(refs):
    index, _ = _inputs()
    with pytest.raises(ValueError):
        F.freeze(index, sample='bad', split='dev', sid='42', source_relative='样书.txt',
                 source_raw_sha256='a' * 64, evidence_refs=refs)


@pytest.mark.parametrize('same_identity', ('sid', 'source_identity', 'content_sha256', 'source_raw_sha256'))
def test_dev_and_heldout_cannot_overlap_through_alias_or_changed_scope(tmp_path, same_identity):
    _index, _book, _story, pack, _annotation = _case()
    F.register(tmp_path, pack)
    changes = {'sample': 'renamed', 'split': 'heldout', 'sid': '99', 'source_identity': 'another-source',
               'content_sha256': 'b' * 64, 'source_raw_sha256': 'c' * 64}
    changes[same_identity] = getattr(pack, same_identity)
    with pytest.raises(ValueError, match='dev.*heldout'):
        F.register(tmp_path, pack.model_copy(update=changes))
    registry = json.loads((tmp_path / 'dataset.json').read_text(encoding='utf-8'))
    assert set(registry['samples']) == {'baby'}
    assert 'evidence' not in registry['samples']['baby'], '全局分集清单不可复制保留集正文'


def test_prepare_rerun_never_overwrites_manual_annotations(tmp_path):
    _index, _book, _story, pack, annotation = _case()
    directory = F.register(tmp_path, pack)
    path = directory / 'annotation.json'
    path.write_text(annotation.model_dump_json(), encoding='utf-8')
    before = path.read_bytes()
    assert F.register(tmp_path, pack) == directory
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='不可覆盖'):
        F.register(tmp_path, pack.model_copy(update={'evidence': dict(list(pack.evidence.items())[:1])}))


def test_structural_matching_is_not_automatic_semantic_recall_or_quality():
    index, book, story, pack, annotation = _case()
    report = F.compare(pack, annotation, story, index=index, candidate_sha256='b' * 64)
    assert report['semantic'] == 'unver'
    assert report['summary']['fact']['positive_denominator'] == 1
    assert report['summary']['fact']['unver'] == 1
    assert report['summary']['fact']['human_annotated_recall'] is None
    assert all(row['human_annotated_recall'] is None for row in report['summary'].values())
    path = ('propositions', book.proposition_handles['来客受邀'])
    aligned_only = deepcopy(report['review_template'])
    aligned_only['findings']['invitation']['candidate_paths'] = [list(path)]
    still_unver = F.compare(pack, annotation, story, index=index, candidate_sha256='b' * 64,
                           review=F.Review.model_validate(aligned_only))
    assert still_unver['expectations']['invitation']['field_checks'][0]['matches']
    assert still_unver['summary']['fact']['human_annotated_recall'] is None
    assert list(path) in still_unver['unreviewed_candidate_paths']
    reviewed = F.compare(pack, annotation, story, index=index, candidate_sha256='b' * 64,
                         review=_review(report, path))
    assert reviewed['summary']['fact']['human_annotated_recall'] == 1
    assert reviewed['semantic'] == 'human_review_record' and reviewed['structural_disagreements'] == 0
    # 人工确认不会抹掉与原标注的确定性字段差异，也不铸造语义passed。
    data = annotation.model_dump(mode='json')
    data['expectations']['invitation']['checks'][0]['value'] = 'established'
    changed = F.Annotation.model_validate(data)
    proposal = F.compare(pack, changed, story, index=index, candidate_sha256='b' * 64)
    conflict = F.compare(pack, changed, story, index=index, candidate_sha256='b' * 64,
                         review=_review(proposal, path))
    assert conflict['structural_disagreements'] == 1 and 'passed' not in conflict


@pytest.mark.parametrize('damage', ('source', 'annotation_scope', 'candidate_hash', 'annotation_hash', 'path'))
def test_comparison_refuses_drift_and_review_outside_bound_scope(damage):
    index, book, story, pack, annotation = _case()
    report = F.compare(pack, annotation, story, index=index, candidate_sha256='b' * 64)
    review = _review(report, ('propositions', book.proposition_handles['来客受邀']))
    if damage == 'source':
        pack = pack.model_copy(update={'content_sha256': 'c' * 64})
    elif damage == 'annotation_scope':
        annotation = annotation.model_copy(update={'scope_sha256': 'd' * 64})
    elif damage == 'candidate_hash':
        review = review.model_copy(update={'candidate_sha256': 'd' * 64})
    elif damage == 'annotation_hash':
        review = review.model_copy(update={'annotation_sha256': 'd' * 64})
    else:
        value = review.model_dump(mode='json')
        value['findings']['invitation']['candidate_paths'] = [['propositions', 'not_real']]
        review = F.Review.model_validate(value)
    with pytest.raises(ValueError):
        F.compare(pack, annotation, story, index=index, candidate_sha256='b' * 64, review=review)


def test_annotation_scope_and_not_applicable_cannot_hide_missing_work():
    index, _book, story, pack, annotation = _case()
    data = annotation.model_dump(mode='json')
    data['coverage']['thread'] = {'status': 'not_applicable', 'note': '', 'evidence_refs': []}
    with pytest.raises(ValueError, match='人工说明'):
        F.Annotation.model_validate(data)
    data = annotation.model_dump(mode='json')
    data['expectations']['invitation']['evidence_refs'] = ['not_in_frozen_scope']
    with pytest.raises(ValueError, match='scope'):
        F.compare(pack, F.Annotation.model_validate(data), story, index=index, candidate_sha256='b' * 64)


def test_entirely_omitted_source_segment_remains_in_gold_recall_denominator():
    index, _book, story, _pack, _annotation = _case()
    evidence = list(index.chapter(1))[1]
    data = story.model_dump(mode='json')
    omitted = next(key for key, row in data['events'].items() if evidence in row['evidence_refs'])
    del data['events'][omitted]
    # 这是一个机械合法但漏掉整段两个目标的候选；不伪造evidence条目撑满源分母。
    for order, row in enumerate((row for row in data['events'].values() if row['chapter_ordinal'] == 1), 1):
        row['order'] = order
    data['state_updates'] = [row for row in data['state_updates'] if row['at'] != omitted]
    del data['evidence'][evidence]
    assert evidence not in F._support(data)
    missing_story = F.SourceStory.model_validate(data)
    assert source_engine.validate_source_story(missing_story, index=index) == ()
    pack = F.freeze(index, sample='omission', split='dev', sid='42', source_relative='样书_42.txt',
                    source_raw_sha256='a' * 64, evidence_refs=[evidence])
    annotation = F.blank_annotation(pack).model_dump(mode='json')
    annotation['coverage']['event'] = {'status': 'annotated', 'note': '核对两个具体目标', 'evidence_refs': [evidence]}
    annotation['expectations'] = {'goals': {'category': 'event', 'statement': '决定查清身世并明日赶集',
        'presence': 'present', 'evidence_refs': [evidence], 'checks': []}}
    annotation = F.Annotation.model_validate(annotation)
    report = F.compare(pack, annotation, missing_story, index=index, candidate_sha256='b' * 64)
    assert report['candidates'] == []
    assert report['summary']['event']['positive_denominator'] == 1
    assert report['summary']['event']['human_annotated_recall'] is None
    review = report['review_template']
    review['reviewer'] = '人工事实核对'
    review['findings']['goals'] = {'verdict': 'missing', 'candidate_paths': [], 'note': '该段事件及其所有状态更新完全漏抽'}
    judged = F.compare(pack, annotation, missing_story, index=index, candidate_sha256='b' * 64,
                       review=F.Review.model_validate(review))
    assert judged['summary']['event']['human_annotated_recall'] == 0


def test_cli_prepares_annotations_then_compares_real_sealed_source_story(tmp_path, monkeypatch):
    from novel.config import Config
    from novel.core.source_index import build_source_index
    from novel.llm.story_requests import request_json
    from novel.llm.transport import ChatResponse, FakeTransport
    from novel.stages.chain import SYSTEM, assemble_paid_boundary, freeze_execution_profile, load_stage1_chapters, run_workflow
    from novel.stages.stage0.removal import run_source, _context
    from novel.stages.stage1.partition import run_partition
    from novel.work_units import initialize_workflow
    source_root, output, dataset = (tmp_path / name for name in ('data', 'output', 'evaluation'))
    source_root.mkdir()
    source = source_root / '样书_42.txt'
    original = '我站在门前。\n1\n我把孩子抱进屋。\n2\n我听见来客解释。\n'
    source.write_text(original, encoding='utf-8')
    book, _result, _report = run_source(source, output)
    run_partition(_context(book, 'stage1.partition'))
    stage1 = load_stage1_chapters(output, '42')
    index = build_source_index(stage1.chapters, stage1.hook_copy, source_identity=stage1.source_identity)
    monkeypatch.setenv('NOVEL_DATA', str(source_root))
    monkeypatch.delenv('NOVEL_CORPUS', raising=False)
    monkeypatch.setenv('NOVEL_OUTPUT', str(output))
    assert F.main(['--dataset-dir', str(dataset), 'prepare', '--sample', 'demo', '--split', 'dev',
                   '--sid', '42', '--source-relative', source.name, '--evidence', *index.evidence]) == 0
    # 标注准备不读取抽取结果，也不从候选自动制造人工金标。
    annotation = F.Annotation.model_validate(F._read(dataset / 'dev/demo/annotation.json'))
    assert annotation.expectations == {} and all(row.status == 'pending' for row in annotation.coverage.values())
    wires, observations = {}, {}
    for ch in index.chapter_ordinals:
        refs = list(index.chapter(ch))
        wires[f'ch{ch:02d}'] = {'contract_version': 'story-v1', 'context_needs': [],
            'referents': {'我': {'kind': 'person', 'label': '我', 'surface': '我', 'evidence_refs': [refs[0]]}},
            'events': {index.evidence[ref].text: {'summary': index.evidence[ref].text, 'participants': ['我'],
                'temporal': {'frame': 'current', 'anchor_event': None}, 'scene_boundary': 'unknown',
                'evidence_refs': [ref], 'claims': [], 'causal_hints': [], 'thread_hints': []} for ref in refs}}
        observations[ch] = source_engine.compile_chapter(wires[f'ch{ch:02d}'], index=index, chapter_ordinal=ch)
    evidence = next(iter(index.evidence))
    wires['book'] = {'contract_version': 'story-v1', 'entities': {'叙述者': {'kind': 'person', 'label': '叙述者',
        'surfaces': [{'literal': '我', 'evidence_refs': [evidence]}],
        'mention_refs': [next(iter(row.referents)) for row in observations.values()]}},
        'events': {key: {'temporal': row.temporal.model_dump(), 'scene_boundary': 'unknown'}
                   for chapter in observations.values() for key, row in chapter.events.items()},
        **{key: {} for key in ('propositions', 'knowledge_updates', 'state_slots', 'state_updates', 'threads', 'world_rules')},
        **{key: [] for key in ('exposures', 'causal_links', 'conflicts', 'unresolved', 'source_issues')},
        'expression': {'person': 'first', 'focalizers': ['叙述者'], 'statement': '第一人称叙述',
                       'mechanisms': [], 'evidence_refs': [evidence]}}
    observed = source_engine.compile_book(wires['book'], index=index, chapters=observations)
    wires['names'] = {'contract_version': 'story-v1', 'people': {next(iter(observed.story.entities)):
        {'family_group': None, 'family': None, 'given': None, 'aliases': [], 'pure_titles': ['我']}}}
    initialize_workflow(output)
    active = {}
    transport = FakeTransport(lambda request, attempt: ChatResponse(
        content=json.dumps(wires[active['owner']], ensure_ascii=False), finish_reason='stop', model=request.model))
    profile = freeze_execution_profile(cfg=Config(), reasoning_effort='high', base=output, sid='42')
    boundary = assemble_paid_boundary(base=output, run_dir=book, source_identity=index.source_identity,
        source_content_sha256=index.content_sha256, root_sha1=stage1.root_sha1,
        profile=profile, yes=True, allow_source_export=True, transport=transport)
    def ask(owner, prompt, compiler, *, index=None, max_chapter=None, system=SYSTEM, revision_seed=None):
        active['owner'] = owner
        request = boundary.build_request(owner, prompt, json_mode=True, system=system, temperature=.4)
        return request_json(boundary.session, owner, request, compiler, repository=boundary.repository,
                            index=index, max_chapter=max_chapter, revision_seed=revision_seed)
    run_workflow(repository=boundary.repository, chapters=stage1.chapters, hook_copy=stage1.hook_copy,
                 ask=ask, through='stage2', source_identity=index.source_identity, concurrency=1)
    heldout_chapters = deepcopy(stage1.chapters)
    heldout_chapters[0]['text'] += '\n另一部书的材料。'
    heldout_index = build_source_index(heldout_chapters, stage1.hook_copy, source_identity='heldout-source')
    heldout = F.freeze(heldout_index, sample='secret', split='heldout', sid='99',
        source_relative='保留书_99.txt', source_raw_sha256='d' * 64, evidence_refs=list(heldout_index.evidence))
    F.register(dataset, heldout)
    original_read = F._read
    def selected_only(path):
        assert Path(path) != dataset / 'heldout/secret/source.json', '开发比较不能隐式加载保留集原文'
        return original_read(path)
    monkeypatch.setattr(F, '_read', selected_only)
    before = {path: path.read_bytes() for path in output.rglob('*') if path.is_file()}
    assert F.main(['--dataset-dir', str(dataset), 'compare', '--sample', 'demo']) == 0
    report = F._read(dataset / 'dev/demo/comparison.json')
    assert report['semantic'] == 'unver' and report['candidates']
    assert {path: path.read_bytes() for path in output.rglob('*') if path.is_file()} == before
    assert source.read_text(encoding='utf-8') == original
    source.write_text(original + '原文变动', encoding='utf-8')
    with pytest.raises(ValueError, match='源身份'):
        F.main(['--dataset-dir', str(dataset), 'compare', '--sample', 'demo'])


def test_tool_entrypoint_and_explicit_independent_roots(tmp_path, monkeypatch):
    result = subprocess.run([sys.executable, '-B', '-m', 'tools.story_facts', '--help'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0 and 'prepare' in result.stdout and 'compare' in result.stdout
    for key in ('NOVEL_DATA', 'NOVEL_CORPUS', 'NOVEL_OUTPUT'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match='没查'):
        F._roots(tmp_path)
    source, output = tmp_path / 'source', tmp_path / 'output'
    source.mkdir()
    output.mkdir()
    monkeypatch.setenv('NOVEL_DATA', str(source))
    monkeypatch.setenv('NOVEL_OUTPUT', str(output))
    with pytest.raises(ValueError, match='独立目录'):
        F._roots(output / 'evaluation')


def test_manual_json_duplicate_keys_cannot_silently_reduce_annotation_denominator(tmp_path):
    path = tmp_path / 'annotation.json'
    path.write_text('{"expectations":{"same":1,"same":2}}', encoding='utf-8')
    with pytest.raises(ValueError, match='重复键'):
        F._read(path)
