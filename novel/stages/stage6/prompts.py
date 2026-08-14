#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompts.py —— 下发给文案节点的那两段。**`HOOK_SYS` 与原型 `N8_SYS` 逐字节相同。**

⚠⚠ **改一个字都要先读这句**：这份 prompt 压着 120+ 条真机 findings 的学费，
而**改写不会报错**——它只会让某一类失效重新出现，且要花一次真钱才看得见。
`tests/test_stage6.py` 有一条会红的测试逐字节钉着它。

【唯一一处有意的搬家】三种写法各自那句说明，原型写在产地的一个 dict 里，
而**枚举表在 `contracts/invariants.py`**——「有哪三种写法」与「每种是什么意思」
分了两个落点。现在合成一份（`HOOK_APPROACH_NOTES`），prompt 这边由 `fill()` 插值。
**那是搬家不是改措辞**：渲染出来的字节与原型逐字相同，由
`test_the_rendered_prompt_is_byte_for_byte_too` 钉着。

⚠⚠ **那条测试是被一次真实失误逼出来的**：模板确实逐字节相同，而三句说明被**转述**了，
渲染结果短了 48 字——丢掉的正是 `result_flashback` 那句里的硬约束
**「不得取后期高潮或结局」**，而那是本层唯一还禁的东西（`R-9.9`）。
**只钉模板抓不到它**：占位符两侧一个字节都没变。⇒ 要钉的是**发出去的字节**。

【`given` 只有这一处产出】发明越界探针（`hook_invented_name` / `hook_invented_number`）
拿它当「给过什么」的全集。它必须由 `build_material` 产出、而不是另找一处拼一遍：
**探针与 prompt 一旦分家，探针就开始误报（把给过的当成编的）或漏报（把没给的当成给过的）。**
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from contracts.invariants import HOOK_APPROACHES, HOOK_APPROACH_NOTES
from novel.core.bands import hook_shape_band
from novel.llm.prompts import fill

# ⚠⚠ 与原型 `Stage6/n8_hook.py::N8_SYS` **逐字节相同**（893 字 / 32 行）。
HOOK_SYS = """\
你在为一本小说写【开篇文案】——放在正文之前、让读者愿意点进第一章的那段话。只输出 JSON。

要求：
- 尽快立住【身份 / 处境 / 危机】，把最有张力的那个冲突亮出来，留下一个明确的未答问题，自然接进第一章；
- 落在给定的【文案写法】与【声音】上：文案的情绪浓度通常高于正文，按给定的「与正文的关系」把握分寸；
- **不许发明事实，必须发明文字**：文案里的事必须在给定材料里有据——不写没发生的事、
  不承诺没兑现的反转、不歪曲人物关系；但**措辞一律自己写**，不要沿用给定材料的句子。
- 文案里出现的人物称谓，只能用【人物表】或【全书卖点】里出现过的那些。

【可以说破】给定的【全书卖点】是这本书最有戏的那几件事，**它们就是拿来用的**：
可以直接写出来当钩子，不必绕开、不必只做暗示。这是文案，不是正文——读者是先看它才决定
要不要点进第一章的。

【事实与时点是两条约束】只有【正文】能作为「开篇这几章已经发生了什么、谁当时知道什么」
的依据。【全书卖点】可以由文案叙述者直接点破、概括或设问，但它不是开篇事件清单：
- 不得把卖点补写成发生在开篇某一夜、某一处的场景，不得为它新编台词、动作或因果；
- 不得把人物后期才获得的知识、能力、身份或关系前移到开篇时点；
- 不得把两条分别真实的材料拼成一件从未发生过的新事件。
「可以说破」授权的是信息披露，不是改动故事时间线。

【硬禁·结局】唯一不许碰的是**故事最终如何收场**：不要写结局、不要写最后的胜负与归宿、
不要写「最终……」这类收束。悬念要留在「接下来会怎样」，不是「原来是这样，完了」。

【硬禁·不复用原句】这是一次**重写**。不要复用你在给定正文里读到的句子，全部用自己的话写。

产出【三个候选】，各用一种策略：
$APPROACHES

输出 JSON：
- candidates: [{approach, text}]，三条，approach 用上面的英文键名；
  text 是完整文案，简短有力，多为短句短段；
- rationale: 一句话，说明这三条各自赌的是什么。"""

#: 结构提示。⚠ 与原型 `N8_HINT` 同形，只是 `approach` 那一格从 `APPROACHES` 派生。
HOOK_HINT = {'candidates': [{'approach': '|'.join(HOOK_APPROACHES), 'text': 'str'}],
             'rationale': 'str'}


