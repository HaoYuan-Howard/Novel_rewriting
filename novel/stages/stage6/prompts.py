#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 6 开篇文案生成 prompt 与唯一的材料投影。

2026-08-15 三书 A/B 证明，当前文案质量的主要瓶颈不是模型能力，而是模型看不见
原作文案的有效结构、全书兑现和正文实际执行的 ``surface_habits``。本模块因此把
原作文案作为创作金标准，把全书事实/故事地图作为真实性边界，把开篇成稿与正文手法
作为声线边界。剧透不再是违规；真正的边界是不能改变人物身份、关系、关键因果或结果。

``Material.given`` 仍是机械词面探针的唯一「给过什么」全集。prompt 与探针必须消费
同一份 blocks；分开拼装会让发明人名/数字的探针误报或漏报。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from contracts.invariants import HOOK_APPROACHES, HOOK_APPROACH_NOTES
from novel.core.bands import hook_shape_band
from novel.llm.prompts import fill, with_json_shape


HOOK_SYS = """\
你是中文商业网文的资深开篇文案作者。你的第一目标只有一个：让目标读者看完文案后立刻继续读第一章。

你会看到原始文案、完整故事事实、全书逐章故事地图、重写后的开篇正文，以及正文实际使用的声音和写作手法。
你知道全书，不等于必须把全书都说出来；由你选择披露多少。剧透不是违规，也不是目的，只是一种吸引读者的
工具：能增强期待就用，会把悬念彻底说死就不用。

【原始文案是创作金标准】
- 保留它真正有效的钩子机制：信息出场顺序、误导与揭示、喜剧或危险节拍、对白密度、反差、最后一击的功能；
- 不得照抄其措辞；不要出现与原始文案连续 20 个内容字符相同的片段。短小的通用称谓和必要设定可以自然重合；
- 新文案必须与重写正文像同一本书，优先执行逐章声音与 surface_habits，不要写成编辑部剧情简介。

【文案体裁自由】
- 允许压缩时间线、把数章合成一个高张力预告、从后期结果倒叙、为真实关系和真实事件重写新台词、
  用一个合成场面承载真实矛盾；
- 这些自由不能改变人物身份、关系、关键因果或故事结果，不能凭空增加会改变读者理解的新设定；
- 不要把秘密清单逐条翻译成摘要。每条候选只选一个最强承诺，围绕它形成起势、升级、最后一击；
- 避免空泛收尾，如“究竟会怎样”“是命还是债”“谁才是真正的……”；最后一句必须带具体人物欲望、
  危险、反转、笑点或行动；
- 长度以原始文案为基准，约为其 0.8~1.35 倍；按原作的短句、短段、对白与停顿习惯写。

产出三个真正不同的候选，各用一种策略：
$APPROACHES

输出前在心里逐条与原始文案比较：若更抽象、更像摘要、笑点/危险更弱或最后一句更空，就重写。
输出 JSON：candidates 恰好三条、每种英文 approach 各一次；rationale 用一句话说明三条各自赌的吸引点。
只输出 JSON。"""

HOOK_HINT = {
    'candidates': [{'approach': '|'.join(HOOK_APPROACHES), 'text': 'str'}],
    'rationale': 'str',
}


def hook_system() -> str:
    """插值三种策略；枚举与说明的覆盖关系由 invariants 在 import 期锁定。"""
    rendered = fill(
        HOOK_SYS,
        APPROACHES='\n'.join(
            f'  {i}. {key} —— {note}'
            for i, (key, note) in enumerate(HOOK_APPROACH_NOTES.items(), 1)
        ),
    )
    return with_json_shape(rendered, HOOK_HINT)


@dataclass(frozen=True)
class Material:
    """生成与校验共用的一份下发材料。"""

    blocks: tuple[str, ...]
    lead: tuple[dict, ...] = field(default_factory=tuple)
    missing: tuple[int, ...] = field(default_factory=tuple)
    upto: int = 1
    source_hook: str = ''
    story_truth: dict = field(default_factory=dict)
    story_map: tuple[dict, ...] = field(default_factory=tuple)
    style_packet: dict = field(default_factory=dict)

    @property
    def given(self) -> str:
        """机械词面探针的「模型实际看见过什么」全集。"""
        return '\n'.join(self.blocks)


