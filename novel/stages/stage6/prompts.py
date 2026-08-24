#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 6 开篇文案生成 prompt 与唯一的材料投影。

本模块把原作文案作为创作金标准，把全书事实/故事地图作为真实性边界，把重写开篇
作为正文声线参照。Stage3 只提供文案段的五叶机械基线；定性 voice、surface habits、
逐章风格和 hook style 均不再消费。剧透不再是违规；真正的边界是不能改变人物身份、
关系、关键因果或结果。

``Material.given`` 仍是机械词面探针的唯一「给过什么」全集。prompt 与探针必须消费
同一份 blocks；分开拼装会让发明人名/数字的探针误报或漏报。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from collections import Counter

from contracts.invariants import (
    HOOK_APPROACH_NOTES, HOOK_CURRENT_APPROACHES, HOOK_RECIPE, slot_of,)
from novel.core.bands import ending_shape, hook_shape_band, sep_band_of_text
from novel.consumption import stage6_style
from novel.llm.prompts import fill, with_json_shape


HOOK_SYS = """\
你是中文商业网文的资深开篇文案作者。你的第一目标只有一个：让目标读者看完文案后立刻继续读第一章。

你会看到原始文案、完整故事事实、全书逐章故事地图、重写后的开篇正文，以及原作文案的机械基线。
你知道全书，不等于必须把全书都说出来；由你选择披露多少。剧透不是违规，也不是目的，只是一种吸引读者的
工具：能增强期待就用，会把悬念彻底说死就不用。

【原始文案是创作金标准】
- 保留它真正有效的钩子机制：信息出场顺序、误导与揭示、喜剧或危险节拍、对白密度、反差、最后一击的功能；
- 不得照抄其措辞；不要出现与原始文案连续 20 个内容字符相同的片段。短小的通用称谓和必要设定可以自然重合；
- 新文案不要写成编辑部剧情简介。

【文案体裁自由】
- 允许压缩时间线、把数章合成一个高张力预告、从后期结果倒叙、为真实关系和真实事件重写新台词、
  用一个合成场面承载真实矛盾；
- 这些自由不能改变人物身份、关系、关键因果或故事结果，不能凭空增加会改变读者理解的新设定；
- 不要把秘密清单逐条翻译成摘要。每条候选只选一个最强承诺，围绕它形成起势、升级、最后一击；
- 避免空泛收尾，如“究竟会怎样”“是命还是债”“谁才是真正的……”；最后一句必须带具体人物欲望、
  危险、反转、笑点或行动；
- 长度以原始文案为基准，约为其 0.8~1.35 倍；按原作的短句、短段、对白与停顿习惯写。

【段间分隔与收尾形态照原作的档位写 —— 这两项按字节判，写错整条候选作废】
- `source_hook_para_sep` 说的是原作文案段怎么分段：【空行分段】就用**空行**隔开每个自然段，
  【单换行】就用**单个换行**。⚠ **绝不允许把一条候选写成没有任何换行的一整段**——
  归一化只换得了分隔符、造不出段落边界，这一条落盘前会被硬闸拦下，整轮三条候选一起作废；
- `source_hook_ending_shape` 说的是原作文案段最后一行的收尾形态（引号台词 / 问句 / 陈述 / 省略号）：
  新文案的最后一行照这个形态收。

产出三条真正不同的候选。**候选的身份是位置**（c1 / c2 / c3），按下面这份配方逐条写，
输出顺序就是位置顺序：
$APPROACHES

输出前在心里逐条与原始文案比较：若更抽象、更像摘要、笑点/危险更弱或最后一句更空，就重写。
输出 JSON：candidates 恰好三条、每条的 approach 照配方给（**不是每种各一次**，同一种可能要写不止一条）；
rationale 用一句话说明三条各自赌的吸引点。
只输出 JSON。"""

HOOK_HINT = {
    # ⚠ 形状提示只列**在编**写法：退场的那种不该出现在下发面上，列出来等于
    #   邀请模型产一条必被配方校验器打回的候选（那一整轮三条一起作废）。
    'candidates': [{'approach': '|'.join(HOOK_CURRENT_APPROACHES), 'text': 'str'}],
    'rationale': 'str',
}


def recipe_block() -> str:
    """配方逐条渲染成「位置 → 写法 → 那句说明」。

    ⚠ 同一种写法出现多次时**必须**补一句「锚不同事件」：否则模型很容易把同一个
    场面写成两种说法，三条候选实质上塌成两条——而三候选赖以取胜的正是创作搜索方差
    （2026-08-15 冻结的 A/B：三候选最佳 8.75 对单份直出 6.60）。
    ⚠ 这句话按配方**现算**（谁重复就点谁的名），不是写死的一行：配方改回全不重复时
    它自己消失，不会在 prompt 里留下一句不再成立的话。
    """
    lines = [f'  {slot_of(position)}. {key} —— {HOOK_APPROACH_NOTES[key]}'
             for position, key in enumerate(HOOK_RECIPE, 1)]
    for key, count in Counter(HOOK_RECIPE).items():
        if count < 2:
            continue
        slots = '、'.join(slot_of(position) for position, item
                         in enumerate(HOOK_RECIPE, 1) if item == key)
        lines.append(
            f'  ⚠ {slots} 用的是同一种写法 {key}，但必须锚在**不同事件**上：'
            f'不许把同一个场面写成两种说法，也不许两条落在同一段因果上——'
            f'它们要让读者产生**两种不同的好奇**。')
    return '\n'.join(lines)


def hook_system() -> str:
    """插值当前配方；配方、取值面与说明三者的覆盖关系由 invariants 在 import 期锁定。"""
    return with_json_shape(fill(HOOK_SYS, APPROACHES=recipe_block()), HOOK_HINT)