def hook_system() -> str:
    """把三种写法插值进 system prompt。

    `fill()` 走 `string.Template`：占位符缺值时**抛 `KeyError`**，
    而 Jinja 那类默认渲染成空串——那会静默产出一个带窟窿的 prompt，与显式终态正相反。
    ⚠ `HOOK_APPROACH_NOTES` 的键与 `HOOK_APPROACHES` 由 invariants 的 import 期断言钉死，
    所以「加了一种写法而忘了写说明」在这里表现为 `KeyError`，不是一句空说明。
    """
    return fill(HOOK_SYS,
                APPROACHES='\n'.join(f'  {i}. {k} —— {v}'
                                     for i, (k, v) in enumerate(HOOK_APPROACH_NOTES.items(), 1)))


@dataclass(frozen=True)
class Material:
    """下发材料。**`given` 是探针的「给过什么」全集，只有这里产出它。**"""

    blocks: tuple[str, ...]
    lead: tuple[dict, ...] = field(default_factory=tuple)
    missing: tuple[int, ...] = field(default_factory=tuple)
    upto: int = 1

    @property
    def given(self) -> str:
        return '\n'.join(self.blocks)


def voice_for_hook(style: dict) -> dict:
    """正文声音里**能给文案节点看的那几维**。

    现役 Stage 3 的 `voice.mode_variation` 只描述当前源章可见的情境变化，Stage 5
    也只消费同源项。这里读取的是书级聚合画像；文案只有一段话，本来就不需要
    「不同情境」这一维，因此继续剥掉它。该投影也兼容历史全书产物：旧产物实测曾把
    「系统摊牌／系统介入」写进这一维，而「系统」到 ch14 才向读者揭示。

    而对文案来说这一维**本来就没用**：文案是一段话，没有「不同情境」。
    ⇒ 处置是按消费方投影，不是删——Stage 5 的逐章写手仍然需要对应局部项。

    ⚠ 这是**下发面**方向的收窄。同一份 `voice` 若将来拿去做检测器
    （比如查文案有没有偏离本书声音），那一边**不能**收窄——方向正相反。
    """
    v = dict(style.get('voice') or {})
    v.pop('mode_variation', None)
    return v


def _roster_upto(ctx: dict, upto: int) -> dict[str, str | None]:
    """人物表 = **逐章名册的并集**，不是 `world_facts.characters`。

    ⚠ 后者是**全书**名册，它自己就带着未来：实测某本的全书名册里有一条
    「系统：绑定许言的穿越系统…」，而这个存在要到 ch14 才对读者揭示。
    整份发下去，写开篇文案的模型当场读到全书最大的那个底。
    逐章 `characters` 是 stage4 已经逐章投影过的，按范围取并集即可。

    ⚠⚠ **这里收窄，`core.naming.name_stems` 那边绝不能收窄**：同一份人名表在这里是
    「要发出去的东西」，在发明越界探针那里是「用来发现问题的东西」，**方向正相反**。
    按章收窄探针，第 1 章就检不出那个后文才出场的人名，真编出来的反而漏网。
    """
    roster: dict[str, str | None] = {}
    for ch in (ctx.get('chapters') or []):
        if (ch.get('output_ordinal') or 0) <= upto:
            for c in (ch.get('characters') or {}).values():
                # 只给【叫什么 + 是谁】。`name_distinctiveness` 的消费方是确定性代码，
                # prompt 里从没定义过这个枚举是什么意思，发过去只是噪声。
                if (c or {}).get('display_name'):
                    roster[c['display_name']] = c.get('role')
    return roster