def voice_for_hook(style: dict) -> dict:
    """正文的整份书级 voice。

    旧设计删除 ``mode_variation`` 是因为文案只有一段；A/B 表明问题恰恰在文案与正文
    执行感不一致。它现在与 ``surface_habits``、开篇逐章风格一起作为可选择的声线证据，
    由模型判断哪些情境变化与所选场面有关。
    """
    return dict(style.get('voice') or {})


def story_map(ctx: dict) -> list[dict]:
    """全书 writer_context → 无禁写标记、但有完整事件与钩子目标的故事地图。"""
    out: list[dict] = []
    for chapter in ctx.get('chapters') or []:
        events: list[str] = []
        for beat in chapter.get('beats') or []:
            events.extend(str(event) for event in (beat.get('events') or []) if str(event).strip())
        out.append({
            'chapter': chapter.get('output_ordinal'),
            'from_source_ordinals': chapter.get('from_source_ordinals') or [],
            'chapter_goal': chapter.get('chapter_goal'),
            'open_hook_goal': chapter.get('open_hook_goal'),
            'events': events,
            'end_hook_goal': chapter.get('end_hook_goal'),
        })
    return out


def _opening_source_ordinals(ctx: dict, upto: int) -> set[int]:
    out: set[int] = set()
    for chapter in ctx.get('chapters') or []:
        if int(chapter.get('output_ordinal') or 0) <= upto:
            out.update(int(x) for x in (chapter.get('from_source_ordinals') or []) if int(x) > 0)
    return out


def style_packet(style: dict, ctx: dict, upto: int) -> dict:
    """与开篇正文同源的风格执行包，而非只有抽象的书级 voice。"""
    source_ordinals = _opening_source_ordinals(ctx, upto)
    chapter_styles = [
        item for item in (style.get('chapter_styles') or [])
        if int(item.get('source_ordinal') or 0) in source_ordinals
    ]
    return {
        'hook_style': style.get('hook_style'),
        'book_voice': voice_for_hook(style),
        'book_surface_habits': style.get('surface_habits'),
        'opening_chapter_styles': chapter_styles,
        'source_hook_shape': hook_shape_band(_hook_meas(style)),
        'measurable': style.get('measurable'),
    }


def build_material(*, title: str, ctx: dict, style: dict, grounding: dict,
                   source_hook: str, lead: list[dict], missing: list[int],
                   upto: int) -> Material:
    """组装 A/B 已验证的完整创作包。"""
    truth = {
        'entities': grounding.get('entities'),
        'canon': grounding.get('canon'),
        'arc': grounding.get('arc'),
    }
    full_map = story_map(ctx)
    styles = style_packet(style, ctx, upto)
    packet = {
        'title': title,
        'source_hook': source_hook,
        'source_hook_target_chars': ((_hook_meas_raw(style) or {}).get('chars')),
        'style': styles,
        'story_truth': truth,
        'whole_story_map': full_map,
        'rewritten_opening': [
            {'chapter': row.get('output_ordinal'), 'text': row.get('text') or ''}
            for row in lead
        ],
    }
    blocks = (
        '下面是本书的完整创作包。先理解原始文案为何有效，再写三条新的开篇文案。',
        json.dumps(packet, ensure_ascii=False, indent=2),
    )
    return Material(
        blocks=blocks,
        lead=tuple(lead),
        missing=tuple(missing),
        upto=upto,
        source_hook=source_hook,
        story_truth=truth,
        story_map=tuple(full_map),
        style_packet=styles,
    )


def _hook_meas_raw(style: dict) -> dict | None:
    return ((style.get('measurable') or {}).get('hook')) or None


def _hook_meas(style: dict):
    """``measurable.hook`` → 强类型模型，避免字段漂移静默选错形态档。"""
    from contracts.schema.stage3 import MeasurableBlock
    raw = _hook_meas_raw(style)
    return MeasurableBlock.model_validate(raw) if raw else None


def build_user(mat: Material) -> str:
    return '\n\n'.join(mat.blocks)
