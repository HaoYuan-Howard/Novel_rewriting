"""候选契约登记测试：激活表（NODES／ARTIFACT_PATHS）、workflow／epoch 身份。

【F8c 改写了什么】迁移包 `contracts/schema/str06/candidates.py` 已删除，节点表与
产物表在 F8a 激活进 `contracts/invariants.py::NODES` 与
`contracts/artifacts.py::ARTIFACT_PATHS`（F8c 起它们是唯一现役面）。本文件随之
改对**激活表**：断言坐标从「候选表」换成「NODES + ARTIFACT_PATHS +
STR06_ARTIFACT_RC_VERSIONS + ARTIFACT_SPECS + STR06_PATH_FAMILIES」，一条用例
都没删——表内自洽、模型声明逐字相等、付费/免费结构禁付、epoch 语义、契约身份
确定性全部照旧。

对账四件事：① 表内自洽（produces↔writer 双向、家族覆盖、版本面）；
② 模型自带 `contract_version` 的产物，表值必须与模型声明逐字相等（I-16）；
③ 激活表就是候选表（S-04：主仓不出现两套现役定义）；
④ 契约身份与 F7e 冻结基线逐字相等（`tests/test_str06_f7e_freeze.py`）。
"""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import BaseModel

from contracts.artifacts import (
    ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS,
)
from contracts.invariants import (
    NODES, RETIRED_NODES, RETIRED_STAGES, STAGES,
    WORK_UNIT_EXECUTOR_MAX_CONTENT_CALLS,
)
from contracts.schema import ARTIFACT_SPECS
from contracts.schema.families import STR06_PATH_FAMILIES
from contracts.schema.workflow import (
    EPOCH_REASONS, STR06_RC_LABELS, STR06_RC_VERSION, STR06_RUNTIME_CONFIG,
    candidate_node_owner,
)
from novel.llm.paid_requests import (
    candidate_artifact, candidate_contract_identity, candidate_node,
    check_candidate_workflow, open_candidate_epoch, open_candidate_workflow,
    roll_candidate_epoch,
)

_STAGES = ('stage2', 'stage4', 'stage5', 'stage7')
_UNIT_KINDS = ('book', 'chapter', 'strategy_and_chapter')
_EPOCH_KWARGS = dict(code_version='code-abc', task_id='task-1',
                     session_run_id='run-1', created_at=1759000000)

#: 候选链节点（`str06.` 命名空间）；Stage0/1 的 `stageN.<name>` 键不在其内。
CANDIDATE_NODES = {node: spec for node, spec in NODES.items()
                   if node.startswith('str06.')}

#: 候选链书产物：rel 键即相对路径（F8a 激活时与候选产物表逐字相等）。
CANDIDATE_ARTIFACTS = {rel: version for rel, version
                       in STR06_ARTIFACT_RC_VERSIONS.items()}


def test_rc_version_constant_matches_labels():
    assert STR06_RC_VERSION in STR06_RC_LABELS


def test_work_unit_content_limit_matches_formal_constant():
    for spec in CANDIDATE_NODES.values():
        if spec.paid:
            assert spec.lifecycle.executor_content_limit == (
                WORK_UNIT_EXECUTOR_MAX_CONTENT_CALLS), spec.node


def test_node_table_integrity():
    nodes = list(CANDIDATE_NODES)
    assert len(nodes) == len(set(nodes))
    for node_id, spec in CANDIDATE_NODES.items():
        assert node_id in NODES and NODES[node_id] is spec, (
            'NODES 键与规格必须同一对象（I-16：每类信息只有一个权威源）')
        assert node_id.startswith('str06.'), '候选节点必须带 str06. 前缀'
        assert spec.stage in _STAGES
        assert candidate_node_owner(node_id) and spec.label
        assert spec.lifecycle.recovery_entries, '每个节点必须有恢复入口'
        assert len(set(spec.flags)) == len(spec.flags)
        assert len(set(spec.paid_flags)) == len(spec.paid_flags)
        assert spec.lifecycle.unit_kind in _UNIT_KINDS
        expected_scope = {'book': 'executor:book', 'chapter': 'executor:chapter',
                          'strategy_and_chapter': 'executor:strategy/chapter'}
        assert spec.lifecycle.session_scope in (
            expected_scope[spec.lifecycle.unit_kind], 'none')
        for rel in spec.produces:
            assert candidate_artifact(rel) is not None, f'{node_id} 产出未登记：{rel}'


def test_free_nodes_structurally_cannot_pay():
    for node_id in ('str06.stage2.compile', 'str06.stage4.compile',
                    'str06.stage7.assemble'):
        spec = CANDIDATE_NODES[node_id]
        assert spec.paid is False
        assert spec.lifecycle.executor_content_limit == 0
        assert spec.flags == (), '免费节点终态不接受任何跑法旗标'


def test_paid_nodes_have_check_only_recheck():
    for node_id, spec in CANDIDATE_NODES.items():
        if spec.paid:
            assert '--check-only' in spec.flags, node_id


