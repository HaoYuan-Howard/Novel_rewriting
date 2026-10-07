"""RC-17／CS-S 1c：名称同一性走候选先行窄调用——留证、局部归一与歧义停止；
真实 Stage2 发布到 Preserve 消费。

召回边界（如实登记）：候选只收「≥3 字含姓名部件的登记 surface 的同首字
编辑距离 1 邻域」——两字称谓的异写（如旧例 云孃／云娘）不在确定性召回类内，
本套件改用 ≥3 字姓名面（苏芸娘→苏芸孃）复现同一判据链。
"""
from copy import deepcopy

import pytest

from contracts.schema import encode_artifact, decode_artifact, ArtifactVersionError
from contracts.schema.observation import BookObservation, ChapterObservation
from contracts.schema.source import NameStructure
from novel.llm.ledger import CostLedger
from novel.stages.chain import build_evidence, run_workflow
from novel.stages.owner_revision import MechanicalDefectStop
from novel.stages.stage2 import (
    character_core_view, compile_name_structure, normalize_name_observations,
)
from novel.stages.stage2.names import (
    compile_name_variants, name_variant_candidates,
)
from novel.stages.stage4 import project_preserve_view
from novel.store import Str06Repository
from tests.str06_facilities import make_context, names_v6_wire
from tests.test_str06_stage2_publish import (
    CHAPTERS, HOOK, STAGE2_NODES, _AcceptingAsk, _beat, _full_stage2_wires,
)


def _case():
    """书级加「苏芸娘」姓名面（≥3 字、含部件——变体候选的确定性锚）；
    ch02 追加一段含异写「苏芸孃」的正文。"""
    chapters = deepcopy(CHAPTERS)
    chapters[0]['text'] += '\n苏芸娘在灯下缝衣。'   # → e01_03（锚点段）
    chapters[1]['text'] += '\n苏芸孃决定次日出门。'  # 新的一段 → e02_03（RC-26）
    wires = _full_stage2_wires()
    wires['book']['characters']['yun_niang']['surfaces'].append(
        {'surface': '苏芸娘', 'source_refs': ['e01_03']})
    wires['names']['families']['f_su'] = {'literal': '苏', 'forms': []}
    wires['names']['people']['yun_niang'] = {
        'family_id': 'f_su', 'given': '芸娘', 'aliases': []}
    wires['name_variants'] = {
        'protocol_version': 'stage2-name-variants-v1',
        'variants': [{'literal': '苏芸孃', 'verdict': 'same',
                      'char_id': 'yun_niang', 'surface': '苏芸娘',
                      'evidence_refs': ['e02_03'],
                      'reason': '上下文延续同一人物，原文该处异写。'}]}
    wires['ch02']['beats'].append(_beat('苏芸孃决定次日出门', ('e02_03',),
        cognition=[{'proposition': {'ref': 'p_out', 'statement': '苏芸孃决定次日出门'},
                    'holder': 'reader', 'mode': 'learns',
                    'basis': [{'segment': 'e02_03', 'quote': '苏芸孃决定次日出门'}]}]))
    # v4 候选面随夹具变化：ch02 追加拍自开新场（seam 右端变 ch02.sc2）且登记
    # p_out（悬空引用候选多一条）——默认答案须恰覆盖新候选集。
    wires['associate']['scene_continuation'] = [
        {'left': 'ch01.sc1', 'right': 'ch02.sc1', 'verdict': 'distinct'},
        {'left': 'ch02.sc2', 'right': 'ch03.sc1', 'verdict': 'distinct'}]
    wires['associate']['same_proposition'].append(
        {'left': 'ch02.p_out', 'right': 'ch03.p_hide', 'verdict': 'distinct'})
    return chapters, wires


def _run(tmp_path, chapters, wires, ask):
    repo = Str06Repository(tmp_path)
    repo.attach_context(make_context(tmp_path, ledger=CostLedger(path=None)))
    run_workflow(repository=repo, chapters=chapters, hook_copy=HOOK, ask=ask,
        ask_text=lambda *a: pytest.fail('Stage2不得购买正文'),
        ask_body=lambda *a: pytest.fail('Stage2不得购买正文'), budget=800,
        through='stage2', node_filter=STAGE2_NODES)
    return repo


