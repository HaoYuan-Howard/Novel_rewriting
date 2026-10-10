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
    NODES, RETIRED_STAGES, STAGES,
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
    # 旧链 14 个节点键必须已经不在现役表（STR-06 关闭批：RETIRED_NODES
    # 名册已删，关闭式拒识按「非现役节点」通则，不再有只读解码登记面）。
    for retired in ('stage2.analyze', 'stage2.characters', 'stage2.global',
                    'stage2.blueprint', 'stage4.design', 'stage4.plan',
                    'stage4.context', 'stage5.write', 'stage5.review',
                    'stage6.prepare', 'stage6.hook', 'stage6.approve',
                    'stage7.rename'):
        assert retired not in NODES, f'旧链节点键仍在现役表：{retired}'
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
                         'operator_redo': s.operator_redo,
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
                            'envelope_version': (ARTIFACT_SPECS[rel].current_version
                                                 if rel in ARTIFACT_SPECS else None),
                            'model': (getattr(ARTIFACT_SPECS[rel].model, '__name__', None)
                                      if rel in ARTIFACT_SPECS else None)}
                      for rel, version in sorted(CANDIDATE_ARTIFACTS.items())},
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()
    identity = candidate_contract_identity()
    assert identity == expected
    assert identity == candidate_contract_identity(), '身份必须确定可复算'


def test_contract_identity_changes_with_envelope_version_and_repair_policy(monkeypatch):
    """同名模型的盘上协议或执行权限改变，不能继续采用旧契约身份。"""
    from dataclasses import replace
    from contracts.invariants import NODES
    baseline = candidate_contract_identity()
    rel = 'stage2/observations/names.json'
    spec = ARTIFACT_SPECS[rel]
    monkeypatch.setitem(ARTIFACT_SPECS, rel, replace(spec, current_version=spec.current_version + 1))
    assert candidate_contract_identity() != baseline
    monkeypatch.setitem(ARTIFACT_SPECS, rel, spec)
    node = 'str06.stage4.hook_design'
    monkeypatch.setitem(NODES, node, NODES[node]._replace(operator_redo='canonical'))
    assert candidate_contract_identity() != baseline


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
    """正文篇幅配置有正式来源；Hook 没有字数闸或冻结的长度选择。"""
    assert not {'hook_target_chars', 'hook_hard_max_chars'} & set(STR06_RUNTIME_CONFIG._fields)
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


# ── CS-H H1b：候选集合、上游差异与称谓规范形（纯函数与登记对账） ──────────────

def test_upstream_diff_aligns_entries_by_kind_and_anchors():
    from novel.stages.candidates import upstream_diff

    before = {'obligations': [
        {'kind': 'event_core', 'statement': '甲嫁入陆家', 'anchors': ['b01_01.e1'],
         'must_preserve': True, 'satisfaction': '婚约成立', 'requires': []},
        {'kind': 'reveal', 'statement': '玉佩之秘后揭', 'anchors': ['b02_01.e1'],
         'must_preserve': False, 'satisfaction': '', 'requires': []}]}
    after = {'obligations': [
        {'kind': 'reveal', 'statement': '玉佩之秘后揭', 'anchors': ['b02_01.e1'],
         'must_preserve': False, 'satisfaction': '', 'requires': []},
        {'kind': 'event_core', 'statement': '甲嫁入陆家', 'anchors': ['b01_01.e1'],
         'must_preserve': True, 'satisfaction': '婚约成立', 'requires': []},
        {'kind': 'causal', 'statement': '旧约之因果不可断', 'anchors': ['b01_01.e1'],
         'must_preserve': False, 'satisfaction': '', 'requires': []}]}
    diff = upstream_diff(before, after)
    by_status = {status: [e.key for e in diff.entries if e.status == status]
                 for status in ('unchanged', 'changed', 'added', 'removed')}
    # anchors 顺序不参与对齐；同 anchors 不同 kind 是不同条目。列表顺序**参与**判定：
    # 义务编号（`ob01…`）由下标派生，重排＝两条义务互换了编号，内容相等也是「变化」。
    assert by_status['unchanged'] == []
    assert sorted(by_status['changed']) == [(('event_core', ('b01_01.e1',))),
                                            (('reveal', ('b02_01.e1',)))]
    assert by_status['added'] == [('causal', ('b01_01.e1',))]
    assert not by_status['removed']
    changed = upstream_diff(before, {'obligations': [
        {'kind': 'event_core', 'statement': '甲嫁入陆家且立约', 'anchors': ['b01_01.e1'],
         'must_preserve': True, 'satisfaction': '婚约成立', 'requires': []}]})
    assert [e.status for e in changed.entries] == ['changed', 'removed']


