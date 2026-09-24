"""F7a-1 候选契约登记测试：节点表、产物表、workflow／epoch 身份。

对账三件事：① 表内自洽（produces↔writer 双向、家族覆盖、版本面）；
② 模型自带 `contract_version` 的产物，表值必须与模型声明逐字相等（I-16）；
③ 候选身份结构上进不了现役注册表（S-04：主仓不出现两套现役定义）。
"""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import BaseModel

from contracts.artifacts import ARTIFACT_PATHS
from contracts.invariants import NODES, WORK_UNIT_EXECUTOR_MAX_CONTENT_CALLS
from contracts.schema.str06 import (
    EPOCH_REASONS,
    RC_LABELS,
    STR06_CANDIDATE_ARTIFACTS,
    STR06_CANDIDATE_NODES,
    STR06_PATH_FAMILIES,
    STR06_RC_VERSION,
    STR06_RUNTIME_CONFIG,
    candidate_artifact,
    candidate_contract_identity,
    candidate_node,
    check_candidate_workflow,
    open_candidate_epoch,
    open_candidate_workflow,
    roll_candidate_epoch,
)
from contracts.schema.str06.candidates import RC_VERSION

_STAGES = ('stage2', 'stage4', 'stage5', 'stage7')
_UNIT_KINDS = ('book', 'chapter', 'strategy_and_chapter')
_EPOCH_KWARGS = dict(code_version='code-abc', task_id='task-1',
                     session_run_id='run-1', created_at=1759000000)


def test_rc_version_constant_matches_package():
    assert RC_VERSION == STR06_RC_VERSION
    assert STR06_RC_VERSION in RC_LABELS


def test_work_unit_content_limit_matches_formal_constant():
    for spec in STR06_CANDIDATE_NODES.values():
        if spec.paid:
            assert spec.lifecycle.executor_content_limit == (
                WORK_UNIT_EXECUTOR_MAX_CONTENT_CALLS), spec.node


def test_node_table_integrity():
    nodes = list(STR06_CANDIDATE_NODES)
    assert len(nodes) == len(set(nodes))
    for node_id, spec in STR06_CANDIDATE_NODES.items():
        assert spec.node == node_id
        assert node_id.startswith('str06.'), '候选节点必须带 str06. 前缀'
        assert spec.stage in _STAGES
        assert spec.owner and spec.label
        assert spec.free_recheck in ('--check-only', 'deterministic-rerun')
        assert spec.lifecycle.recovery_entries, '每个节点必须有恢复入口'
        assert len(set(spec.flags)) == len(spec.flags)
        assert len(set(spec.paid_flags)) == len(spec.paid_flags)
        assert spec.lifecycle.unit_kind in _UNIT_KINDS
        expected_scope = {'book': 'executor:book', 'chapter': 'executor:chapter',
                          'strategy_and_chapter': 'executor:strategy/chapter'}
        assert spec.lifecycle.session_scope == expected_scope[spec.lifecycle.unit_kind]
        for rel in spec.produces:
            assert candidate_artifact(rel) is not None, f'{node_id} 产出未登记：{rel}'


def test_free_nodes_structurally_cannot_pay():
    for node_id in ('str06.stage4.compile', 'str06.stage7.assemble'):
        spec = STR06_CANDIDATE_NODES[node_id]
        assert spec.paid is False
        assert spec.lifecycle.executor_content_limit == 0
        assert spec.free_recheck == 'deterministic-rerun'


def test_paid_nodes_have_check_only_recheck():
    for node_id, spec in STR06_CANDIDATE_NODES.items():
        if spec.paid:
            assert '--check-only' in spec.flags, node_id
            assert spec.free_recheck == '--check-only', node_id


def test_artifact_table_integrity():
    rels = list(STR06_CANDIDATE_ARTIFACTS)
    assert len(rels) == len(set(rels))
    for rel, spec in STR06_CANDIDATE_ARTIFACTS.items():
        assert spec.rel == rel
        assert spec.family in STR06_PATH_FAMILIES, f'{rel} 家族未登记'
        family = STR06_PATH_FAMILIES[spec.family]
        assert rel.startswith(family.prefix), f'{rel} 不属于家族 {spec.family}'
        assert spec.writer in STR06_CANDIDATE_NODES, f'{rel} writer 不是候选节点'
        assert spec.form in ('json', 'text')
        assert spec.schema_version in RC_LABELS
        if spec.form == 'json':
            if spec.model is not None:
                assert issubclass(spec.model, BaseModel), f'{rel} model 不是契约模型'
        else:
            assert spec.model is None, f'{rel} 是正文，不得挂 schema 模型'


