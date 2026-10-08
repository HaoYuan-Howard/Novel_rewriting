"""Stage2→Stage4 RC4 真实隔离入口的离线阶段测试（语义 wire 固定，不请求 LLM）。

B2 五节点（D9）：Stage2 的 fake wire 是观察协议形状（book/chNN/associate），
canonical 双真源由 `str06.stage2.compile` 免费发布；Stage4 只读 canonical。
"""
import json
from copy import deepcopy

import pytest

from contracts.artifacts import STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
from novel.llm.ledger import CostLedger
from novel.store import Str06Repository
from novel.stages.chain import run_workflow
from tests.str06_facilities import make_context, synthetic_ref
from tests.test_str06_target import _preserve_wire, _design_wire


def _kp_from_design_prompt(prompt: str, statement: str) -> str:
    """从 design prompt 内嵌的 Brief 里取命题的 kp 句柄（模型按 prompt 作答）。

    命题正式 ref 由 stage2 compile 按序派发（k{nn}），fixture 无法静态预计算
    kp——ask 回调按「模型读到什么就用什么」从 prompt 的 brief JSON 解出。
    """
    import json as _json
    payload = _json.loads(prompt.split('输入：\n', 1)[1])
    return next(p['kp_id'] for p in payload['brief']['propositions']
                if p['statement'] == statement)


from tests.str06_expression_inputs import (
    sample_book_expression, sample_design_expression,
)

PV = 'stage2-chapters-v7'   # R10a a1：章观察独立协议字面量（names 无字面量面）


def _design_wire_v7():
    """stage4-design-v8（CS-3）：real 承载标注替 bind；义务 ob01–ob03 的接受拍
    都是首拍（与原 bind wire 等价）；kp 知情行经 ob02 的锁派生，不写在 wire。"""
    return {
        'protocol_version': 'stage4-design-v8',
        'book_style': '冷峻克制的第三人称叙事，重对话张力。',
        'character_voices': [{'char_id': 'yun', 'voice': '冷峻'}],
        'initial_state': [],
        'scenes': [
            {'driver': {'focal_actor': 'yun', 'immediate_want': '保全婚姻',
                        'opposition': '家族压力', 'turn': '约定'},
             'brief': '重心在约定成立的承诺瞬间；从家族施压的晚宴写起，'
                      '展开两人独处的对峙，在立约处收住，衔接下一场的兑现。',
             'reads': [],   # v6：narration 派生自源叙述（focalizers=narrator），pov 缺省 narrator
             'beats': [
                 {'ev': '约定成立', 'who': ['yun', 'fu_jun'],
                  'real': ['ob01', 'ob02', 'ob03'],
                  'state': [{'at': 'yun+fu_jun', 'key': 'marital_status',
                             'val': '夫妻'}]}],
             }],
        'chapters': [{'purpose': '婚姻从名分走向盟约', 'scenes': 1}],
    }


def _obs_beat(event, refs, **over):
    beat = {'event': event, 'participants': [], 'source_refs': list(refs),
            'scene_boundary': 'new', 'temporal_mode': 'single',
            'effects': [], 'cognition': [], 'loops': []}
    beat.update(over)
    return beat