def test_upstream_diff_aligns_duplicate_keys_by_occurrence_instead_of_raising():
    """同 kind 同 anchors 的两条义务（Preserve 并不禁止）：按出现序配对，不抛错。

    此前重复键直接 `ValueError`，而免费沿用判定在 `run_body_design` 里没有兜底——
    只要上游有这样一对义务，重入 Design 就崩，整本书停在这里。配对只会把「未变」
    保守地判成「变化」，不会把变化判成未变（未变要求条目逐字相等）。
    """
    from novel.stages.candidates import entry_keys, upstream_diff

    def ob(statement):
        return {'kind': 'event_core', 'statement': statement, 'anchors': ['b01_01.e1'],
                'must_preserve': True, 'satisfaction': '', 'requires': []}

    assert entry_keys([('event_core', ['b01_01.e1']), ('event_core', ['b01_01.e1']),
                       ('reveal', ['b01_01.e1'])]) == [
        ('event_core', ('b01_01.e1',)), ('event_core', ('b01_01.e1', '#2')),
        ('reveal', ('b01_01.e1',))]
    before = {'obligations': [ob('甲'), ob('乙')]}
    assert upstream_diff(before, {'obligations': [ob('甲'), ob('乙')]}).empty
    changed = upstream_diff(before, {'obligations': [ob('甲'), ob('丙')]})
    assert {e.key[1][-1]: e.status for e in changed.entries} == {
        'b01_01.e1': 'unchanged', '#2': 'changed'}
    swapped = upstream_diff(before, {'obligations': [ob('乙'), ob('甲')]})
    assert {e.status for e in swapped.entries} == {'changed'}, '换序不会被误判成未变'
    removed = upstream_diff(before, {'obligations': [ob('甲')]})
    assert {e.key[1][-1]: e.status for e in removed.entries} == {
        'b01_01.e1': 'unchanged', '#2': 'removed'}


def _ob(kind, anchor, statement='甲', **over):
    return {'kind': kind, 'statement': statement, 'anchors': [anchor], 'must_preserve': True,
            'satisfaction': '婚约成立', 'requires': [], **over}


def test_position_drift_is_a_change_because_obligation_ids_are_positional():
    """义务编号（`ob01…`）由下标派生，候选里的 `real: ['ob07']` 引用的是位置。

    重排、或前面有增删，都让同一条目换了编号——内容逐字相等也不是「未变」，否则
    候选里的承载标注会悄悄指向另一条义务，而差异摘要为空、预检又恰好全绿。
    """
    from novel.stages.candidates import upstream_diff

    a, b, c = _ob('event_core', 'a1', '甲'), _ob('reveal', 'b1', '乙'), _ob('causal', 'c1', '丙')
    reorder = upstream_diff({'obligations': [a, b]}, {'obligations': [b, a]})
    assert not reorder.empty and {e.status for e in reorder.entries} == {'changed'}
    insert = upstream_diff({'obligations': [a, b]}, {'obligations': [a, c, b]})
    assert {e.key[0]: e.status for e in insert.entries} == {
        'event_core': 'unchanged', 'causal': 'added', 'reveal': 'changed'}, \
        '插入点之后的条目编号后移：变化；插入点之前的不受影响'
    assert upstream_diff({'obligations': [a, b]}, {'obligations': [a, b]}).empty


