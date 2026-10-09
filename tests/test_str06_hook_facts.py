"""Hook v2：一次建立、时态和有效区间的反例，及已购回答免费恢复的真实预检。"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from contracts.schema.design import BodyDesign, TargetDelta
from contracts.schema.hook import HookConstraints
from novel.stages.stage4.hook import assemble_hook_design, hook_patch_slice, hook_patch_legal_values, publish_graph
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage4.hook_facts import established_fact, fact_ref_of, hook_facts
from novel.stages.stage4.hook_wire import (
    expand_hook_wire, hook_catalog, hook_wire_schema, project_hook_input, upgrade_purchased_hook_wire,
)
from tests.factories import hook_wire_v2
from tests.test_str06_hook import _characters, _green_pair, _hook_wire, _projection


ADDRESS = ('character', ('yun_niang',), '', 'marital_status')


def _accept(body, wire):
    return assemble_hook_design(wire, body=body, known_char_ids={'yun_niang', 'fu_jun'},
                                name_projection=_projection())


def _append_delta(body, si, bi, delta):
    raw = body.model_dump(mode='json')
    raw['scenes'][si]['beats'][bi]['target_delta'].append(delta)
    return BodyDesign.model_validate(raw)


def _state(**over):
    return {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
            'key': 'marital_status', 'op': 'set', 'value': '已婚',
            'modality': 'actual', 'scope': 'book', **over}


def test_later_same_address_material_does_not_reject_established_initial_fact():
    """本书首答反例：init 已成立，同地址在材料拍再次更新，不等于引用该拍增量。"""
    body, _, _ = _green_pair()
    body = _append_delta(body, 1, 0, _state())
    wire = hook_wire_v2(_hook_wire())
    before = deepcopy(wire)
    hook, errors = _accept(body, wire)
    assert hook is not None and not errors, errors
    assert hook.opening_facts[0].ref == 'init' and wire == before
    # 实际引用材料拍这一次建立，仍然拒绝；不能靠地址相同把它当旧事实。
    wire['opening_facts'][0]['fact_ref'] = fact_ref_of('sc02#b01', ADDRESS)
    hook, errors = _accept(body, wire)
    assert hook is None and any('材料拍' in e for e in errors)


@pytest.mark.parametrize('change', [_state(), _state(op='unset', value=None, modality=None),
                                    _state(op='unknown', value=None, modality=None)])
def test_invalidated_initial_fact_is_still_rejected(change):
    body, _, _ = _green_pair()
    body = _append_delta(body, 0, 0, change)
    hook, errors = _accept(body, hook_wire_v2(_hook_wire()))
    assert hook is None and any('失效' in e for e in errors)


@pytest.mark.parametrize('op', ['unknown', 'unset'])
def test_unknown_and_unset_are_not_established_opening_facts(op):
    body, _, _ = _green_pair()
    delta = body.initial_state[0].model_copy(update={'op': op, 'value': None, 'modality': None})
    body = body.model_copy(update={'initial_state': (delta,)})
    hook, errors = _accept(body, hook_wire_v2(_hook_wire()))
    assert hook is None and any('未建立' in e for e in errors)
    assert fact_ref_of('init', ADDRESS) not in hook_wire_schema(body)[
        '$defs']['HookOpeningFactWire']['properties']['fact_ref']['enum']


def test_scene_overlay_expires_and_does_not_destroy_book_initial_value():
    body, _, _ = _green_pair()
    body = _append_delta(body, 0, 0, _state(scope='scene'))
    wire = hook_wire_v2(_hook_wire())
    assert _accept(body, wire)[1] == []
    wire['opening_facts'][0]['fact_ref'] = fact_ref_of('sc01#b01', ADDRESS)
    assert any('失效' in e for e in _accept(body, wire)[1])


def test_expired_scene_establishment_is_rejected_even_if_book_value_is_identical():
    body, _, _ = _green_pair()
    body = _append_delta(body, 0, 0, _state(scope='scene', value=body.initial_state[0].value))
    wire = hook_wire_v2(_hook_wire())
    wire['opening_facts'][0]['fact_ref'] = fact_ref_of('sc01#b01', ADDRESS)
    assert any('失效' in e for e in _accept(body, wire)[1])


def test_coldopen_anchor_is_first_presented_material_and_reader_uses_max_horizon():
    body, _, _ = _green_pair()
    wire = hook_wire_v2(_hook_wire())
    # 第一材料 sc02#b01，随后回到 sc01#b02；婚礼事实 sc01#b01 在冷开前已成立。
    wire['opening_facts'] = [{'fact_ref': fact_ref_of('sc01#b01',
        ('relation', ('yun_niang', 'fu_jun'), '', 'marital_status')), 'audience': 'world'}]
    assert _accept(body, wire)[1] == []
    wire['opening_facts'] = [{'fact_ref': fact_ref_of('sc02#b02',
        ('knowledge', ('fu_jun',), 'yun_niang', 'secret')), 'audience': 'reader'}]
    assert any('视界' in e for e in _accept(body, wire)[1])


@pytest.mark.parametrize('modality', ['planned', 'expected', 'conditional'])
def test_plans_are_not_world_facts_and_writer_keeps_their_modality(modality):
    body, _, _ = _green_pair()
    delta = body.initial_state[0].model_copy(update={'modality': modality})
    body = body.model_copy(update={'initial_state': (delta,)})
    wire = hook_wire_v2(_hook_wire())
    assert any('不能把' in e for e in _accept(body, wire)[1])
    wire['opening_facts'][0]['audience'] = 'pov'
    hook, errors = _accept(body, wire)
    assert hook is not None and not errors, errors
    context, errors = compile_hook_context(publish_graph(body, hook), _projection(),
                                          HookConstraints(target_chars=280, hard_max_chars=360))
    assert context is not None and not errors, errors
    text = json.dumps(context.model_dump(mode='json'), ensure_ascii=False)
    assert ('尚未发生' if modality != 'conditional' else '条件成立时才发生') in text


def test_pov_knowledge_requires_correct_holder_and_keeps_knowledge_mode():
    body, _, _ = _green_pair()
    delta = TargetDelta.model_validate({'domain': 'knowledge', 'subjects': ['fu_jun'],
        'holder': 'fu_jun', 'key': 'rumor', 'op': 'set', 'value': '玉佩另有主人',
        'knowledge_mode': 'suspected', 'scope': 'book'})
    body = body.model_copy(update={'initial_state': (*body.initial_state, delta)})
    wire = hook_wire_v2(_hook_wire())
    wire['opening_facts'] = [{'fact_ref': fact_ref_of('init', delta.address()), 'audience': 'pov'}]
    assert any('持有人' in e for e in _accept(body, wire)[1])
    wire['pov']['focalizers'] = ['fu_jun']
    hook, errors = _accept(body, wire)
    assert hook is not None and not errors, errors
    context, errors = compile_hook_context(publish_graph(body, hook), _projection(),
                                          HookConstraints(target_chars=280, hard_max_chars=360))
    assert context is not None and not errors, errors
    assert '怀疑' in json.dumps(context.model_dump(mode='json'), ensure_ascii=False)


def test_forbidden_fact_is_one_occurrence_not_every_update_at_same_address():
    body, _, _ = _green_pair()
    body = _append_delta(body, 1, 1, _state())
    wire = hook_wire_v2(_hook_wire())
    wire['forbidden_reveals'] = [{'kind': 'fact', 'fact_ref': fact_ref_of('sc02#b02', ADDRESS)}]
    assert _accept(body, wire)[1] == []
    wire['forbidden_reveals'][0]['fact_ref'] = fact_ref_of('init', ADDRESS)
    assert any('禁止揭示集' in e for e in _accept(body, wire)[1])


def test_catalog_reference_is_stable_and_ambiguous_establishment_is_not_accepted():
    body, _, _ = _green_pair()
    initial_ref = fact_ref_of('init', ADDRESS)
    extra = TargetDelta.model_validate(_state(key='residence', value='陆家'))
    changed = body.model_copy(update={'initial_state': (extra, *body.initial_state)})
    assert hook_catalog(body)[initial_ref] == hook_catalog(changed)[initial_ref]
    duplicate = body.model_copy(update={'initial_state': (*body.initial_state, body.initial_state[0])})
    assert established_fact(hook_facts(duplicate), 'init', ADDRESS) is None


def test_v2_closed_schema_rejects_made_up_ref_and_cross_beat_withholding():
    body, _, _ = _green_pair()
    wire = hook_wire_v2(_hook_wire())
    wire['opening_facts'][0]['fact_ref'] = 'f不存在'
    assert any('未知事实引用' in e for e in _accept(body, wire)[1])
    wire = hook_wire_v2(_hook_wire())
    wire['materials'][0]['withheld'] = [fact_ref_of('init', ADDRESS)]
    assert any('不属于该拍' in e for e in _accept(body, wire)[1])
    wire = hook_wire_v2(_hook_wire())
    wire['forbidden_reveals'][0]['address'] = {'key': '乱加字段'}
    assert any('形状非法' in e for e in _accept(body, wire)[1])
    assert any('protocol_version' in e for e in _accept(body, _hook_wire())[1])


def test_narrow_patch_has_initial_facts_boundaries_and_complete_branch_schema():
    body, _, _ = _green_pair()
    wire = hook_wire_v2(_hook_wire())
    sliced = hook_patch_slice(body, wire, [('opening_facts', 0)])
    assert sliced['facts'][fact_ref_of('init', ADDRESS)]['ref'] == 'init'
    assert sliced['materials'] == wire['materials'] and sliced['pov'] == wire['pov']
    legal = hook_patch_legal_values(body=body)
    assert set(legal['fact_refs']) == set(sliced['facts'])
    assert legal['wire_schema'] == hook_wire_schema(body)
    projected = project_hook_input(body)
    assert projected['facts'] == sliced['facts']
    assert projected['world_rules'] == list(body.world_rules)
    assert projected['character_voices'] == [v.model_dump(mode='json') for v in body.character_voices]
    assert projected['expression_decisions'] == [d.model_dump(mode='json') for d in body.expression_decisions]
    assert all('fact_refs' in b and 'target_delta' not in b
               for s in projected['scenes'] for b in s['beats'])
    for scene, projected_scene in zip(body.scenes, projected['scenes'], strict=True):
        for beat, projected_beat in zip(scene.beats, projected_scene['beats'], strict=True):
            assert {k: v for k, v in projected_beat.items() if k != 'fact_refs'} == beat.model_dump(
                mode='json', exclude={'target_delta'})
    assert all('length_budget' not in c for c in projected['chapters'])


def test_explicit_v1_upgrade_is_lossless_readonly_and_rejects_dangling_address():
    body, _, _ = _green_pair()
    v1 = _hook_wire()
    original = deepcopy(v1)
    upgraded = upgrade_purchased_hook_wire(v1, body)
    canonical, errors = expand_hook_wire(upgraded, body)
    assert not errors and canonical is not None and v1 == original
    assert canonical['opening_facts'][0]['ref'] == 'init'
    assert canonical['design_brief'] == v1['design_brief']
    assert _accept(body, upgraded)[1] == []
    v1['opening_facts'][0]['address']['key'] = '不存在'
    with pytest.raises(ValueError, match='无法无损换算'):
        upgrade_purchased_hook_wire(v1, body)
    with pytest.raises(ValueError, match='只接受显式'):
        upgrade_purchased_hook_wire(upgraded, body)


def test_upgrade_cli_uses_authenticated_purchase_and_real_acceptance_without_published_graph(tmp_path, monkeypatch, capsys):
    """真实 Preserve/Design → v1 已购证据 → 工具捕获生产闸/下游预检；不发新调用。"""
    from novel.llm.paid_requests import ask_json
    from novel.stages.chain import run_body_design
    from tests.test_str06_candidate_preflight import _prepare
    from tests.test_str06_r3_selfheal import _ScriptedTransport, _request
    from tests.test_str06_hook import _hook_wire_v7
    from tools import str06_review
    ctx, _, _, _ = _prepare(tmp_path)
    run_body_design(ctx)
    transport = _ScriptedTransport([json.dumps(_hook_wire_v7(), ensure_ascii=False)])
    from tests.test_str06_r3_selfheal import _json_session
    _, _, session = _json_session(tmp_path, transport)
    _, answer = ask_json(session, 'hook_design', _request(), lambda w: [])
    repo = ctx.repository
    source = SimpleNamespace(chapters=ctx.chapters, hook_copy=None)
    monkeypatch.setattr(str06_review, 'load_stage1_chapters', lambda *_a: source)
    assert not repo.path('stage4/design/presentation_graph.json').exists()
    ledger = repo.root.parent / '_service/ledger.jsonl'
    before = ledger.read_bytes()
    code = str06_review.main(['--base', str(repo.root.parent), '--sid', repo.root.name,
        '--save', 'upgrade-hook', '--baseline-request-sha256', answer.request_sha256,
        '--request-profile', json.dumps({'provider': 'deepseek', 'model': 'deepseek-flash',
            'reasoning_effort': 'high', 'thinking_mode': 'on'})])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result['new_paid_calls'] == 0
    assert result['downstream']['status'] == 'ran' and result['downstream']['errors'] == []
    assert ledger.read_bytes() == before and len(transport.calls) == 1
    assert not repo.path('stage4/design/presentation_graph.json').exists()
    note = json.loads((repo.root / 'runtime/review/hook_v2_upgrade/hook_design_patch_note.json').read_text(encoding='utf-8'))
    assert note['base_response_identity'] == answer.request_sha256
    assert note['applies_to_request_sha256'] != answer.request_sha256