def test_model_declared_contract_version_matches_table():
    """模型自带 contract_version 的产物：表值必须与模型声明逐字相等（I-16）。"""
    checked = 0
    for rel, spec in STR06_CANDIDATE_ARTIFACTS.items():
        if spec.model is None:
            continue
        field = spec.model.model_fields.get('contract_version')
        if field is None:
            continue
        assert spec.schema_version == field.default, (
            f'{rel}：表值 {spec.schema_version} 与模型声明 {field.default} 不一致')
        checked += 1
    assert checked >= 8, '至少 8 份产物模型自带声明，覆盖不足说明表漏登'


def test_produces_and_writer_bidirectional():
    """produces ↔ writer 双向对账：每个产物恰一个执行 writer（I-25）。"""
    produced: dict[str, str] = {}
    for node_id, spec in STR06_CANDIDATE_NODES.items():
        for rel in spec.produces:
            assert rel not in produced, f'{rel} 被 {produced[rel]} 与 {node_id} 同时产出'
            produced[rel] = node_id
            assert STR06_CANDIDATE_ARTIFACTS[rel].writer == node_id, (
                f'{rel} 的 writer 与 produces 不一致')
    assert set(produced) == set(STR06_CANDIDATE_ARTIFACTS), (
        '产物表与节点 produces 集合不一致：存在无执行 writer 的产物')


def test_every_family_has_registered_artifact():
    covered = {spec.family for spec in STR06_CANDIDATE_ARTIFACTS.values()}
    assert covered == set(STR06_PATH_FAMILIES), (
        f'未覆盖家族：{set(STR06_PATH_FAMILIES) - covered}')


def test_candidate_ids_never_enter_production_registries():
    for node_id in STR06_CANDIDATE_NODES:
        assert node_id not in NODES, '候选节点不得借用现役节点身份'
    production_rels = set(ARTIFACT_PATHS.values())
    for rel in STR06_CANDIDATE_ARTIFACTS:
        assert rel not in production_rels
    for family in STR06_PATH_FAMILIES:
        assert family not in ARTIFACT_PATHS


def test_candidate_node_lookup_refuses_unknown():
    with pytest.raises(ValueError, match='未登记的候选节点'):
        candidate_node('str06.stage9.ghost')


def test_candidate_artifact_pattern_matching():
    assert candidate_artifact('stage5/body/ch03.txt').rel == 'stage5/body/ch{nn}.txt'
    assert candidate_artifact('stage5/body/ch3.txt') is None, '章序必须两位'
    assert candidate_artifact(
        'stage7/final/夫君的报恩_rewriting.txt').rel == (
            'stage7/final/{title}_rewriting.txt')
    assert candidate_artifact('stage5/body/ch03.txt.bak') is None
    assert candidate_artifact('stage9/ghost.json') is None