def test_entry_equivalence_needs_every_field_not_just_the_statement():
    """等价＝位置不变＋文本字段称谓规范形相等＋其余字段逐字相等。

    此前只比 `statement`：`satisfaction`（验收条件）、`requires`（前置序号）、
    `must_preserve` 变了也被当成「只差称谓」而零调用放行。
    """
    from novel.stages.candidates import entry_equivalent, upstream_diff

    people = {'fu_jun': ('夫君', '陆大哥'), 'yun_niang': ('苏芸娘', '芸娘')}
    base = _ob('event_core', 'a1', '苏芸娘嫁给夫君')

    def verdict(**over):
        diff = upstream_diff({'obligations': [base]}, {'obligations': [{**base, **over}]})
        return bool(diff.changed) and all(entry_equivalent(e, people) for e in diff.changed)

    assert verdict(statement='芸娘嫁给陆大哥'), '只换登记称谓'
    assert verdict(satisfaction='婚约，成立'), '标点空白'
    assert not verdict(statement='芸娘嫁给陆大哥且立约'), '多了一段事实'
    assert not verdict(satisfaction='婚约不成立'), '验收条件变了'
    assert not verdict(requires=[3]), '前置义务变了'
    assert not verdict(must_preserve=False), '必保性变了'
    assert not verdict(extra_field='x'), '字段集变了'
    # 位置漂移的条目即使内容等价也不放行（编号换了所指）
    drift = upstream_diff({'obligations': [_ob('reveal', 'b1'), base]},
                          {'obligations': [base, _ob('reveal', 'b1')]})
    assert drift.changed and not any(entry_equivalent(e, people) for e in drift.changed)


def test_added_or_removed_entries_are_never_carried_forward_for_free():
    """差异里只要有新增／删除条目（编号漂移、新义务无人承载），即使其余变化都等价
    也不得零调用采用——此前 `changed_equivalent` 只看变化条目。"""
    from novel.stages.candidates import CandidateAssessment, upstream_diff

    base, extra = _ob('event_core', 'a1'), _ob('causal', 'c1', '乙')
    renamed = {**base, 'statement': '甲，'}
    added = upstream_diff({'obligations': [base]}, {'obligations': [renamed, extra]})
    removed = upstream_diff({'obligations': [base, extra]}, {'obligations': [renamed]})
    for diff in (added, removed):
        assessment = CandidateAssessment(entry='c', ordinal=1, compatible=True, has_lineage=True,
                                         diff=diff, changed_equivalent=True, preflight_passed=True)
        assert not assessment.adoptable
    assert CandidateAssessment(
        entry='c', ordinal=1, compatible=True, has_lineage=True,
        diff=upstream_diff({'obligations': [base]}, {'obligations': [renamed]}),
        changed_equivalent=True, preflight_passed=True).adoptable


def test_plan_reuse_never_hides_a_full_purchase_behind_an_empty_narrow_review():
    """窄复核没有目标时：候选全绿＝无拍受影响，免费沿用；候选自身预检未过＝如实标成
    普通接受循环（同请求身份免费重放已购回答），而不是冒充窄复核——更不能从该候选
    起步再买一轮：重入那样做会每次重启都重置修订额度。"""
    from novel.stages.candidates import CandidateAssessment, plan_reuse, upstream_diff

    base = _ob('event_core', 'a1', '甲')
    changed = upstream_diff({'obligations': [base]},
                            {'obligations': [{**base, 'statement': '甲且乙'}]})

    def plan(passed):
        return plan_reuse([CandidateAssessment(
            entry='c', ordinal=1, compatible=True, has_lineage=True, diff=changed,
            changed_equivalent=False, preflight_passed=passed, root_causes=0 if passed else 2,
            carriers={})])

    green = plan(True)
    assert green.action == 'carry_forward' and '无承载拍引用' in green.reason
    broken = plan(False)
    assert broken.action == 'ordinary_ask' and not broken.targets
    assert '自身预检未过' in broken.reason
    assert broken.candidate == 'c', '仍记下最优候选供留痕，但不从它起步再买一轮'


def test_canonical_appellation_form_preserves_mentions_and_ignores_punctuation_and_surface_choice():
    from novel.stages.candidates import canonical_appellation_form

    people = {'fu_jun': ('夫君', '陆大哥', '陆兄'), 'yun_niang': ('苏芸娘', '芸娘', '苏姑娘')}
    same = canonical_appellation_form('苏芸娘嫁入陆家，夫君立下旧约。', people)
    for variant in ('芸娘嫁入陆家，陆大哥立下旧约。', '苏姑娘嫁入陆家；陆兄立下旧约',
                    '芸娘 嫁入 陆家 ， 夫君 立下 旧约'):
        assert canonical_appellation_form(variant, people) == same, variant
    # 规范形没有独立同位语证据：两次提及不得仅因归属同人就压成一个记号。
    people_with_kin = {'a': ('裴书远', '表弟')}
    assert canonical_appellation_form('表弟裴书远来了', people_with_kin) != \
        canonical_appellation_form('裴书远来了', people_with_kin)
    assert canonical_appellation_form('裴书远来了', people_with_kin) == \
        canonical_appellation_form('表弟来了', people_with_kin)
    # 语义差异不相等（多了一段事实）
    assert canonical_appellation_form('苏芸娘嫁入陆家且夫君许诺必归', people) != same
    # 多义字面（同一称谓归属两人）显式拒绝——规范形不猜归属
    with pytest.raises(ValueError, match='唯一归属'):
        canonical_appellation_form('夫君来了', {'fu_jun': ('夫君',), 'other': ('夫君',)})