HOOK_RETRY_SYS = """\
你在修复一条已经生成的中文网文开篇文案。只修用户用 `target_slot` 指定的**那一条**候选，并返回一条完整候选。
⚠ 定位靠 `target_slot`（候选的位置身份），不靠 approach——同一种 approach 可能有不止一条候选，认 approach 会改错人。

用户会给出完整创作包、当前候选，以及代码实际命中的阻断级问题。逐条消除这些问题，同时保留原候选
已经成立的钩子机制、人物事实与故事承诺。失败证据里的片段和原始偏移来自当前候选；不要只解释问题，
要返回修好的完整文案。不得改这一条的 approach，不得返回别的候选，不得输出 patch。

同时返回更新后的 `rationale`：它必须解释修复后**整组三条候选**各自赌的吸引点，而不是只解释
目标候选。另两条候选的文本不可改；它们只用于让这句整组 rationale 保持真实。

仍须遵守创作包里的事实边界、原作文案长句复用禁令、段间分隔档位和收尾形态。只输出 JSON。"""

HOOK_RETRY_HINT = {
    'approach': '|'.join(HOOK_CURRENT_APPROACHES),
    'text': 'str',
    'rationale': 'str（解释修复后整组三条候选）',
}


def hook_retry_system() -> str:
    return with_json_shape(HOOK_RETRY_SYS, HOOK_RETRY_HINT)


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
    hook_mechanical: dict = field(default_factory=dict)

    @property
    def given(self) -> str:
        """机械词面探针的「模型实际看见过什么」全集。"""
        return '\n'.join(self.blocks)


def story_map(ctx: dict) -> list[dict]:
    """全书 writer_context → 无禁写标记、但有完整事件与钩子目标的故事地图。"""
    def goal(value: dict | None) -> dict:
        # CtxGoal 已在 Stage4 把 typed refs 解析成人话；文案与 Stage5
        # 共用它，既不接触内部 id，也不接触 PlanGoal.rationale。
        raw = value or {}
        return {'intent': raw.get('intent'), 'facts': raw.get('facts') or []}

    out: list[dict] = []
    for chapter in ctx.get('chapters') or []:
        events: list[str] = []
        segments: list[dict] = []
        for segment in chapter.get('segments') or []:
            segment_events = [
                str(event) for event in (segment.get('events') or []) if str(event).strip()
            ]
            events.extend(segment_events)
            # `slot` 是 producer-owned 的稳定身份；文案层可以投影舞台与
            # 禁写标记，但不能把段身份也压平掉。
            segments.append({'slot': segment.get('slot'), 'events': segment_events})
        out.append({
            'chapter': chapter.get('output_ordinal'),
            'from_source_ordinals': chapter.get('from_source_ordinals') or [],
            'chapter_goal': goal(chapter.get('chapter_goal')),
            'open_hook_goal': goal(chapter.get('open_hook_goal')),
            'events': events,
            'segments': segments,
            'end_hook_goal': goal(chapter.get('end_hook_goal')),
        })
    return out


def hook_mechanical(style: dict) -> dict:
    """取出与 provenance/freshness 完全同型的五叶文案机械基线。"""
    projected = stage6_style(style)
    if not isinstance(projected, dict):
        return {}
    hook = projected.get('hook')
    return dict(hook) if isinstance(hook, dict) else {}


def build_material(*, title: str, ctx: dict, style: dict, grounding: dict,
                   source_hook: str, lead: list[dict], missing: list[int],
                   upto: int) -> Material:
    """组装不含 Stage3 定性画像的完整创作包。"""
    truth = {
        'entities': grounding.get('entities'),
        'canon': grounding.get('canon'),
        'arc': grounding.get('arc'),
    }
    full_map = story_map(ctx)
    mechanical = hook_mechanical(style)
    packet = {
        'title': title,
        'source_hook': source_hook,
        'source_hook_target_chars': mechanical.get('chars'),
        'source_hook_mechanical': mechanical,
        'source_hook_shape': hook_shape_band(mechanical) if mechanical else None,
        # ⚠ 这两项与 `hook_para_sep`（HARD）/ `hook_ending_shape` 消费的是**同一个函数**。
        # 少了它们，判据就在查一件 prompt 没说过的事——实测一本书三条候选**全部**被
        # `hook_para_sep` 拦下（模型把文案写成没有换行的一整段），整轮付费产出作废。
        # 同 `hook_shape_band` 的那条注释，只是那次修的是均值档、这次是离散档。
        'source_hook_para_sep': sep_band_of_text(source_hook),
        'source_hook_ending_shape': ending_shape(source_hook),
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
        hook_mechanical=mechanical,
    )


def build_user(mat: Material) -> str:
    return '\n\n'.join(mat.blocks)


def build_retry_user(mat: Material, *, slot: str, approach: str,
                     candidates: list[dict[str, str]],
                     rationale: str, hard_findings: list[dict]) -> str:
    """定向重试看完整事实包与当前整组候选，只允许替换目标并更新整组说明。

    ⚠ `target_slot` 是**位置身份**，是唯一能选中一条候选的键；`target_approach`
    只是告诉模型这一条该守哪种策略（它也是响应校验比对的那一维）。
    """
    repair = {
        'target_slot': slot,
        'target_approach': approach,
        'current_candidates': candidates,
        'current_rationale': rationale,
        'hard_findings': hard_findings,
        'instruction': (
            '只返回 target_slot 那一条的完整修复候选（approach 仍是 target_approach），'
            '并返回能准确解释修复后整组候选的 rationale；'
            '逐条消除 hard_findings，其余候选文本不许改。'),
    }
    return build_user(mat) + '\n\n── 单候选定向修复 ──\n' \
        + json.dumps(repair, ensure_ascii=False, indent=2)