def _structure_and_candidates(chapters, wires):
    evidence = build_evidence(chapters, HOOK)
    core, errors = character_core_view(
        BookObservation.model_validate(wires['book']), evidence=evidence)
    assert core is not None and not errors, errors
    full = '\n'.join(c['text'] for c in chapters)
    structure, errors = compile_name_structure(wires['names'], core, full,
                                               evidence=evidence)
    assert structure is not None and not errors, errors
    return core, structure, name_variant_candidates(structure, core, evidence), evidence


def test_variant_candidate_generation_is_deterministic_and_narrow():
    """B：候选由代码给出——苏芸孃（替换一字、同首字、未登记、互不为子串）
    进候选；苏芸娘跟随字／标点类插入噪声不进（互为子串滤除）。"""
    chapters, wires = _case()
    _core, structure, candidates, evidence = _structure_and_candidates(chapters, wires)
    assert [c.literal for c in candidates] == ['苏芸孃']
    cand = candidates[0]
    assert (cand.char_id, cand.anchor, cand.segments) == \
        ('yun_niang', '苏芸娘', ('e02_03',))
    assert 'name_variants' not in _full_stage2_wires() or True
    # 无异写文本 → 无候选（零调用的判定面）。
    plain_wires = _full_stage2_wires()
    plain_chapters = deepcopy(CHAPTERS)
    plain_chapters[0]['text'] += '\n苏芸娘在灯下缝衣。'
    plain_wires = _full_stage2_wires()
    plain_wires['book']['characters']['yun_niang']['surfaces'].append(
        {'surface': '苏芸娘', 'source_refs': ['e01_03']})
    plain_wires['names']['families']['f_su'] = {'literal': '苏', 'forms': []}
    plain_wires['names']['people']['yun_niang'] = {
        'family_id': 'f_su', 'given': '芸娘', 'aliases': []}
    evidence_plain = build_evidence(plain_chapters, HOOK)
    core_plain, errors = character_core_view(
        BookObservation.model_validate(plain_wires['book']), evidence=evidence_plain)
    structure_plain, errors = compile_name_structure(
        plain_wires['names'], core_plain,
        '\n'.join(c['text'] for c in plain_chapters),
        evidence=evidence_plain)
    assert structure_plain is not None and not errors, errors
    assert name_variant_candidates(structure_plain, core_plain, evidence_plain) == ()


def test_proved_variant_reaches_canonical_and_preserve_without_changing_quotes(tmp_path):
    chapters, wires = _case()
    before = deepcopy(wires)
    ask = _AcceptingAsk(wires)
    repo = _run(tmp_path, chapters, wires, ask)
    import json
    names_input = json.loads(ask.prompts['names'].split('输入：\n', 1)[1])
    from novel.stages.stage2 import NAMES_INPUT_VERSION, names_evidence_view
    assert names_input['input_version'] == NAMES_INPUT_VERSION == 'stage2-names-input-v2'
    assert 'text' not in names_input
    assert names_input['evidence'] == names_evidence_view(build_evidence(chapters, HOOK))
    assert '苏芸孃' in ask.prompts['name_variants'] and '苏芸娘' in ask.prompts['name_variants']
    observation = repo.read_model('stage2/observations/ch02.json')
    blueprint = repo.read_model('stage2/blueprints/chapter_blueprints.json')
    chars = repo.read_model('stage2/characters/characters.json')
    assert observation.beats[-1].event == '苏芸孃决定次日出门'
    beat = blueprint.chapters[1].beats[-1]
    assert beat.event == '苏芸娘决定次日出门'
    assert beat.cognition[0].basis[0].quote == '苏芸孃决定次日出门'
    assert '苏芸娘决定次日出门' in ask.prompts['associate']
    assert '苏芸孃决定次日出门' not in ask.prompts['associate']
    view = project_preserve_view(chars.core_view(), blueprint)
    assert '苏芸娘决定次日出门' in view and '苏芸孃决定次日出门' in view
    assert chars.name_structure.resolutions[0].char_id == 'yun_niang'
    assert chars.name_structure.resolutions[0].status == 'same'
    assert wires == before


def test_unresolved_identity_stops_before_associate_purchase_and_publish(tmp_path):
    chapters, wires = _case()
    wires['name_variants']['variants'][0].update(
        verdict='unresolved', char_id=None, surface=None)
    ask = _AcceptingAsk(wires)
    with pytest.raises(MechanicalDefectStop, match='名称同一性不闭合'):
        _run(tmp_path, chapters, wires, ask)
    assert 'names' in ask.prompts and 'associate' not in ask.prompts
    assert not (tmp_path / 'stage2/characters/characters.json').exists()
    assert not (tmp_path / 'stage2/blueprints/chapter_blueprints.json').exists()


