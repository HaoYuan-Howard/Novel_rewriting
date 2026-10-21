"""首次候选结论不可被免费重验覆盖，合法材料请求与失败分开。"""
from copy import deepcopy

from novel.story_metrics import story_metrics
from novel.llm.transport import ChatResponse
from tests.story_facilities import make_pipeline, pipeline_materials


def test_material_control_has_cost_and_does_not_fail_first_semantic_candidate(tmp_path):
    _, index, _ = pipeline_materials()
    control = {'kind': 'need_materials', 'requests': [
        {'operation': 'read_segments', 'segment_ids': [next(iter(index.evidence))]}]}
    case = make_pipeline(tmp_path, scripts={'book': [control]})
    case.run()
    report = story_metrics(tmp_path, 'book')
    assert case.calls.count('book') == 2
    assert report['totals']['material_responses'] == 1
    assert report['totals'].get('first_failed', 0) == 0
    assert report['first_semantic_candidate_pass_rate'] == 1
    assert report['totals']['first_response_failed'] == 1
    assert report['first_response_direct_pass_rate'] < 1
    assert report['first_response_denominator'] == report['totals']['units']
    assert report['totals']['new_paid_calls'] == len(case.calls)
    assert report['source_quality'] == report['novel_quality'] == 'unver'


def test_free_revalidation_preserves_initial_rejection_and_patch_denominator(tmp_path):
    _, _, wires = pipeline_materials()
    bad = deepcopy(wires['design'])
    bad['premise'] = {'invalid': 'wrong type'}
    patch = {'patches': [{'path': ['premise'], 'value': wires['design']['premise']}]}
    case = make_pipeline(tmp_path, scripts={'design': [bad, patch]})
    case.run()
    before = story_metrics(tmp_path, 'book')
    assert before['totals']['first_failed'] == 1
    assert before['totals']['patch_responses'] == 1
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run()
    after = story_metrics(tmp_path, 'book')
    assert resumed.calls == []
    assert after['first_semantic_candidate_pass_rate'] == before['first_semantic_candidate_pass_rate']
    assert after['first_candidate_denominator'] == before['first_candidate_denominator']
    assert after['first_response_direct_pass_rate'] == before['first_response_direct_pass_rate']
    assert after['totals']['new_paid_calls'] == before['totals']['new_paid_calls']


def test_format_failure_cannot_be_counted_as_a_first_complete_candidate(tmp_path):
    malformed = ChatResponse(content='不是 JSON', finish_reason='stop',
        usage={'prompt_tokens': 12, 'completion_tokens': 8}, model='fake')
    case = make_pipeline(tmp_path, scripts={'design': [malformed]})
    case.run()
    report = story_metrics(tmp_path, 'book')
    assert report['totals']['format_failure_responses'] == 1
    assert report['totals']['first_response_failed'] == 1
    assert report['first_semantic_candidate_pass_rate'] == 1
    assert report['first_response_direct_pass_rate'] < 1
