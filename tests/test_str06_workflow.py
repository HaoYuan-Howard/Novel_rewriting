"""Stage2→Stage4 RC4 真实隔离入口的离线阶段测试（语义 wire 固定，不请求 LLM）。"""
import json
from copy import deepcopy

import pytest

from contracts.artifacts import STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
from novel.llm.ledger import CostLedger
from novel.store import Str06Repository
from novel.stages.chain import run_workflow
from tests.str06_facilities import make_context, v13_beat, v13_chapter
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
        repo.context.note_adoption(owner=owner, model='fake',
                                   prompts={'user': prompt})
        return data
    def ask_text(owner, prompt):
        raise AssertionError('stage4 干跑不应调用文本 owner')
    repo = Str06Repository(tmp_path / 'book')
    repo.attach_context(make_context(tmp_path / 'book', ledger=CostLedger(path=None)))
    chapters = [{'chapter_ordinal': 1, 'text': '云娘提出约定，夫君知晓真相。'}]
    report = run_workflow(repository=repo, chapters=chapters, ask=ask,
                          ask_text=ask_text,
                          ask_body=lambda owner, prompt: (_ for _ in ()).throw(
                              AssertionError('stage4 干跑不应调用正文 owner')),
                          budget=800)
    assert report['guard_errors'] == [] and report['accepted_body'] is False
    # WriterBookIR 唯一落盘；场景上下文嵌入其中（不再单独写场景文件）
    writer_ir = repo.inspect('stage4/ir/writer_book_ir.json')
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
    assert len(repo.inspect('stage4/grounding/reconstruction_brief.json')['requirements']) == 3
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


# ───────────────────────── 章前文字（源引子）进 Stage2（C 批） ─────────────────────────

def _write_stage1(base, sid, *, chapters, hook):
    """按正式契约信封写 Stage1 两份材料（只供本文件的身份反例）。"""
    from contracts.schema import encode_artifact

    book = base / sid / 'stage1'
    book.mkdir(parents=True, exist_ok=True)
    (book / 'main_text.json').write_bytes(
        json.dumps(encode_artifact(STAGE1_MAIN_TEXT, chapters),
                   ensure_ascii=False).encode('utf-8'))
    (book / 'hook_copy.json').write_bytes(
        json.dumps(encode_artifact(STAGE1_HOOK_COPY, {'text': hook}),
                   ensure_ascii=False).encode('utf-8'))
    return book


def test_stage1_source_identity_covers_hook_copy(tmp_path):
    """组合源身份：只改引子也换身份——旧根不得沿用旧购买（C 批）。"""
    from contracts.artifacts import STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
    from novel.stages.chain import load_stage1_chapters

    base = tmp_path / 'out'
    base.mkdir()
    chapters = [{'chapter_ordinal': 1, 'text': '云娘提出约定，夫君知晓真相。'},
                {'chapter_ordinal': 2, 'text': '第二夜窗外有响动。'}]
    _write_stage1(base, '9001', chapters=chapters, hook='新婚夜，夫君坦白借种之约。')
    first = load_stage1_chapters(base, '9001')
    assert first.hook_copy == '新婚夜，夫君坦白借种之约。'
    # 同字节复用：身份稳定
    assert load_stage1_chapters(base, '9001').source_identity == first.source_identity
    # 只改 hook_copy：源身份与根 SHA-1 都变
    _write_stage1(base, '9001', chapters=chapters, hook='改过的引子。')
    changed = load_stage1_chapters(base, '9001')
    assert changed.hook_copy == '改过的引子。'
    assert changed.source_identity != first.source_identity
    assert changed.root_sha1 != first.root_sha1
    # 只改正文：同样换身份（两份字节各自参与）
    _write_stage1(base, '9001', chapters=[chapters[0], {'chapter_ordinal': 2,
                                                        'text': '第二夜平安无事。'}],
                  hook='新婚夜，夫君坦白借种之约。')
    assert load_stage1_chapters(base, '9001').source_identity != first.source_identity


