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
    compile_name_variants, name_variant_candidates, validate_name_resolutions,
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
        'family_id': 'f_su', 'given': '芸娘', 'aliases': [], 'additional_surfaces': []}
    wires['name_variants'] = {
        'protocol_version': 'stage2-name-variants-v1',
        'variants': [{'literal': '苏芸孃', 'verdict': 'same',
                      'char_id': 'yun_niang', 'surface': '苏芸娘',
                      'evidence_refs': ['e02_03'],
                      'reason': '上下文延续同一人物，原文该处异写。'}]}
    wires['ch02']['beats'].append(_beat('苏芸孃决定次日出门', ('e02_03',),
        cognition=[{'proposition': {'ref': 'p_out', 'statement': '苏芸孃决定次日出门'},
                    'holder': 'yun_niang', 'mode': 'learns',
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


def _supplement_case():
    """Book 保留既有人物；Names 只声明源文已给的本名和称呼，不从部件扩词。"""
    chapters, wires = deepcopy(CHAPTERS), _full_stage2_wires()
    chapters[1]['text'] += '\n夫君本名陆远松，云娘唤他“远松”，门房则称他“陆公子”。'
    wires['names']['people']['fu_jun'].update(
        given='远松', additional_surfaces=[
            {'surface': surface, 'evidence_refs': ['e02_03']}
            for surface in ('陆远松', '远松', '陆公子')])
    return chapters, wires


def test_source_surface_supplement_reaches_characters_and_bound_stage7(tmp_path):
    """Names→Stage2真实发布→Stage7真实改名：遗漏简称进入同一Characters真源。"""
    from novel.core.name_text import resolve_name_text
    from novel.stages.stage4.naming import apply_bound_names, compile_bound_replacements, compile_name_map
    chapters, wires = _supplement_case()
    before = deepcopy(wires['book'])
    repo = _run(tmp_path, chapters, wires, _AcceptingAsk(wires))
    book = repo.read_model('stage2/observations/book.json')
    names = repo.read_model('stage2/observations/names.json')
    characters = repo.read_model('stage2/characters/characters.json')
    blueprint = repo.read_model('stage2/blueprints/chapter_blueprints.json')
    assert [surface.surface for surface in book.characters['fu_jun'].surfaces] == ['夫君']
    assert wires['book'] == before, '补面不得改写Book观察或付费输入'
    assert names.protocol_version == 'stage2-names-v5'
    assert {mention.surface for mention in names.additional_surfaces['fu_jun']} == {
        '陆远松', '远松', '陆公子'}
    extra = {surface.surface: surface for surface in characters.characters['fu_jun'].surfaces
             if surface.surface != '夫君'}
    assert set(extra) == {'陆远松', '远松', '陆公子'}
    assert all(surface.from_chapter == 2 and surface.source_refs == ('e02_03',)
               for surface in extra.values())
    assert 'e02_03' in blueprint.evidence, '新增正式源引用须经唯一Blueprint证据窗口送达下游'
    assert characters.name_structure.people['fu_jun'].surface_forms['远松'] == '{given}'
    assert characters.name_structure.people['fu_jun'].surface_forms['陆公子'] == '{family}公子'
    name_map, errors = compile_name_map({'entries': [{
        'char_ref': 'fu_jun', 'name': {'family': '岑', 'given': '时舟'},
        'aliases': {}, 'keep_reason': None}]}, characters)
    assert name_map is not None and not errors, errors
    replacements, errors = compile_bound_replacements(characters, name_map)
    assert not errors, errors
    resolved = resolve_name_text('[[name:fu_jun|远松]]回头，门房称他[[name:fu_jun|陆公子]]。',
        {cid: tuple(surface.surface for surface in entry.surfaces)
         for cid, entry in characters.characters.items()})
    assert not resolved.errors
    renamed, errors = apply_bound_names(resolved.text, resolved.mentions, replacements)
    assert not errors and renamed == '时舟回头，门房称他岑公子。'


@pytest.mark.parametrize('fault', ['missing_field', 'unknown_segment', 'missing_literal', 'duplicate', 'existing'])
def test_surface_supplement_rejects_unproved_or_redundant_declarations(fault):
    from novel.stages.stage2.names import compile_names_observation, names_patch_locate
    chapters, wires = _supplement_case()
    evidence = build_evidence(chapters, HOOK)
    book = BookObservation.model_validate(wires['book'])
    core, errors = character_core_view(book, evidence=evidence)
    assert not errors
    raw = wires['names']['people']['fu_jun']
    if fault == 'missing_field':
        raw.pop('additional_surfaces')
    elif fault == 'unknown_segment':
        raw['additional_surfaces'][0]['evidence_refs'] = ['e99_99']
    elif fault == 'missing_literal':
        raw['additional_surfaces'][0]['evidence_refs'] = ['e01_01']
    elif fault == 'duplicate':
        raw['additional_surfaces'].append(deepcopy(raw['additional_surfaces'][0]))
    else:
        raw['additional_surfaces'].append({'surface': '夫君', 'evidence_refs': ['e01_01']})
    before = deepcopy(wires['names'])
    full = '\n'.join(chapter['text'] for chapter in chapters)
    observed, extended, errors = compile_names_observation(wires['names'], core, full, evidence=evidence)
    assert observed is None and extended is None and errors
    located = names_patch_locate(wires['names'], errors, core, full, evidence=evidence)
    assert located is not None and {path for path, _errors in located} == {('people', 'fu_jun')}
    assert wires['names'] == before


def test_names_v4_read_adapter_keeps_historical_protocol_and_unknown_supplement_scope():
    from pydantic import ValidationError
    from contracts.schema.observation import NamesObservation
    from novel.stages.stage2.names import extend_name_surfaces
    from tests.test_str06_stage2_publish import _names_structure
    payload = {'protocol_version': 'stage2-names-v4', 'structure': _names_structure()}
    legacy = NamesObservation.model_validate(payload)
    assert legacy.protocol_version == 'stage2-names-v4' and legacy.additional_surfaces == {}
    assert 'additional_surfaces' not in legacy.model_dump(mode='json')
    assert NamesObservation.model_validate(legacy.model_dump(mode='json')).protocol_version == 'stage2-names-v4'
    with pytest.raises(ValidationError, match='v4 不得携带'):
        NamesObservation.model_validate({**payload, 'additional_surfaces': {}})
    with pytest.raises(ValidationError, match='v5 必须显式声明'):
        NamesObservation.model_validate({**payload, 'protocol_version': 'stage2-names-v5'})
    from contracts.schema.observation import SurfaceMention
    from pydantic_core import PydanticSerializationError
    forged = legacy.model_copy(update={'additional_surfaces': {
        'fu_jun': (SurfaceMention(surface='陆公子', source_refs=('e01_01',)),)}})
    with pytest.raises(PydanticSerializationError, match='拒绝丢弃证据'):
        forged.model_dump(mode='json')
    evidence = build_evidence(CHAPTERS, HOOK)
    core, errors = character_core_view(BookObservation.model_validate(_full_stage2_wires()['book']), evidence=evidence)
    assert not errors
    preserved, errors = extend_name_surfaces(core, legacy, evidence=evidence)
    assert preserved is core and not errors, '历史v4只读通过不代表已观察过补充称谓'


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
    # 无异写文本 → 无候选（零调用的判定面）。
    plain_chapters = deepcopy(CHAPTERS)
    plain_chapters[0]['text'] += '\n苏芸娘在灯下缝衣。'
    plain_wires = _full_stage2_wires()
    plain_wires['book']['characters']['yun_niang']['surfaces'].append(
        {'surface': '苏芸娘', 'source_refs': ['e01_03']})
    plain_wires['names']['families']['f_su'] = {'literal': '苏', 'forms': []}
    plain_wires['names']['people']['yun_niang'] = {
        'family_id': 'f_su', 'given': '芸娘', 'aliases': [], 'additional_surfaces': []}
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


def test_names_candidate_decisions_are_complete_at_canonical_consumers(tmp_path):
    """正式观察仍须完整覆盖当前候选；删判定不能绕过 wire 的恰答一次闸。

    not_name 是持久化的完整判定；候选随源证据增加时必须重新判定。消费者
    重算候选，不只相信付费回答曾通过机械验收。
    """
    from contracts.schema.observation import NamesObservation
    from novel.stages.chain import ChainContext, run_associate
    from novel.stages.stage2.compile import compile_source_truth
    chapters, wires = _case()
    wires['name_variants']['variants'][0].update(
        verdict='not_name', char_id=None, surface=None)
    repo = _run(tmp_path, chapters, wires, _AcceptingAsk(wires))
    book = repo.read_model('stage2/observations/book.json')
    names = repo.read_model('stage2/observations/names.json')
    chapter_obs = {n: repo.read_model(f'stage2/observations/ch{n:02d}.json')
                   for n in (1, 2, 3)}
    evidence = build_evidence(chapters, HOOK)
    core, errors = character_core_view(book, evidence=evidence)
    assert not errors and names.structure.resolutions[0].status == 'not_name'
    assert validate_name_resolutions(names.structure, core, evidence=evidence) == []

    missing = names.structure.model_copy(update={'resolutions': ()})
    errors = validate_name_resolutions(missing, core, evidence=evidence)
    assert any('苏芸孃' in message and '当前 0 条' in message for message in errors)
    duplicate = names.structure.model_copy(update={
        'resolutions': names.structure.resolutions * 2})
    errors = validate_name_resolutions(duplicate, core, evidence=evidence)
    assert any('当前 2 条' in message for message in errors)
    wrong_target = names.structure.model_copy(update={'resolutions': (
        names.structure.resolutions[0].model_copy(update={
            'status': 'same', 'char_id': 'fu_jun', 'surface': '夫君'}),)})
    errors = validate_name_resolutions(wrong_target, core, evidence=evidence)
    assert any('只能指向候选近似锚人物' in message for message in errors)

    changed_evidence = deepcopy(evidence)
    changed_evidence['e03_99'] = {'chapter': 3, 'text': '苏芸良推门进来。'}
    errors = validate_name_resolutions(names.structure, core, evidence=changed_evidence)
    assert any('苏芸良' in message and '当前 0 条' in message for message in errors)
    changed_plain = deepcopy(evidence)
    changed_plain['e03_99'] = {'chapter': 3, 'text': '苏芸娘推门进来。'}
    assert validate_name_resolutions(names.structure, core, evidence=changed_plain) == []

    broken_names = names.model_copy(update={'structure': missing})
    outcome = compile_source_truth(
        book=book, chapters=chapter_obs, names=broken_names,
        associate=repo.read_model('stage2/observations/associate.json'),
        evidence=evidence)
    assert not outcome.publishable and any('当前 0 条' in e for e in outcome.errors)

    # 同一正式观察改动从 paid associate 的真实入口被挡住，不能继续买关联。
    from tests.str06_facilities import synthetic_ref
    repo.context.begin_run()
    repo.read_model('stage2/observations/book.json')
    repo.write('stage2/observations/names.json', broken_names, owners=('names',),
               refs=(synthetic_ref('names', '已购输入'),))
    with pytest.raises(MechanicalDefectStop, match='名称同一性不闭合'):
        run_associate(ChainContext(
            repository=repo, chapters=chapters, hook_copy=HOOK,
            ask=lambda *args, **kwargs: pytest.fail('漏判定不得购买关联'),
            ask_text=lambda *args: pytest.fail('不得购买正文'),
            ask_body=lambda *args: pytest.fail('不得购买正文'), budget=800))


@pytest.mark.parametrize('defect', ['missing_person', 'missing_surface', 'unknown_family'])
def test_names_structure_edits_stop_before_associate_purchase(tmp_path, defect):
    """NamesObservation 单份模型合法仍不代表对人物真账闭合；购买前复用合并判据。"""
    from contracts.schema.observation import NamesObservation
    from novel.stages.chain import ChainContext, run_associate
    from tests.str06_facilities import synthetic_ref
    chapters, wires = _case()
    repo = _run(tmp_path, chapters, wires, _AcceptingAsk(wires))
    names = repo.read_model('stage2/observations/names.json')
    people = dict(names.structure.people)
    if defect == 'missing_person':
        people.pop('yun_niang')
    elif defect == 'missing_surface':
        forms = dict(people['yun_niang'].surface_forms)
        forms.pop('苏芸娘')
        people['yun_niang'] = people['yun_niang'].model_copy(update={'surface_forms': forms})
    else:
        people['yun_niang'] = people['yun_niang'].model_copy(update={'family_id': 'not_registered'})
    edited = names.model_copy(update={
        'structure': names.structure.model_copy(update={'people': people})})
    repo.context.begin_run()
    repo.read_model('stage2/observations/book.json')
    repo.write('stage2/observations/names.json', edited, owners=('names',),
               refs=(synthetic_ref('names', '已购结构编辑'),))
    with pytest.raises(MechanicalDefectStop, match='名称同一性不闭合') as excinfo:
        run_associate(ChainContext(
            repository=repo, chapters=chapters, hook_copy=HOOK,
            ask=lambda *args, **kwargs: pytest.fail('坏名称结构不得购买关联'),
            ask_text=lambda *args: pytest.fail('不得购买正文'),
            ask_body=lambda *args: pytest.fail('不得购买正文'), budget=800))
    assert any('合并名称结构失败' in str(issue) for issue in excinfo.value.issues)


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


@pytest.mark.parametrize('defect,expected', [
    ('override_no_placeholder', '无占位符'),
    ('override_not_string', '必须是构词模板字符串或 null'),
    ('override_name_literal_left_in_text', '非占位部分'),
    ('alias_without_id', '化名条目形状非法'),
    ('alias_not_list', 'aliases 必须是数组'),
    ('family_form_without_form', '称谓条目形状非法'),
    ('family_forms_not_list', 'forms 必须是数组'),
    ('family_literal_not_string', 'literal 缺失或为空'),
    ('family_id_not_string', 'family_id 必须是字符串或 null'),
    ('family_form_literal_not_string', '称谓条目形状非法'),
])
def test_names_structure_refuses_override_bypass_and_malformed_entries(defect, expected):
    """form_overrides 不得绕开纯称谓防漏标（无占位符模板恒等 surface、不产生替换键，
    名字面留在文本里改名时漏改）；畸形条目是条目级错误而不是 KeyError／TypeError
    崩节点（崩溃＝exit 70 未知，而不是一轮返修）。"""
    chapters, wires = _case()
    core, _structure, _candidates, evidence = _structure_and_candidates(chapters, wires)
    wire = deepcopy(wires['names'])
    person = wire['people']['yun_niang']
    if defect == 'override_no_placeholder':
        person['form_overrides'] = {'苏芸娘': '苏芸娘'}
    elif defect == 'override_not_string':
        person['form_overrides'] = {'苏芸娘': 5}
    elif defect == 'override_name_literal_left_in_text':
        person['form_overrides'] = {'苏芸娘': '{family}芸娘'}
    elif defect == 'alias_without_id':
        person['aliases'] = [{'literal': '苏阿娘'}]
    elif defect == 'alias_not_list':
        person['aliases'] = 3
    elif defect == 'family_form_without_form':
        wire['families']['f_su']['forms'] = [{'literal': '苏家', 'source_refs': []}]
    elif defect == 'family_forms_not_list':
        wire['families']['f_su']['forms'] = 7
    elif defect == 'family_literal_not_string':
        wire['families']['f_su']['literal'] = ['苏']
    elif defect == 'family_id_not_string':
        person['family_id'] = ['f_su']
    else:
        wire['families']['f_su']['forms'] = [
            {'literal': ['苏家'], 'form': '{family}家', 'source_refs': ['e03_02']}]
    before = deepcopy(wire)
    full = '\n'.join(c['text'] for c in chapters)
    structure, errors = compile_name_structure(wire, core, full, evidence=evidence)
    assert structure is None and any(expected in message for message in errors), errors
    assert wire == before


def test_names_structure_keeps_valid_override_and_null_pure_title():
    """反向守卫：合法例外不被误伤——带占位符且无残留名字面的模板、写 null 的
    纯称谓照常接受。"""
    chapters, wires = _case()
    core, _structure, _candidates, evidence = _structure_and_candidates(chapters, wires)
    wire = deepcopy(wires['names'])
    wire['people']['yun_niang']['form_overrides'] = {
        '苏芸娘': '{family}{given}', '云娘': None}
    full = '\n'.join(c['text'] for c in chapters)
    structure, errors = compile_name_structure(wire, core, full, evidence=evidence)
    assert structure is not None and not errors, errors
    forms = structure.people['yun_niang'].surface_forms
    assert forms['苏芸娘'] == '{family}{given}' and forms['云娘'] is None


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
