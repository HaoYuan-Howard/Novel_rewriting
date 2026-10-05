"""CS-F3 离线标定 harness 的免费反例与正例（fake transport 跑通面）。

【验什么】
· 标签 schema：类别闭集、证据不足只配未确定、high_impact 只属 stage2、已裁决
  正反例只进开发集、case_id 唯一；
· 候选投影：章观察 → 候选闭集的句柄与行确定性派生；
· 请求组装：标记行、源盲机械闸（设计材料含源段 id 即拒）、目标-材料配对、
  同输入恒同字节；
· 判定解析：协议字面量、判定值域、断言必带证据、证据逐字命中、闭集覆盖、
  stage2 必须给 selection；
· 评分：九格结局、高影响漏选／正确跳过、证据不足被断言＝fabrication 违反、
  已裁决反例错误支持／否决＝硬规则违反、阻断条件（fake／金标待确认／留出集
  ／分歧／缺失）与 unver 纪律——fake 绿例绝不记成语义核验已通过；
· 运行器与 CLI：脚本化 transport 分发、未脚本化案件拒绝、报告身份登记与
  确定性。

合成 fixture 的结构镜像已裁决反例（ch03 主体误派／ob44 无依据限制／赏花锁绑
无关内容），但 label_source 是 synthetic_fixture——真实案件的标签冻结待金标
方案确认（用户裁决项，不在本文件内代签）。
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from contracts.schema.observation import ChapterObservation
from tests.semantic_calibration import (
    CALIBRATION_SYSTEM, CSF3_RULES_REVISION, CSF3_VERIFY_PROTOCOL,
    DesignMaterial, FactCandidate, FrozenLabels, CalibrationCase,
    ObligationMaterial, ScriptedTransport, Stage2FactMaterial, VerdictWire,
    build_verification_prompt, build_verification_request,
    fact_candidates_from_observation, main, parse_verdict_payload,
    run_calibration, score_case,
)

FROZEN = '2026-10-05T00:00:00+00:00'


def _case(case_id: str, target: str, category: str, item: str, expected: str,
          *, ref: str = 'e03_01', quote: str = '帐外传来表弟的声音',
          **over) -> CalibrationCase:
    payload = {'case_id': case_id, 'target': target, 'category': category,
               'item': item, 'expected': expected, 'frozen_at': FROZEN,
               'gold_evidence': [{'ref': ref, 'quote': quote}]}
    payload.update(over)
    return CalibrationCase.model_validate(payload)


# ───────────────────────── 标签 schema ─────────────────────────

def test_label_schema_rejects_shape_drift():
    with pytest.raises(ValidationError, match='闭集'):
        _case('bad-category', 'stage2_fact', 'restriction_basis',
              'ch03.b1.e1', '矛盾')
    with pytest.raises(ValidationError, match='未确定'):
        _case('bad-insufficient', 'stage2_fact', 'relation', 'ch03.b1.e1',
              '支持', evidence_insufficient=True)
    with pytest.raises(ValidationError, match='stage2_fact'):
        _case('bad-impact', 'preserve_basis', 'negative_basis', 'ob44', '矛盾',
              high_impact=True)
    with pytest.raises(ValidationError, match='开发集'):
        _case('bad-holdout', 'stage2_fact', 'relation', 'ch03.b1.e1', '矛盾',
              label_source='user_adjudicated', subset='holdout')
    with pytest.raises(ValidationError, match='重复'):
        FrozenLabels.model_validate({
            'book': '合成书', 'cases': [
                {'case_id': 'a', 'target': 'design_fragment',
                 'category': 'fact_preservation', 'item': 'kf_01',
                 'expected': '矛盾', 'frozen_at': FROZEN,
                 'gold_evidence': [{'ref': 'kf_01', 'quote': '暂缓和离'}]},
                {'case_id': 'a', 'target': 'design_fragment',
                 'category': 'fact_preservation', 'item': 'kf_02',
                 'expected': '支持', 'frozen_at': FROZEN,
                 'gold_evidence': [{'ref': 'kf_02', 'quote': '旧约'}]}]})


# ───────────────────────── 候选投影（章观察 → 闭集） ─────────────────────────

def _observation() -> ChapterObservation:
    return ChapterObservation.model_validate({
        'protocol_version': 'stage2-chapters-v4', 'beats': [
            {'event': '帐外传来表弟的声音', 'participants': ['yun_niang'],
             'source_refs': ['e03_01'], 'scene_boundary': 'new',
             'temporal_mode': 'single',
             'effects': [{'target': {'kind': 'relation', 'subject': 'yun_niang',
                                     'object': 'biao_di'},
                          'key': 'union', 'op': 'set', 'modality': 'actual',
                          'value': '已发生', 'scope': 'book',
                          'disclosure': {'visibility': 'on_page'},
                          'basis': [{'segment': 'e03_01',
                                     'quote': '帐外传来表弟的声音'}]}],
             'cognition': [{'proposition': {'ref': 'p_disguise',
                                            'statement': '来者是夫君假作表弟'},
                            'holder': 'yun_niang', 'mode': 'confirms',
                            'basis': [{'segment': 'e03_02',
                                       'quote': '她听出那是夫君学舌的腔调'}]}],
             'loops': []}]})


def test_candidates_project_from_observation_with_stable_handles():
    candidates = fact_candidates_from_observation(3, _observation())
    assert [c.handle for c in candidates] == ['ch03.b1.e1', 'ch03.b1.k1']
    assert candidates[0].kind == 'effect'
    assert candidates[0].line == '关系 yun_niang↔biao_di的 union：确立「已发生」（已发生）'
    assert candidates[0].basis == ('帐外传来表弟的声音',)
    assert candidates[1].line == 'yun_niang确认「来者是夫君假作表弟」'
    assert fact_candidates_from_observation(3, _observation()) == candidates, \
        '同观察恒同候选（确定性）'


# ───────────────────────── 请求组装 ─────────────────────────

def _stage2_material() -> Stage2FactMaterial:
    return Stage2FactMaterial.model_validate({
        'segments': [
            {'segment_id': 'e03_01', 'text': '夜里，帐外传来表弟的声音，唤芸娘的名。'},
            {'segment_id': 'e03_02',
             'text': '她听出那是夫君学舌的腔调，心下明白是他假作表弟。'},
            {'segment_id': 'e02_04', 'role': 'preceding',
             'text': '前夜夫君说起过旧约的由头。'}],
        'candidates': [
            {'handle': 'ch03.b1.e1', 'kind': 'effect',
             'line': '关系 yun_niang↔biao_di的 union：确立「已发生」（已发生）',
             'basis': ['帐外传来表弟的声音']},
            {'handle': 'ch03.b1.k1', 'kind': 'cognition',
             'line': 'yun_niang确认「来者是夫君假作表弟」',
             'basis': ['她听出那是夫君学舌的腔调']}]})


def _preserve_material() -> ObligationMaterial:
    return ObligationMaterial.model_validate({
        'obligation_id': 'ob44',
        'statement': '芸娘不得当场识破夫君的假扮，须留到后文揭示',
        'satisfaction': '正文保持芸娘的不知情', 'must_preserve': True,
        'anchors': ['帐外传来表弟的声音'],
        'segments': [{'segment_id': 'e03_01',
                      'text': '夜里，帐外传来表弟的声音，唤芸娘的名。'},
                     {'segment_id': 'e03_02',
                      'text': '她听出那是夫君学舌的腔调，心下明白是他假作表弟。'}]})


def _design_material() -> DesignMaterial:
    return DesignMaterial.model_validate({
        'locks': [{'item': 'kf_01', 'statement': '暂缓和离的悬念保留为禁区，不得提前兑现'}],
        'rows': ['sc02#b01｜园中赏花，众人看新开的芍药。']})


def test_prompt_carries_marker_segments_and_rules_and_is_deterministic():
    prompt = build_verification_prompt('dev-ch03', 'stage2_fact', _stage2_material())
    assert prompt.split('\n', 1)[0] == '【标定案件】dev-ch03｜stage2_fact'
    assert '[本章 e03_01]' in prompt and '[前文 e02_04]' in prompt
    assert 'ch03.b1.e1｜' in prompt and CSF3_VERIFY_PROTOCOL in prompt
    assert build_verification_prompt('dev-ch03', 'stage2_fact',
                                     _stage2_material()) == prompt
    request = build_verification_request('dev-ch03', 'stage2_fact',
                                         _stage2_material(), model='fake')
    assert request.system == CALIBRATION_SYSTEM and request.json_mode
    with pytest.raises(ValueError, match='不符'):
        build_verification_prompt('dev-ch03', 'design_fragment', _stage2_material())


def test_design_material_refuses_source_segment_references():
    with pytest.raises(ValidationError, match='源盲'):
        DesignMaterial.model_validate({
            'locks': [{'item': 'kf_01', 'statement': '暂缓和离保留为禁区'}],
            'rows': ['sc02#b01｜原文 e03_02 写明她已识破。']})


# ───────────────────────── 判定解析闸 ─────────────────────────

def _wire(*verdicts: dict, selection: tuple[str, ...] = (),
          protocol: str = CSF3_VERIFY_PROTOCOL) -> dict:
    return {'protocol_version': protocol, 'selection': list(selection),
            'verdicts': list(verdicts)}


def _verdict(item: str, verdict: str, *, evidence: tuple[str, ...] = ('帐外传来表弟的声音',),
             obj: str = 'union 断言的主体') -> dict:
    return {'item': item, 'verdict': verdict, 'object': obj,
            'evidence': list(evidence)}


_STAGE2_TEXTS = ('夜里，帐外传来表弟的声音，唤芸娘的名。',
                 '她听出那是夫君学舌的腔调，心下明白是他假作表弟。')
_STAGE2_CLOSED = ('ch03.b1.e1', 'ch03.b1.k1')


def test_parse_accepts_a_well_formed_selection_and_verdicts():
    wire, errors = parse_verdict_payload(
        _wire(_verdict('ch03.b1.e1', '矛盾'), _verdict('ch03.b1.k1', '支持'),
              selection=('ch03.b1.e1', 'ch03.b1.k1')),
        closed_items=_STAGE2_CLOSED, texts=_STAGE2_TEXTS, require_selection=True)
    assert errors == [] and wire is not None
    assert wire.selection == ('ch03.b1.e1', 'ch03.b1.k1')


def test_parse_rejects_shape_and_content_drift():
    bad_payloads = {
        '协议字面量': _wire(_verdict('ch03.b1.e1', '矛盾'),
                          selection=('ch03.b1.e1',), protocol='csf3-verify-v0'),
        '判定值域': _wire({'item': 'ch03.b1.e1', 'verdict': '部分支持',
                          'object': 'x', 'evidence': ['帐外']}, selection=('ch03.b1.e1',)),
        '断言缺证据': _wire({'item': 'ch03.b1.e1', 'verdict': '矛盾',
                           'object': 'x', 'evidence': []}, selection=('ch03.b1.e1',)),
        '闭集外条目': _wire(_verdict('ch09.b01.e1', '矛盾'),
                           selection=('ch09.b01.e1',)),
    }
    for name, payload in bad_payloads.items():
        wire, errors = parse_verdict_payload(
            payload, closed_items=_STAGE2_CLOSED, texts=_STAGE2_TEXTS,
            require_selection=True)
        assert wire is None, name
        assert errors, name
    # 证据引文未逐字命中材料。
    wire, errors = parse_verdict_payload(
        _wire(_verdict('ch03.b1.e1', '矛盾', evidence=('原文说她已识破',)),
              selection=('ch03.b1.e1',)),
        closed_items=_STAGE2_CLOSED, texts=_STAGE2_TEXTS, require_selection=True)
    assert wire is None and any('逐字命中' in e for e in errors)
    # stage2 必须 selection；preserve 必须覆盖闭集。
    wire, errors = parse_verdict_payload(
        _wire(_verdict('ch03.b1.e1', '未确定', evidence=())),
        closed_items=_STAGE2_CLOSED, texts=_STAGE2_TEXTS, require_selection=True)
    assert wire is None and any('selection' in e for e in errors)
    wire, errors = parse_verdict_payload(
        _wire(_verdict('ob99', '支持')), closed_items=('ob44',),
        texts=_STAGE2_TEXTS, require_selection=False)
    assert wire is None and any('未覆盖闭集' in e for e in errors)
    # 非法 JSON 与非对象如实报错。
    assert parse_verdict_payload('不是 JSON', closed_items=('ob44',),
                                 texts=_STAGE2_TEXTS)[1] != []
    assert parse_verdict_payload('[1, 2]', closed_items=('ob44',),
                                 texts=_STAGE2_TEXTS)[1] != []


# ───────────────────────── 评分 ─────────────────────────

def _wire_for(case: CalibrationCase, verdict: str, *, selected: bool = True,
              evidence: tuple[str, ...] = ('帐外传来表弟的声音',)) -> VerdictWire:
    payload = {'protocol_version': CSF3_VERIFY_PROTOCOL,
               'selection': [case.item] if selected and case.target == 'stage2_fact' else [],
               'verdicts': [{'item': case.item, 'verdict': verdict,
                             'object': '合成对象', 'evidence': list(evidence)}]}
    return VerdictWire.model_validate(payload)


def test_outcome_covers_all_verdict_pairs():
    table = {(model, expected): score_case(
        _case(f'c-{model}-{expected}', 'preserve_basis', 'negative_basis',
              'ob44', expected), _wire_for(
                  _case(f'c-{model}-{expected}', 'preserve_basis',
                        'negative_basis', 'ob44', expected), model)).get('outcome')
        for model in ('支持', '矛盾', '未确定')
        for expected in ('支持', '矛盾', '未确定')}
    assert table == {
        ('支持', '支持'): 'correct_support',
        ('矛盾', '支持'): 'wrong_rejection',
        ('未确定', '支持'): 'undecided_miss',
        ('矛盾', '矛盾'): 'correct_rejection',
        ('支持', '矛盾'): 'wrong_support',
        ('未确定', '矛盾'): 'undecided_miss',
        ('未确定', '未确定'): 'correct_undecided',
        ('支持', '未确定'): 'wrong_support',
        ('矛盾', '未确定'): 'wrong_rejection'}


def test_stage2_selection_dimensions_score_miss_and_skip():
    case = _case('c-miss', 'stage2_fact', 'relation', 'ch03.b1.e1', '矛盾',
                 high_impact=True)
    assert score_case(case, _wire_for(case, '矛盾', selected=False))['outcome'] \
        == 'missed_high_impact'
    plain = _case('c-skip', 'stage2_fact', 'identity', 'ch03.b1.e9', '支持',
                  high_impact=False)
    assert score_case(plain, _wire_for(plain, '支持', selected=False))['outcome'] \
        == 'correct_skip'
    assert score_case(case, None)['outcome'] == 'missing'
    assert score_case(case, None, error=True)['outcome'] == 'wire_error'


# ───────────────────────── 运行器（fake transport 全链） ─────────────────────────

def _labels(**case_overrides) -> FrozenLabels:
    """六案合成金标：三目标＋证据不足＋留出集＋镜像已裁决反例。"""
    cases = [
        _case('dev-stage2-misread', 'stage2_fact', 'relation', 'ch03.b1.e1',
              '矛盾', high_impact=True,
              notes='镜像 ch03：夫君假作表弟被记成已发生关系'),
        _case('dev-stage2-known', 'stage2_fact', 'key_knowledge', 'ch03.b1.k1',
              '支持', high_impact=True),
        _case('dev-preserve-ob44', 'preserve_basis', 'negative_basis', 'ob44',
              '矛盾', notes='镜像 ob44：无依据的否定限制'),
        _case('dev-design-lock', 'design_fragment', 'restriction_preservation',
              'kf_01', '矛盾', ref='kf_01', quote='暂缓和离',
              notes='镜像赏花：锁绑到无关内容'),
        _case('dev-stage2-thin', 'stage2_fact', 'identity', 'ch03.b1.e2',
              '未确定', high_impact=True, evidence_insufficient=True,
              quote='传闻他早年远行'),
        _case('hold-stage2-known', 'stage2_fact', 'key_knowledge', 'ch03.b1.k1',
              '支持', high_impact=True, subset='holdout'),
    ]
    for index, case in enumerate(cases):
        cases[index] = case.model_copy(update=case_overrides.get(case.case_id, {}))
    return FrozenLabels.model_validate({'book': '合成书', 'cases': cases})


def _materials() -> dict:
    thin = _stage2_material().model_copy(update={'candidates': (
        FactCandidate(handle='ch03.b1.e1', kind='effect',
                      line='关系 yun_niang↔biao_di的 union：确立「已发生」（已发生）',
                      basis=('帐外传来表弟的声音',)),
        FactCandidate(handle='ch03.b1.k1', kind='cognition',
                      line='yun_niang确认「来者是夫君假作表弟」',
                      basis=('她听出那是夫君学舌的腔调',)),
        FactCandidate(handle='ch03.b1.e2', kind='effect',
                      line='人物 biao_di的 whereabouts：维度未知', basis=()))})
    return {'dev-stage2-misread': thin, 'dev-stage2-known': thin,
            'dev-stage2-thin': thin, 'hold-stage2-known': thin,
            'dev-preserve-ob44': _preserve_material(),
            'dev-design-lock': _design_material()}


def _perfect_responses() -> dict:
    quote1, quote2 = '帐外传来表弟的声音', '她听出那是夫君学舌的腔调'
    return {
        'dev-stage2-misread': _wire(
            _verdict('ch03.b1.e1', '矛盾', evidence=(quote2,)),
            _verdict('ch03.b1.k1', '支持', evidence=(quote2,)),
            _verdict('ch03.b1.e2', '未确定', evidence=()),
            selection=('ch03.b1.e1', 'ch03.b1.k1', 'ch03.b1.e2')),
        'dev-stage2-known': _wire(
            _verdict('ch03.b1.e1', '矛盾', evidence=(quote2,)),
            _verdict('ch03.b1.k1', '支持', evidence=(quote2,)),
            _verdict('ch03.b1.e2', '未确定', evidence=()),
            selection=('ch03.b1.e1', 'ch03.b1.k1', 'ch03.b1.e2')),
        'dev-stage2-thin': _wire(
            _verdict('ch03.b1.e1', '矛盾', evidence=(quote2,)),
            _verdict('ch03.b1.k1', '支持', evidence=(quote2,)),
            _verdict('ch03.b1.e2', '未确定', evidence=()),
            selection=('ch03.b1.e1', 'ch03.b1.k1', 'ch03.b1.e2')),
        'hold-stage2-known': _wire(
            _verdict('ch03.b1.e1', '矛盾', evidence=(quote2,)),
            _verdict('ch03.b1.k1', '支持', evidence=(quote2,)),
            _verdict('ch03.b1.e2', '未确定', evidence=()),
            selection=('ch03.b1.e1', 'ch03.b1.k1', 'ch03.b1.e2')),
        'dev-preserve-ob44': _wire(
            _verdict('ob44', '矛盾', obj='不得当场识破的否定限制', evidence=(quote2,))),
        'dev-design-lock': _wire(
            _verdict('kf_01', '矛盾', obj='绑定落在无关赏花拍',
                     evidence=('园中赏花',))),
    }


def test_fake_green_run_scores_reports_and_stays_unverified():
    labels = _labels()
    scripted = ScriptedTransport(_perfect_responses())
    report = run_calibration(labels, _materials(), scripted)
    outcomes = {row['case_id']: row['outcome'] for row in report['cases']}
    assert outcomes == {
        'dev-stage2-misread': 'correct_rejection',
        'dev-stage2-known': 'correct_support',
        'dev-preserve-ob44': 'correct_rejection',
        'dev-design-lock': 'correct_rejection',
        'dev-stage2-thin': 'correct_undecided',
        'hold-stage2-known': 'correct_support'}
    assert report['by_target']['stage2_fact']['wrong_support'] == 0
    assert report['by_subset']['holdout']['denominator'] == 1
    assert report['hard_rules'] == {'adjudicated_violations': [],
                                    'fabrication_violations': [],
                                    'incomplete': []}
    # unver 纪律：即使全对，fake 绿例也不得记成语义核验已通过。
    assert report['semantic_gate_status'] == 'unver'
    assert report['gate_ready'] is False
    assert '非真实付费执行（scripted fake）' in report['gate_blockers']
    assert '留出集未建立' not in ''.join(report['gate_blockers'])
    assert report['identity']['protocol'] == CSF3_VERIFY_PROTOCOL
    assert report['identity']['rules_revision'] == CSF3_RULES_REVISION
    assert report['identity']['paid'] is False
    assert all(row['prompt_chars'] > 0 for row in report['requests'])
    assert scripted.calls == [c.case_id for c in labels.cases]
    assert run_calibration(labels, _materials(),
                           ScriptedTransport(_perfect_responses())) == report, \
        '同输入恒同报告（确定性；报告不含时间戳）'


def test_fake_red_run_surfaces_hard_rule_and_fabrication_violations():
    responses = dict(_perfect_responses())
    # 已裁决反例被错误支持（镜像 ch03：模型把误派关系判成支持）。
    responses['dev-stage2-misread'] = _wire(
        _verdict('ch03.b1.e1', '支持', evidence=('帐外传来表弟的声音',)),
        _verdict('ch03.b1.k1', '支持', evidence=('她听出那是夫君学舌的腔调',)),
        _verdict('ch03.b1.e2', '未确定', evidence=()),
        selection=('ch03.b1.e1', 'ch03.b1.k1', 'ch03.b1.e2'))
    # 证据确实不足的候选被断言（fabrication）。
    thin = _wire(
        _verdict('ch03.b1.e1', '矛盾', evidence=('她听出那是夫君学舌的腔调',)),
        _verdict('ch03.b1.k1', '支持', evidence=('她听出那是夫君学舌的腔调',)),
        _verdict('ch03.b1.e2', '支持', evidence=('帐外传来表弟的声音',)),
        selection=('ch03.b1.e1', 'ch03.b1.k1', 'ch03.b1.e2'))
    responses['dev-stage2-thin'] = thin
    labels = _labels(**{'dev-stage2-misread': {'label_source': 'user_adjudicated'}})
    report = run_calibration(labels, _materials(),
                             ScriptedTransport(responses))
    assert report['hard_rules']['adjudicated_violations'] == ['dev-stage2-misread']
    assert report['hard_rules']['fabrication_violations'] == ['dev-stage2-thin']
    outcomes = {row['case_id']: row['outcome'] for row in report['cases']}
    assert outcomes['dev-stage2-misread'] == 'wrong_support'
    assert outcomes['dev-stage2-thin'] == 'wrong_support'
    assert any('已裁决正反例' in b for b in report['gate_blockers'])
    assert any('证据不足' in b for b in report['gate_blockers'])


def test_missing_material_and_wire_error_block_with_incomplete_list():
    materials = _materials()
    del materials['dev-preserve-ob44']
    responses = dict(_perfect_responses())
    # 解析失败：证据引文不在材料里。
    responses['dev-design-lock'] = _wire(
        _verdict('kf_01', '矛盾', evidence=('原文写道暂缓和离已被提起',)))
    report = run_calibration(labels := _labels(), materials,
                             ScriptedTransport(responses))
    outcomes = {row['case_id']: row['outcome'] for row in report['cases']}
    assert outcomes['dev-preserve-ob44'] == 'missing'
    assert outcomes['dev-design-lock'] == 'wire_error'
    assert report['hard_rules']['incomplete'] == ['dev-design-lock',
                                                  'dev-preserve-ob44']
    assert any('缺失或解析失败' in b for b in report['gate_blockers'])
    # rc15 真实材料的草案标签是合法来源（不阻断）；错误来源关闭式拒识。
    assert _labels(**{'hold-stage2-known': {
        'label_source': 'rc15_material'}}).cases[-1].label_source == 'rc15_material'
    bad = _labels().model_dump(mode='json')
    bad['cases'][-1]['label_source'] = 'golden_yaml_pending_confirmation'
    with pytest.raises(ValidationError, match='label_source'):
        FrozenLabels.model_validate(bad)


def test_unscripted_case_is_refused_instead_of_passing_silently():
    responses = dict(_perfect_responses())
    del responses['dev-design-lock']
    with pytest.raises(ValueError, match='未脚本化'):
        run_calibration(_labels(), _materials(), ScriptedTransport(responses))


def test_disputed_labels_block_even_when_answers_are_perfect():
    labels = _labels(**{'dev-preserve-ob44': {'disputed': True}})
    report = run_calibration(labels, _materials(),
                             ScriptedTransport(_perfect_responses()))
    assert any('标签分歧未裁决' in b for b in report['gate_blockers'])
    assert report['gate_ready'] is False


# ───────────────────────── CLI（fake 模式） ─────────────────────────

def test_cli_writes_the_report(tmp_path):
    labels = _labels()
    materials = _materials()
    responses = _perfect_responses()
    paths = {}
    for name, payload in (('labels', labels.model_dump(mode='json')),
                          ('materials', {k: m.model_dump(mode='json')
                                         for k, m in materials.items()}),
                          ('responses', responses)):
        paths[name] = tmp_path / f'{name}.json'
        paths[name].write_text(json.dumps(payload, ensure_ascii=False),
                               encoding='utf-8')
    out = tmp_path / 'report.json'
    code = main(['--labels', str(paths['labels']),
                 '--materials', str(paths['materials']),
                 '--responses', str(paths['responses']), '--out', str(out)])
    assert code == 0
    report = json.loads(out.read_text(encoding='utf-8'))
    assert report['semantic_gate_status'] == 'unver'
    assert report['overall']['denominator'] == 6
    assert report['overall']['wrong_support'] == 0
    assert report['identity']['transport'] == 'scripted_fake'