def test_changed_hook_copy_refuses_the_old_root_and_changes_requests(tmp_path):
    """只改引子：候选根复校拒绝；book 与 ch01 的请求身份随之变化。"""
    from novel.llm.paid_requests import (
        ProvenanceRefused, digest, init_or_resume_candidate_workflow,
    )
    from novel.stages.chain import run_workflow

    chapters = [{'chapter_ordinal': 1, 'text': '云娘提出约定，夫君知晓真相。'},
                {'chapter_ordinal': 2, 'text': '第二夜窗外有响动。'}]
    wires = {
        'book': {'characters': {
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'from_chapter': 1, 'until_chapter': None,
                 'source_refs': ['e01_01']}]},
            'yun': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'from_chapter': 1, 'until_chapter': None,
                 'source_refs': ['e01_01']}]}},
            'initial_state': [], 'world_constraints': []},
        'ch01': v13_chapter(v13_beat('b01_01', '约定成立',
                                     participants=('yun', 'fu_jun'))),
        'ch02': v13_chapter(v13_beat('b02_01', '夜半响动', participants=('yun',),
                                     source_refs=('e02_01',))),
    }
    def run(hook_copy):
        base = tmp_path / f'out_{abs(hash(hook_copy))}'
        base.mkdir(exist_ok=True)
        repo = Str06Repository(base / 'run')
        repo.attach_context(make_context(base / 'run', ledger=CostLedger(path=None)))
        prompts: dict[str, str] = {}

        def ask(owner, prompt, validate):
            prompts[owner] = prompt
            repo.context.note_adoption(owner=owner, model='fake',
                                       prompts={'user': prompt})
            return deepcopy(wires[owner])

        report = run_workflow(
            repository=repo, chapters=chapters, hook_copy=hook_copy, ask=ask,
            ask_text=lambda owner, prompt: (_ for _ in ()).throw(
                AssertionError('本反例不跑文本 owner')),
            ask_body=lambda owner, prompt: (_ for _ in ()).throw(
                AssertionError('本反例不跑正文 owner')),
            budget=800, through='stage2', node_filter=['str06.stage2.source'])
        return base, repo, prompts, report

    with_hook, repo_hook, prompts_hook, _ = run('新婚夜，夫君坦白借种之约。')
    without_hook, repo_plain, prompts_plain, _ = run('')
    # e00 证据只进有引子臂的 book 与 ch01；ch02 两臂一致
    assert 'e00_01' in prompts_hook['book'] and '章前文字' in prompts_hook['book']
    assert 'e00_01' in prompts_hook['ch01'] and '章前文字分流' in prompts_hook['ch01']
    assert 'e00_01' not in prompts_plain['book'] and 'e00_01' not in prompts_plain['ch01']
    assert prompts_hook['ch02'] == prompts_plain['ch02'], '其余各章不得因引子变化'
    # run 级身份覆盖引子
    assert (repo_hook.read('runtime/workflow_status.json')['input_sha256']
            != repo_plain.read('runtime/workflow_status.json')['input_sha256'])

    # 候选根复校：初始化后用另一个源身份复校 → 关闭式拒绝
    init_or_resume_candidate_workflow(
        repository=repo_hook, base=with_hook, code='code-x', root_sha1='a' * 40,
        source_identity='b' * 64, task_id='task-1', configured_model='fake',
        created_at=1759000000)
    with pytest.raises(ProvenanceRefused, match='源输入身份'):
        init_or_resume_candidate_workflow(
            repository=repo_hook, base=with_hook, code='code-x', root_sha1='c' * 40,
            source_identity='d' * 64, task_id='task-1', configured_model='fake',
            created_at=1759000001)
    assert digest  # 身份函数同源引用（本反例不另算一份）


