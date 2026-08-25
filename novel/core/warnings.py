#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
warnings.py —— 一条可疑项**读给人听是什么样**，唯一落点。

【为什么措辞要单独有个家】
Stage0 的 warning 是 current revision 的运行时派生；Stage1 的重复章号告警也由当前原文
复扫后直接渲染，不再经过中间产物。措辞总得有人生成，
而它有两个出口：S-13 校验报告的 `info` 级结论，和 S-00 审阅屏上那一行黄字。
两个出口各写一份，就是同一句话的第二个落点 —— 那正是本仓反复付学费的形状。
⇒ 都从这里取。**前端不再自己拼措辞**：HTTP 层把渲染好的那句话随响应一起发下去
（`contracts/schema/api.py::Stage0WarningView`），前端只负责画。

【为什么阈值在这里现取，而不是随证据落盘】
`BODY_RATIO_MIN` / `DROP_RATIO_MAX` / `NONBODY_MARKER_WARN_CP` / `NOISY_DUPLICATE_MIN`
的唯一落点是 `contracts/invariants.py`。把它们抄进产物，就是把一个会变的判准
冻在一份长期文件里，而两份必然分家。
⇒ 阈值改了以后**旧产物的措辞跟着变才是对的**：这句话说的是
「按今天的判准，当时那份数据长这样」，而不是「当时的人看到过这句话」。

⚠ 本模块只依赖 contracts 与 `novel.core.ingest` 的标记归一函数，不反向依赖 stage 或 view。
⚠ **认告警一律用 `kind`，不许拿这里产出的字符串去认**。这条纪律没变——恰恰是因为
  措辞现在更自由了（随阈值变），拿它做分支比以前更危险。
"""
from __future__ import annotations

from collections.abc import Sequence

from contracts.invariants import (
    BODY_RATIO_MIN,
    DROP_RATIO_MAX,
    NOISY_DUPLICATE_MIN,
    NONBODY_MARKER_WARN_CP,
)
from contracts.schema.stage0 import (
    DropRatioWarning, EarlyCutWarning, LongMarkerWarning, RemovedSegment, Stage0Warning,
)
from novel.core.ingest import marker_core


def stage0_warnings(total: int, body: int,
                    removed: Sequence[RemovedSegment]) -> list[Stage0Warning]:
    """按 current 内容现算 Stage0 可疑项；不参与 revision 身份。"""
    out: list[Stage0Warning] = []
    ratio = body / total if total else 0.0
    removed_points = sum(segment.span[1] - segment.span[0] for segment in removed)
    if ratio < BODY_RATIO_MIN:
        out.append(EarlyCutWarning(
            kind='early_cut', body_code_points=body, total_code_points=total))
    if total and removed_points / total > DROP_RATIO_MAX:
        out.append(DropRatioWarning(
            kind='drop_ratio', removed_code_points=removed_points,
            total_code_points=total))
    for segment in removed:
        length = len(marker_core(segment.marker_line))
        if length > NONBODY_MARKER_WARN_CP:
            out.append(LongMarkerWarning(
                kind='long_marker', span_start=segment.span[0],
                marker_core_code_points=length))
    return out


def describe(warning: Stage0Warning) -> str:
    """一条可疑项读给人听的那句话。**穷举 `kind`，不留 else 兜底。**

    没有兜底分支是有意的：新增一类 warning 而忘了给它措辞时，这里当场
    `ValueError` —— 而不是安静地发一句「未知告警」给人看。取值面加了一类却没人
    出声，正是 `contracts/invariants.py` 那三张登记表在防的方向。
    """
    kind = warning.kind
    if kind == 'early_cut':
        ratio = warning.body_code_points / warning.total_code_points
        return (f'清理后正文只占 {ratio:.1%}（'
                f'{warning.body_code_points}/{warning.total_code_points} 码点），'
                f'低于 {BODY_RATIO_MIN:.0%}')
    if kind == 'drop_ratio':
        ratio = warning.removed_code_points / warning.total_code_points
        return (f'剔除段占全文 {ratio:.1%}（'
                f'{warning.removed_code_points}/{warning.total_code_points} 码点），'
                f'高于 {DROP_RATIO_MAX:.0%}')
    if kind == 'long_marker':
        return (f'原文偏移 {warning.span_start} 处标记 core 长 '
                f'{warning.marker_core_code_points}，'
                f'超过实测真标记上限 {NONBODY_MARKER_WARN_CP}')
    raise ValueError(
        f'可疑项 kind={kind!r} 没有措辞——新增取值面时必须同批在这里给它一句话，'
        f'否则界面上会出现一条读不懂的告警')


def describe_noisy_duplicate(source_chapter_no: int, count: int) -> str:
    """Stage1 当前复扫得到的重复章号读给人听。"""
    return (f'chapter_no={source_chapter_no} 出现 {count} 次'
            f'（≥{NOISY_DUPLICATE_MIN}），疑似固定标记被误判成章节号，'
            f'请人工核对原文标记规律')
