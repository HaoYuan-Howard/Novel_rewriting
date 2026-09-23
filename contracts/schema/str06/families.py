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
    kind:              canonical / derived / report / prose / runtime / audit。
                       正文与付费 checkpoint 不是可随意删除的缓存——kind 即删除
                       政策的输入（F8 收口前不得实现任何按 kind 的清理）。
    writer_owner:      唯一 writer（I-25）；登记为语义 owner 名，F2–F5 落实到模块。
    """

    prefix: str
    kind: str
    writer_owner: str


#: 家族键 → 身份。键用 `str06.<owner>.<family>` 命名，防与现役 `ARTIFACT_PATHS`
#: 的 `stageN.<name>` 键撞语义空间。
STR06_PATH_FAMILIES: Final[dict[str, PathFamily]] = {
    'str06.stage2.characters': PathFamily(
        prefix='stage2/characters/', kind='canonical', writer_owner='stage2.characters'),
    'str06.stage2.blueprints': PathFamily(
        prefix='stage2/blueprints/', kind='canonical', writer_owner='stage2.blueprint'),
    'str06.stage2.compiled': PathFamily(
        prefix='stage2/compiled/', kind='derived', writer_owner='stage2.compiler'),
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
    'str06.stage4.context': PathFamily(
        prefix='stage4/context/', kind='derived', writer_owner='stage4.context'),
    'str06.stage4.acceptance.guard': PathFamily(
        prefix='stage4/acceptance/guard/', kind='report', writer_owner='stage4.guard'),
    'str06.stage5.scenes': PathFamily(
        prefix='stage5/scenes/', kind='prose', writer_owner='stage5.writer'),
    'str06.stage5.acceptance.fidelity': PathFamily(
        prefix='stage5/acceptance/fidelity/', kind='report', writer_owner='stage5.judge'),
    'str06.runtime': PathFamily(
        prefix='runtime/', kind='runtime', writer_owner='runtime'),
}

#: 两级特例：Stage5 最终正文。它是 Assemble 的唯一产物，但目录深度天然两级，
#: 继续走现役两级准入即可——登记在这里只是让「新链正式产物」有一份完整清单，
#: 不代表它需要多级支持。
STR06_TWO_LEVEL_PATHS: Final[frozenset[str]] = frozenset({
    'stage5/rewritten_body.txt',
})


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
