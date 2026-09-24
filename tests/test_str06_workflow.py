"""Stage2→Stage4 RC4 真实隔离入口的离线阶段测试（语义 wire 固定，不请求 LLM）。"""
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
        # F5 起 Hook Design 属 Stage4 腿：完整 graph 与 WriterIR 一起发布
        'hook_design': {
            'pov': {'person': 'first', 'focalizers': ['yun']},
            'materials': [{'beat_ref': 's01#bt1', 'withheld': []}],
            'surface_releases': [],
            'forbidden_reveals': [],
            'design_brief': '从约定成立的瞬间切入，快速带过背景，收在立约落地。',
            'delivery_brief': '第一人称限知，短句，不解释。',
        },
    }
    requests = []
    def ask(owner, prompt, validate):
        data = deepcopy(wires[owner])
        assert not validate(data), (owner, validate(data))
        requests.append((owner, prompt))
        return data
    def ask_text(owner, prompt):
        raise AssertionError('stage4 干跑不应调用文本 owner')
    repo = Str06Repository(tmp_path / 'book')
    chapters = [{'chapter_ordinal': 1, 'text': '云娘提出约定，夫君知晓真相。'}]
    report = run_workflow(repository=repo, chapters=chapters, ask=ask,
                          ask_text=ask_text,
                          ask_body=lambda owner, prompt: (_ for _ in ()).throw(
                              AssertionError('stage4 干跑不应调用正文 owner')),
                          budget=800)
    assert report['guard_errors'] == [] and report['accepted_body'] is False
    # WriterBookIR 唯一落盘；场景上下文嵌入其中（不再单独写场景文件）
    writer_ir = repo.read('stage4/ir/writer_book_ir.json')
    assert writer_ir['book_brief']['target_chapter_count'] == 1
    chapter_ir = writer_ir['chapters'][0]
    assert chapter_ir['purpose'] == '婚姻从名分走向盟约'
    assert chapter_ir['length_budget'] == 800
    context = chapter_ir['scenes'][0]
    assert len(context['verification_ir']['obligations']) == 3
    assert context['writer_brief']['realization_brief'].startswith('重心在')
    assert context['verification_ir']['beats'][0]['beat_ref'] == 's01#bt1'
    assert any('约定被提出且被接受' in o['satisfaction']
               for o in context['verification_ir']['obligations'])
    assert context['writer_brief']['pov'] == 'yun'
    assert len(repo.read('stage4/grounding/reconstruction_brief.json')['requirements']) == 3
    design_prompt = next(prompt for owner, prompt in requests if owner == 'design')
    assert '知晓时刻与反应' in design_prompt and '夫君身份约束' in design_prompt
    assert 'b01_01' not in design_prompt
    assert 'realization_brief' in design_prompt and 'purpose' in design_prompt
    binding_prompt = next(prompt for owner, prompt in requests if owner == 'bind')
    assert 'dramatic_intent' in binding_prompt and 'target_delta' in binding_prompt
    before = repo.path('stage4/ir/writer_book_ir.json').read_bytes()
    run_workflow(repository=repo, chapters=chapters, ask=ask,
                 ask_text=ask_text,
                 ask_body=lambda owner, prompt: (_ for _ in ()).throw(
                     AssertionError('stage4 干跑不应调用正文 owner')),
                 budget=800)
    assert repo.path('stage4/ir/writer_book_ir.json').read_bytes() == before
    assert repo.read('runtime/workflow_status.json')['status'] == 'planned_validated'
    def rejected(owner, prompt, validate):
        raise ValueError('book 输入待免费修复')
    import pytest
    with pytest.raises(ValueError, match='免费修复'):
        run_workflow(repository=repo, chapters=chapters, ask=rejected,
                     ask_text=ask_text, ask_body=lambda owner, prompt: None)
    assert repo.read('runtime/workflow_status.json')['status'] == 'failed'
    assert repo.read('runtime/workflow_status.json')['accepted_body'] is False


def test_rc_repository_rejects_traversal_and_unregistered_family(tmp_path):
    import pytest
    from novel.errors import PathRefused
    repo = Str06Repository(tmp_path)
    for rel in ('../escape.json', 'unregistered/nested/file.json', 'other/file.json'):
        with pytest.raises(PathRefused):
            repo.write(rel, {'bad': True})
