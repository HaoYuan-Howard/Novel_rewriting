"""RC-17：名称同一性留证、局部归一与歧义停止；真实 Stage2 发布到 Preserve 消费。"""
from copy import deepcopy

import pytest

from contracts.schema import encode_artifact, decode_artifact, ArtifactVersionError
from contracts.schema.observation import BookObservation, ChapterObservation
from contracts.schema.source import NameStructure
from novel.llm.ledger import CostLedger
from novel.stages.chain import build_evidence, run_workflow
from novel.stages.owner_revision import MechanicalDefectStop
from novel.stages.stage2 import character_core_view, compile_name_structure, normalize_name_observations
from novel.stages.stage4 import project_preserve_view
from novel.store import Str06Repository
from tests.str06_facilities import make_context
from tests.test_str06_stage2_publish import (
    CHAPTERS, HOOK, STAGE2_NODES, _AcceptingAsk, _beat, _full_stage2_wires,
)


def _case():
    chapters = deepcopy(CHAPTERS)
    chapters[1]['text'] += '\n云孃决定次日出门。'  # 新的一段 → e02_03（RC-26）
    wires = _full_stage2_wires()
    basis = [{'segment': 'e02_03', 'quote': '云孃决定次日出门'}]
    row = {'literal': '云孃', 'status': 'same', 'char_id': 'yun_niang', 'surface': '云娘',
           'basis': basis, 'reason': '上下文延续同一人物，原文该处异写。'}
    wires['names']['resolutions'] = [row]
    wires['ch02']['beats'].append(_beat('云孃决定次日出门', ('e02_03',),
        cognition=[{'proposition': {'ref': 'p_out', 'statement': '云孃决定次日出门'},
                    'holder': 'reader', 'mode': 'learns', 'basis': basis}]))
    return chapters, wires


def _run(tmp_path, chapters, wires, ask):
    repo = Str06Repository(tmp_path)
    repo.attach_context(make_context(tmp_path, ledger=CostLedger(path=None)))
    run_workflow(repository=repo, chapters=chapters, hook_copy=HOOK, ask=ask,
        ask_text=lambda *a: pytest.fail('Stage2不得购买正文'),
        ask_body=lambda *a: pytest.fail('Stage2不得购买正文'), budget=800,
        through='stage2', node_filter=STAGE2_NODES)
    return repo


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
    observation = repo.read_model('stage2/observations/ch02.json')
    blueprint = repo.read_model('stage2/blueprints/chapter_blueprints.json')
    chars = repo.read_model('stage2/characters/characters.json')
    assert observation.beats[-1].event == '云孃决定次日出门'
    beat = blueprint.chapters[1].beats[-1]
    assert beat.event == '云娘决定次日出门'
    assert beat.cognition[0].basis[0].quote == '云孃决定次日出门'
    assert '云娘决定次日出门' in ask.prompts['associate']
    assert '云孃决定次日出门' not in ask.prompts['associate']
    view = project_preserve_view(chars.core_view(), blueprint)
    assert '云娘决定次日出门' in view and '云孃决定次日出门' in view
    assert chars.name_structure.resolutions[0].char_id == 'yun_niang'
    assert wires == before


def test_unresolved_identity_stops_before_associate_purchase_and_publish(tmp_path):
    chapters, wires = _case()
    wires['names']['resolutions'][0].update(status='unresolved', char_id=None, surface=None)
    ask = _AcceptingAsk(wires)
    with pytest.raises(MechanicalDefectStop, match='名称同一性未闭合'):
        _run(tmp_path, chapters, wires, ask)
    assert 'names' in ask.prompts and 'associate' not in ask.prompts
    assert not (tmp_path / 'stage2/characters/characters.json').exists()
    assert not (tmp_path / 'stage2/blueprints/chapter_blueprints.json').exists()


@pytest.mark.parametrize('defect,expected', [
    ('quote', '原字面'), ('segment', 'unknown_segment'), ('target', '未登记'),
    ('registered', '已在人物表'), ('conflict', '冲突'), ('old', 'stage2-names-v3'),
])
def test_name_proof_refuses_bad_evidence_target_conflict_and_old_protocol(defect, expected):
    chapters, wires = _case()
    evidence = build_evidence(chapters, HOOK)
    core, errors = character_core_view(BookObservation.model_validate(wires['book']), evidence=evidence)
    assert not errors
    wire = wires['names']
    row = wire['resolutions'][0]
    if defect == 'quote':
        row['basis'][0]['quote'] = '决定次日出门'
    elif defect == 'segment':
        row['basis'][0]['segment'] = 'e99_01'
    elif defect == 'target':
        row['surface'] = '未登记称谓'
    elif defect == 'registered':
        row.update(literal='云娘', basis=[{'segment': 'e01_02', 'quote': '云娘听见床板响动'}])
    elif defect == 'conflict':
        wire['resolutions'].append({**row, 'char_id': 'fu_jun', 'surface': '夫君'})
    else:
        wire.pop('protocol_version')
    before = deepcopy(wire)
    value, errors = compile_name_structure(wire, core, '\n'.join(c['text'] for c in chapters), evidence=evidence)
    assert value is None and any(expected in e for e in errors), errors
    assert wire == before


def test_mapping_is_local_and_idempotent():
    _, wires = _case()
    # 相同字面在未获同一性证明的另一句段不能被全局替换。
    wires['ch01']['beats'][0]['event'] = '云孃旁观'
    book = BookObservation.model_validate(wires['book'])
    chapters = {n: ChapterObservation.model_validate(wires[f'ch{n:02d}']) for n in (1, 2, 3)}
    structure = NameStructure.model_validate({k: v for k, v in wires['names'].items() if k != 'protocol_version'})
    normalized = normalize_name_observations(book, chapters, structure)
    assert normalized[1][1].beats[0].event == '云孃旁观'
    assert normalized[1][2].beats[-1].event == '云娘决定次日出门'
    assert normalize_name_observations(*normalized, structure) == normalized


@pytest.mark.parametrize('rel', ['stage2/observations/names.json', 'stage2/characters/characters.json'])
def test_old_name_artifact_requires_explicit_rebuild(rel):
    payload = encode_artifact(rel, {})
    assert payload['_schema_version'] == 2
    payload['_schema_version'] = 1
    with pytest.raises(ArtifactVersionError):
        decode_artifact(rel, payload)
