#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_design_slim —— 设计瘦 wire（CS-3 第一步，2026-10-03）。

覆盖 `contracts/schema/design.py::ResolvedDesign`、
`novel/stages/stage4/slim.py::expand_design_wire` 的确定性派生（know 标注→
知情/读者行、默认值回填、entry 派生、world_rules/expression/narration 顶层
派生）与 `assemble_body_design` 的字面量路由（v7→expand→既有管道＋必保义务
覆盖闸）。语义判据不放宽：C1/C2/C6/C9 锁校验、D4 确认闭合、称谓闭集全部
照旧（经既有管道）。r10b 全产物实证见 REQUIREMENTS CS-3 行。
"""
from __future__ import annotations

from types import SimpleNamespace

from contracts.schema.grounding import ReconstructionBrief
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.slim import expand_design_wire

PV_CURRENT = 'stage4-design-v13'
PV5 = 'stage4-design-v6'


def test_v12_plain_author_text_is_accepted_and_never_rewritten():
    """rc26（命名前移批 C）：identity-text-v1 退役后 Design 自有自然语言一律
    普通目标文本——无 [[name:…]]／[[text|…]] 标记的 wire 经真实组装入口接受，
    assembler 不改写作者字段、不替普通词补姓名标记。"""
    from copy import deepcopy
    from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
    brief = _brief()
    wire = _current_wire()
    wire['protocol_version'] = STAGE4_DESIGN_WIRE_PROTOCOL
    before = deepcopy(wire)
    body, errors = assemble_body_design(
        wire, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief,
        obligations=_obligations(brief))
    assert errors == [], errors
    assert wire == before, '普通目标文本不经标记协议，assembler 不改写作者字段'
    assert body.scenes[0].beats[0].event == '夫君坦白不能生育。'
    assert body.scenes[0].realization_brief == '新婚夜坦白，重心在荒诞与错愕的对照。'
    assert body.scenes[0].beats[0].target_delta[0].value == '幼时落水伤了根本'


def test_v12_schema_carries_current_protocol_and_retired_literals_stay_archived():
    """现役 wire 协议是 stage4-design-v13；v11 及更早已购候选只读归档，
    不再作为读面接收（I-13 无隐式迁移）；指令面不再要求逐次声明候选字面。"""
    import json
    import pytest
    from pydantic import ValidationError
    from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
    from novel.stages.common import design_rules
    from novel.stages.stage4.slim import design_wire_model
    assert STAGE4_DESIGN_WIRE_PROTOCOL == 'stage4-design-v13'
    rules = design_rules(known_char_ids={'yun_niang'})
    schema = json.loads(rules.rsplit('\n', 1)[1])
    assert schema['title'] == 'DesignWireV13'
    assert schema['properties']['protocol_version']['const'] == STAGE4_DESIGN_WIRE_PROTOCOL
    assert '需逐次声明的姓名候选字面' not in rules, '候选覆盖硬闸随协议退役'
    wire = _current_wire()
    wire['protocol_version'] = STAGE4_DESIGN_WIRE_PROTOCOL
    # 带 Brief 的解码把短引用展开回 canonical，并归一成内部 v9 模型。
    assert design_wire_model(wire, _brief()).protocol_version == PV_CURRENT
    from novel.stages.stage4.slim import project_design_input
    for protocol in ('stage4-design-v9', 'stage4-design-v10',
                     'stage4-design-v11', 'stage4-design-v12'):
        archived = _current_wire()
        archived['protocol_version'] = protocol
        before = __import__('copy').deepcopy(archived)
        with pytest.raises(ValidationError):
            design_wire_model(archived, _brief())
        with pytest.raises(ValueError, match='未登记'):
            design_rules(protocol=protocol)
        with pytest.raises(ValueError, match='未登记'):
            project_design_input(_brief(), _obligations(_brief()), protocol=protocol)
        assert archived == before


def test_model_design_schema_keeps_dynamic_knowledge_branches_valid():
    import json
    from jsonschema import Draft202012Validator
    from novel.stages.common import design_rules
    for refs in ({}, {'ob01.k01': False, 'ob01.k02': True,
                     'ob02.k01': False, 'ob02.k02': True}):
        schema = json.loads(design_rules(known_char_ids={'lead'},
                                        knowledge_refs=refs).rsplit('\n', 1)[1])
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator({
            '$ref': '#/$defs/SlimKnowledgePlacement', '$defs': schema['$defs']})
        assert len(schema['$defs']['SlimKnowledgePlacement'].get('allOf', [])) <= 2
        samples = [({'ref': 'ob01.k99'}, False)]
        if refs:
            fact = {'domain': 'world', 'key': 'evidence'}
            samples.extend([
                ({'ref': 'ob01.k01'}, True),
                ({'ref': 'ob01.k02'}, False),
                ({'ref': 'ob01.k02', 'confirms': None}, False),
                ({'ref': 'ob01.k02', 'confirms': fact}, True),
                ({'ref': 'ob02.k02', 'confirms': fact}, True),
                ({'ref': 'ob02.k02'}, False),
                ({'ref': 'ob02.k01', 'confirms': fact}, False),
                ({'ref': 'ob02.k01'}, True),
                ({'ref': 'ob01.k01', 'confirms': fact}, False),
                ({'ref': 'ob01.k02', 'confirms': {
                    'domain': 'knowledge', 'key': 'p', 'holder': 'reader'}}, False),
            ])
        assert all((not list(validator.iter_errors(row))) == passes
                   for row, passes in samples)


def test_design_input_keeps_details_source_blind_and_projects_summary_coverage():
    from novel.stages.stage4.slim import project_design_input
    brief = _brief()
    raw = brief.model_dump(mode='json')
    raw['requirements'][0].update({
        'details': [{'detail_id': 'dt_1234567890abcdef',
                     'text': '先佯装走开，再从床底钻出'}],
        'series_group': 'sr_group',
    })
    raw['requirements'][1].update({
        'series_group': 'sr_group', 'summarizes_cores': ['req_aaaa'],
    })
    brief = ReconstructionBrief.model_validate(raw)
    projected = project_design_input(brief, _obligations(brief))
    first, summary = (projected['requirements'][ref] for ref in ('ob01', 'ob02'))
    assert first['details']
    assert '先佯装走开，再从床底钻出' in str(first['details'])
    assert summary['summarizes'] == ['ob01']
    assert 'summarizes_cores' not in summary
    assert 'anchor' not in str(first['details']) and 'quotes' not in str(first['details'])
    assert 'req_aaaa' not in str(projected)


def _brief() -> ReconstructionBrief:
    return ReconstructionBrief.model_validate({
        'brief_id': 'brief_test', 'preserve_checkpoint_sha256': 'a' * 64,
        'contract_version': 'str06-rc27',
        'requirements': [
            {'core_id': 'req_aaaa', 'summary': '芸娘确知夫君不育',
             'kind': 'reveal', 'must_preserve': True, 'satisfaction': '开场揭示',
             'participants': ['lu_fujun', 'yun_niang'], 'requires_cores': [],
             'locks': [], 'knowledge_changes': [{'op': 'set', 'polarity': 'positive', 'change_id': 'kc_1111111111111111', 'holder': 'yun_niang', 'kp': 'kp_1111111111111111', 'mode': 'known'}],
             'realize_locks': [], 'series_group': None, 'series_mechanism': None},
            {'core_id': 'req_bbbb', 'summary': '借种之计成事',
             'kind': 'event_core', 'must_preserve': True, 'satisfaction': '孕事坐实',
             'participants': [], 'requires_cores': [], 'locks': [],
             'knowledge_changes': [], 'realize_locks': [], 'series_group': None,
             'series_mechanism': None},
            {'core_id': 'req_cccc', 'summary': '可综合的场面设定',
             'kind': 'world_fact', 'must_preserve': False, 'satisfaction': '',
             'participants': [], 'requires_cores': [], 'locks': [],
             'knowledge_changes': [], 'realize_locks': [], 'series_group': None,
             'series_mechanism': None},
        ],
        'propositions': [
            {'kp_id': 'kp_1111111111111111', 'statement': '夫君不能生育',
             'publicly_claimed_by': ()}],
        'knowledge_locks': [
            {'op': 'set', 'polarity': 'positive', 'holder': 'yun_niang', 'kp': 'kp_1111111111111111', 'mode': 'known'}],
        'fact_locks': [], 'book_constraints': [
            {'constraint_id': 'wc_01', 'statement': '女子极难自立门户'}],
        'source_narration': {'person': 'first', 'focalizers': ['yun_niang']},
        'expression': [{'ex_id': 'ex_01', 'kind': 'humor', 'statement': '一本正经的荒诞'}],
        'expression_readings': {'status': 'measured', 'paragraph_mean_chars': 20.0,
                                'sentence_mean_chars': 21.0, 'dialogue_ratio': 0.3},
    })


def _obligations(brief: ReconstructionBrief) -> dict:
    by_summary = {r.summary: r for r in brief.requirements}
    return {'ob01': by_summary['芸娘确知夫君不育'],
            'ob02': by_summary['借种之计成事'],
            'ob03': by_summary['可综合的场面设定']}


def _current_wire() -> dict:
    return {
        'protocol_version': PV_CURRENT,
        'book_style': '第一人称克制叙述，短句为主。',
        'character_voices': [
            {'char_id': 'yun_niang', 'voice': '对外端方，内心直白。'}],
        'initial_state': [
            {'domain': 'relation', 'key': 'marital_status',
             'subjects': ['lu_fujun', 'yun_niang'], 'value': 'married'}],
        'initial_surfaces': [{'char_id': 'yun_niang', 'surfaces': ['芸娘']}],
        'scenes': [
            {'driver': {'focal_actor': 'lu_fujun', 'immediate_want': '坦白',
                        'opposition': '难以启齿', 'turn': '抛出借种计划'},
             'brief': '新婚夜坦白，重心在荒诞与错愕的对照。',
             'reads': [{'domain': 'relation', 'key': 'marital_status',
                        'subjects': ['lu_fujun', 'yun_niang']}],
             'beats': [
                 {'ev': '夫君坦白不能生育。', 'who': ['lu_fujun', 'yun_niang'],
                  'real': ['ob01'], 'know': [{'ref': 'ob01.k01'}],
                  'state': [{'at': 'lu_fujun', 'key': 'health',
                             'val': '幼时落水伤了根本'}],
                  'ex': ['ex_01']},
                 {'ev': '夫君提出借种计划。', 'who': ['lu_fujun'],
                  'state': [{'at': 'lu_fujun+pei_shuyuan', 'key': 'kinship',
                             'val': 'cousins'},
                            {'at': 'lu_fujun', 'key': 'intent',
                             'val': '借种计划仍在推进', 'modality': 'planned'}]},
             ]},
            {'driver': {'focal_actor': 'yun_niang', 'immediate_want': '安身',
                        'opposition': '世道', 'turn': '认清无路可退'},
             'brief': '夜深算账，处境逼迫。',
             'beats': [
                 {'ev': '芸娘认清无路可退，决意留下。',
                  'who': ['yun_niang'], 'real': ['ob02']}]},
        ],
        'chapters': [
            {'purpose': '开篇坦白与计划', 'scenes': 1},
            {'purpose': '权衡与顺从', 'scenes': 1,
             'entry': {'transition': 'time_skip', 'carryover': '借种之计待行'}},
        ],
    }


def test_expand_derives_defaults_and_top_level():
    brief = _brief()
    expanded, issues = expand_design_wire(_current_wire(), brief=brief,
                                          obligations=_obligations(brief))
    assert issues == []
    # 顶层派生：narration／world_rules／expression keep-all／均匀权重。
    assert expanded['narration'] == {'person': 'first', 'focalizers': ['yun_niang']}
    assert expanded['world_rules'] == ['女子极难自立门户']
    assert expanded['expression_decisions'] == [
        {'ex_id': 'ex_01', 'action': 'keep', 'reason': None, 'target_statement': None}]
    assert expanded['scene_weights'] == [1.0, 1.0]
    scene = expanded['scenes'][0]
    # pov 缺省＝唯一焦点；voice＝该人物声线。
    assert scene['delivery']['pov'] == 'yun_niang'
    assert scene['delivery']['voice'] == '对外端方，内心直白。'
    assert scene['beats'][0]['delivery_hint'] == ''
    # entry：recap 由 transition 派生。
    assert expanded['chapters'][1]['entry']['recap'] == 'brief_orientation'
    assert expanded['chapters'][1]['entry']['anchor'] is None


def test_expand_know_derives_knowledge_at_selected_beat():
    brief = _brief()
    expanded, issues = expand_design_wire(_current_wire(), brief=brief,
                                          obligations=_obligations(brief))
    assert issues == []
    beat1 = expanded['scenes'][0]['beats'][0]
    assert beat1['target_delta'] == [
        {'domain': 'character', 'subjects': ('lu_fujun',), 'holder': None,
         'key': 'health', 'op': 'set', 'value': '幼时落水伤了根本',
         'scope': 'book', 'knowledge_mode': None,
         'modality': 'actual'},
        {'domain': 'knowledge', 'subjects': (), 'holder': 'yun_niang',
         'key': 'kp_1111111111111111', 'op': 'set', 'value': 'true',
         'scope': 'book', 'knowledge_mode': 'known',
         'modality': None, 'confirms': None},
    ]
    # 仅 real 承载标注不补造知情；这条义务没有认知变化。
    wire = _current_wire()
    wire['scenes'][1]['beats'][0]['real'] = ['ob02']
    wire['scenes'][0]['beats'].append(
        {'ev': '计划初次受挫（支撑拍）。', 'real': ['ob02']})
    expanded2, issues2 = expand_design_wire(wire, brief=brief,
                                            obligations=_obligations(brief))
    assert issues2 == []
    supporting = expanded2['scenes'][0]['beats'][2]['target_delta']
    accept = expanded2['scenes'][1]['beats'][0]['target_delta']
    assert supporting == [] and accept == []      # ob02 无锁：两侧都无增量


def test_expand_kp_unknown_exception_and_rejects_state_set_or_unset():
    brief = _brief()
    wire = _current_wire()
    # kp 显式值未知可走state；接受／撤回均须选择各自的know身份。
    wire['scenes'][1]['beats'][0]['state'] = [
        {'at': 'yun_niang', 'key': 'kp_11111111', 'op': 'unknown'}]
    expanded, issues = expand_design_wire(wire, brief=brief,
                                          obligations=_obligations(brief))
    assert issues == []
    assert expanded['scenes'][1]['beats'][0]['target_delta'] == [
        {'domain': 'knowledge', 'subjects': (), 'holder': 'yun_niang',
         'key': 'kp_1111111111111111', 'op': 'unknown', 'value': None,
         'scope': 'book', 'knowledge_mode': None,
         'modality': None}]

    wire2 = _current_wire()
    wire2['scenes'][0]['beats'][0]['state'].append(
        {'at': 'yun_niang', 'key': 'kp_11111111', 'val': 'true'})
    _, issues2 = expand_design_wire(wire2, brief=brief,
                                    obligations=_obligations(brief))
    assert any('经 know 标注派生' in m for _p, m in issues2)

    wire_unset = _current_wire()
    wire_unset['scenes'][1]['beats'][0]['state'] = [
        {'at': 'yun_niang', 'key': 'kp_11111111', 'op': 'unset'}]
    _, unset_issues = expand_design_wire(wire_unset, brief=brief, obligations=_obligations(brief))
    assert any('撤回须选择对应 unset 的 know 引用' in message for _, message in unset_issues)

    wire3 = _current_wire()
    wire3['scenes'][0]['beats'][0]['real'] = ['ob99']
    _, issues3 = expand_design_wire(wire3, brief=brief,
                                    obligations=_obligations(brief))
    assert any('闭集外义务编号' in m for _p, m in issues3)

    # 缺 obligations 映射：v6 无从展开，fail fast。
    _, issues4 = expand_design_wire(_current_wire(), brief=brief)
    assert any('obligations' in m for _p, m in issues4)


def test_assemble_routes_v7_and_enforces_coverage():
    brief = _brief()
    obligations = _obligations(brief)
    wire = _current_wire()
    body, errors = assemble_body_design(
        wire, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief,
        obligations=obligations)
    assert errors == [], errors
    assert body.scenes[0].beats[0].target_delta[1].domain.value == 'knowledge'
    assert body.world_rules == ('女子极难自立门户',)

    # 必保义务漏标注 → 覆盖闸拦下（同节点修订轮修复）。
    wire_missing = _current_wire()
    wire_missing['scenes'][1]['beats'][0].pop('real')
    _, errors_missing = assemble_body_design(
        wire_missing, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief,
        obligations=obligations)
    assert any('未被任何拍标注承载' in e and 'ob02' in e for e in errors_missing)

    # v5 wire 直通（兼容读面）：协议字面量仍是 v5 判定。
    expanded, _ = expand_design_wire(wire, brief=brief, obligations=obligations)
    body5, errors5 = assemble_body_design(
        expanded, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief)
    assert errors5 == [], errors5
    assert body5.scenes[0].beats[0].target_delta[1].domain.value == 'knowledge'


def test_design_rules_prompt_carries_current_protocol_and_obligation_table():
    from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
    from novel.stages.common import design_rules
    from novel.stages.stage4.slim import project_design_input
    rules = design_rules(known_char_ids={'yun_niang'})
    assert STAGE4_DESIGN_WIRE_PROTOCOL in rules and 'SlimDesignBeatV10' in rules
    assert 'real' in rules and 'obNN' in rules
    brief = _brief()
    table = project_design_input(brief, _obligations(brief))['requirements']
    assert table['ob01']['knowledge_changes']['ob01.k01'] == {'op': 'set', 'polarity': 'positive',
        'holder': 'yun_niang', 'kp': 'kp_11111111', 'mode': 'known'}
    assert 'ob03' in table      # 非必保也在清单（标注可选）


def test_required_background_needs_real_and_can_share_an_existing_action_beat():
    """付费配对反例：初态已有关系不能冒充必保承载，也不必因此新增说明拍。"""
    from copy import deepcopy
    raw = _brief().model_dump(mode='json')
    raw['requirements'][2].update(
        summary='芸娘与夫君是夫妻', kind='identity', must_preserve=True,
        satisfaction='开篇须呈现二人的夫妻身份', participants=['lu_fujun', 'yun_niang'])
    brief = ReconstructionBrief.model_validate(raw)
    obligations = {f'ob{i:02d}': r for i, r in enumerate(brief.requirements, start=1)}
    wire = _current_wire()
    wire['protocol_version'] = 'stage4-design-v13'
    wire['scenes'][0]['beats'][0]['ev'] = '新婚之夜，芸娘的夫君坦白不能生育。'
    before = deepcopy(wire)
    args = dict(known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
                expression_requirements=brief.expression,
                source_narration=brief.source_narration, brief=brief, obligations=obligations)
    _, errors = assemble_body_design(wire, **args)
    assert any('未被任何拍标注承载' in e and 'ob03' in e for e in errors)
    assert wire == before, 'validator 不从初态或自然语言猜测承载位'
    wire['scenes'][0]['beats'][0]['real'].append('ob03')
    body, errors = assemble_body_design(wire, **args)
    assert not errors, errors
    assert sum(len(s.beats) for s in body.scenes) == 3, '两个条件共用已有行动拍'
    assert wire['initial_state'] == before['initial_state']


def test_design_projection_preserves_complete_semantics_and_references():
    from copy import deepcopy
    import pytest
    from novel.stages.stage4.slim import project_design_input, knowledge_change_index, design_reference_aliases
    raw = _brief().model_dump(mode='json')
    raw['requirements'][1]['requires_cores'] = ['req_aaaa']
    raw['requirements'][1]['satisfaction'] = '完整条件不截断。' * 30
    brief = ReconstructionBrief.model_validate(raw)
    obligations = _obligations(brief)
    before = deepcopy(brief.model_dump())
    view = project_design_input(brief, obligations)
    assert view['requirements']['ob02']['requires'] == ['ob01']
    assert set(view['requirements']) == set(obligations)
    assert view['input_version'] == 'stage4-design-input-v3'
    aliases = design_reference_aliases(brief)
    # 非引用语义字段逐值保留；省略字段仅使用 v2 明示的空集合/null 默认值。
    for key, value in brief.model_dump(mode='json').items():
        if key not in {'brief_id', 'preserve_checkpoint_sha256', 'requirements',
                       'propositions', 'fact_locks', 'knowledge_locks'}:
            assert view.get(key, value if value in (None, [], {}) else None) == value
    for ob, requirement in obligations.items():
        for key, value in requirement.model_dump(mode='json').items():
            if key not in {'core_id', 'requires_cores', 'knowledge_changes', 'details', 'summarizes_cores'}:
                expected = [aliases[ref] for ref in value] if key in ('locks', 'realize_locks') else value
                assert view['requirements'][ob].get(key, value if value in (None, [], {}) else None) == expected
    for ref, (ob, change) in knowledge_change_index(obligations).items():
        row = view['requirements'][ob]['knowledge_changes'][ref]
        assert (row['holder'], row['kp'], row['mode'], row.get('requires_released', [])) == (
            change.holder, aliases[change.kp], change.mode, list(change.requires_released))
    for p in brief.propositions:
        row = view['propositions'][aliases[p.kp_id]]
        assert (row['statement'], row.get('publicly_claimed_by', [])) == (p.statement, list(p.publicly_claimed_by))
    for f in brief.fact_locks:
        row = view['fact_locks'][aliases[f.kf_id]]
        assert (row['statement'], row['kind'], row.get('subjects', []), row.get('modality')) == (
            f.statement, f.kind, list(f.subjects), f.modality)
    authorized = {(c['holder'], c['kp'], c['mode']) for r in view['requirements'].values()
                  for c in r.get('knowledge_changes', {}).values()}
    authorized.update((c['holder'], c['kp'], c['mode']) for c in view.get('knowledge_locks', []))
    assert authorized == {(k.holder, aliases[k.kp], k.mode) for k in brief.knowledge_locks}
    assert view['requirements']['ob03']['must_preserve'] is False
    assert brief.model_dump() == before
    with pytest.raises(ValueError, match='完整且唯一'):
        project_design_input(brief, {'ob01': obligations['ob01']})


def test_world_rule_key_reference_and_zero_info_value_refused():
    """CS-3 world 规则 id：书级规则断言用 wc_{nn} 引用（闭集＋val 回填
    'true'）；剧情事实 val='true'（零信息）结构性拒绝。"""
    brief = _brief()
    obligations = _obligations(brief)

    # 合法：初始态引用清单内规则编号（val 省略回填 'true'）。
    wire = _current_wire()
    wire['initial_state'].append(
        {'domain': 'world', 'subjects': [], 'holder': None, 'key': 'wc_01',
         'knowledge_mode': None})
    expanded, issues = expand_design_wire(wire, brief=brief,
                                          obligations=obligations)
    assert issues == []
    assert expanded['initial_state'][-1]['value'] == 'true'

    # 闭集外规则编号：拒。
    wire_bad = _current_wire()
    wire_bad['initial_state'].append(
        {'domain': 'world', 'subjects': [], 'holder': None, 'key': 'wc_99',
         'knowledge_mode': None})
    _, issues_bad = expand_design_wire(wire_bad, brief=brief,
                                       obligations=obligations)
    assert any('不在世界约束清单' in m for _p, m in issues_bad)

    # 剧情事实 val='true'：零信息值拒绝（wc 引用才是命题成立断言的通道）。
    wire_true = _current_wire()
    wire_true['scenes'][1]['beats'][0]['state'] = [
        {'at': 'world', 'key': 'courtyard_bought', 'val': 'true'}]
    _, issues_true = expand_design_wire(wire_true, brief=brief,
                                        obligations=obligations)
    assert any('val 不得为 "true"' in m for _p, m in issues_true)

    # 拍上合法的 wc 规则断言（无 val）回填 'true'。
    wire_beat = _current_wire()
    wire_beat['scenes'][1]['beats'][0]['state'] = [
        {'at': 'world', 'key': 'wc_01'}]
    expanded_beat, issues_beat = expand_design_wire(
        wire_beat, brief=brief, obligations=obligations)
    assert issues_beat == []
    row = expanded_beat['scenes'][1]['beats'][0]['target_delta'][0]
    assert (row['domain'], row['key'], row['value']) == ('world', 'wc_01', 'true')


def test_reader_reveal_expands_as_knowledge_holder_reader():
    """D5 统一：reader 义务的增量展开成 knowledge(holder=reader)——无独立
    reader 域、无 conf 例外面。"""
    brief = _brief()
    # ob01 加 reader 锁（brief 夹具改造：requirements[0] 的 realize_holders
    # 追加 reader|kp），real 展开产出 reader 行。
    requirement = brief.requirements[0]
    from contracts.schema.grounding import BriefKnowledgeChange
    changes = requirement.knowledge_changes + (BriefKnowledgeChange(op='set', polarity='positive', change_id='kc_2222222222222222', holder='reader', kp='kp_1111111111111111', mode='known'),)
    brief = brief.model_copy(update={
        'requirements': (
            (requirement.model_copy(update={'knowledge_changes': changes}),)
            + tuple(brief.requirements[1:])),
        'knowledge_locks': brief.knowledge_locks + (
            type(brief.knowledge_locks[0])(op='set', polarity='positive', holder='reader',
                                           kp='kp_1111111111111111',
                                           mode='known'),)})
    obligations = _obligations(brief)
    wire = _current_wire()
    wire['scenes'][0]['beats'][0]['know'].append({'ref': 'ob01.k02', 'confirms': {'domain': 'character', 'subjects': ['lu_fujun'], 'key': 'health'}})
    expanded, issues = expand_design_wire(wire, brief=brief, obligations=obligations)
    assert issues == []
    rows = expanded['scenes'][0]['beats'][0]['target_delta']
    reader_rows = [r for r in rows if r['holder'] == 'reader']
    assert len(reader_rows) == 1
    assert (reader_rows[0]['domain'], reader_rows[0]['knowledge_mode'],
            reader_rows[0]['value']) == ('knowledge', 'known', 'true')
    assert reader_rows[0]['confirms']['key'] == 'health'


def test_world_rule_and_immediate_continuation_reach_canonical():
    brief, wire = _brief(), _current_wire()
    wire['scenes'][1]['beats'][0]['state'] = [{'at': 'world', 'key': 'wc_01'}]
    wire['chapters'][1]['entry'] = {'transition': 'immediate_continuation',
                                   'carryover': '前章余波'}
    body, errors = assemble_body_design(
        wire, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression, source_narration=brief.source_narration,
        brief=brief, obligations=_obligations(brief))
    assert not errors, errors
    assert body.scenes[1].beats[0].target_delta[0].modality == 'actual'
    assert body.chapters[1].entry.anchor == 'sc01#b02'


def test_knowledge_short_refs_are_closed_complete_and_unique():
    from copy import deepcopy
    brief, wire = _brief(), _current_wire()
    for placements, message in [([], '未标注拍位'),
                                ([{'ref': 'ob99.k01'}], '闭集外认知'),
                                ([{'ref': 'ob01.k01'}] * 2, '重复标注')]:
        candidate = deepcopy(wire)
        candidate['scenes'][0]['beats'][0]['know'] = placements
        expanded, issues = expand_design_wire(candidate, brief=brief, obligations=_obligations(brief))
        assert expanded is None and any(message in m for _, m in issues)


# ───────────── CS-F2：事实锁引用（stage4-design-v9 的 `lock`） ─────────────

KF = 'kf_' + '0123456789abcdef'


def _schema_of(rules: str) -> dict:
    import json
    return json.loads(rules.rsplit('\n', 1)[1])


def test_lock_reference_is_a_closed_set_injected_from_the_brief():
    """`lock` 是选择而非书写：可选句柄集合由 Brief 注入 schema；空集合时只允许 null；
    未注入（离线）时仍受句柄形状约束。"""
    from jsonschema import Draft202012Validator
    from novel.stages.common import design_rules

    def validator(**kw):
        schema = _schema_of(design_rules(known_char_ids={'lead'}, protocol=PV_CURRENT, **kw))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator({'$ref': '#/$defs/SlimDesignStateV10',
                                     '$defs': schema['$defs']})
    row = {'at': 'world', 'key': 'intent', 'val': '暂缓', 'modality': 'planned'}
    closed = validator(fact_lock_ids=[KF[:11]])
    assert not list(closed.iter_errors({**row, 'lock': KF[:11]}))
    assert list(closed.iter_errors({**row, 'lock': 'kf_' + 'f' * 8})), '闭集外句柄'
    assert not list(closed.iter_errors(row)), 'lock 可省＝普通状态'
    nothing = validator(fact_lock_ids=[])
    assert list(nothing.iter_errors({**row, 'lock': KF[:11]})), '没有可引用的锁时不得写 lock'
    assert not list(nothing.iter_errors(row))
    shape_only = validator()
    assert list(shape_only.iter_errors({**row, 'lock': 'kf_short'}))


def test_expand_keeps_lock_out_of_the_intermediate_shape_and_marks_project_the_address():
    from novel.stages.stage4.slim import design_fact_marks
    from contracts.schema.design import ResolvedDesign
    brief = _brief()
    wire = _current_wire()
    from contracts.schema.grounding import BriefFactLock
    brief = brief.model_copy(update={'fact_locks': (BriefFactLock(
        kf_id=KF, statement='仍在计划', kind='planned', modality='planned',
        subjects=('lu_fujun',)),)})
    wire['scenes'][0]['beats'][1]['state'][1]['lock'] = KF[:11]
    expanded, issues = expand_design_wire(wire, brief=brief, obligations=_obligations(brief))
    assert issues == []
    rows = expanded['scenes'][0]['beats'][1]['target_delta']
    assert all('lock' not in row for row in rows), '锁引用不进中间形状，更不进 BodyDesign'
    from novel.stages.stage4.slim import design_wire_model
    marks = design_fact_marks(design_wire_model(wire, brief))
    assert len(marks) == 1
    mark = marks[0]
    assert (mark.kf_id, mark.scene, mark.beat, mark.state) == (KF, 0, 1, 1)
    assert mark.path == ('scenes', 0, 'beats', 1, 'state', 1)
    assert (mark.op, mark.modality) == ('set', 'planned')
    assert mark.address.domain.value == 'character' and mark.address.subjects == ('lu_fujun',)
    assert mark.address.key == 'intent'


def test_mark_defaults_follow_the_state_row_not_the_lock():
    """标注的 op／modality 取状态行自身（缺省 set／actual；非 set 无时态），
    不由锁推断——锁与状态是否一致由接受校验判。"""
    from novel.stages.stage4.slim import design_fact_marks
    from contracts.schema.design import ResolvedDesign
    wire = _current_wire()
    beat = wire['scenes'][0]['beats'][0]
    beat['state'] = [
        {'at': 'lu_fujun', 'key': 'a', 'val': 'x', 'lock': KF},
        {'at': 'lu_fujun', 'key': 'b', 'op': 'unknown', 'lock': 'kf_' + '1' * 16},
        {'at': 'a+b+c', 'key': 'c', 'val': 'x', 'lock': 'kf_' + '2' * 16}]
    marks = design_fact_marks(ResolvedDesign.model_validate(wire))
    assert [(m.kf_id, m.op, m.modality) for m in marks] == [
        (KF, 'set', 'actual'), ('kf_' + '1' * 16, 'unknown', None)]


def test_know_off_real_beat_reports_beat_level_path_for_narrow_repair():
    """判据（2026-10-08，《夫君的报恩》实证三轮原地打转）：know 的认知变化未落
    在所属义务的 real 承载拍时，issue 路径必须是**拍级**——该错的正确修法常是
    给本拍 real 补义务标注（一拍可承载多条义务），只开 know 级窄修会禁止模型
    改 real，语义正确的答案修不动。"""
    brief = _brief()
    wire = _current_wire()
    wire['scenes'][0]['beats'][0]['know'] = []
    wire['scenes'][0]['beats'][1]['know'] = [{'ref': 'ob01.k01'}]
    _expanded, issues = expand_design_wire(wire, brief=brief,
                                           obligations=_obligations(brief))
    assert [i for i in issues if 'real 承载拍' in i[1]] == [
        (('scenes', 0, 'beats', 1),
         'ob01.k01 必须落在对应义务 ob01 的 real 承载拍')]


def test_detail_schema_closes_short_refs_and_empty_catalog_remains_optional():
    import json
    from jsonschema import Draft202012Validator
    from novel.stages.common import design_rules

    for refs in ((), ('ob01.d01', 'ob03.d01')):
        schema = json.loads(design_rules(detail_refs=refs).rsplit('\n', 1)[1])
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema['$defs']['SlimDesignBeatV10']['properties']['details'])
        assert not list(validator.iter_errors([]))
        assert list(validator.iter_errors(['ob99.d01']))
        assert list(validator.iter_errors(['dt_1234567890abcdef']))
        assert bool(list(validator.iter_errors(['ob01.d01']))) == (not refs)


def test_expression_schema_and_narrow_choices_close_on_beat_expression_ids():
    """已知类别先收紧请求：narration 不能成为拍选择，空目录只接受空数组。"""
    import json
    from jsonschema import Draft202012Validator
    from novel.stages.common import design_rules
    from novel.stages.stage4.slim import design_patch_legal_values

    for protocol, beat in ((PV_CURRENT, 'SlimDesignBeatV10'),):
        for refs in ((), ('ex_02', 'ex_03')):
            schema = json.loads(design_rules(
                expression_ids=refs, protocol=protocol).rsplit('\n', 1)[1])
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema['$defs'][beat]['properties']['ex'])
            assert not list(validator.iter_errors([]))
            assert list(validator.iter_errors(['ex_01'])), '书级 narration 不入拍闭集'
            assert list(validator.iter_errors(['ex_99'])), '未知表达不能靠模型自由编造'
            assert bool(list(validator.iter_errors(['ex_02']))) == (not refs)
            assert design_patch_legal_values(expression_ids=refs)['expression_ids'] == list(refs)


def test_expression_refs_are_reported_even_when_knowledge_cannot_expand():
    """know/real 不匹配不能遮住独立表达错误，反馈路径仍能发起条目返修。"""
    from copy import deepcopy
    from contracts.schema.expression import ExpressionRequirement
    from novel.stages.stage4.design import inspect_body_design
    from novel.stages.stage4.design_paths import wire_paths_of
    from novel.stages.stage4.slim import design_patch_slice

    brief = _brief()
    brief = brief.model_copy(update={'expression': (
        *brief.expression,
        ExpressionRequirement(ex_id='ex_02', kind='narration', statement='第一人称叙述'),)})
    wire = _current_wire()
    wire['scenes'][0]['beats'][0]['real'] = []
    wire['scenes'][0]['beats'][1]['ex'] = ['ex_02', 'ex_99']
    before = deepcopy(wire)
    body, errors = inspect_body_design(
        wire, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression, source_narration=brief.source_narration,
        brief=brief, obligations=_obligations(brief))
    assert body is None and wire == before
    assert any(getattr(error, 'code', '') == 'design_wire_expand' for error in errors)
    refs = [error for error in errors if getattr(error, 'code', '') == 'design_expression_ref']
    assert {error.location[-1] for error in refs} == {'ex_02', 'ex_99'}
    assert all(wire_paths_of(error) == (('scenes', 0, 'beats', 1, 'ex'),) for error in refs)
    dependency = design_patch_slice(
        wire, brief, _obligations(brief), targets=[('scenes', 0, 'beats', 1, 'ex')])
    assert dependency['beat_expressions'] == {
        'ex_01': {'kind': 'humor', 'statement': '一本正经的荒诞'}}


def test_current_reference_expansion_keeps_full_identity_and_raw_wire():
    """短引用只展开类型化字段；当前已购 wire 与自然语言中的 hash 字面不改。"""
    from copy import deepcopy
    from novel.stages.stage4.slim import design_wire_model, design_fact_marks
    from contracts.schema.grounding import BriefFactLock
    brief = _brief().model_copy(update={'fact_locks': (
        BriefFactLock(kf_id=KF, statement='夫君仍有借种打算', kind='planned',
                      modality='planned', subjects=('lu_fujun',)),)})
    legacy = _current_wire()
    legacy['scenes'][0]['beats'][1]['state'][1]['lock'] = KF
    legacy['scenes'][0]['beats'][1]['ev'] += f'（字面引用 {KF} 不应被替换）'
    legacy['scenes'][1]['reads'] = [{'domain': 'knowledge', 'holder': 'yun_niang',
                                    'key': 'kp_1111111111111111'}]
    current = deepcopy(legacy)
    current['protocol_version'] = 'stage4-design-v13'
    current['scenes'][0]['beats'][1]['state'][1]['lock'] = KF[:11]
    current['scenes'][1]['reads'][0]['key'] = 'kp_11111111'
    before = deepcopy(current)
    expanded, errors = expand_design_wire(current, brief=brief, obligations=_obligations(brief))
    assert errors == []
    assert expanded['scenes'][1]['state_reads'][0]['key'] == 'kp_1111111111111111'
    assert expanded['scenes'][0]['beats'][1]['event'] == legacy['scenes'][0]['beats'][1]['ev']
    assert design_fact_marks(design_wire_model(current, brief))[0].kf_id == KF
    assert current == before
    current['scenes'][1]['reads'][0]['key'] = 'kp_99'
    expanded, errors = expand_design_wire(current, brief=brief, obligations=_obligations(brief))
    assert expanded is None and errors == [(('scenes', 1, 'reads', 0, 'key'), "闭集外短引用 'kp_99'")]


def test_v2_keeps_repeated_changes_extra_authorizations_and_disclosure():
    from contracts.schema.grounding import BriefKnowledgeChange, BriefKnowledgeLock
    from novel.stages.stage4.slim import project_design_input
    brief = _brief()
    first = brief.requirements[0]
    change = first.knowledge_changes[0].model_copy(update={'requires_released': ('芸娘',)})
    repeated = BriefKnowledgeChange(op='set', polarity='positive', change_id='kc_2222222222222222', holder=change.holder,
                                    kp=change.kp, mode=change.mode)
    first = first.model_copy(update={'knowledge_changes': (change, repeated)})
    extra = BriefKnowledgeLock(op='set', polarity='positive', holder='reader', kp=change.kp, mode='heard')
    brief = brief.model_copy(update={'requirements': (first, *brief.requirements[1:]),
                                    'knowledge_locks': (*brief.knowledge_locks, extra)})
    view = project_design_input(brief, _obligations(brief))
    changes = view['requirements']['ob01']['knowledge_changes']
    assert len(changes) == 2 and changes['ob01.k01']['requires_released'] == ['芸娘']
    assert view['knowledge_locks'] == [{'op': 'set', 'polarity': 'positive', 'holder': 'reader', 'kp': 'kp_11111111', 'mode': 'heard'}]
    assert view['requirements']['ob01']['satisfaction'] == first.satisfaction


def test_short_references_stay_bound_across_upstream_additions_and_reject_collisions():
    import pytest
    from contracts.schema.grounding import BriefProposition
    from novel.stages.stage4.slim import design_reference_aliases, design_wire_model
    brief = _brief()
    original = brief.propositions[0]
    added = BriefProposition(kp_id='kp_0000000000000000', statement='新命题')
    expanded = brief.model_copy(update={'propositions': (added, *brief.propositions)})
    assert design_reference_aliases(expanded)[original.kp_id] == design_reference_aliases(brief)[original.kp_id]
    wire = _current_wire()
    wire['protocol_version'] = 'stage4-design-v13'
    wire['scenes'][0]['reads'] = [{'domain': 'knowledge', 'holder': 'yun_niang',
                                 'key': original.kp_id[:11]}]
    assert design_wire_model(wire, expanded).scenes[0].reads[0].key == original.kp_id
    collision = BriefProposition(kp_id='kp_1111111100000000', statement='不同命题')
    with pytest.raises(ValueError, match='短引用前缀冲突'):
        design_reference_aliases(brief.model_copy(update={'propositions': (original, collision)}))


def test_narrow_detail_context_carries_nonreal_owner_without_global_material_text():
    from novel.stages.stage4.slim import design_patch_legal_values, design_patch_slice

    raw = _brief().model_dump(mode='json')
    raw['requirements'][1]['details'] = [
        {'detail_id': 'dt_1111111111111111', 'text': '无关义务的细节全文'}]
    raw['requirements'][2]['details'] = [
        {'detail_id': 'dt_3333333333333333', 'text': '佯装走开，再从床底钻出'}]
    brief = ReconstructionBrief.model_validate(raw)
    obligations = _obligations(brief)
    wire = _current_wire()
    wire['scenes'][0]['beats'][1]['details'] = ['ob03.d01']
    assert not wire['scenes'][0]['beats'][1].get('real')
    for targets in (
        (('scenes', 0, 'beats', 1, 'details'),),
        (('scenes', 0, 'brief'),),
        (('chapters', 0, 'purpose'),),
        (('scenes', 0, 'brief'), ('scenes', 0, 'beats', 0)),
    ):
        payload = design_patch_slice(wire, brief, obligations, targets=targets)
        assert 'ob03' in payload['requirements']
        assert payload['requirements']['ob03']['details'] == {
            'ob03.d01': {'text': '佯装走开，再从床底钻出'}}
        assert '无关义务的细节全文' not in str(payload)
        assert 'dt_3333333333333333' not in str(payload)
    legal = design_patch_legal_values(obligations=obligations)
    assert legal['detail_refs'] == ['ob02.d01', 'ob03.d01']
    assert '细节全文' not in str(legal) and '佯装走开' not in str(legal)


def test_writer_packet_rejects_detail_identity_in_every_reading_partition():
    import pytest
    from pydantic import ValidationError
    from contracts.schema.context import WriterPacket

    sections = ('must_realize', 'must_respect', 'continuity',
                'creative_guidance', 'background', 'guard_only')
    for section in sections:
        payload = {'chapter_ordinal': 1, **{key: [] for key in sections}}
        payload[section] = ['误把细节身份 dt_1234567890abcdef 写成阅读材料']
        with pytest.raises(ValidationError, match='内部 ID'):
            WriterPacket.model_validate(payload)


def test_summary_narrow_slice_includes_only_explicit_covered_missing_instance():
    from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
    from novel.stages.stage4 import locate_design_patches
    from novel.stages.stage4.slim import design_patch_slice
    from tests.test_str06_a5_positions import _summary_design_fixture

    wire, brief, index = _summary_design_fixture(
        {'ob01': [(0, 0)], 'ob03': [(1, 0)]}, optional_instance=True)
    # 夹具 wire 由 tests/test_str06_hook.py 提供；此处只取 Brief 与结构，协议
    # 字面量按现役读面钉住（v11 已只读归档，不进 DESIGN_READ_PROTOCOLS）。
    wire['protocol_version'] = STAGE4_DESIGN_WIRE_PROTOCOL
    accepted, errors = assemble_body_design(
        wire, known_char_ids={'fu_jun', 'yun_niang'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief, obligations=index)
    assert accepted is None
    missing = [e for e in errors if getattr(e, 'code', '') == 'series_summary_unbound']
    assert len(missing) == 1
    targets = locate_design_patches(wire, missing)
    payload = design_patch_slice(wire, brief, index,
                                targets=[target[0] for target in targets])
    assert payload['requirements']['ob03']['summarizes'] == ['ob01', 'ob02']
    assert payload['requirements']['ob02']['summary'] == '第二次经历'
    assert payload['requirements']['ob02']['satisfaction'] == '第二次经历'
    assert 'anchors' not in str(payload) and 'core_second' not in str(payload)


def _scene_repair_slice_fixture(protocol):
    """依赖分别放在不同拍／引用面，避免只补 details 仍然得到假绿。"""
    from contracts.schema.grounding import IdentityProjection

    raw = _brief().model_dump(mode='json')
    raw['requirements'][1].update({
        'participants': ['requirement_actor'],
        'details': [{'detail_id': 'dt_2222222222222222', 'text': '第二拍专用细节'}],
        'knowledge_changes': [{'op': 'set', 'polarity': 'positive', 'change_id': 'kc_2222222222222222',
                               'holder': 'knowledge_holder', 'kp': 'kp_2222222222222222',
                               'mode': 'heard'}],
    })
    raw['requirements'][2]['details'] = [
        {'detail_id': 'dt_3333333333333333', 'text': '未涉及场的细节全文'}]
    raw['propositions'].extend([
        {'kp_id': 'kp_2222222222222222', 'statement': '第二拍认知命题'},
        {'kp_id': 'kp_3333333333333333', 'statement': '场级读取命题'},
        {'kp_id': 'kp_4444444444444444', 'statement': '非法位置仍携带的候选命题'},
        {'kp_id': 'kp_5555555555555555', 'statement': '无关命题'},
    ])
    raw['knowledge_locks'].append({'op': 'set', 'polarity': 'positive', 'holder': 'knowledge_holder',
                                    'kp': 'kp_2222222222222222', 'mode': 'heard'})
    raw['knowledge_locks'].append({'op': 'set', 'polarity': 'positive', 'holder': 'extra_holder',
                                    'kp': 'kp_3333333333333333', 'mode': 'suspected'})
    raw['fact_locks'] = [
        {'kf_id': KF, 'statement': '第二拍状态选择的锁', 'kind': 'planned',
         'modality': 'planned', 'subjects': ['lock_subject']},
        {'kf_id': 'kf_5555555555555555', 'statement': '无关锁', 'kind': 'planned',
         'modality': 'planned', 'subjects': ['unrelated_actor']},
    ]
    brief = ReconstructionBrief.model_validate(raw)
    def ref(value):
        return value[:11]

    wire = _current_wire()
    wire['protocol_version'] = protocol
    wire['initial_surfaces'] = [{'char_id': 'initial_actor', 'surfaces': ['初始称谓']}]
    wire['scenes'] = [
        {'driver': {'focal_actor': 'prefix_actor'}, 'brief': '目标之前', 'beats': [
            {'ev': '前拍', 'release': [{'char_id': 'prefix_actor', 'surfaces': ['前缀称谓']}]}]},
        {'driver': {'focal_actor': 'driver_actor'}, 'brief': '整场修订的候选上下文',
         'pov': 'pov_actor',
         'reads': [
             {'domain': 'knowledge', 'key': ref('kp_3333333333333333'),
              'holder': 'read_holder'},
             {'domain': 'relation', 'key': 'marriage', 'subjects': ['read_left', 'read_right']}],
         'beats': [
             {'ev': '第一拍', 'real': ['ob01'], 'know': [{'ref': 'ob01.k01'}],
              'who': ['who_actor'],
              'state': [{'at': 'state_left+state_right', 'key': 'local', 'val': '事实'}],
              'release': [{'char_id': 'first_release', 'surfaces': ['第一拍称谓']}]},
             {'ev': '第二拍问称谓', 'real': ['ob02'], 'know': [{'ref': 'ob02.k01'}],
              'details': ['ob02.d01'],
              'state': [{'at': 'state_actor', 'key': 'intent', 'val': '仍在计划',
                         'modality': 'planned', 'lock': ref(KF)}],
              'reads': [{'domain': 'knowledge', 'key': ref('kp_4444444444444444'),
                         'holder': 'illegal_holder'}],
              'release': [{'char_id': 'last_release', 'surfaces': ['第二拍称谓']}]}]},
        {'driver': {'focal_actor': 'unrelated_actor'}, 'brief': '目标之后', 'beats': [
            {'ev': '后拍', 'real': ['ob03'], 'details': ['ob03.d01'],
             'state': [{'at': 'unrelated_actor', 'key': 'later', 'val': '未来',
                        'lock': ref('kf_5555555555555555')}],
             'release': [{'char_id': 'future_actor', 'surfaces': ['未来称谓']}]}]},
    ]
    wire['chapters'] = [{'purpose': '前章', 'scenes': 1},
                        {'purpose': '修订章', 'scenes': 1},
                        {'purpose': '后章', 'scenes': 1}]
    people = ('initial_actor', 'prefix_actor', 'driver_actor', 'pov_actor', 'read_holder',
              'read_left', 'read_right', 'who_actor', 'state_left', 'state_right',
              'first_release', 'last_release', 'state_actor', 'knowledge_holder',
              'requirement_actor', 'lock_subject', 'illegal_holder', 'unrelated_actor',
              'future_actor', 'extra_holder', 'mentioned_actor', 'lu_fujun', 'yun_niang')
    identities = tuple(IdentityProjection(char_id=person, target_name=person, identity_statement=f'{person}的身份')
                       for person in people)
    return wire, brief, _obligations(brief), identities


def test_scene_and_chapter_patch_slices_close_every_editable_beat_dependency():
    from copy import deepcopy
    from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
    from novel.stages.stage4.slim import design_patch_slice

    for protocol in (STAGE4_DESIGN_WIRE_PROTOCOL,):
        wire, brief, obligations, identities = _scene_repair_slice_fixture(protocol)
        before = deepcopy(wire)
        for target in (('scenes', 1), ('scenes', 1, 'beats'), ('chapters', 1)):
            payload = design_patch_slice(wire, brief, obligations,
                                         targets=[target], identities=identities)
            assert payload['slice_version'] == 'stage4-design-patch-slice-v4'
            assert payload['wire_protocol_version'] == protocol
            assert wire == before, '形状坏稿只作为候选上下文，切片不删除或迁移非法读取'
            assert payload['scenes'] == [wire['scenes'][1]]
            assert payload['scenes'][0]['beats'][1]['reads'] == before['scenes'][1]['beats'][1]['reads']
            assert set(payload['requirements']) == {'ob01', 'ob02'}
            assert payload['requirements']['ob02']['details'] == {'ob02.d01': {'text': '第二拍专用细节'}}
            suffix = 11
            assert set(payload['propositions']) == {ref[:suffix] for ref in (
                'kp_1111111111111111', 'kp_2222222222222222',
                'kp_3333333333333333', 'kp_4444444444444444')}
            assert set(payload['fact_locks']) == {KF[:suffix]}, '短锁与完整锁查同一个正式身份'
            assert payload['requirements']['ob02']['knowledge_changes']['ob02.k01']['kp'] == 'kp_22222222'
            assert payload['knowledge_locks'] == [
                {'op': 'set', 'polarity': 'positive', 'holder': 'extra_holder', 'kp': 'kp_33333333', 'mode': 'suspected'}]
            assert [row['ref'] for row in payload['neighbor_beats']] == [
                'scenes[0].beats[0]', 'scenes[2].beats[0]']
            assert [row['char_id'] for row in payload['release_prefix']] == [
                'initial_actor', 'prefix_actor', 'first_release', 'last_release']
            included = {row['char_id'] for row in payload['identities']}
            assert {'initial_actor', 'prefix_actor', 'driver_actor', 'pov_actor', 'read_holder',
                    'read_left', 'read_right', 'who_actor', 'state_left', 'state_right',
                    'first_release', 'last_release', 'state_actor', 'knowledge_holder',
                    'requirement_actor', 'lock_subject', 'illegal_holder', 'extra_holder',
                    } <= included
            assert not {'unrelated_actor', 'future_actor'} & included
            assert '未涉及场的细节全文' not in str(payload) and '无关命题' not in str(payload)


def test_beat_patch_keeps_dependencies_narrow_while_scene_array_expands_all_beats():
    from novel.stages.stage4.slim import design_patch_slice

    wire, brief, obligations, identities = _scene_repair_slice_fixture(PV_CURRENT)
    narrow = design_patch_slice(wire, brief, obligations,
                                targets=[('scenes', 1, 'beats', 0)], identities=identities)
    assert narrow['scenes'] == [wire['scenes'][1]], '同场其它拍是只读上下文'
    assert set(narrow['requirements']) == {'ob01'}
    assert set(narrow['propositions']) == {'kp_11111111', 'kp_33333333'}
    assert not narrow['fact_locks'], '不可编辑邻拍的锁不扩大窄输入'
    assert [row['char_id'] for row in narrow['release_prefix']] == [
        'initial_actor', 'prefix_actor', 'first_release']
    assert 'last_release' not in {row['char_id'] for row in narrow['identities']}
    assert 'mentioned_actor' not in {row['char_id'] for row in narrow['identities']}
    # rc26：ev 里的普通文本不再派生身份依赖（identity-text 标记协议退役）——
    # 只在自然语言里出现的人物不进窄返修身份面。
    wide = design_patch_slice(wire, brief, obligations, targets=[('scenes',)], identities=identities)
    assert wide['scenes'] == wire['scenes']
    assert set(wide['requirements']) == {'ob01', 'ob02', 'ob03'}
    assert set(wide['fact_locks']) == {KF[:11], 'kf_55555555'}
    assert wide['neighbor_beats'] == []
    assert wide['release_prefix'][-1]['char_id'] == 'future_actor'


def test_shape_repair_slice_preserves_malformed_candidate_containers_without_guessing():
    from copy import deepcopy
    from novel.stages.stage4.slim import design_patch_slice

    wire, brief, obligations, _ = _scene_repair_slice_fixture(PV_CURRENT)
    wire['scenes'][0] = '非法场对象'
    wire['scenes'][1]['beats'] = [None, {'real': {'非法': '容器'}, 'know': '非法列表',
                                        'state': [None, {'at': ['坏地址']}],
                                        'release': [None]}]
    wire['scenes'][2]['beats'] = {'非法': '拍数组'}
    wire['initial_surfaces'] = [None, {'char_id': 'initial_actor', 'surfaces': ['初始称谓']}]
    wire['chapters'][1]['scenes'] = '非法场数'
    before = deepcopy(wire)
    for target in (('scenes',), ('scenes', 0), ('scenes', 1), ('scenes', 2), ('chapters', 1)):
        payload = design_patch_slice(wire, brief, obligations, targets=[target])
        assert wire == before and payload['scenes'], '原始坏值随上下文送达，切片只读'
    wire['scenes'] = {'非法': '场数组'}
    payload = design_patch_slice(wire, brief, obligations, targets=[('scenes',)])
    assert payload['scenes'] == wire['scenes']


def test_scene_repair_state_history_supplies_initial_and_prior_values_and_closes_confirms():
    from copy import deepcopy
    from contracts.schema.grounding import IdentityProjection
    from novel.stages.stage4.slim import design_patch_slice

    wire, brief, obligations, identities = _scene_repair_slice_fixture(PV_CURRENT)
    marriage = {'domain': 'relation', 'key': 'marriage',
                'subjects': ['history_left', 'history_right'], 'value': '已经成婚'}
    knowledge = {'domain': 'knowledge', 'key': 'kp_33333333', 'holder': 'history_holder',
                 'value': 'true', 'knowledge_mode': 'known',
                 'confirms': {'domain': 'character', 'key': 'proof', 'subjects': ['proof_subject']}}
    proof = {'domain': 'character', 'key': 'proof', 'subjects': ['proof_subject'], 'value': '已有凭证'}
    wire['initial_state'].extend([marriage, knowledge, proof])
    wire['scenes'][0]['beats'][0]['state'] = [
        {'at': 'past_left+past_right', 'key': 'marriage', 'val': '婚事已公开'},
        {'at': 'proof_subject', 'key': 'proof', 'val': '凭证已出示'},
        {'at': 'intent_actor', 'key': 'intent', 'val': '此前已有打算'},
        {'at': 'unrelated_actor', 'key': 'irrelevant', 'val': '无关键的历史值'}]
    wire['scenes'][1]['beats'][1]['reads'] = [
        {'domain': 'relation', 'key': 'marriage', 'subjects': ['history_left', 'history_right']}]
    wire['scenes'][2]['beats'][0]['state'].append(
        {'at': 'history_left+history_right', 'key': 'marriage', 'val': '尚未发生的未来婚事'})
    identities += tuple(IdentityProjection(char_id=person, target_name=person, identity_statement=f'{person}的身份')
                        for person in ('history_left', 'history_right', 'history_holder',
                                       'past_left', 'past_right', 'proof_subject', 'intent_actor'))
    before = deepcopy(wire)
    payload = design_patch_slice(wire, brief, obligations,
                                 targets=[('scenes', 1)], identities=identities)
    assert wire == before, '历史只读，不替模型迁移非法的非空reads'
    history = {row['ref']: row['value'] for row in payload['state_history']}
    assert history == {
        'initial_state[1]': marriage,
        'initial_state[2]': knowledge,
        'initial_state[3]': proof,
        'scenes[0].beats[0].state[0]': wire['scenes'][0]['beats'][0]['state'][0],
        'scenes[0].beats[0].state[1]': wire['scenes'][0]['beats'][0]['state'][1],
        'scenes[0].beats[0].state[2]': wire['scenes'][0]['beats'][0]['state'][2],
    }
    assert not any(ref.startswith('scenes[1]') for ref in history), '目标场全文已有的state不重复发送'
    assert '尚未发生的未来婚事' not in str(history) and '无关键的历史值' not in str(history)
    included = {row['char_id'] for row in payload['identities']}
    assert {'history_left', 'history_right', 'history_holder', 'past_left', 'past_right',
            'proof_subject', 'intent_actor'} <= included
    narrow = design_patch_slice(wire, brief, obligations,
                                targets=[('scenes', 1, 'beats', 0)], identities=identities)
    assert 'scenes[0].beats[0].state[2]' not in {row['ref'] for row in narrow['state_history']}
    assert 'intent_actor' not in {row['char_id'] for row in narrow['identities']}


def test_all_beat_repair_scope_supplies_semantics_for_unmarked_obligations_only_when_authorized():
    from novel.stages.stage4.slim import design_patch_slice

    raw = _brief().model_dump(mode='json')
    raw['requirements'][2].update({
        'must_preserve': True, 'satisfaction': '未标注的必保义务也必须得到兑现',
        'details': [{'detail_id': 'dt_3333333333333333', 'text': '漏标义务的正式细节'}]})
    brief = ReconstructionBrief.model_validate(raw)
    obligations = _obligations(brief)
    wire = _current_wire()
    assert all('ob03' not in beat.get('real', ()) for scene in wire['scenes'] for beat in scene['beats'])
    closed_set = [('scenes', si, 'beats', bi)
                  for si, scene in enumerate(wire['scenes']) for bi, _beat in enumerate(scene['beats'])]
    payload = design_patch_slice(wire, brief, obligations, targets=closed_set)
    assert payload['requirements']['ob03']['satisfaction'] == '未标注的必保义务也必须得到兑现'
    assert payload['requirements']['ob03']['details'] == {'ob03.d01': {'text': '漏标义务的正式细节'}}
    narrow = design_patch_slice(wire, brief, obligations, targets=[closed_set[0]])
    assert 'ob03' not in narrow['requirements']
    assert '漏标义务的正式细节' not in str(narrow)
    assert 'anchors' not in str(payload) and 'quotes' not in str(payload)