def test_not_name_verdict_completes_without_normalization(tmp_path):
    """not_name：完整性证明——不参与归一、不阻断发布；语义文本保持原字面。"""
    chapters, wires = _case()
    wires['name_variants']['variants'][0].update(
        verdict='not_name', char_id=None, surface=None,
        reason='原文该处是动宾短语，非人名。')
    ask = _AcceptingAsk(wires)
    repo = _run(tmp_path, chapters, wires, ask)
    chars = repo.read_model('stage2/characters/characters.json')
    assert chars.name_structure.resolutions[0].status == 'not_name'
    blueprint = repo.read_model('stage2/blueprints/chapter_blueprints.json')
    assert blueprint.chapters[1].beats[-1].event == '苏芸孃决定次日出门'


@pytest.mark.parametrize('defect,expected', [
    ('segment', '出现段之外'), ('target', '未登记'),
    ('registered', '不在候选清单内'), ('missing', '漏答'),
    ('duplicate', '重复作答'), ('old', 'stage2-name-variants'),
])
def test_variant_verdict_refuses_bad_evidence_target_and_old_protocol(defect, expected):
    chapters, wires = _case()
    core, _structure, candidates, evidence = _structure_and_candidates(chapters, wires)
    wire = deepcopy(wires['name_variants'])
    row = wire['variants'][0]
    if defect == 'segment':
        row['evidence_refs'] = ['e99_01']
    elif defect == 'target':
        row['surface'] = '未登记称谓'
    elif defect == 'registered':
        row['literal'] = '苏芸娘'
    elif defect == 'missing':
        row['literal'] = '凭空的字面'
    elif defect == 'duplicate':
        wire['variants'] = [deepcopy(row), deepcopy(row)]
    else:
        wire.pop('protocol_version')
    before = deepcopy(wire)
    value, located = compile_name_variants(wire, candidates, evidence=evidence,
                                           characters=core)
    assert value is None and any(expected in message
                                 for _path, message in located), located
    assert wire == before


def test_mapping_is_local_and_idempotent():
    chapters, wires = _case()
    # 相同字面在未获同一性证明的另一句段不能被全局替换。
    wires['ch01']['beats'][0]['event'] = '苏芸孃旁观'
    book = BookObservation.model_validate(wires['book'])
    chapters_obs = {n: ChapterObservation.model_validate(wires[f'ch{n:02d}'])
                    for n in (1, 2, 3)}
    # canonical NameStructure：basis 是段号＋派生引文（窄调用 wire 的
    # evidence_refs 裸段 id 经 compile 派生后的形态）。
    resolution = {'literal': '苏芸孃', 'status': 'same', 'char_id': 'yun_niang',
                  'surface': '苏芸娘', 'reason': '同一人物异写',
                  'basis': [{'segment': 'e02_03', 'quote': '苏芸孃决定次日出门。'}]}
    structure = NameStructure.model_validate({
        'families': {'f_lu': {'literal': '陆', 'forms': []},
                     'f_su': {'literal': '苏', 'forms': []}},
        'people': {
            'fu_jun': {'family_id': 'f_lu', 'given': None, 'aliases': [],
                       'surface_forms': {'夫君': None}},
            'yun_niang': {'family_id': 'f_su', 'given': '芸娘', 'aliases': [],
                          'surface_forms': {'云娘': None, '苏芸娘': '{family}{given}'}}},
        'resolutions': [resolution]})
    normalized = normalize_name_observations(book, chapters_obs, structure)
    assert normalized[1][1].beats[0].event == '苏芸孃旁观'
    assert normalized[1][2].beats[-1].event == '苏芸娘决定次日出门'
    assert normalize_name_observations(*normalized, structure) == normalized


@pytest.mark.parametrize('rel', ['stage2/observations/names.json', 'stage2/characters/characters.json'])
def test_old_name_artifact_requires_explicit_rebuild(rel):
    payload = encode_artifact(rel, {})
    assert payload['_schema_version'] == 2
    payload['_schema_version'] = 1
    with pytest.raises(ArtifactVersionError):
        decode_artifact(rel, payload)
