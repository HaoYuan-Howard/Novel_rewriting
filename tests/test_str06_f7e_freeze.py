#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_f7e_freeze —— 候选契约冻结基线钉在测试里（免费）。

【冻结什么】激活的节点表（`contracts/invariants.py::NODES`）、产物表
（`contracts/artifacts.py::ARTIFACT_PATHS`＋`STR06_ARTIFACT_RC_VERSIONS`）与
运行期配置。本文件把冻结时的契约身份、节点／产物计数写成字面快照：表里任何
一行变化都会改变 `candidate_contract_identity()`，本测试当场红——不能
「顺手多激活一个节点」而不改冻结记录。

【B2 重新冻结（2026-09-27）】职责解耦 D9 把 Stage2 拆为五节点
（book／chapters／names／associate／compile），`str06.stage2.source` 退役，
公开节点 11→14；观察与关联协议落地（`contracts/schema/observation.py`，
stage2-observation-v1），联合抽取派生输入（book_extraction／input_snapshots）
退役。F7e 基线（11 节点，`7fb5ecfa…`）原样保存在 Git 历史里；旧候选根按
「契约表已漂移」拒读，只读保留，新协议下新根全量重购（I-13，无转换层）。

【B3 前半重新冻结（2026-09-27）】Stage4 四协议独立版化
（stage4-{preserve,design,bind,hook-design}-v1），Design wire 瘦身——场景/拍
ID、复合引用、权重归一与整数预算改由 compiler 生成（I-21）；TargetDesign 增
结构化跨章字段；读者确认必填事实地址（D4）。节点与运行期配置不变；Stage4
产物（body_design／presentation_graph／writer_book_ir）版本升 `str06-rc7`。
B2 基线（14 节点，`a0f5fb5e…`）原样保存在 Git 历史里；既有候选根按
「契约表已漂移」拒读，只读保留。

【B4 前半重新冻结（2026-09-27）】章包写手（D2）：WriterBookIR 增章级验证材料
（称谓许可／禁用字面 compile 时算好）并升 `str06-rc8`；章回执改绑章包摘要
（回执升 `str06-rc5`），不再绑定全书 IR 与前文链；正文节点并发与集合恢复
（各章独立有效）。节点集合与运行期配置不变。B3 基线（14 节点，
`29cc19d0…`）原样保存在 Git 历史里；既有候选根按「契约表已漂移」拒读，
只读保留，新协议下新根全量重购（I-13，无转换层）。

【R1 重新冻结（2026-09-28）】Stage2 关联无损：associate 观察协议独立升版
`stage2-associate-v2`（`contracts/schema/observation.py`；typed 作用对象显式
序列化＋状态句柄位置编码），观察产物 `stage2/observations/associate.json`
形状升 `str06-rc9`。三类观察协议（book／chapters／names）字面量与请求逐字
不变；canonical（Characters／Blueprint）无形状变化，不升版（A1.5）。节点
集合与运行期配置不变。B4 基线（14 节点，`d552b280…`）原样保存在 Git 历史
里；既有候选根按「契约表已漂移」拒读，只读保留。

【R2 重新冻结（2026-09-28）】语义送达与两层 IR：Design wire 升
`stage4-design-v2`（拍上线程 threads、书级 world_rules、章级 entry；四个静态
跨章列表删除，AUD-04），Stage4 设计产物（body_design／presentation_graph）
形状升 `str06-rc10`；WriterBookIR 升 `str06-rc10`（拍级读取值挂拍、线程投影
面、continuity 分区、world_rules 进书级层）；章回执身份拆分（回执升
`str06-rc6`）。节点集合与运行期配置不变。R1 基线（14 节点，`0d546fda…`）
原样保存在 Git 历史里；既有候选根按「契约表已漂移」拒读，只读保留。

【为什么不只靠激活表自己的对账】`test_str06_candidates.py` 查的是表内自洽
（runner／writer／版本逐字相等）；本文件查的是**表与冻结基线**相等：
前者防表内部撒谎，后者防表在批次之间无登记漂移。
"""
from __future__ import annotations

from contracts.artifacts import ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS
from contracts.invariants import NODES
from contracts.schema.workflow import (
    STR06_RC_VERSION, STR06_RUNTIME_CONFIG,
)
from novel.llm.paid_requests import candidate_contract_identity

#: R2 重新冻结（2026-09-28 语义送达）时的候选契约身份：节点表＋产物表＋RC
#: 版式＋运行期配置的 SHA-256。表内任何一行变化都会改变它——先改本记录并
#: 登记原因（REQUIREMENTS／DECISIONS），再动表。
FROZEN_CONTRACT_IDENTITY = 'b8501a9d7cffdd18b7db03ac46cef006fff1e4cabd38756a0bb24e637f06039b'

#: 冻结时的节点集合（14 个：stage0/1 两座＋Stage2 五节点＋Stage4 五＋Stage5 两
#: ＋Stage7 两）。
FROZEN_NODES = (
    'str06.stage2.associate', 'str06.stage2.book', 'str06.stage2.chapters',
    'str06.stage2.compile', 'str06.stage2.names',
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
        '候选契约身份与 R1 冻结基线不符：先更新本文件的冻结记录，'
        '并在 REQUIREMENTS 登记变化原因（不静默改冻结面）')


def test_frozen_node_set_matches_the_live_table():
    nodes = _candidate_nodes()
    assert tuple(sorted(nodes)) == FROZEN_NODES
    assert len(nodes) == 14
    paid = sorted(n for n, spec in nodes.items() if spec.paid)
    free = sorted(n for n, spec in nodes.items() if not spec.paid)
    assert len(paid) == 11
    assert free == ['str06.stage2.compile', 'str06.stage4.compile',
                    'str06.stage7.assemble'], (
        '付费／免费节点面变化：compile 与 assemble 必须保持 executor_content_limit=0 '
        '结构性禁付')


def test_frozen_runtime_config_matches():
    assert dict(STR06_RUNTIME_CONFIG._asdict()) == FROZEN_RUNTIME_CONFIG


def test_frozen_artifact_table_shape():
    """24 份书产物；每条的 writer 是表内节点、schema_version 非空。"""
    assert len(STR06_ARTIFACT_RC_VERSIONS) == 24
    for rel, version in STR06_ARTIFACT_RC_VERSIONS.items():
        assert rel in ARTIFACT_PATHS, f'{rel}: 未激活进 ARTIFACT_PATHS'
        writer = next((n for n, spec in _candidate_nodes().items()
                       if rel in spec.produces), None)
        assert writer is not None, f'{rel}: writer 不是候选节点'
        assert version.startswith('str06-rc'), f'{rel}: 版本标签形状'
    # 模式产物（{nn}／{title}）恰好四份：冻结面不含第二处逐章／逐书名路径。
    patterned = sorted(rel for rel in STR06_ARTIFACT_RC_VERSIONS
                       if '{nn}' in rel or '{title}' in rel)
    assert patterned == ['stage2/observations/ch{nn}.json',
                         'stage5/acceptance/body/ch{nn}.json',
                         'stage5/body/ch{nn}.txt',
                         'stage7/final/{title}_rewriting.txt'], patterned


def test_frozen_rc_version_label():
    assert STR06_RC_VERSION == 'str06-rc10'
