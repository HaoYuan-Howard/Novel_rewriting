#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""families —— STR-06 三级路径家族登记（F0）。

【为什么需要一张登记表而不是放开深度】
现役 `novel/paths.py::check_rel` 强制两级，不是洁癖：来源账的键是两级的，三级路径
记进 `inputs` 会塌成一个反查不到文件的键，那条边从此永远显示对得上（见
`check_rel` docstring）。STR-06 的正式产物全部落在三级子目录（如
`stage4/grounding/semantic_binding.json`），所以多级支持必须**按登记家族**给出——
家族外的多级路径仍然拒绝，两级一般规则一寸不放。

【来源账键规则（随家族一起登记，不许实现层各自发挥）】
登记家族内的路径作为来源账边的键时，用**完整相对路径**（三级原样，不塌两级）。
理由：塌掉一级等于把「同一前缀下不同子目录的产物」折叠成同一条边，反查必错；
完整路径没有这个问题，作用域仍由 run 级实例边界给出（同现役 `input_key` 的论证）。

本模块只登记数据；准入与键的实现在 `novel/paths.py`（family 前缀匹配、INVALID
孪生名与 `_rounds/` 归档沿用现役机制）。
"""
from __future__ import annotations

from typing import Final, NamedTuple


class PathFamily(NamedTuple):
    """一个三级产物目录家族的身份。

    prefix:            目录前缀（以 `/` 结尾）；家族内文件名模式由各 owner 契约束
                       （如 `scene_{nnn}.json`），路径层不管内容命名。
    kind:              canonical / semantic / derived / report / prose / runtime /
                       audit。正文、付费 checkpoint 与付费观察决定不是可随意
                       删除的缓存——kind 即删除政策的输入（F8 收口前不得实现
                       任何按 kind 的清理）。`semantic`（B2）＝付费 semantic unit
                       的观察/关联决定（已购恢复证据，非派生缓存）。
    writer_owner:      唯一 writer（I-25）；登记为语义 owner 名，F2–F5 落实到模块。
    """

    prefix: str
    kind: str
    writer_owner: str


#: 家族键 → 身份。键用 `str06.<owner>.<family>` 命名，防与现役 `ARTIFACT_PATHS`
#: 的 `stageN.<name>` 键撞语义空间。
STR06_PATH_FAMILIES: Final[dict[str, PathFamily]] = {
    # B2 五节点观察家族（2026-09-27，D3/D8/D9）：观察与关联决定是付费的
    # 内部材料（已购恢复证据），不是可重建派生物也不是普通缓存——kind 取
    # 'semantic'（删除政策输入：与 canonical 同级保留，不随派生物清理）。
    'str06.stage2.observations': PathFamily(
        prefix='stage2/observations/', kind='semantic', writer_owner='stage2.semantic'),
    # Characters／Blueprint 双真源的唯一 writer 是 str06.stage2.compile（D9）。
    'str06.stage2.characters': PathFamily(
        prefix='stage2/characters/', kind='canonical', writer_owner='stage2.compile'),
    'str06.stage2.blueprints': PathFamily(
        prefix='stage2/blueprints/', kind='canonical', writer_owner='stage2.compile'),
    'str06.stage2.acceptance': PathFamily(
        prefix='stage2/acceptance/', kind='audit', writer_owner='stage2.acceptance'),
    'str06.stage4.design': PathFamily(
        prefix='stage4/design/', kind='canonical', writer_owner='stage4.design'),
    # grounding 目录混居两职：reconstruction_brief.json 是 Derived，
    # semantic_binding.json 是完整 Canonical snapshot。按文件区分，登记在同一
    # 家族；「checkpoint 存储不是下游读取接口」同理——它也不在本表内。
    'str06.stage4.grounding': PathFamily(
        prefix='stage4/grounding/', kind='derived+canonical', writer_owner='stage4.grounding'),
    'str06.stage4.compiled': PathFamily(
        prefix='stage4/compiled/', kind='derived', writer_owner='stage4.compile'),
    # 全书静态写作 IR（F5）：嵌入全部场景双视图的唯一 Derived artifact。
    'str06.stage4.ir': PathFamily(
        prefix='stage4/ir/', kind='derived', writer_owner='stage4.context'),
    'str06.stage4.context': PathFamily(
        prefix='stage4/context/', kind='derived', writer_owner='stage4.context'),
    'str06.stage4.acceptance.guard': PathFamily(
        prefix='stage4/acceptance/guard/', kind='report', writer_owner='stage4.guard'),
    # R2.8：compile 的 WriterPacket 指标（audit 报面；先采数、不设阈值）。
    # ⚠ 前缀宽于 guard 家族，必须排在其后——家族匹配按插入序取首个命中。
    'str06.stage4.acceptance.metrics': PathFamily(
        prefix='stage4/acceptance/', kind='report', writer_owner='stage4.compile'),
    # 正文按章成文（F5）：ch{nn}.txt 是 prose，章级 attempts 记录是 audit；
    # 按文件区分，登记在同一家族（同 stage5.hook 的先例）。
    'str06.stage5.body': PathFamily(
        prefix='stage5/body/', kind='prose+audit', writer_owner='stage5.body_writer'),
    'str06.stage5.acceptance.body': PathFamily(
        prefix='stage5/acceptance/body/', kind='report', writer_owner='stage5.body_writer'),
    # Hook 目录混居两职：hook.txt 是 prose，receipt.json 是 audit；按文件区分，
    # 登记在同一家族（同 stage4.grounding 的先例）。
    'str06.stage5.hook': PathFamily(
        prefix='stage5/hook/', kind='prose+audit', writer_owner='stage5.hook_writer'),
    # 目标命名前置（2026-10-18，rc26）：name_map 从 Stage7 末端前移到 Stage4，
    # 成为 Preserve 与设计链的正式输入；家族随节点迁移。
    'str06.stage4.naming': PathFamily(
        prefix='stage4/naming/', kind='canonical', writer_owner='stage4.naming'),
    # 交付目录混居两职：final 正文是 prose，组装报告与 diff proof 是 audit。
    'str06.stage7.final': PathFamily(
        prefix='stage7/final/', kind='prose+audit', writer_owner='stage7.assembly'),
    'str06.runtime': PathFamily(
        prefix='runtime/', kind='runtime', writer_owner='runtime'),
}


def family_for(rel: str) -> PathFamily | None:
    """返回路径所属的登记家族；未登记返回 None（调用方据此拒绝，而不是猜）。

    只做前缀匹配；INVALID 孪生名与 `_rounds/` 归档形态由 `novel/paths.py`
    在调用本函数之前归一。
    """
    norm = str(rel).replace('\\', '/')
    for family in STR06_PATH_FAMILIES.values():
        if norm.startswith(family.prefix):
            return family
    return None