def test_stage2_stage4_repository_replay_and_writer_consumer(tmp_path):
    book = {**sample_book_expression({
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}]},
        'yun': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_01']}]}}), 'protocol_version': 'stage2-book-v7', 'characters': {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}]},
        'yun': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_01']}]}},
        'initial_state': [
            {'target': {'kind': 'character', 'char_id': 'fu_jun'},
             'key': 'identity', 'value': '养子', 'persistence': 'stable',
             'source_refs': ['e01_01'],
             'basis': [{'segment': 'e01_01', 'quote': '云娘提出约定'}]}],
        'world_constraints': []}
    wires = {
        'book': book,
        'ch01': {'protocol_version': PV, 'beats': [
            _obs_beat('云娘提出约定，夫君知晓真相。', ('e01_01',),
                      participants=('yun', 'fu_jun'),
                      effects=[{'target': {'kind': 'relation', 'subject': 'fu_jun',
                                           'object': 'yun'},
                                'key': 'agreement', 'op': 'set', 'value': '成立',
                                'modality': 'actual',
                                'basis': [{'segment': 'e01_01',
                                           'quote': '云娘提出约定'}]}],
                      cognition=[{'proposition': {'ref': 'p_known',
                                                  'statement': '借种之约是共同秘密'},
                                  'holder': 'yun', 'mode': 'learns',
                                  'basis': [{'segment': 'e01_01',
                                             'quote': '云娘提出约定'}]}])]},
        'names': {
            'protocol_version': 'stage2-names-v6',
            'families': {'fam_yun': {'literal': '云', 'forms': []}},
            'people': {
                'fu_jun': {'family_id': None, 'given': None, 'aliases': []},
                'yun': {'family_id': 'fam_yun', 'given': '娘', 'aliases': []}},
        },
        'associate': {'protocol_version': 'stage2-associate-v5',
                      'same_proposition': [],
                      'loop_pairing': [], 'cross_chapter_cause': [],
                      'scene_continuation': []},
        'cross_cause': {'protocol_version': 'stage2-cross-cause-v1', 'causes': []},
        'preserve': _preserve_wire(), 'design': _design_wire_v7(),
        # F5 起 Hook Design 属 Stage4 腿：完整 graph 与 WriterIR 一起发布
        'hook_design': {
            'protocol_version': 'stage4-hook-design-v1',
            'pov': {'person': 'first', 'focalizers': ['yun']},
            'materials': [{'beat_ref': {'scene': 'sc01', 'beat': 'b01'}, 'withheld': []}],
            'surface_releases': [],
            'forbidden_reveals': [],
            'design_brief': '从约定成立的瞬间切入，快速带过背景，收在立约落地。',
            'delivery_brief': '第一人称限知，短句，不解释。',
        },
    }
    wires['design']['initial_surfaces'] = [{'char_id': 'yun', 'surfaces': ['云娘']},
                                          {'char_id': 'fu_jun', 'surfaces': ['夫君']}]
    wires['design']['scenes'][0]['beats'][0]['know'] = [{'ref': 'ob02.k01'}]
    requests = []
    def ask(owner, prompt, validate, patch=None, **kwargs):
        data = deepcopy(wires[owner])
        assert not validate(data), (owner, validate(data))
        requests.append((owner, prompt))
        return data, synthetic_ref(owner, prompt)
    def ask_text(owner, prompt, validate=None):
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
    # canonical 双真源由 compile 发布；观察与派生输入退役面不在盘上
    assert repo.inspect('stage2/characters/characters.json')['contract_version']
    assert repo.inspect('stage2/blueprints/chapter_blueprints.json')['chapters']
    assert not (tmp_path / 'book/stage2/compiled/book_extraction.json').exists()
    # WriterBookIR 唯一落盘；场景上下文嵌入其中（不再单独写场景文件）
    writer_ir = repo.inspect('stage4/ir/writer_book_ir.json')
    assert writer_ir['book_brief']['target_chapter_count'] == 1
    chapter_ir = writer_ir['chapters'][0]
    assert chapter_ir['purpose'] == '婚姻从名分走向盟约'
    assert chapter_ir['length_budget'] == 800
    context = chapter_ir['scenes'][0]
    assert len(context['verification_ir']['obligations']) == 3
    assert context['writer_brief']['realization_brief'].startswith('重心在')
    assert context['verification_ir']['beats'][0]['beat_ref'] == 'sc01#b01'
    assert any('约定被提出且被接受' in o['satisfaction']
               for o in context['verification_ir']['obligations'])
    assert context['writer_brief']['pov'] == 'narrator'   # v6：narration←源叙述
    assert len(repo.inspect('stage4/grounding/reconstruction_brief.json')['requirements']) == 3
    design_prompt = next(prompt for owner, prompt in requests if owner == 'design')
    assert '知晓时刻与反应' in design_prompt and '夫君身份约束' in design_prompt
    assert 'b01_01' not in design_prompt
    import json
    design_input = json.loads(design_prompt.split('输入：\n', 1)[1])
    assert design_input['brief']['input_version'] == 'stage4-design-input-v1'
    assert 'obligation_table' not in design_input
    assert set(design_input['brief']['requirements']) == {'ob01', 'ob02', 'ob03'}
    assert 'realization_brief' in design_prompt and 'purpose' in design_prompt
    # CS-3 bind 退役：承载位由 design wire 的 real 标注派生，无 bind 请求。
    assert all(owner != 'bind' for owner, _prompt in requests)
    before = repo.path('stage4/ir/writer_book_ir.json').read_bytes()
    run_workflow(repository=repo, chapters=chapters, ask=ask,
                 ask_text=ask_text,
                 ask_body=lambda owner, prompt: (_ for _ in ()).throw(
                     AssertionError('stage4 干跑不应调用正文 owner')),
                 budget=800)
    assert repo.path('stage4/ir/writer_book_ir.json').read_bytes() == before
    assert repo.read('runtime/workflow_status.json')['status'] == 'planned_validated'
    def rejected(owner, prompt, validate, patch=None, **kwargs):
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
    """只改引子：候选根复校拒绝；book 与 ch01 的观察请求随之变化（C 批，B2 换轨）。

    B2 五节点（D3）：e00 证据只进书级观察与第 1 章观察；其余各章请求不因引子
    变化（也不因任何前章产物变化）。
    """
    from novel.llm.paid_requests import (
        ProvenanceRefused, digest, init_or_resume_candidate_workflow,
    )
    from novel.stages.chain import run_workflow

    chapters = [{'chapter_ordinal': 1, 'text': '云娘提出约定，夫君知晓真相。'},
                {'chapter_ordinal': 2, 'text': '第二夜窗外有响动。'}]
    wires = {
        'book': {**sample_book_expression({
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'source_refs': ['e01_01']}]},
            'yun': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'source_refs': ['e01_01']}]}}), 'protocol_version': 'stage2-book-v7', 'characters': {
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'source_refs': ['e01_01']}]},
            'yun': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'source_refs': ['e01_01']}]}},
            'initial_state': [], 'world_constraints': []},
        'ch01': {'protocol_version': PV, 'beats': [
            {'event': '约定成立', 'participants': ['yun', 'fu_jun'],
             'source_refs': ['e01_01'], 'scene_boundary': 'new',
             'temporal_mode': 'single', 'effects': [], 'cognition': [],
             'loops': []}]},
        'ch02': {'protocol_version': PV, 'beats': [
            {'event': '夜半响动', 'participants': ['yun'],
             'source_refs': ['e02_01'], 'scene_boundary': 'new',
             'temporal_mode': 'single', 'effects': [], 'cognition': [],
             'loops': []}]},
    }
    stage2_nodes = ['str06.stage2.book', 'str06.stage2.chapters']

    def run(hook_copy):
        base = tmp_path / f'out_{abs(hash(hook_copy))}'
        base.mkdir(exist_ok=True)
        repo = Str06Repository(base / 'run')
        repo.attach_context(make_context(base / 'run', ledger=CostLedger(path=None)))
        prompts: dict[str, str] = {}

        def ask(owner, prompt, validate, patch=None, **kwargs):
            data = deepcopy(wires[owner])
            errors = validate(data)
            assert not errors, (owner, errors)
            prompts[owner] = prompt
            return deepcopy(wires[owner]), synthetic_ref(owner, prompt)

        report = run_workflow(
            repository=repo, chapters=chapters, hook_copy=hook_copy, ask=ask,
            ask_text=lambda owner, prompt: (_ for _ in ()).throw(
                AssertionError('本反例不跑文本 owner')),
            ask_body=lambda owner, prompt: (_ for _ in ()).throw(
                AssertionError('本反例不跑正文 owner')),
            budget=800, through='stage2', node_filter=stage2_nodes)
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

    B2 五节点：concealed 建立与读者揭示在**同章**（章内句柄引用闭合归 compile）；
    跨章版本由 associate 配对承接（见 test_str06_stage2_publish）。
    断言：确认前各场清单列出该事实；确认场标注确认拍；fold 的未揭示报告为空
    （读者确已在该拍确认）。
    """
    book = {**sample_book_expression({
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}]},
        'yun': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_01']}]}}), 'protocol_version': 'stage2-book-v7', 'characters': {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}]},
        'yun': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_01']}]}},
        'initial_state': [], 'world_constraints': []}
    ch01 = {'protocol_version': PV, 'beats': [
        _obs_beat('夫君佯称离开，实则藏身床底', ('e01_01',), participants=('fu_jun',),
                  # CS-S 1b：concealed／effect 命题删——藏身是普通 scene_location
                  # 效应；读者确认链走 cognition（b01_02.c1 的 reader learns）。
                  effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                            'key': 'scene_location', 'op': 'set', 'value': '床底',
                            'modality': 'actual',
                            'basis': [{'segment': 'e01_01',
                                       'quote': '夫君与云娘一处住着'}]}]),
        _obs_beat('床板响动，读者与云娘确认床底有人', ('e01_01',),
                  participants=('yun', 'fu_jun'), scene_boundary='continue',
                  access='narrated',
                  cognition=[{'proposition': {'ref': 'p_hide',
                                              'statement': '夫君藏身床底'},
                              'holder': 'yun', 'mode': 'confirms',
                              'basis': [{'segment': 'e01_01',
                                         'quote': '夫君与云娘一处住着'}]}])]}
    design = {
        'protocol_version': 'stage4-design-v8',
        'book_style': '冷峻克制的第三人称叙事。',
        'character_voices': [{'char_id': 'yun', 'voice': '冷峻'}],
        'initial_state': [],
        'scenes': [
            {'driver': {'focal_actor': 'yun', 'immediate_want': '确认声响来源',
                        'opposition': '无人可问', 'turn': '决意亲自去看'},
             'brief': '重心在夜半响动后的试探；压缩寻问无果，'
                      '在决意亲自去到处收束，衔接下一场的揭示。',
             'reads': [],
             'beats': [{'ev': '夜半响动', 'who': ['yun'],
                        'real': ['ob01'],
                        'state': [{'at': 'world', 'key': 'secret',
                                   'val': '床底有人'}]}]},
            {'driver': {'focal_actor': 'yun', 'immediate_want': '看清床底',
                        'opposition': '恐惧', 'turn': '确认'},
             'brief': '重心在床底确认瞬间；从俯身写到确认，在反应处收住。',
             'reads': [{'domain': 'world', 'subjects': [], 'holder': None,
                        'key': 'secret'}],
             'beats': [{'ev': '确认床底有人', 'who': ['yun', 'fu_jun'],
                        'real': ['ob02'], 'know': [{'ref': 'ob02.k01', 'confirms': {'domain': 'world', 'key': 'secret'}}]}]}],
        'chapters': [{'purpose': '夜半响动到床底确认', 'scenes': 2}]}
    preserve = {'protocol_version': 'stage4-preserve-v2', 'obligations': [
        {'kind': 'event_core',
         'statement': '夫君佯称离开实则藏身床底',
         'anchors': ['b01_01.e1'],
         'must_preserve': True,
         'satisfaction': '正文呈现佯离与藏身的关系', 'requires': []},
        {'kind': 'reveal',
         'statement': '读者在床板响动后确认床底有人',
         'anchors': ['b01_02.c2'],  # CS-S Phase 3：读者条目由 compile 派生（c2＝派生位）
         'must_preserve': True,
         'satisfaction': '正文呈现确认时点与反应', 'requires': [1]}]}
    wires = {
        'book': book, 'ch01': ch01,
        'names': {
            'protocol_version': 'stage2-names-v6',
            'families': {'fam_yun': {'literal': '云', 'forms': []}},
            'people': {
                'fu_jun': {'family_id': None, 'given': None, 'aliases': []},
                'yun': {'family_id': 'fam_yun', 'given': '娘', 'aliases': []}}},
        'associate': {'protocol_version': 'stage2-associate-v5',
                      'same_proposition': [],
                      'loop_pairing': [], 'cross_chapter_cause': [],
                      'scene_continuation': []},
        'cross_cause': {'protocol_version': 'stage2-cross-cause-v1', 'causes': []},
        'preserve': preserve, 'design': design,
        'hook_design': {
            'protocol_version': 'stage4-hook-design-v1',
            'pov': {'person': 'first', 'focalizers': ['yun']},
            'materials': [{'beat_ref': {'scene': 'sc02', 'beat': 'b01'}, 'withheld': []}],
            'surface_releases': [], 'forbidden_reveals': [],
            'design_brief': '从夜半响动切入，快速带过藏身，收在床底确认。',
            'delivery_brief': '第一人称限知，短句，不解释。'},
    }
    repo = Str06Repository(tmp_path / 'book')
    repo.attach_context(make_context(tmp_path / 'book', ledger=CostLedger(path=None)))
    requests = []

    def ask(owner, prompt, validate, patch=None, **kwargs):
        data = deepcopy(wires[owner])
        assert not validate(data), (owner, validate(data))
        requests.append((owner, prompt))
        return data, synthetic_ref(owner, prompt)

    report = run_workflow(
        repository=repo, chapters=[{'chapter_ordinal': 1,
                                    'text': '夫君与云娘一处住着。夜半床板响动，云娘确认床底有人。'}],
        ask=ask,
        ask_text=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('不跑文本 owner')),
        ask_body=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('不跑正文 owner')),
        budget=800)
    assert report['guard_errors'] == []
    # 源侧 fold：读者已在 b01_02 确认（CS-S 1b：concealed 读数删，确认由
    # 认知转移承载）
    fold = repo.inspect('stage2/acceptance/fold.json')
    assert fold['chapters']['1']['empty_transition_beats'] == 0
    # 写手 IR：确认前（s01）列出该事实；确认场（s02）标注确认拍
    writer_ir = repo.inspect('stage4/ir/writer_book_ir.json')
    s01, s02 = writer_ir['chapters'][0]['scenes']
    # D5：确认清单带读者知情地址，投影进确认拍所在场与同 key 的场。s01 的
    # 事实行 key（hiding）与揭示行 key（kp 句柄）不同名 ⇒ s01 不再拿清单
    #（D4 经 confirms 显式关联的通道已删；提前暴露防护的其余承载是义务
    # 满足面与 hook 禁区）；确认场（s02）标注确认拍。
    assert [(p['fact'], p['in_scene']) for p in s01['verification_ir']['pending_reader_confirmations']] == [('夫君藏身床底', False)]
    pending = s02['verification_ir']['pending_reader_confirmations']
    assert [(p['fact'], p['position'], p['in_scene']) for p in pending] == [
        ('夫君藏身床底', 'sc02#b01', True)]  # 批0 D6：fact 回填为 Brief 登记的命题 statement
    assert pending[0]['address'] == {
        'domain': 'world', 'key': 'secret',
        'subjects': [], 'holder': None}
    # 渲染进写手阅读面（地址可追溯）
    from novel.stages.common import project_chapter_packet, render_chapter_packet
    book_ir = repo.read_model('stage4/ir/writer_book_ir.json')
    packet = render_chapter_packet(project_chapter_packet(book_ir, 1))
    assert '「夫君藏身床底」须到本场第1拍起才可向读者确认' in packet
    assert 'sc02#b01' not in packet and 'world:hiding' not in packet
    # Preserve 提示词带着揭示时点要求（v3 规则）
    preserve_prompt = next(p for o, p in requests if o == 'preserve')
    assert '谁在何时确认' in preserve_prompt