@pytest.mark.parametrize('before,after', [
    ('陆大哥陆大哥，快来！', '陆大哥，快来！'),
    ('陆大哥陆兄，快来！', '陆兄，快来！'),
    ('夫君，快来！', '夫君夫君，快来！'),
])
def test_repeated_mentions_are_not_proven_equivalent_for_free_reuse(before, after):
    """提及次数变化不能借称谓规范形绕过承载拍复核，已有候选仍走窄臂。"""
    from novel.stages.candidates import (
        CandidateAssessment, canonical_appellation_form, entry_equivalent,
        plan_reuse, upstream_diff,
    )

    people = {'fu_jun': ('夫君', '陆大哥', '陆兄')}
    assert canonical_appellation_form(before, people) != canonical_appellation_form(after, people)
    base = _ob('event_core', 'a1', before)
    diff = upstream_diff({'obligations': [base]},
                         {'obligations': [{**base, 'statement': after}]})
    assert diff.changed and not any(entry_equivalent(e, people) for e in diff.changed)
    target = ('scenes', 0, 'beats', 0)
    plan = plan_reuse([CandidateAssessment(
        entry='purchased', ordinal=1, compatible=True, has_lineage=True,
        diff=diff, changed_equivalent=False, preflight_passed=True,
        carriers={diff.changed[0].key: (target,)})])
    assert plan.action == 'narrow_review' and plan.candidate == 'purchased'
    assert [item.path for item in plan.targets] == [target]


@pytest.mark.parametrize('before_name,after_name,action', [
    ('夫君夫君', '夫君', 'narrow_review'),
    ('夫君', '夫君夫君', 'narrow_review'),
    ('夫君陆大哥', '夫君', 'narrow_review'),
    ('夫君夫君', '陆大哥陆大哥', 'carried_forward'),
])
@pytest.mark.stage_scope('stage4')
def test_mention_count_changes_reach_real_design_reuse_consumer(
        tmp_path, before_name, after_name, action):
    """真实 Design 重入：增删提及复核承载拍；保留次数的别称替换仍零调用沿用。"""
    from dataclasses import replace

    from novel.stages.chain import run_body_design
    from tests.str06_facilities import carry_forward_ask, json_ask, republish_checkpoint
    from tests.test_str06_candidate_preflight import _design_ctx, _prepare, _reopened_session

    ctx, _, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    original = repo.read_model('runtime/preserve_checkpoint.json').obligations[0].statement
    assert '夫君' in original

    def change_statement(name):
        return lambda cp: cp.model_copy(update={'obligations': tuple(
            o.model_copy(update={'statement': original.replace('夫君', name, 1)})
            if index == 0 else o for index, o in enumerate(cp.obligations))})

    republish_checkpoint(repo, change_statement(before_name))
    session = _reopened_session(repo, transport)
    ctx = _design_ctx(ctx, session)
    run_body_design(ctx)
    before_design = repo.read_model('stage4/design/body_design.json')
    calls = transport.calls.count('design')
    seen = {}

    def recording_ask(owner, prompt, validate, patch=None, **kwargs):
        seen['review_targets'] = kwargs.get('review_targets')
        return json_ask(session)(owner, prompt, validate, patch=patch)

    republish_checkpoint(repo, change_statement(after_name))
    ctx = replace(ctx, ask=recording_ask, carry_forward=carry_forward_ask(session))
    result = run_body_design(ctx)
    assert result['reuse']['action'] == action
    assert transport.calls.count('design') == calls + int(action == 'narrow_review')
    after_design = repo.read_model('stage4/design/body_design.json')
    if action == 'narrow_review':
        paths = {item.path for item in seen['review_targets']}
        assert ('scenes', 0, 'beats', 0) in paths
        assert ('scenes', 1, 'beats', 0) not in paths
        assert after_design.scenes[1] == before_design.scenes[1], '无关场字节不变'
    else:
        assert not seen and after_design == before_design