def test_stage5_body_lifecycle_registers_packet_input_and_set_recovery():
    """R4：`str06.stage5.body` 的生命周期如实登记章包输入与集合恢复语义（D2/B4）。

    旧登记写着「WriterBookIR 与已提交章前缀（实际前文）」＋恢复入口
    `chapter_prefix_replay`——那是 D2 之前的串行前文链语义；现役实现各章只收
    章包投影（不读前文正文），恢复按有效提交集合只补缺章（回执＋正文＋章包
    摘要三方绑定）。登记经 `/api/enums` 与职责表流向监控面，与实现漂移就是
    第二套语义（I-16）；`chapter_prefix_replay` 改名后旧名不得残留在任何节点。
    """
    life = CANDIDATE_NODES['str06.stage5.body'].lifecycle
    assert '章包' in life.input_workpack, life.input_workpack
    assert '前文' not in life.input_workpack and '前缀' not in life.input_workpack
    assert 'committed_set_recovery' in life.recovery_entries
    assert 'chapter_prefix_replay' not in life.recovery_entries
    for node_id, spec in CANDIDATE_NODES.items():
        assert 'chapter_prefix_replay' not in spec.lifecycle.recovery_entries, node_id
    # 审阅面同步集合语义：无合法候选停该章，其余章照常——不是停后续章。
    assert '停该章' in life.review_scope, life.review_scope
    assert '停止后续章' not in life.review_scope


def test_artifact_table_integrity():
    rels = list(CANDIDATE_ARTIFACTS)
    assert len(rels) == len(set(rels))
    for rel, version in CANDIDATE_ARTIFACTS.items():
        assert rel in ARTIFACT_PATHS, f'{rel} 未激活进 ARTIFACT_PATHS'
        assert rel in STR06_ARTIFACT_RC_VERSIONS
        family = STR06_PATH_FAMILIES[_family_key(rel)]
        assert rel.startswith(family.prefix), f'{rel} 不属于家族 {family}'
        assert _writer_of(rel) in CANDIDATE_NODES, f'{rel} writer 不是候选节点'
        assert version in STR06_RC_LABELS
        if rel.endswith('.txt'):
            assert rel not in ARTIFACT_SPECS, f'{rel} 是正文，不得挂 schema 模型'
        elif rel in ARTIFACT_SPECS:
            assert issubclass(ARTIFACT_SPECS[rel].model, BaseModel), (
                f'{rel} model 不是契约模型')


def test_model_declared_contract_version_matches_table():
    """模型自带 contract_version 的产物：表值必须与模型声明逐字相等（I-16）。"""
    checked = 0
    for rel, version in CANDIDATE_ARTIFACTS.items():
        model = ARTIFACT_SPECS[rel].model if rel in ARTIFACT_SPECS else None
        if model is None:
            continue
        field = model.model_fields.get('contract_version')
        if field is None:
            continue
        assert version == field.default, (
            f'{rel}：表值 {version} 与模型声明 {field.default} 不一致')
        checked += 1
    assert checked >= 8, '至少 8 份产物模型自带声明，覆盖不足说明表漏登'


def test_produces_and_writer_bidirectional():
    """produces ↔ writer 双向对账：每个产物恰一个执行 writer（I-25）。"""
    produced: dict[str, str] = {}
    for node_id, spec in CANDIDATE_NODES.items():
        for rel in spec.produces:
            assert rel not in produced, f'{rel} 被 {produced[rel]} 与 {node_id} 同时产出'
            produced[rel] = node_id
            assert _writer_of(rel) == node_id, (
                f'{rel} 的 writer 与 produces 不一致')
    assert set(produced) == set(CANDIDATE_ARTIFACTS), (
        '产物表与节点 produces 集合不一致：存在无执行 writer 的产物')


def test_every_family_has_registered_artifact():
    covered = {_family_key(rel) for rel in CANDIDATE_ARTIFACTS}
    assert covered == set(STR06_PATH_FAMILIES), (
        f'未覆盖家族：{set(STR06_PATH_FAMILIES) - covered}')


