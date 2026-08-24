#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 v2 的免费机械基线节点。

默认执行、测量预览和复检都只读取 Stage1 正文与原作文案，调用同一把
measure_all 尺子；本模块不导入 LLM 客户端，也不存在定性响应、修复、人工编辑、
INVALID 或提升入口。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_HOOK_COPY,
    STAGE1_MAIN_TEXT,
    STAGE3_STYLE_GROUNDING,
)
from contracts.schema import artifact_spec
from contracts.schema.report import CheckReport
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage3 import Measurable, StyleGrounding, StyleProvenance
from novel import paths
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.report import build
from novel.run import RunContext
from novel.stages.stage3 import checks
from novel.stages.stage3.measure import MEASUREMENT_CONTRACT, measure_all
from novel.store import ArtifactStore

REL = STAGE3_STYLE_GROUNDING
MAIN_TEXT = STAGE1_MAIN_TEXT
HOOK_COPY = STAGE1_HOOK_COPY


def _upstreams(store: ArtifactStore) -> tuple[MainText, HookCopy]:
    """读取机械测量实际消费的两份 Stage1 上游。"""
    return (
        store.read(MainText, MAIN_TEXT),
        store.read(HookCopy, HOOK_COPY),
    )


def _artifact(ctx: RunContext, main: MainText, hook: HookCopy) -> StyleGrounding:
    """以本次真实读取边铸造一份免费 v2 产物。"""
    return StyleGrounding(
        measurable=measure_all(main, hook),
        _provenance=ctx.free_provenance(
            StyleProvenance,
            measurement_contract=MEASUREMENT_CONTRACT,
        ),
    )


def judge_with(data: dict, main: MainText, hook: HookCopy):
    """不摸盘的唯一判定调度点，生产与测试共用。"""
    return checks.check(data, measured_now=measure_all(main, hook))


def _judge(store: ArtifactStore, data: dict):
    """摸盘版判定入口。"""
    main, hook = _upstreams(store)
    return judge_with(data, main, hook)


def run_style(ctx: RunContext) -> StyleGrounding:
    """默认 producer：免费测量并写入当前 v2 产物，零 LLM 调用。"""
    store = ArtifactStore(ctx)
    main, hook = _upstreams(store)
    artifact = _artifact(ctx, main, hook)
    outcomes = checks.check(
        artifact.model_dump(by_alias=True),
        measured_now=artifact.measurable,
    )
    hard = [
        outcome.rule
        for outcome in outcomes
        if outcome.findings
    ]
    if hard:
        raise RuntimeError(f'Stage3 自产机械基线未通过自身契约：{hard}')
    # 仅当 live 正式稿/INVALID 全部是旧版本时走 shape-break 替换；当前 v2 的普通重跑
    # 仍走正式 write 语义。这样旧 v1 不污染 _rounds，也不把当前轮次当成废字节删除。
    current = artifact_spec(REL)
    assert current is not None
    invalid_rel = paths.invalid_of(REL)
    versions = (store.schema_version_of(REL), store.schema_version_of(invalid_rel))
    present = tuple(version for version in versions if version is not None)
    if present and all(version < current.current_version for version in present):
        store.replace_shape_break(REL, artifact)
    else:
        if any(version != current.current_version for version in present):
            raise ArtifactUnreadable(
                f'{REL} 的 live 正式稿/INVALID 版本混杂或来自未来：{versions}；拒绝覆盖')
        if versions[1] is not None:
            raise ArtifactUnreadable(
                f'{invalid_rel} 是 current v{current.current_version}，但 Stage3 v2 不存在 '
                'INVALID 生产/恢复面；拒绝把它静默归档成 current round')
        store.write(REL, artifact)
    return artifact


def run_measure_only(ctx: RunContext) -> Measurable:
    """只返回当前机械测量，不写盘。"""
    store = ArtifactStore(ctx)
    main, hook = _upstreams(store)
    return measure_all(main, hook)


def run_check_only(ctx: RunContext) -> CheckReport:
    """复检当前 v2 产物，不写盘。"""
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先运行 stage3.style 免费生成机械基线')
    artifact, data = store.read_own(StyleGrounding, REL)
    main, hook = _upstreams(store)
    outcomes = checks.check(data, measured_now=measure_all(main, hook))
    return build(
        sid=ctx.sid,
        node=ctx.node,
        freshness=store.freshness(REL),
        outcomes=outcomes,
    )