def test_plan_reuse_prefers_green_then_fewest_root_causes_then_most_recent():
    from novel.stages.candidates import (
        CandidateAssessment, UpstreamDiff, plan_reuse,
    )

    def diff_of(*statuses):
        return UpstreamDiff(tuple(), (), 'd' * 64)

    green_early = CandidateAssessment(entry='early', ordinal=1, compatible=True,
                                      preflight_passed=True, root_causes=0,
                                      diff=diff_of(), changed_equivalent=False)
    green_late = CandidateAssessment(entry='late', ordinal=3, compatible=True,
                                     preflight_passed=True, root_causes=0,
                                     diff=diff_of(), changed_equivalent=False)
    failing = CandidateAssessment(entry='failing', ordinal=2, compatible=True,
                                  preflight_passed=False, root_causes=5, diff=diff_of())
    assert plan_reuse([failing, green_late, green_early]).candidate == 'late', '同分取最近'
    assert plan_reuse([green_early, green_late]).candidate == 'late'
    # 较早的全绿不被较晚的失败候选挡住
    assert plan_reuse([failing, green_early]).action == 'carry_forward'
    assert plan_reuse([failing, green_early]).candidate == 'early'
    # 没有可用候选：整份新购只限闭集原因并留痕
    none_plan = plan_reuse([])
    assert none_plan.action == 'full_purchase' and none_plan.reason == '没有候选'
    incompatible = CandidateAssessment(entry='x', ordinal=1, compatible=False,
                                       incompatibility_reason='协议字面量不兼容')
    assert plan_reuse([incompatible]).reason == '协议字面量不兼容'
    no_lineage = CandidateAssessment(entry='y', ordinal=2, compatible=True,
                                     has_lineage=False,
                                     incompatibility_reason='上游是全新回答无修订谱系')
    assert plan_reuse([no_lineage]).action == 'full_purchase'
    assert plan_reuse([no_lineage]).reason == '上游是全新回答无修订谱系'


def test_candidate_registry_is_a_rebuildable_index_of_the_unit_account(tmp_path):
    from novel.stages import candidates
    from novel.stages.chain import run_body_design
    from tests.test_str06_candidate_preflight import _design_ctx, _prepare, _reopened_session

    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    run_body_design(_design_ctx(ctx, _reopened_session(repo, transport)))
    registry = candidates.load_registry(repo, 'design')
    assert registry is not None and registry.node == 'str06.stage4.body_design'
    entry = registry.candidates[0]
    # 登记可由单元账重建：轮次、请求身份、wire 摘要与账面一致
    unit, rounds = candidates.purchased_rounds(repo.root.parent, repo.root.name, 'design')
    assert unit is not None and unit.unit_id == registry.unit_id
    assert [r.turn_id for r in rounds] == [entry['turn_id']]
    assert rounds[0].request_sha256 == entry['request_sha256']
    assert rounds[0].wire_sha256 == entry['wire_sha256']
    # 登记丢失即从账重建（索引不是第二本账）：沿用判定不依赖它存在
    repo.path(candidates.candidates_rel('design')).unlink()
    assert candidates.load_registry(repo, 'design') is None
    rebuilt_unit, rebuilt = candidates.purchased_rounds(repo.root.parent, repo.root.name, 'design')
    assert [r.turn_id for r in rebuilt] == [entry['turn_id']]
    # 登记形状损坏显式拒绝（不静默当作没有候选）
    repo.write(candidates.candidates_rel('design'), {'owner': 'other'})
    with pytest.raises(ValueError, match='owner/node'):
        candidates.load_registry(repo, 'design')


def test_new_stages_modules_stay_out_of_the_view_and_service_import_face():
    """candidates.py 可依赖 llm/账（同 revisions.py），但视图与服务层不得经它够到 LLM。"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]

    def imports_of(rel):
        tree = ast.parse((root / rel).read_text(encoding='utf-8'))
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                out.add(node.module)
            elif isinstance(node, ast.Import):
                out.update(a.name for a in node.names)
        return out

    modules = imports_of('novel/stages/candidates.py')
    assert 'novel.stages.revisions' in modules
    for rel in ('novel/views/str06.py', 'novel/api/routes/str06.py'):
        leaked = sorted(m for m in imports_of(rel) if m.startswith('novel.llm'))
        assert not leaked, f'{rel} 不得直接 import LLM 层：{leaked}'
