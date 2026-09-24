"""Stage2→Stage4 RC2 真实隔离入口的离线阶段测试（语义 wire 固定，不请求 LLM）。"""
from copy import deepcopy

from novel.store import Str06Repository
from tools.str06_workflow import run_workflow
from tests.test_str06_target import _source_pair, _preserve_wire, _design_wire


def test_stage2_stage4_repository_replay_and_writer_consumer(tmp_path):
    chars, bp = _source_pair()
    wires = {
        'book': {**chars.model_dump(mode='json'), 'world_constraints': []},
        'ch01': {'beats': [b.model_dump(mode='json') for b in bp.chapters[0].beats], 'causal_edges': []},
        'names': {
            'families': {'fam_yun': {'literal': '云', 'forms': []}},
            'people': {
                'fu_jun': {'family_id': None, 'given': None, 'aliases': [],
                           'surface_forms': {'夫君': None}},
                'yun': {'family_id': 'fam_yun', 'given': '娘', 'aliases': [],
                        'surface_forms': {'云娘': '{family}{given}'}},
            }},
        'preserve': _preserve_wire(), 'design': _design_wire(),
        'bind': {'entries': [{'obligation_id': f'ob{i:02}', 'scene_id': 's01', 'beat_ref': 's01#bt1'}
                             for i in range(1, 4)], 'unbound': []},
    }
    requests = []
    def ask(owner, prompt, validate):
        data = deepcopy(wires[owner])
        assert not validate(data), (owner, validate(data))
        requests.append((owner, prompt))
        return data
    repo = Str06Repository(tmp_path / 'book')
    chapters = [{'chapter_ordinal': 1, 'text': '云娘提出约定，夫君知晓真相。'}]
    report = run_workflow(repository=repo, chapters=chapters, ask=ask,
                          ask_text=lambda owner, prompt: (_ for _ in ()).throw(
                              AssertionError('stage4 干跑不应调用文本 owner')),
                          budget=800)
    assert report['guard_errors'] == [] and report['accepted_body'] is False
    context = repo.read('stage4/context/s01.json')
    assert len(context['verification_ir']['obligations']) == 3
    assert any('约定被提出且被接受' in x for x in context['writer_brief']['required_content'])
    assert context['writer_brief']['pov'] == 'yun'
    assert len(repo.read('stage4/grounding/reconstruction_brief.json')['requirements']) == 3
    design_prompt = next(prompt for owner, prompt in requests if owner == 'design')
    assert '知晓时刻与反应' in design_prompt and '夫君身份约束' in design_prompt
    assert 'b01_01' not in design_prompt
    binding_prompt = next(prompt for owner, prompt in requests if owner == 'bind')
    assert 'dramatic_intent' in binding_prompt and 'target_delta' in binding_prompt
    before = repo.path('stage4/context/s01.json').read_bytes()
    run_workflow(repository=repo, chapters=chapters, ask=ask,
                 ask_text=lambda owner, prompt: (_ for _ in ()).throw(
                     AssertionError('stage4 干跑不应调用文本 owner')), budget=800)
    assert repo.path('stage4/context/s01.json').read_bytes() == before
    assert repo.read('runtime/workflow_status.json')['status'] == 'planned_validated'
    def rejected(owner, prompt, validate):
        raise ValueError('book 输入待免费修复')
    import pytest
    with pytest.raises(ValueError, match='免费修复'):
        run_workflow(repository=repo, chapters=chapters, ask=rejected,
                     ask_text=lambda owner, prompt: None)
    assert repo.read('runtime/workflow_status.json')['status'] == 'failed'
    assert repo.read('runtime/workflow_status.json')['accepted_body'] is False


def test_rc_repository_rejects_traversal_and_unregistered_family(tmp_path):
    import pytest
    from novel.errors import PathRefused
    repo = Str06Repository(tmp_path)
    for rel in ('../escape.json', 'unregistered/nested/file.json', 'other/file.json'):
        with pytest.raises(PathRefused):
            repo.write(rel, {'bad': True})
