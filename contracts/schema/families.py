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
    'stage2.observations': PathFamily('stage2/observations/', 'semantic', 'stage2.semantic'),
    'stage2.story': PathFamily('stage2/story/', 'canonical', 'stage2.compile'),
    'stage4.naming': PathFamily('stage4/naming/', 'canonical', 'stage4.naming'),
    'stage4.story': PathFamily('stage4/story/', 'canonical', 'stage4.compile'),
    'stage4.drafts': PathFamily('stage4/drafts/', 'semantic', 'stage4.semantic'),
    'stage4.packets': PathFamily('stage4/packets/', 'derived', 'stage4.compile'),
    'stage5.body': PathFamily('stage5/body/', 'prose', 'stage5.body'),
    'stage5.receipts': PathFamily('stage5/acceptance/body/', 'audit', 'stage5.body'),
    'stage5.hook': PathFamily('stage5/hook/', 'prose+audit', 'stage5.hook'),
    'stage7.final': PathFamily('stage7/final/', 'prose+audit', 'stage7.assembly'),
    'runtime': PathFamily('runtime/', 'runtime', 'runtime'),
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
