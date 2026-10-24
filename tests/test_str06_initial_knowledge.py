"""开场已确认知识经双真源、Preserve、Design 和写手材料保留正负极性。

Book 声明负责故事时间；证据出现章节不承担知情时间判定。机械面只验证
同持有者、同极性的确认依据，普通测试不调用真实模型。
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
from contracts.schema.naming import NameAssignment, NameMapV6
from contracts.schema.source import CharactersCore
from contracts.schema.workflow import STAGE4_PRESERVE_PROTOCOL, route_owner_issue
from novel.llm.ledger import CostLedger
from novel.stages.chain import run_workflow
from novel.stages.common import project_chapter_packet, render_chapter_packet
from novel.stages.stage2 import book_patch_locate, compile_book_observation
from novel.stages.stage2.book import compile_initial_knowledge
from novel.stages.stage4.book_ir import compile_writer_book_ir
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.design import assemble_body_design, _check_lock_acceptance
from novel.stages.stage4.grounding import (
    assemble_preserve, derive_semantic_binding, design_obligation_index,
    project_brief, project_preserve_view,
)
from novel.stages.stage4.hook_context import render_hook_fact_text
from novel.stages.stage4.guard import _read_resolves_in, check_epistemic_lock_order
from novel.stages.stage4.slim import (
    design_placements, design_reference_aliases, design_wire_model, project_design_input,
)
from novel.store import Str06Repository
from tests.str06_facilities import make_context, synthetic_ref
from tests.test_str06_identity_cognition import STAGE2_NODES, _typed_case


def _initial_case():
    chapters, wires, evidence, _book, _observations, _names, associate = _typed_case()
    quote = '云娘姓云，望着阿姐。开篇之前，她就已确认钥匙不在旧匣中。'
    chapters[0]['text'] = quote
    evidence['e01_01']['text'] = quote
    wires['ch01']['beats'][0]['event'] = '云娘开始寻找失踪的钥匙。'
    wires['book']['facts'].append({
        'statement': '钥匙在旧匣中',
        'known_at_start': [{'holder': 'yun_niang', 'polarity': 'negative'}],
        'evidence': [{'kind': 'confirmed', 'holder': 'yun_niang',
                      'polarity': 'negative', 'evidence_refs': ['e01_01']}]})
    identity = associate.model_dump(mode='json')
    causes = identity.pop('cross_chapter_cause')
    identity['cross_chapter_cause'] = []
    wires['associate'] = identity
    wires['cross_cause'] = {'protocol_version': 'stage2-cross-cause-v1', 'causes': causes}
    return chapters, wires, evidence


@pytest.mark.parametrize('fault', ['claim', 'inference', 'other_holder', 'opposite'])
def test_initial_knowledge_requires_matching_confirmed_support_and_free_repair(fault):
    _chapters, wires, evidence = _initial_case()
    bad = deepcopy(wires['book'])
    support = bad['facts'][-1]['evidence'][0]
    if fault in ('claim', 'inference'):
        support['kind'] = fault
    elif fault == 'other_holder':
        support['holder'] = 'a_jie'
    else:
        support['polarity'] = 'positive'
    rejected, errors = compile_book_observation(bad, evidence=evidence)
    assert rejected is None and any('缺少同持有者、同极性的 confirmed 证据' in e for e in errors)
    located = book_patch_locate(bad, errors, evidence=evidence)
    assert located and [path for path, _ in located] == [('facts', 2)]
    good, errors = compile_book_observation(wires['book'], evidence=evidence)
    assert good is not None and errors == []
    initial, = compile_initial_knowledge(good)
    assert (initial.knowledge_id, initial.holder, initial.polarity) == (
        'k0_01', 'yun_niang', 'negative')
    assert initial.proposition.ref == 'f03'


def test_late_narrative_evidence_can_confirm_knowledge_that_predates_opening():
    _chapters, wires, evidence = _initial_case()
    # 晚章回述开场知识，不把叙述出现时间当成知情时间。
    evidence['e17_03']['text'] += '她在开篇之前就已确认钥匙不在旧匣中。'
    wires['book']['facts'][-1]['evidence'][0]['evidence_refs'] = ['e17_03']
    book, errors = compile_book_observation(wires['book'], evidence=evidence)
    assert book is not None and not errors, errors
    assert compile_initial_knowledge(book)[0].basis[0].segment == 'e17_03'


@pytest.mark.parametrize('polarity', ['negative', 'positive'])
def test_repeated_or_contradictory_initial_knowledge_is_refused(polarity):
    _chapters, wires, evidence = _initial_case()
    fact = wires['book']['facts'][-1]
    fact['known_at_start'].append({'holder': 'yun_niang', 'polarity': polarity})
    fact['evidence'].append({'kind': 'confirmed', 'holder': 'yun_niang',
                             'polarity': polarity, 'evidence_refs': ['e01_01']})
    book, errors = compile_book_observation(wires['book'], evidence=evidence)
    assert book is None and any('开场知识不得重复持有者' in e for e in errors)


def test_initial_knowledge_canonical_holder_and_conflict_route_are_closed():
    assert route_owner_issue('source_evidence_conflict', conflict_anchor='k0_01') == (
        'revise_upstream', 'str06.stage2.book')
    with pytest.raises(ValidationError, match='引用未知人物'):
        CharactersCore.model_validate({
            'characters': {'hero': {'canonical': '主角', 'identity_bindings': [],
                'surfaces': [{'surface': '主角', 'source_refs': ['e01_01'], 'from_chapter': 1}]}},
            'initial_knowledge': [{'knowledge_id': 'k0_01', 'holder': 'ghost',
                'proposition': {'ref': 'f01', 'statement': '钥匙在旧匣中'},
                'polarity': 'negative', 'basis': [{'segment': 'e01_01', 'quote': '证据'}]}]})


def test_initial_knowledge_authorizes_restatement_before_later_confirmation():
    from contracts.schema.design import TargetDelta
    from contracts.schema.grounding import BindingEntry, SemanticBinding, SourceObligation, _lock_id
    from tests.test_str06_fold_guard import _graph

    graph = _graph()
    kp = _lock_id('kp_', 'f01')
    accepted = TargetDelta(domain='knowledge', key=kp, holder='ch1', op='set',
                           value='false', scope='book', knowledge_mode='known')
    graph.initial_state = (accepted,)
    graph.scenes[0].beats[0].target_delta = (accepted,)
    cognition = {'handle': 'b02_01.c1', 'source_kind': 'cognition', 'holder': 'ch1',
        'op': 'set', 'mode': 'confirms', 'polarity': 'negative', 'support': 'confirmed',
        'reader_visible': False, 'proposition_ref': 'f01',
        'proposition_statement': '钥匙在旧匣中'}
    baseline = {**cognition, 'handle': 'k0_01', 'source_kind': 'initial_knowledge', 'mode': 'learns'}
    obligation = SourceObligation(obligation_id='ob01', kind='character_arc', statement='再次确认',
        must_preserve=True, satisfaction='保留确认', anchors=[cognition],
        lineage={'blueprint_refs': ['b02_01'], 'character_ids': ['ch1']})
    binding = SemanticBinding(binding_id='test', preserve_checkpoint_sha256='p' * 64,
        obligations=[obligation], entries=[BindingEntry(obligation_id='ob01',
            scene_id='s02', beat_ref='s02#bt1')], unbound=[])
    assert len(check_epistemic_lock_order(graph, binding)) == 1
    obligation.anchors = (*obligation.anchors, baseline)
    assert check_epistemic_lock_order(graph, binding) == []


def test_heard_claim_does_not_prove_a_current_knowledge_read_and_mixed_entries_are_safe():
    from contracts.schema.context import KnowledgeEntry, StateEntry
    from contracts.schema.state import StateRead

    knowledge_read = StateRead(domain='knowledge', key='kp_x', holder='hero')
    state_read = StateRead(domain='world', key='key_location')
    state = StateEntry(domain='world', key='key_location', value='别处',
                       status='set', established_by='init')
    heard = KnowledgeEntry(holder='hero', key='kp_x', proposition='钥匙在旧匣中',
        mode='heard', polarity='negative', status='set', established_by='s01#b01')
    believed = heard.model_copy(update={'mode': 'believes', 'polarity': 'positive'})
    assert not _read_resolves_in(knowledge_read, (state, heard))
    assert _read_resolves_in(knowledge_read, (state, heard, believed))
    assert _read_resolves_in(state_read, (heard, state))


def test_initial_negative_knowledge_reaches_published_truth_design_ir_and_hook(tmp_path):
    chapters, wires, _evidence = _initial_case()

    def ask(owner, prompt, validate, patch=None, downstream_preview=None):
        candidate = deepcopy(wires[owner])
        assert validate(candidate) == [], owner
        return candidate, synthetic_ref(owner, prompt)

    root = tmp_path / 'initial_knowledge'
    repository = Str06Repository(root)
    repository.attach_context(make_context(root, ledger=CostLedger(path=None)))
    report = run_workflow(repository=repository, chapters=chapters, hook_copy='',
                          ask=ask, ask_text=None, ask_body=None, budget=800,
                          through='stage2', node_filter=STAGE2_NODES)
    assert report['undecided'] == 0
    characters = repository.read_model('stage2/characters/characters.json')
    blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    initial, = characters.initial_knowledge
    assert initial.proposition.statement == '钥匙在旧匣中' and initial.polarity == 'negative'
    assert not any(c.holder == 'reader' and c.proposition.ref == 'f03'
                   for chapter in blueprint.chapters for beat in chapter.beats for c in beat.cognition)
    fold = repository.read('stage2/acceptance/fold.json')
    assert 'yun_niang=learns[negative](known_at_start)' in '\n'.join(fold['snapshots']['0'])
    view = project_preserve_view(characters.core_view(), blueprint)
    assert '[k0_01] yun_niang 开场已确认 negative 命题 f03' in view

    name_map = NameMapV6(entries=tuple(NameAssignment(
        char_ref=slug, name=None, aliases={}, keep_reason='测试保留称谓')
        for slug in characters.characters))
    preserve = {'protocol_version': STAGE4_PRESERVE_PROTOCOL, 'obligations': [{
        'kind': 'initial_state', 'statement': '保留云娘开场已确认钥匙不在旧匣的知识',
        'anchors': ['k0_01'], 'must_preserve': True,
        'satisfaction': '云娘从开篇就知道钥匙不在旧匣中', 'requires': []}], 'series': []}
    checkpoint, errors = assemble_preserve(
        preserve, policy_id='test', policy_version=13, characters=characters.core_view(),
        blueprint=blueprint, name_map=name_map, name_map_sha256='n' * 64,
        provider='fake', model='fake', request_sha256='r' * 64)
    assert checkpoint is not None and errors == [], errors
    assert checkpoint.obligations[0].anchors[0].source_kind == 'initial_knowledge'
    assert checkpoint.obligations[0].lineage.character_ids == ('yun_niang',)
    brief, _ = project_brief(checkpoint, characters.core_view(), blueprint,
        structure=characters.name_structure, name_map=name_map)
    obligations = design_obligation_index(checkpoint, brief)
    requirement = obligations['ob01']
    assert requirement.knowledge_changes == ()
    assert requirement.initial_knowledge[0].polarity == 'negative'
    kp = requirement.initial_knowledge[0].kp
    short = design_reference_aliases(brief)[kp]
    prompt = project_design_input(brief, obligations)
    assert prompt['requirements']['ob01']['initial_knowledge'][0]['kp'] == short
    assert kp not in str(prompt), '完整命题句柄不能漏进瘦短引用协议'
    wire = {'protocol_version': STAGE4_DESIGN_WIRE_PROTOCOL,
        'book_style': '保留开场已知和读者不知的边界。',
        'initial_surfaces': [{'char_id': 'yun_niang', 'surfaces': ['云娘']}],
        'initial_state': [{'domain': 'knowledge', 'key': short, 'holder': 'yun_niang',
                           'value': 'false', 'knowledge_mode': 'known'}],
        'scenes': [{'driver': {'focal_actor': 'yun_niang', 'immediate_want': '找到钥匙',
            'opposition': '去向不明', 'turn': '转去别处寻找'}, 'brief': '开场即知不在旧匣。',
            'beats': [{'ev': '云娘转去别处寻找钥匙', 'who': ['yun_niang'], 'real': ['ob01']}]}],
        'chapters': [{'purpose': '寻找钥匙', 'scenes': 1}]}
    kwargs = {'known_char_ids': set(characters.characters), 'brief': brief, 'obligations': obligations,
        'source_narration': brief.source_narration, 'expression_requirements': brief.expression}
    body, errors = assemble_body_design(wire, **kwargs)
    assert body is not None and errors == [], errors
    assert body.initial_state[0].value == 'false'
    binding, errors = derive_semantic_binding(
        design_placements(design_wire_model(wire, brief)), body, checkpoint)
    assert binding is not None and not errors, errors
    plan, errors = compile_execution_plan(body, binding)
    assert plan is not None and not errors, errors
    statements = {p.kp_id: p.statement for p in brief.propositions}
    ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
        total_budget=1200, length_band=(0.8, 1.2), projection=characters.name_projection(),
        structure=characters.name_structure, name_map=name_map, kp_statements=statements)
    text = render_chapter_packet(project_chapter_packet(ir, 1))
    assert '并非：钥匙在旧匣中' in text
    assert '并非：钥匙在旧匣中' in render_hook_fact_text(
        characters.name_projection(), body.initial_state[0].address(), body.initial_state[0], statements)

    for fault in ('missing', 'positive', 'reader'):
        bad = deepcopy(wire)
        if fault == 'missing':
            bad['initial_state'] = []
        elif fault == 'positive':
            bad['initial_state'][0]['value'] = 'true'
        else:
            bad['initial_state'][0]['holder'] = 'reader'
            bad['initial_state'][0]['confirms'] = {'domain': 'world', 'key': 'key_location'}
            bad['initial_state'].append({'domain': 'world', 'key': 'key_location',
                                         'value': '别处'})
        rejected, errors = assemble_body_design(bad, **kwargs)
        assert rejected is None and any(getattr(e, 'code', '') in (
            'design_knowledge_lock', 'design_kp_mode_authority') for e in errors), (fault, errors)
    late = body.model_copy(deep=True)
    delta = late.initial_state[0]
    late.initial_state = ()
    late.scenes[0].beats[0].target_delta = (delta,)
    errors = []
    _check_lock_acceptance(late, brief, errors, obligations=obligations,
                           real_sites={'ob01': [(0, 0)]})
    assert any(getattr(e, 'code', '') == 'design_knowledge_lock' for e in errors), errors
