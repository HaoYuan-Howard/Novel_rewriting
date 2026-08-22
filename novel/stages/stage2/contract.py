#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contract.py —— `stage2.blueprint` 的响应契约注册表。

【为什么是一张表而不是 if】v3 与 ir1 要在 A/B 期间**并存**：整跑按配置选，
定向重生成按**基线产物的 architecture** 选（修复必须与基线同构，配置说了不算）。
把「system / 响应模型 / 编译函数 / 投影函数」捆成一条 spec，是为了让
「换契约」在结构上是**换整套**，而不是四处各换一半——各换一半的失败形态是
「prompt 是新的、编译器还是旧的」，而那不会报错，只会安静产出错位的产物。

【`schema_mode` 是诚实标签，不是开关】DeepSeek V4 官方 API 只支持
`response_format=json_object`（`json_schema` 被拒，2026-08-20 文档探针）⇒ 两套契约
都只能是 `prompt_plus_local_validation`：**这不是 constrained decoding**，
是 prompt 约束 + 本地 pydantic 校验。
⚠ 2026-08-22 起连 `json_object` 都不发了（`novel/llm/client.py::JSON_OBJECT_MODE`）——
这**不改变本标签**：它本来说的就是「靠 prompt + 本地校验」，而 `json_object` 从来
只保证「是合法 JSON」、一格 schema 都不保证（TODO-40 实测两臂都出越界枚举）。把它写成字段是为了让实验结论没法把
「枚举前缀带来的改善」误归因给 decoder 约束。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel

from contracts.invariants import BLUEPRINT_ARCHITECTURES
from contracts.schema.stage2 import (
    ChapterBlueprintsResponse, ChapterBlueprintsResponseV2,
)
from novel.llm.prompts import with_json_shape
from novel.stages.stage2 import prompts
from novel.stages.stage2.compile_v2 import canonical_ir_projection, compile_v2
from novel.stages.stage2.normalize import normalize_blueprints, response_shape


def _compile_v3(data: dict[str, Any], main_text: list[dict[str, Any]],
                characters: dict[str, dict[str, Any]], *,
                summaries: dict[int, str] | None = None) -> tuple[dict, dict]:
    """v3 归一的同签名包装：v3 的 summary 由模型自带，`summaries` 无意义、必须为空。"""
    assert not summaries, 'bounded-fidelity-v3 的 summary 在响应里，不许再从摘要批注入'
    return normalize_blueprints(data, main_text, characters)


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
    #: ir1 专属：summary 不在响应里，编译后要一次书级摘要批。
    needs_summary_batch: bool

    #: 这套契约的失败面能不能走 `stage2.blueprint --patch`（字段级修复，量级是条不是章）。
    #:
    #: 【为什么是一格能力，不是 `--patch` 里的一个名字】`repair.py` 原来写的是
    #: `architecture != 'extraction-ir-v1'` 与 `contract_for('extraction-ir-v1')`
    #: 两处**字面**比较。ir1.1 上线后当场显影：它与 ir1 只差 `system`/`sys_name`
    #: 两格，`response_model`/`compile_fn`/`projection_fn` 等逐格共用 ⇒ 字段级修复
    #: 结构上完全适用（`plan_repairs` 读的 `verbatim_failures` 与
    #: `hinge_ambiguous_pending` 两笔账都由共用的 `compile_v2` 产），却被名字挡在门外。
    #: ⇒ 判「能不能字段级修」按**这一格**，新架构进表时自己回答一次，
    #: 不必回头改 `repair.py` 里的名单——漏改那份名单不会有任何东西出声。
    #:
    #: v3 判 `False` 不是「还没做」：它的 hinge 带段号、失败面是整章级根因
    #: （`--patch` 的候选枚举依赖 IR 的拍区间），走 `--chapters` 整章重生成。
    supports_field_patch: bool


CONTRACTS: dict[str, ContractSpec] = {
    'bounded-fidelity-v3': ContractSpec(
        architecture='bounded-fidelity-v3',
        system=with_json_shape(prompts.BLUEPRINT_SYS, prompts.BLUEPRINT_HINT),
        sys_name='BLUEPRINT_SYS',
        response_model=ChapterBlueprintsResponse,
        schema_mode='prompt_plus_local_validation',
        compile_fn=_compile_v3,
        projection_fn=response_shape,
        needs_summary_batch=False,
        supports_field_patch=False,
    ),
    'extraction-ir-v1': ContractSpec(
        architecture='extraction-ir-v1',
        system=with_json_shape(prompts.BLUEPRINT_SYS_V2, prompts.BLUEPRINT_HINT_V2),
        sys_name='BLUEPRINT_SYS_V2',
        response_model=ChapterBlueprintsResponseV2,
        schema_mode='prompt_plus_local_validation',
        compile_fn=compile_v2,
        projection_fn=canonical_ir_projection,
        needs_summary_batch=True,
        supports_field_patch=True,
    ),
    # ⚠ 与上面那条**只差 `system` 一处**：响应模型、编译器、投影函数、摘要批开关
    #   与字段级修复能力全部原样共用（place 的**形状**没动，
    #   动的只是 system 里那一段措辞的次序）。
    #   这不是省事——它是**单变量对照能成立的前提**：两臂的差别若不止一处，
    #   跑出来的读数就归不了因。同理，结构提示复用 `BLUEPRINT_HINT_V2`，
    #   另起一份只会让两处漂移。
    #   改动的理由与风险写在 `prompts.py::_BLUEPRINT_TPL_V1_1` 上方。
    'extraction-ir-v1.1': ContractSpec(
        architecture='extraction-ir-v1.1',
        system=with_json_shape(prompts.BLUEPRINT_SYS_V1_1, prompts.BLUEPRINT_HINT_V2),
        sys_name='BLUEPRINT_SYS_V1_1',
        response_model=ChapterBlueprintsResponseV2,
        schema_mode='prompt_plus_local_validation',
        compile_fn=compile_v2,
        projection_fn=canonical_ir_projection,
        needs_summary_batch=True,
        supports_field_patch=True,
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