def test_contract_identity_covers_both_tables():
    """身份摘要必须覆盖节点表与产物表全部内容（防改表不换身份）。"""
    expected = hashlib.sha256(json.dumps({
        'rc': RC_VERSION,
        'config': STR06_RUNTIME_CONFIG._asdict(),
        'nodes': {node: {'stage': s.stage, 'owner': s.owner, 'paid': s.paid,
                         'produces': list(s.produces),
                         'free_recheck': s.free_recheck,
                         'flags': list(s.flags), 'paid_flags': list(s.paid_flags),
                         'lifecycle': {
                             'unit_kind': s.lifecycle.unit_kind,
                             'session_scope': s.lifecycle.session_scope,
                             'executor_content_limit': s.lifecycle.executor_content_limit,
                             'recovery_entries': list(s.lifecycle.recovery_entries)}}
                  for node, s in sorted(STR06_CANDIDATE_NODES.items())},
        'artifacts': {rel: {'family': a.family, 'form': a.form, 'writer': a.writer,
                            'schema_version': a.schema_version,
                            'model': getattr(a.model, '__name__', None)}
                      for rel, a in sorted(STR06_CANDIDATE_ARTIFACTS.items())},
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()
    identity = candidate_contract_identity()
    assert identity == expected
    assert identity == candidate_contract_identity(), '身份必须确定可复算'


def test_epoch_open_is_deterministic_and_distinct():
    first = open_candidate_epoch(**_EPOCH_KWARGS)
    assert first.ordinal == 1 and first.reason == 'initial'
    assert first.epoch_id == open_candidate_epoch(**_EPOCH_KWARGS).epoch_id
    drifted = open_candidate_epoch(**{**_EPOCH_KWARGS, 'code_version': 'code-xyz'})
    assert drifted.epoch_id != first.epoch_id
    with pytest.raises(ValueError, match='只能是 initial'):
        open_candidate_epoch(**{**_EPOCH_KWARGS, 'reason': 'code_drift'})


def test_epoch_roll_rules():
    first = open_candidate_epoch(**_EPOCH_KWARGS)
    rolled = roll_candidate_epoch(first, reason='code_drift',
                                  **{**_EPOCH_KWARGS, 'code_version': 'code-xyz'})
    assert rolled.ordinal == 2 and rolled.reason == 'code_drift'
    assert rolled.epoch_id != first.epoch_id
    # 链条可验证：同一 prev 与同一输入滚动结果确定。
    assert rolled.epoch_id == roll_candidate_epoch(
        first, reason='code_drift', **{**_EPOCH_KWARGS, 'code_version': 'code-xyz'}).epoch_id
    # 假漂移拒绝：身份没变不许开新 epoch。
    with pytest.raises(ValueError, match='拒绝假漂移'):
        roll_candidate_epoch(first, reason='code_drift', **_EPOCH_KWARGS)
    with pytest.raises(ValueError, match='拒绝假漂移'):
        roll_candidate_epoch(first, reason='contract_drift', **_EPOCH_KWARGS)
    # typed 修复允许同身份续开（显式 producer 边界）。
    repaired = roll_candidate_epoch(first, reason='repair', **_EPOCH_KWARGS)
    assert repaired.ordinal == 2 and repaired.code_version == first.code_version
    with pytest.raises(ValueError, match='滚动理由不在面内'):
        roll_candidate_epoch(first, reason='initial', **_EPOCH_KWARGS)
    assert 'repair' in EPOCH_REASONS


def test_workflow_open_and_check():
    epoch = open_candidate_epoch(**_EPOCH_KWARGS)
    source = hashlib.sha256(b'source-bytes').hexdigest()
    record = open_candidate_workflow(epoch=epoch, source_identity=source,
                                     created_at=1759000001)
    assert record.contract_identity == candidate_contract_identity()
    assert check_candidate_workflow(record, code_version='code-abc') == []
    with pytest.raises(ValueError, match='64 位十六进制'):
        open_candidate_workflow(epoch=epoch, source_identity='not-a-sha',
                                created_at=1759000001)


def test_workflow_check_detects_drift_and_tampering():
    epoch = open_candidate_epoch(**_EPOCH_KWARGS)
    source = hashlib.sha256(b'source-bytes').hexdigest()
    record = open_candidate_workflow(epoch=epoch, source_identity=source,
                                     created_at=1759000001)
    problems = check_candidate_workflow(record, code_version='code-xyz')
    assert any('代码身份漂移' in p for p in problems)
    tampered = record._replace(contract_identity='0' * 64)
    problems = check_candidate_workflow(tampered, code_version='code-abc')
    assert any('契约表已漂移' in p for p in problems)
    broken = record._replace(source_identity='short')
    problems = check_candidate_workflow(broken, code_version='code-abc')
    assert any('源输入身份' in p for p in problems)
    bad_epoch = record._replace(epoch=epoch._replace(epoch_id='xyz'))
    problems = check_candidate_workflow(bad_epoch, code_version='code-abc')
    assert any('epoch 形状损坏' in p for p in problems)


def test_runtime_config_is_the_formal_source_and_identity_bound():
    """Hook 长度／篇幅带／取整粒度有正式来源，且进契约身份（配置变化换身份）。"""
    assert STR06_RUNTIME_CONFIG.hook_hard_max_chars > STR06_RUNTIME_CONFIG.hook_target_chars
    assert 0 < STR06_RUNTIME_CONFIG.length_band_low < STR06_RUNTIME_CONFIG.length_band_high
    before = candidate_contract_identity()
    assert 'config' in json.loads(json.dumps({
        'config': STR06_RUNTIME_CONFIG._asdict()})), '配置必须可序列化进身份'


def test_derived_budget_is_deterministic_from_source_chapters():
    from novel.stages.str06.runner import derive_total_length_budget
    chapters = [{'chapter_ordinal': 1, 'text': '九' * 2500},
                {'chapter_ordinal': 2, 'text': '八' * 2500}]
    assert derive_total_length_budget(chapters) == 5000      # 5000 正向取整
    assert derive_total_length_budget(chapters, rounding=1000) == 5000
    assert derive_total_length_budget(chapters, rounding=100) == 5000
    assert derive_total_length_budget(chapters[:1], rounding=1000) == 3000
    with pytest.raises(ValueError, match='无法派生'):
        derive_total_length_budget([])
