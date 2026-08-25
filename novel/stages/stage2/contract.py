#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contract.py —— `stage2.blueprint` 的响应契约注册表。

【为什么是一张表而不是 if】多套响应契约要在 A/B 与存量修复期间**并存**：整跑按配置选，
定向重生成按**基线产物的 architecture** 选（修复必须与基线同构，配置说了不算）。
把「system / 响应模型 / 编译函数 / 投影函数」捆成一条 spec，是为了让
「换契约」在结构上是**换整套**，而不是四处各换一半——各换一半的失败形态是
「prompt 是新的、编译器还是旧的」，而那不会报错，只会安静产出错位的产物。

【`schema_mode` 是诚实标签，不是开关】DeepSeek V4 官方 API 只支持
`response_format=json_object`（`json_schema` 被拒，2026-08-20 文档探针）⇒ 全部契约
都只能是 `prompt_plus_local_validation`：**这不是 constrained decoding**，
是 prompt 约束 + 本地 pydantic 校验。
⚠ 2026-08-22 曾把 `json_object` 关掉又当天改回（`novel/llm/client.py::JSON_OBJECT_MODE`）——
两种口径都**不改变本标签**：它说的本来就是「靠 prompt + 本地校验」，
而 `json_object` 从来只保证「是合法 JSON」、一格 schema 都不保证
（2026-08-22 json-mode 对照实测两臂都出越界枚举）。把它写成字段是为了让实验结论没法把
「枚举前缀带来的改善」误归因给 decoder 约束。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel

from contracts.invariants import BLUEPRINT_ARCHITECTURES
from contracts.schema.stage2 import (
    ChapterBoundariesResponse, ChapterBlueprintsResponseV2Refs,
    ChapterBlueprintsResponseV31,
)
from novel.llm.prompts import with_json_shape
from novel.stages.stage2 import prompts
from novel.stages.stage2.compile_v2 import (
    canonical_ir_projection_refs, canonical_ir_projection_v31,
    compile_v2_refs, compile_v31_refs,
)


@dataclass(frozen=True)
class ContractSpec:
    """一套响应契约的全部构件。`system` 是**已拼好结构提示**的完整字节
    （指纹、缓存、真发共用这一份，`with_json_shape` 只在这里调一次）。"""

    architecture: str
    system: str
    sys_name: str                       # `_provenance.prompts` 里这条臂的键名
    response_model: type[BaseModel]
    schema_mode: Literal['provider_json_schema', 'tool_call',
                         'prompt_plus_local_validation']
    compile_fn: Callable[..., tuple[dict[str, Any], dict[str, Any]]]
    projection_fn: Callable[[dict[str, Any], str], dict[str, Any]]
    #: 这套契约的失败面能不能走 `stage2.blueprint --patch`（字段级修复，量级是条不是章）。
    #:
    #: 【为什么是一格能力，不是 `--patch` 里的一个名字】历史上按
    #: architecture 字面分流曾把新增的同构契约挡在修复门外。因此判「能不能
    #: 字段级修」只按**这一格**，新架构进表时自己回答一次，
    #: 不必回头改 `repair.py` 里的名单——漏改那份名单不会有任何东西出声。
    #:
    #: v3 判 `False` 不是「还没做」：它的 hinge 带段号、失败面是整章级根因
    #: （`--patch` 的候选枚举依赖 IR 的拍区间），走 `--chapters` 整章重生成。
    supports_field_patch: bool

    #: 显式调用拓扑。不能从 `boundary_system is None` 反推：那分不清普通一相与
    #: “同一次响应内带召回脚手架”的一相，会让预览、生成和修复各自猜一遍。
    topology: Literal['single', 'boundary_then_detail', 'scaffolded_single']

    #: 两段式契约的第一相；仅 `boundary_then_detail` 拓扑使用。
    boundary_system: str | None = None
    boundary_sys_name: str | None = None
    boundary_response_model: type[BaseModel] | None = None
    #: v3.1 的进一步收窄面；调用方按能力分派，不按架构名字猜。
    uses_hinge_candidates: bool = False

    #: v3.1 的同请求 A/B 观察到救回稿会把一章过切为 15 拍且地点全 unknown，
    #: 正常正文重采则为 10 拍且地点全明确；但两本空缓存整跑又观察到全拒救回稿
    #: 会把 41 章放大为 69 次真实调用。折中不是加 prompt，也不是无条件采纳：
    #: 首份合法救回稿先留作同档后备，再争取一次正常 `content`；第二份仍不可用时
    #: 才采纳后备，绝不再切 Pro 白烧。多数结构化契约保持“合法即采纳”。
    prefers_content_over_reasoning_salvage: bool = False


