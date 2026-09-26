#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_f7e_freeze —— F7e 候选冻结：F8 激活基线钉在测试里（免费）。

【冻结什么】F8a 激活候选节点、产物与判据时翻的正是迁移包
`contracts/schema/str06/candidates.py` 那两张表。本文件把 F7e 冻结时的契约身份、
节点／产物计数与运行期配置写成字面快照：表里任何一行变化都会改变
`candidate_contract_identity()`，本测试当场红——F8 不能「顺手多激活一个节点」
而不改冻结记录。

【F8c 分流后冻结面落在哪】迁移包已删除，节点表在
`contracts/invariants.py::NODES`、产物表在
`contracts/artifacts.py::ARTIFACT_PATHS`＋`STR06_ARTIFACT_RC_VERSIONS`、
运行期配置与身份记录模型在 `contracts/schema/workflow.py`。**冻结值一字未改**：
`candidate_contract_identity()` 仍逐字复算 F7e 基线的那个 SHA-256（盘上
`runtime/candidate_workflow.json` 记的就是它，改了全部既有候选根都会被判
「契约表已漂移」而拒绝消费已购证据）——本文件继续钉住这一点。

【为什么不只靠激活表自己的对账】`test_str06_candidates.py` 查的是表内自洽
（runner／writer／版本逐字相等）；本文件查的是**表与 F7e 冻结基线**相等：
前者防表内部撒谎，后者防表在 F7e→F8 之间漂移。
"""
from __future__ import annotations

from contracts.artifacts import ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS
from contracts.invariants import NODES
from contracts.schema.workflow import (
    STR06_RC_VERSION, STR06_RUNTIME_CONFIG,
)
from novel.llm.paid_requests import candidate_contract_identity

#: F7e 冻结（2026-09-25）时的候选契约身份：节点表＋产物表＋RC 版式＋运行期
#: 配置的 SHA-256。F8a 激活前若它与现状不等，先改冻结记录再激活。
#: v13（2026-09-26 质量改进批次二）：Stage2 拍模型改三类转移＋场景分段，blueprints
#: 与 fold／snapshots 三条产物换 rc5 标签、族版本 rc4→rc5 ⇒ 身份随形状变化同批
#: 更新（登记见 docs/REQUIREMENTS.md 质量改进批次二；旧形状盘面明确拒读）。
FROZEN_CONTRACT_IDENTITY = '55132ab2c0d4c391a0a529b385d414a31719773721b79a484444d46e690f4542'

#: 冻结时的节点集合（11 个；F8a 激活后翻为正式 NODES）。
FROZEN_NODES = (
    'str06.stage2.names', 'str06.stage2.source',
    'str06.stage4.bind', 'str06.stage4.body_design', 'str06.stage4.compile',
    'str06.stage4.hook_design', 'str06.stage4.preserve',
    'str06.stage5.body', 'str06.stage5.hook',
    'str06.stage7.assemble', 'str06.stage7.naming',
)

#: 冻结时的运行期配置（进契约身份；与付费调用额度是两件事）。
FROZEN_RUNTIME_CONFIG = {
    'hook_target_chars': 280, 'hook_hard_max_chars': 360,
    'length_band_low': 0.6, 'length_band_high': 1.3,
    'length_budget_rounding': 1000,
}


def _candidate_nodes() -> dict:
    return {node: spec for node, spec in NODES.items() if node.startswith('str06.')}


def test_frozen_contract_identity_matches_the_live_tables():
    assert candidate_contract_identity() == FROZEN_CONTRACT_IDENTITY, (
        '候选契约身份与 F7e 冻结基线不符：F8a 激活前先更新本文件的冻结记录，'
        '并在 REQUIREMENTS 登记变化原因（不静默改冻结面）')


def test_frozen_node_set_matches_the_live_table():
    nodes = _candidate_nodes()
    assert tuple(sorted(nodes)) == FROZEN_NODES
    assert len(nodes) == 11
    paid = sorted(n for n, spec in nodes.items() if spec.paid)
    free = sorted(n for n, spec in nodes.items() if not spec.paid)
    assert len(paid) == 9 and free == ['str06.stage4.compile', 'str06.stage7.assemble'], (
        '付费／免费节点面变化：compile 与 assemble 必须保持 executor_content_limit=0 '
        '结构性禁付')


def test_frozen_runtime_config_matches():
    assert dict(STR06_RUNTIME_CONFIG._asdict()) == FROZEN_RUNTIME_CONFIG


def test_frozen_artifact_table_shape():
    """22 份书产物；每条的 writer 是表内节点、schema_version 非空。"""
    assert len(STR06_ARTIFACT_RC_VERSIONS) == 22
    for rel, version in STR06_ARTIFACT_RC_VERSIONS.items():
        assert rel in ARTIFACT_PATHS, f'{rel}: 未激活进 ARTIFACT_PATHS'
        writer = next((n for n, spec in _candidate_nodes().items()
                       if rel in spec.produces), None)
        assert writer is not None, f'{rel}: writer 不是候选节点'
        assert version.startswith('str06-rc'), f'{rel}: 版本标签形状'
    # 模式产物（{nn}／{title}）恰好三份：冻结面不含第二处逐章／逐书名路径。
    patterned = sorted(rel for rel in STR06_ARTIFACT_RC_VERSIONS
                       if '{nn}' in rel or '{title}' in rel)
    assert patterned == ['stage5/acceptance/body/ch{nn}.json', 'stage5/body/ch{nn}.txt',
                         'stage7/final/{title}_rewriting.txt'], patterned


def test_frozen_rc_version_label():
    assert STR06_RC_VERSION == 'str06-rc5'