def build_material(*, title: str, ctx: dict, style: dict, secrets: list[dict],
                   lead: list[dict], missing: list[int], upto: int) -> Material:
    """组装下发材料。**块的措辞逐字移植自原型。**"""
    hook_style = style.get('hook_style')
    facts = [s for s in (secrets or []) if s.get('fact')]
    blocks = [
        f'《{title}》 —— 为这本书写开篇文案。',

        '── 正文（开篇这几章的成稿；文案须与它真实相符，且【不得复用它的句子】）──\n'
        + '\n\n'.join(f"【第 {r.get('output_ordinal')} 章】\n{r.get('text') or ''}" for r in lead),

        # 【为什么送的是 `fact` 而不是多给几章正文】判据 2「原文以点不以段下发」：
        # `secrets[].fact` 是**离散的事实陈述**，写手只能把它放进去或不放进去，
        # 模仿不出文风；多给几章成稿是**连续文本段**，模型必然沿着它的句序和节奏走。
        # 而且加章还解决不了问题——实测某本的卖点是 reveal=4 的那条，
        # 放宽到第 4 章它仍然是 secret、仍然被禁。**挡路的从来是剧透口径，不是章节范围。**
        ('── 全书卖点（可直接说破的事实；不是『开篇已经发生』的事件清单）──\n'
         + '\n'.join(f"· {s.get('fact')}" for s in facts)
         + '\n挑最有戏的一到两件当钩子。可以由文案叙述者概括、点破或设问；'
           '不得把它补写成开篇场景，不得擅加发生时点、地点、台词、动作或因果。'
         if facts else
         '── 全书卖点 ──\n（这本书没有抽到 secret，只能靠开篇那几章自己立钩子）'),

        # 人物表不再是唯一的名字来源：卖点里必然带着后文才出场的人（实测五本每本都有）。
        # `hook_invented_name` 拿整个下发面 `given` 当依据，所以那些名字自动有据。
        '── 人物表（文案里的人物称谓只能用这里或【全书卖点】里出现过的）──\n'
        + json.dumps(_roster_upto(ctx, upto), ensure_ascii=False),

        '── 文案写法（这本书的文案是怎么写的）──\n' + json.dumps(hook_style, ensure_ascii=False),

        '── 正文的声音（文案与它的关系见上面的 relation_to_body）──\n'
        + json.dumps(voice_for_hook(style), ensure_ascii=False),

        # 【形态给档位，不给数值】既给「权威数字」又禁止复述数字，是一对自相冲突的指令。
        # 实测动机：首跑三个候选**全被写成一整段**（162/178/185 字/段），
        # 而本书文案段是 16.4 字/段。`hook_shape` 抓到了，
        # 可 prompt 当时只有一句含糊的「多为短句短段」——**判据在查一件 prompt 没说清的事**。
        '── 这本书的文案是什么形态（按原作文案量出的档位，权威）──\n'
        + json.dumps(hook_shape_band(_hook_meas(style)), ensure_ascii=False)
        + '\n照这个形态写：段落怎么切、句子多长，跟它一致。'
        # 【篇幅给数值，形态给档位 —— 这两条不矛盾】「喂档位不喂数值」是对**描述性节点**
        # （stage3）说的：那里既要它参考权威数字又禁止复述，模型只能靠写进描述来自证。
        # stage6 是**生成**节点，字数是它的**目标**不是它要描述的对象。
        # 实测动机：首跑两本**全部偏短**（118/114 vs 180、116/74/79 vs 319），
        # 因为当时只说了一句「简短有力」。
        + f"\n目标篇幅：约 {(_hook_meas_raw(style) or {}).get('chars', '?')} 字"
          f'（本书原作文案段的长度）。这是**目标**，不是要写进文案里的信息。',

        # 【剧透边界】旧版这里只给「还有 N 条未揭示的秘密」这个数字、不给内容，
        # 论证是「给了 fact 就是把答案发过去」。用户裁决把上界整个挪了：
        # **秘密可以说破，结局不行**。标定依据是原作自己：5 本原作文案**没有一本**
        # 提结局或高潮，而其中一本的原作文案确实说破了一条 secret。
        # **这条边界不是我们定的，是量出来的。**
        '── 唯一的禁区 ──\n'
        '全书的结局与最终高潮不许写，也不要暗示故事最终如何收场（上面的卖点里若牵到结局，'
        '只用它的前半段）。除此之外没有别的信息禁区——秘密该说破就说破；但事实的时点仍须'
        '真实：可提前告诉读者，不可把后期事实搬成开篇已经发生的戏。',
    ]
    return Material(blocks=tuple(blocks), lead=tuple(lead),
                    missing=tuple(missing), upto=upto)


def _hook_meas_raw(style: dict) -> dict | None:
    return ((style.get('measurable') or {}).get('hook')) or None


def _hook_meas(style: dict):
    """`measurable.hook` → `MeasurableBlock`，喂给 `hook_shape_band`。

    ⚠ 走模型而不是裸 dict：`hook_shape_band` 的两个入参是 `avg_sentence_chars` /
    `avg_paragraph_chars`，拿 dict 直接 `.get` 会在字段改名时静默取到 `None`
    并渲染出一档错的档位词——而那一档**逐字进 prompt**。
    """
    from contracts.schema.stage3 import MeasurableBlock
    raw = _hook_meas_raw(style)
    return MeasurableBlock.model_validate(raw) if raw else None


def build_user(mat: Material) -> str:
    return '\n\n'.join(mat.blocks)
