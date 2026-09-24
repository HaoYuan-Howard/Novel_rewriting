#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render —— 模型阅读界面的确定性 renderer（F5，Stage5）。

【内部 IR 与模型阅读文本分别定义】
renderer 只排序、分组、引用解析和格式化：不压缩语义、不改写 brief、不新增
模型调用、不摘要。全部实质约束（义务与满足条件、知情模式、否定条件、称谓
释放位置、逐拍结果）可追溯地进入模型输入；source lineage、源章拓扑与审计
字段在格式化时剥离，不进 Writer 阅读面。

【分层与完整输入】
完整 WriterBookIR 进入请求：当前章材料集中展示，其余章完整场景 IR 明确标为
规划参考（作者知道 ≠ 人物或读者已知）；已提交前文按章完整给出。首版不自动
缩短上下文——完整输入超限由预算闸阻断，不静默摘要或截断。
"""
from __future__ import annotations

import json
from typing import Mapping

from contracts.schema.str06.book_ir import WriterBookIR
from contracts.schema.str06.context import WriterContext

#: renderer 版本：进入正文请求身份。格式化规则变化必须换版。
RENDERER_VERSION = 'str06-render-v1'

#: 整书写作约定与自由度（Writer 协议，固定文本）。表达自由边界：entry 是
#: 起笔已成立的状态、effects 是结束需成立的结果、义务必须实际兑现、局部
#: 实现自由、局部细节不得产生新的关键因果/知情/后续依赖。
WRITER_PROTOCOL_VERSION = 'str06-body-protocol-v1'
WRITER_PROTOCOL = """\
1. 【事实约束】里标为入场状态（entry）的事实是起笔时已成立的状态：按叙事需要自然带入，不要求开场逐条介绍。
2. 【事实约束】里的义务与逐拍结果是本章结束后需要成立的结果：通过情节实际完成，不要求用旁白逐条宣布；义务必须实际兑现，仅提到关键词不算完成。
3. 在事实约束与导演说明的边界内，鼓励创造具体的对白、动作、停顿与即时反应；不因「保险」而一律写成说明文或全面回避心理与对白。
4. 局部细节（小动作、临时物件、即兴短句）必须与既定时令、地点、世界规则、称谓释放和 POV 相容；不得新增关键因果、人物前史、秘密、承诺、知情或后文依赖——这些只能由导演说明与事实约束给出。
5. 只使用已释放的称谓面；事实约束中标明「第 X 拍起可用」的称谓不得提前使用。
6. 每一场的 POV 与 voice 是该场的叙述约定；多场 POV 不同时按场切换，不做全书统一。"""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True)


def _obligation_view(obligation) -> dict:
    """义务的阅读面：statement/satisfaction 全量保留，lineage（源坐标）剥离。"""
    view = obligation.model_dump(mode='json')
    view.pop('lineage', None)
    return view


def _scene_view(context: WriterContext) -> str:
    ir = context.verification_ir
    brief = context.writer_brief
    parts = [
        f'◆ 场景 {context.scene_id}（pov={brief.pov}，voice={brief.voice}）',
        '【导演说明】' + brief.realization_brief,
        '【驱动】' + brief.scene_driver_summary,
    ]
    if brief.delivery_hints:
        parts.append('【拍级表达提示】\n' + '\n'.join(f'- {h}' for h in brief.delivery_hints))
    parts.append('【事实约束】\n' + _json({
        'obligations': [_obligation_view(o) for o in ir.obligations],
        'beats': [b.model_dump(mode='json') for b in ir.beats],
        'entry_state': [e.model_dump(mode='json') for e in ir.entry_state],
        'entry_knowledge': [e.model_dump(mode='json') for e in ir.entry_knowledge],
        'released_surfaces': [r.model_dump(mode='json') for r in ir.released_surfaces],
        'planned_delta': [d.model_dump(mode='json') for d in ir.planned_delta],
        'prohibitions': list(ir.prohibitions),
        'continuity_fact_view': list(ir.continuity_fact_view),
    }))
    return '\n'.join(parts)


def render_chapter_packet(
    book_ir: WriterBookIR, chapter_ordinal: int,
    prior_chapters: Mapping[int, str],
) -> str:
    """章请求 user 段：六层固定结构（协议→规划参考→前文→章目的→场景→交付要求）。

    纯函数：相同（IR, 章序, 前文）⇒ 相同字节。不检查回执有效性——前文合法性
    由调用方核对章级回执后传入。
    """
    chapter = book_ir.chapter(chapter_ordinal)
    brief = book_ir.book_brief
    parts: list[str] = ['【整书写作约定与自由度】', WRITER_PROTOCOL]

    parts.append('\n【全书规划参考：作者知道，不等于人物或读者已知】')
    parts.append('导航（其余各章材料在下方「规划参考」内完整给出）：')
    for nav in brief.chapters:
        lines = '\n'.join(f'  - {line}' for line in nav.scene_lines)
        parts.append(f'第{nav.chapter_ordinal}章：{nav.purpose}\n{lines}')
    if brief.release_schedule:
        parts.append('称谓释放位置（未到位置不得使用对应字面）：')
        parts.extend(
            f'  - {entry.char_id}：{"、".join(entry.surfaces)}（第{entry.from_chapter}章起）'
            for entry in brief.release_schedule)
    for other in book_ir.chapters:
        if other.chapter_ordinal == chapter_ordinal:
            continue
        parts.append(f'—— 第{other.chapter_ordinal}章（规划参考）——')
        for context in other.scenes:
            parts.append(_scene_view(context))

    parts.append('\n【完整已提交前文】')
    if prior_chapters:
        for ordinal in sorted(prior_chapters):
            parts.append(f'〔第{ordinal}章〕\n{prior_chapters[ordinal]}')
    else:
        parts.append('（本章为全书第一章，无前文）')

    parts.append('\n【本章目的】')
    parts.append(f'第{chapter.chapter_ordinal}章：{chapter.purpose}')

    parts.append('\n【本章各场景：导演说明＋事实约束】')
    for context in chapter.scenes:
        parts.append(_scene_view(context))

    low = round(chapter.length_budget * brief.length_band_low)
    high = round(chapter.length_budget * brief.length_band_high)
    parts.append('\n【本章交付要求】')
    parts.append(
        f'1. 一次写出本章完整正文：覆盖本章全部场景，按场景阅读序连续成文；'
        f'篇幅目标约 {chapter.length_budget} 字（按非空白字符计），'
        f'合法区间 [{low}, {high}] 字。')
    parts.append('2. 直接输出正文本身：不要章号与标题（章号由系统添加），'
                 '不要场景 id、任何解释、前言、后缀或 markdown 围栏。')
    parts.append('3. 整章是一段连续的小说正文：场景之间自然过渡，'
                 '不要输出「场景一」之类的标记或分隔线。')
    return '\n\n'.join(parts) + '\n'
