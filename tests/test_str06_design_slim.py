#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_design_slim —— 设计瘦 wire（CS-3 第一步，2026-10-03）。

覆盖 `contracts/schema/design.py::DesignWireV9`、
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

PV9 = 'stage4-design-v9'
PV5 = 'stage4-design-v5'


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
        'contract_version': 'str06-rc22',
        'requirements': [
            {'core_id': 'req_aaaa', 'summary': '芸娘确知夫君不育',
             'kind': 'reveal', 'must_preserve': True, 'satisfaction': '开场揭示',
             'participants': ['lu_fujun', 'yun_niang'], 'requires_cores': [],
             'locks': [], 'knowledge_changes': [{'change_id': 'kc_1111111111111111', 'holder': 'yun_niang', 'kp': 'kp_1111111111111111', 'mode': 'known'}],
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
            {'holder': 'yun_niang', 'kp': 'kp_1111111111111111', 'mode': 'known'}],
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


def _v9_wire() -> dict:
    return {
        'protocol_version': PV9,
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
    expanded, issues = expand_design_wire(_v9_wire(), brief=brief,
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
    expanded, issues = expand_design_wire(_v9_wire(), brief=brief,
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
    wire = _v9_wire()
    wire['scenes'][1]['beats'][0]['real'] = ['ob02']
    wire['scenes'][0]['beats'].append(
        {'ev': '计划初次受挫（支撑拍）。', 'real': ['ob02']})
    expanded2, issues2 = expand_design_wire(wire, brief=brief,
                                            obligations=_obligations(brief))
    assert issues2 == []
    supporting = expanded2['scenes'][0]['beats'][2]['target_delta']
    accept = expanded2['scenes'][1]['beats'][0]['target_delta']
    assert supporting == [] and accept == []      # ob02 无锁：两侧都无增量


def test_expand_kp_state_exception_and_bad_inputs():
    brief = _brief()
    wire = _v9_wire()
    # kp 值未知／解除例外可走 state 面；set 增量不得走（必须经 know）。
    wire['scenes'][1]['beats'][0]['state'] = [
        {'at': 'yun_niang', 'key': 'kp_1111111111111111', 'op': 'unset'}]
    expanded, issues = expand_design_wire(wire, brief=brief,
                                          obligations=_obligations(brief))
    assert issues == []
    assert expanded['scenes'][1]['beats'][0]['target_delta'] == [
        {'domain': 'knowledge', 'subjects': (), 'holder': 'yun_niang',
         'key': 'kp_1111111111111111', 'op': 'unset', 'value': None,
         'scope': 'book', 'knowledge_mode': None,
         'modality': None}]

    wire2 = _v9_wire()
    wire2['scenes'][0]['beats'][0]['state'].append(
        {'at': 'yun_niang', 'key': 'kp_1111111111111111', 'val': 'true'})
    _, issues2 = expand_design_wire(wire2, brief=brief,
                                    obligations=_obligations(brief))
    assert any('经 know 标注派生' in m for _p, m in issues2)

    wire3 = _v9_wire()
    wire3['scenes'][0]['beats'][0]['real'] = ['ob99']
    _, issues3 = expand_design_wire(wire3, brief=brief,
                                    obligations=_obligations(brief))
    assert any('闭集外义务编号' in m for _p, m in issues3)

    # 缺 obligations 映射：v6 无从展开，fail fast。
    _, issues4 = expand_design_wire(_v9_wire(), brief=brief)
    assert any('obligations' in m for _p, m in issues4)


def test_assemble_routes_v7_and_enforces_coverage():
    brief = _brief()
    obligations = _obligations(brief)
    wire = _v9_wire()
    body, errors = assemble_body_design(
        wire, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief,
        obligations=obligations)
    assert errors == [], errors
    assert body.scenes[0].beats[0].target_delta[1].domain.value == 'knowledge'
    assert body.world_rules == ('女子极难自立门户',)

    # 必保义务漏标注 → 覆盖闸拦下（同节点修订轮修复）。
    wire_missing = _v9_wire()
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


def test_design_rules_prompt_carries_v7_and_obligation_table():
    from novel.stages.common import design_rules
    from novel.stages.stage4.slim import project_design_input
    rules = design_rules(known_char_ids={'yun_niang'})
    assert 'stage4-design-v9' in rules and 'SlimDesignBeat' in rules
    assert 'real' in rules and 'obNN' in rules
    brief = _brief()
    table = project_design_input(brief, _obligations(brief))['requirements']
    assert table['ob01']['knowledge_changes']['ob01.k01'] == {
        'holder': 'yun_niang', 'kp': 'kp_1111111111111111', 'mode': 'known',
        'requires_released': []}
    assert 'ob03' in table      # 非必保也在清单（标注可选）


def test_design_projection_preserves_complete_semantics_and_references():
    from copy import deepcopy
    import pytest
    from novel.stages.stage4.slim import project_design_input, knowledge_change_index
    raw = _brief().model_dump(mode='json')
    raw['requirements'][1]['requires_cores'] = ['req_aaaa']
    raw['requirements'][1]['satisfaction'] = '完整条件不截断。' * 30
    brief = ReconstructionBrief.model_validate(raw)
    obligations = _obligations(brief)
    before = deepcopy(brief.model_dump())
    view = project_design_input(brief, obligations)
    assert view['requirements']['ob02']['requires'] == ['ob01']
    assert set(view['requirements']) == set(obligations)
    # 除明确改为短引用的机械身份外，每个语义字段逐值保留。
    for key, value in brief.model_dump(mode='json').items():
        if key not in {'brief_id', 'preserve_checkpoint_sha256', 'requirements'}:
            assert view[key] == value
    for ob, requirement in obligations.items():
        for key, value in requirement.model_dump(mode='json').items():
            if key not in {'core_id', 'requires_cores', 'knowledge_changes', 'details', 'summarizes_cores'}:
                assert view['requirements'][ob][key] == value
    for ref, (ob, change) in knowledge_change_index(obligations).items():
        assert view['requirements'][ob]['knowledge_changes'][ref] == change.model_dump(
            mode='json', exclude={'change_id'})
    assert brief.model_dump() == before
    with pytest.raises(ValueError, match='完整且唯一'):
        project_design_input(brief, {'ob01': obligations['ob01']})


def test_world_rule_key_reference_and_zero_info_value_refused():
    """CS-3 world 规则 id：书级规则断言用 wc_{nn} 引用（闭集＋val 回填
    'true'）；剧情事实 val='true'（零信息）结构性拒绝。"""
    brief = _brief()
    obligations = _obligations(brief)

    # 合法：初始态引用清单内规则编号（val 省略回填 'true'）。
    wire = _v9_wire()
    wire['initial_state'].append(
        {'domain': 'world', 'subjects': [], 'holder': None, 'key': 'wc_01',
         'knowledge_mode': None})
    expanded, issues = expand_design_wire(wire, brief=brief,
                                          obligations=obligations)
    assert issues == []
    assert expanded['initial_state'][-1]['value'] == 'true'

    # 闭集外规则编号：拒。
    wire_bad = _v9_wire()
    wire_bad['initial_state'].append(
        {'domain': 'world', 'subjects': [], 'holder': None, 'key': 'wc_99',
         'knowledge_mode': None})
    _, issues_bad = expand_design_wire(wire_bad, brief=brief,
                                       obligations=obligations)
    assert any('不在世界约束清单' in m for _p, m in issues_bad)

    # 剧情事实 val='true'：零信息值拒绝（wc 引用才是命题成立断言的通道）。
    wire_true = _v9_wire()
    wire_true['scenes'][1]['beats'][0]['state'] = [
        {'at': 'world', 'key': 'courtyard_bought', 'val': 'true'}]
    _, issues_true = expand_design_wire(wire_true, brief=brief,
                                        obligations=obligations)
    assert any('val 不得为 "true"' in m for _p, m in issues_true)

    # 拍上合法的 wc 规则断言（无 val）回填 'true'。
    wire_beat = _v9_wire()
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
    changes = requirement.knowledge_changes + (BriefKnowledgeChange(change_id='kc_2222222222222222', holder='reader', kp='kp_1111111111111111', mode='known'),)
    brief = brief.model_copy(update={
        'requirements': (
            (requirement.model_copy(update={'knowledge_changes': changes}),)
            + tuple(brief.requirements[1:])),
        'knowledge_locks': brief.knowledge_locks + (
            type(brief.knowledge_locks[0])(holder='reader',
                                           kp='kp_1111111111111111',
                                           mode='known'),)})
    obligations = _obligations(brief)
    wire = _v9_wire()
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
    brief, wire = _brief(), _v9_wire()
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
    brief, wire = _brief(), _v9_wire()
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
        schema = _schema_of(design_rules(known_char_ids={'lead'}, **kw))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator({'$ref': '#/$defs/SlimDesignState',
                                     '$defs': schema['$defs']})
    row = {'at': 'world', 'key': 'intent', 'val': '暂缓', 'modality': 'planned'}
    closed = validator(fact_lock_ids=[KF])
    assert not list(closed.iter_errors({**row, 'lock': KF}))
    assert list(closed.iter_errors({**row, 'lock': 'kf_' + 'f' * 16})), '闭集外句柄'
    assert not list(closed.iter_errors(row)), 'lock 可省＝普通状态'
    nothing = validator(fact_lock_ids=[])
    assert list(nothing.iter_errors({**row, 'lock': KF})), '没有可引用的锁时不得写 lock'
    assert not list(nothing.iter_errors(row))
    shape_only = validator()
    assert list(shape_only.iter_errors({**row, 'lock': 'kf_short'}))


def test_expand_keeps_lock_out_of_the_intermediate_shape_and_marks_project_the_address():
    from novel.stages.stage4.slim import design_fact_marks
    from contracts.schema.design import DesignWireV9
    brief = _brief()
    wire = _v9_wire()
    wire['scenes'][0]['beats'][1]['state'][1]['lock'] = KF
    expanded, issues = expand_design_wire(wire, brief=brief, obligations=_obligations(brief))
    assert issues == []
    rows = expanded['scenes'][0]['beats'][1]['target_delta']
    assert all('lock' not in row for row in rows), '锁引用不进中间形状，更不进 BodyDesign'
    marks = design_fact_marks(DesignWireV9.model_validate(wire))
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
    from contracts.schema.design import DesignWireV9
    wire = _v9_wire()
    beat = wire['scenes'][0]['beats'][0]
    beat['state'] = [
        {'at': 'lu_fujun', 'key': 'a', 'val': 'x', 'lock': KF},
        {'at': 'lu_fujun', 'key': 'b', 'op': 'unknown', 'lock': 'kf_' + '1' * 16},
        {'at': 'a+b+c', 'key': 'c', 'val': 'x', 'lock': 'kf_' + '2' * 16}]
    marks = design_fact_marks(DesignWireV9.model_validate(wire))
    assert [(m.kf_id, m.op, m.modality) for m in marks] == [
        (KF, 'set', 'actual'), ('kf_' + '1' * 16, 'unknown', None)]


def test_know_off_real_beat_reports_beat_level_path_for_narrow_repair():
    """判据（2026-10-08，《夫君的报恩》实证三轮原地打转）：know 的认知变化未落
    在所属义务的 real 承载拍时，issue 路径必须是**拍级**——该错的正确修法常是
    给本拍 real 补义务标注（一拍可承载多条义务），只开 know 级窄修会禁止模型
    改 real，语义正确的答案修不动。"""
    brief = _brief()
    wire = _v9_wire()
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
        validator = Draft202012Validator(schema['$defs']['SlimDesignBeat']['properties']['details'])
        assert not list(validator.iter_errors([]))
        assert list(validator.iter_errors(['ob99.d01']))
        assert list(validator.iter_errors(['dt_1234567890abcdef']))
        assert bool(list(validator.iter_errors(['ob01.d01']))) == (not refs)


def test_narrow_detail_context_carries_nonreal_owner_without_global_material_text():
    from novel.stages.stage4.slim import design_patch_legal_values, design_patch_slice

    raw = _brief().model_dump(mode='json')
    raw['requirements'][1]['details'] = [
        {'detail_id': 'dt_1111111111111111', 'text': '无关义务的细节全文'}]
    raw['requirements'][2]['details'] = [
        {'detail_id': 'dt_3333333333333333', 'text': '佯装走开，再从床底钻出'}]
    brief = ReconstructionBrief.model_validate(raw)
    obligations = _obligations(brief)
    wire = _v9_wire()
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
    from novel.stages.stage4 import locate_design_patches
    from novel.stages.stage4.slim import design_patch_slice
    from tests.test_str06_a5_positions import _summary_design_fixture

    wire, brief, index = _summary_design_fixture(
        {'ob01': [(0, 0)], 'ob03': [(1, 0)]}, optional_instance=True)
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