_V2 = ContractSpec(
    architecture='extraction-ir-v2',
    system=with_json_shape(
        prompts.BLUEPRINT_SYS_V2_REFS, prompts.BLUEPRINT_HINT_V2_REFS),
    sys_name='BLUEPRINT_SYS_V2_REFS',
    response_model=ChapterBlueprintsResponseV2Refs,
    schema_mode='prompt_plus_local_validation',
    compile_fn=compile_v2_refs,
    projection_fn=canonical_ir_projection_refs,
    supports_field_patch=True,
    topology='boundary_then_detail',
    boundary_system=with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2),
    boundary_sys_name='BOUNDARY_SYS_V2',
    boundary_response_model=ChapterBoundariesResponse,
)

CONTRACTS: dict[str, ContractSpec] = {
    # Flash 的主要失效不是「字段写错」，而是单次请求同时切拍和填十余个字段时
    # 系统性少切拍。v2 先只交止段，再让第二相在代码冻结的边界上填事实；同时把
    # 在场人物改成可见 char_id 的关闭式选择、地点恢复成字符串或 null。
    # 正式产物形状不变，变化只存在于模型响应边界和调用拓扑。
    'extraction-ir-v2': _V2,
    # v3.1 把叙述身份收成可见 char_id；hinge 优先选确定性原文候选、覆盖不到
    # 才回抄 phrase。它保留单次调用内的召回脚手架。
    'extraction-ir-v3.1': ContractSpec(
        architecture='extraction-ir-v3.1',
        system=with_json_shape(prompts.BLUEPRINT_SYS_V31,
                               prompts.BLUEPRINT_HINT_V31),
        sys_name='BLUEPRINT_SYS_V31',
        response_model=ChapterBlueprintsResponseV31,
        schema_mode='prompt_plus_local_validation',
        compile_fn=compile_v31_refs,
        projection_fn=canonical_ir_projection_v31,
        supports_field_patch=True,
        topology='scaffolded_single',
        uses_hinge_candidates=True,
        prefers_content_over_reasoning_salvage=True,
    ),
}

assert set(CONTRACTS) == set(BLUEPRINT_ARCHITECTURES), \
    '契约注册表与 `BLUEPRINT_ARCHITECTURES` 取值面分家了——两边必须一起改'
assert all(spec.architecture == key for key, spec in CONTRACTS.items())


def active_contract(env: Mapping[str, str] | None = None) -> ContractSpec:
    """按运行期配置选契约（整跑用）。`load_config` 已对取值面关闭式拒绝。"""
    from novel.config import load_config
    return CONTRACTS[load_config(env=env).stage2_blueprint_contract]


def contract_for(architecture: str) -> ContractSpec:
    """按**基线产物的 architecture** 选契约（定向重生成用）。

    ⚠ 修复必须与基线同构：配置是 ir1 而基线是 v3 时，正确动作是先整跑换架构，
    不是拿 ir1 的 prompt 去修 v3 的产物——那个不一致由调用方判成显式终态。
    """
    if architecture not in CONTRACTS:
        raise KeyError(
            f'未登记的 blueprint 架构 {architecture!r}；取值面 {BLUEPRINT_ARCHITECTURES}')
    return CONTRACTS[architecture]