def test_concealed_fact_reaches_writer_as_pending_reader_confirmation(tmp_path):
    """2F：源侧 concealed 效应＋读者揭示 → Preserve reveal 义务 → Design 在确认拍放
    reader delta → 写手章包逐场列出「尚未向读者确认的事实」（D15 全链）。

    断言：确认前各场清单列出该事实；确认场标注确认拍；fold 的未揭示报告为空
    （读者确已在该拍确认）。
    """
    book = {
        'characters': {
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'from_chapter': 1, 'until_chapter': None,
                 'source_refs': ['e01_01']}]},
            'yun': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'from_chapter': 1, 'until_chapter': None,
                 'source_refs': ['e01_01']}]}},
        'initial_state': [], 'world_constraints': []}
    ch01 = v13_chapter(
        v13_beat('b01_01', '夫君佯称离开，实则藏身床底', participants=('fu_jun',),
                 effects=({'target': {'kind': 'character', 'char_id': 'fu_jun'},
                           'key': 'scene_location', 'op': 'set', 'value': '床底',
                           'scope': 'scene',
                           'disclosure': {'visibility': 'concealed',
                                          'known_by': ['fu_jun']},
                           'proposition': {'ref': 'p_hide',
                                           'statement': '夫君藏身床底'}},)),
        v13_beat('b01_02', '床板响动，读者与云娘确认床底有人',
                 participants=('yun', 'fu_jun'), boundary='continue',
                 cognition=({'proposition': {'ref': 'p_hide'},
                             'holder': 'reader', 'mode': 'learns'},)))
    design = {
        'initial_state': [],
        'scenes': [
            {'scene_id': 's01', 'state_reads': [],
             'driver': {'focal_actor': 'yun', 'immediate_want': '确认声响来源',
                        'opposition': '无人可问', 'turn': '决意亲自去看'},
             'realization_brief': '重心在夜半响动后的试探；压缩寻问无果，'
                                  '在决意亲自去到处收束，衔接下一场的揭示。',
             'beats': [{'beat_id': 'bt1', 'event': '夜半响动', 'participants': ['yun'],
                        'dramatic_intent': '加压', 'target_delta': [], 'delivery_hint': ''}],
             'delivery': {'pov': 'yun', 'voice': '冷峻', 'avoid': []}},
            {'scene_id': 's02', 'state_reads': [],
             'driver': {'focal_actor': 'yun', 'immediate_want': '看清床底',
                        'opposition': '恐惧', 'turn': '确认'},
             'realization_brief': '重心在床底确认瞬间；从俯身写到确认，'
                                  '在反应处收住。',
             'beats': [{'beat_id': 'bt1', 'event': '确认床底有人',
                        'participants': ['yun', 'fu_jun'], 'dramatic_intent': '揭示',
                        'target_delta': [
                            {'domain': 'reader', 'subjects': [], 'op': 'set',
                             'scope': 'scene', 'key': 'hiding', 'value': '床底有人'}],
                        'delivery_hint': ''}],
             'delivery': {'pov': 'yun', 'voice': '冷峻', 'avoid': []}}],
        'chapters': [{'chapter_ordinal': 1, 'purpose': '夜半响动到床底确认',
                      'scene_ids': ['s01', 's02']}],
        'chapter_weights': {'s01': 0.5, 's02': 0.5}}
    preserve = {'obligations': [
        {'obligation_id': 'ob01', 'kind': 'event_core',
         'statement': '夫君佯称离开实则藏身床底',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': ['fu_jun']},
         'must_preserve': True,
         'satisfaction': '正文呈现佯离与藏身的关系', 'requires': []},
        {'obligation_id': 'ob02', 'kind': 'reveal',
         'statement': '读者在床板响动后确认床底有人',
         'lineage': {'blueprint_refs': ['b01_02'], 'character_ids': ['yun', 'fu_jun']},
         'must_preserve': True,
         'satisfaction': '正文呈现确认时点与反应', 'requires': ['ob01']}]}
    wires = {
        'book': book, 'ch01': ch01,
        'names': {
            'families': {'fam_yun': {'literal': '云', 'forms': []}},
            'people': {
                'fu_jun': {'family_id': None, 'given': None, 'aliases': [],
                           'surface_forms': {'夫君': None}},
                'yun': {'family_id': 'fam_yun', 'given': '娘', 'aliases': [],
                        'surface_forms': {'云娘': '{family}{given}'}}}},
        'preserve': preserve, 'design': design,
        'bind': {'entries': [
            {'obligation_id': 'ob01', 'scene_id': 's01', 'beat_ref': 's01#bt1'},
            {'obligation_id': 'ob02', 'scene_id': 's02', 'beat_ref': 's02#bt1'}],
            'unbound': []},
        'hook_design': {
            'pov': {'person': 'first', 'focalizers': ['yun']},
            'materials': [{'beat_ref': 's02#bt1', 'withheld': []}],
            'surface_releases': [], 'forbidden_reveals': [],
            'design_brief': '从夜半响动切入，快速带过藏身，收在床底确认。',
            'delivery_brief': '第一人称限知，短句，不解释。'},
    }
    repo = Str06Repository(tmp_path / 'book')
    repo.attach_context(make_context(tmp_path / 'book', ledger=CostLedger(path=None)))
    requests = []

    def ask(owner, prompt, validate):
        data = deepcopy(wires[owner])
        assert not validate(data), (owner, validate(data))
        requests.append((owner, prompt))
        repo.context.note_adoption(owner=owner, model='fake', prompts={'user': prompt})
        return data

    report = run_workflow(
        repository=repo, chapters=[{'chapter_ordinal': 1,
                                    'text': '夫君与云娘一处住着。夜半床板响动，云娘确认床底有人。'}],
        ask=ask,
        ask_text=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('不跑文本 owner')),
        ask_body=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('不跑正文 owner')),
        budget=800)
    assert report['guard_errors'] == []
    # 源侧 fold：读者已在 b01_02 确认 ⇒ 未揭示 concealed 报告为空
    fold = repo.inspect('stage2/acceptance/fold.json')
    assert fold['concealed_unrevealed'] == []
    assert fold['chapters']['1']['empty_transition_beats'] == 0
    # 写手 IR：确认前（s01）列出该事实；确认场（s02）标注确认拍
    writer_ir = repo.inspect('stage4/ir/writer_book_ir.json')
    s01, s02 = writer_ir['chapters'][0]['scenes']
    assert [(p['fact'], p['position'], p['in_scene'])
            for p in s01['verification_ir']['pending_reader_confirmations']] == [
        ('床底有人', 's02#bt1', False)]
    assert [(p['fact'], p['position'], p['in_scene'])
            for p in s02['verification_ir']['pending_reader_confirmations']] == [
        ('床底有人', 's02#bt1', True)]
    # 渲染进写手阅读面
    from novel.stages.common import render_chapter_packet
    from novel.store import Str06Repository as _Repo  # noqa: F401（仅示意依赖方向）
    book_ir = repo.read_model('stage4/ir/writer_book_ir.json')
    packet = render_chapter_packet(book_ir, 1, {})
    assert '床底有人（hiding）——s02#bt1 才可确认' in packet
    # Preserve 提示词带着揭示时点要求（v3 规则）
    preserve_prompt = next(p for o, p in requests if o == 'preserve')
    assert '谁在何时确认' in preserve_prompt