def test_candidate_table_is_now_the_production_registry():
    """F8A 翻转：候选表**就是**现役注册表（F7e 冻结基线原样激活）。

    切换后同一张表成为唯一现役面：每个 `str06.` 节点必须在 NODES 里且
    paid／produces／unit_kind／executor_content_limit 与冻结基线一致；每个候选
    产物 rel 必须在 ARTIFACT_PATHS 里；旧链节点键一个都不在。
    """
    for node_id, spec in CANDIDATE_NODES.items():
        assert node_id in NODES, f'候选节点未激活：{node_id}'
        live = NODES[node_id]
        assert live.paid == spec.paid, node_id
        assert set(live.produces) == set(spec.produces), node_id
        assert live.lifecycle.unit_kind == spec.lifecycle.unit_kind, node_id
        assert (live.lifecycle.executor_content_limit
                == spec.lifecycle.executor_content_limit), node_id
    production_rels = set(ARTIFACT_PATHS.values())
    for rel in CANDIDATE_ARTIFACTS:
        assert rel in production_rels, f'候选产物未激活：{rel}'
    # 旧链 14 个节点键必须已经不在现役表（移入 RETIRED_NODES 只读解码）。
    for retired in ('stage2.analyze', 'stage2.characters', 'stage2.global',
                    'stage2.blueprint', 'stage4.design', 'stage4.plan',
                    'stage4.context', 'stage5.write', 'stage5.review',
                    'stage6.prepare', 'stage6.hook', 'stage6.approve',
                    'stage7.rename'):
        assert retired not in NODES, f'旧链节点键仍在现役表：{retired}'
        assert retired in RETIRED_NODES, f'旧链节点键未登记只读解码：{retired}'
    # Stage6 整层不在现役 stage 面。
    assert 'stage6' not in STAGES and 'stage6' in RETIRED_STAGES


def test_candidate_node_lookup_refuses_unknown():
    with pytest.raises(ValueError, match='未登记的候选节点'):
        candidate_node('str06.stage9.ghost')


def test_candidate_artifact_pattern_matching():
    assert candidate_artifact('stage5/body/ch03.txt') == 'stage5/body/ch{nn}.txt'
    assert candidate_artifact('stage5/body/ch3.txt') is None, '章序必须两位'
    assert candidate_artifact(
        'stage7/final/夫君的报恩_rewriting.txt') == (
            'stage7/final/{title}_rewriting.txt')
    assert candidate_artifact('stage5/body/ch03.txt.bak') is None
    assert candidate_artifact('stage9/ghost.json') is None


def test_contract_identity_covers_both_tables():
    """身份摘要必须覆盖节点表与产物表全部内容（防改表不换身份）。"""
    expected = hashlib.sha256(json.dumps({
        'rc': STR06_RC_VERSION,
        'config': STR06_RUNTIME_CONFIG._asdict(),
        'nodes': {node: {'stage': s.stage, 'owner': candidate_node_owner(node),
                         'paid': s.paid,
                         'produces': list(s.produces),
                         'free_recheck': ('--check-only' if s.paid
                                          else 'deterministic-rerun'),
                         'flags': list(s.flags), 'paid_flags': list(s.paid_flags),
                         'lifecycle': {
                             'unit_kind': s.lifecycle.unit_kind,
                             'session_scope': _identity_session_scope(node, s),
                             'executor_content_limit': s.lifecycle.executor_content_limit,
                             'recovery_entries': list(s.lifecycle.recovery_entries)}}
                  for node, s in sorted(CANDIDATE_NODES.items())},
        'artifacts': {rel: {'family': _family_key(rel),
                            'form': 'text' if rel.endswith('.txt') else 'json',
                            'writer': _writer_of(rel),
                            'schema_version': version,
                            'model': (getattr(ARTIFACT_SPECS[rel].model, '__name__', None)
                                      if rel in ARTIFACT_SPECS else None)}
                      for rel, version in sorted(CANDIDATE_ARTIFACTS.items())},
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
    assert 'config' in json.loads(json.dumps({
        'config': STR06_RUNTIME_CONFIG._asdict()})), '配置必须可序列化进身份'


def test_derived_budget_is_deterministic_from_source_chapters():
    from novel.stages.chain import derive_total_length_budget
    chapters = [{'chapter_ordinal': 1, 'text': '九' * 2500},
                {'chapter_ordinal': 2, 'text': '八' * 2500}]
    assert derive_total_length_budget(chapters) == 5000      # 5000 正向取整
    assert derive_total_length_budget(chapters, rounding=1000) == 5000
    assert derive_total_length_budget(chapters, rounding=100) == 5000
    assert derive_total_length_budget(chapters[:1], rounding=1000) == 3000
    with pytest.raises(ValueError, match='无法派生'):
        derive_total_length_budget([])


# ─── 激活表的坐标助手（与 `novel/llm/paid_requests.py` 同一套派生规则）────────

def _family_key(rel: str) -> str:
    for key, family in STR06_PATH_FAMILIES.items():
        if rel.startswith(family.prefix):
            return key
    raise AssertionError(rel)


def _writer_of(rel: str) -> str:
    for node_id, node in NODES.items():
        if rel in node.produces:
            return node_id
    raise AssertionError(rel)


def _identity_session_scope(node_id: str, spec) -> str:
    """契约身份用的 session_scope：免费节点按冻结表登记 executor:book。

    `NODES` 对免费节点登记 `'none'`（调度面：它们确实不跑 executor）；F7e 冻结
    基线按 executor 面登记。补齐格见
    `contracts/schema/workflow.py::STR06_NODE_IDENTITY_EXTRAS`。
    """
    from contracts.schema.workflow import STR06_NODE_IDENTITY_EXTRAS
    extra = STR06_NODE_IDENTITY_EXTRAS.get(node_id, {})
    return extra.get('session_scope', spec.lifecycle.session_scope)
