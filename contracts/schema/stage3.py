#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage3.py —— 对应 `docs/schema/stage3.md`。文风画像。**全书一次调用，产一份产物。**

【四条贯穿本层的事实，读模型前先知道】

  ① **这份产物由两种东西拼成**：`voice` / `surface_habits` / `hook_style` 是 **LLM** 写的
     自由散文；`measurable` 是**确定性代码**算的。后者是唯一一块**可以 0 成本重算并逐项判等**
     的数据（`--remeasure`），也是本层唯一一条**能红**的结构判据（`measurable_drift`）。

  ② **盘上三本全 PASS，而这句话要连含义一起读**：9 条现役判据里 6 条是「扫过 N 个字段、
     0 处命中」的文本探针。**`voice` 八维、`hook_style` 七项、`surface_habits` 的内容质量
     一条判据都没有**（`R-4.7` → `B-09`）。⇒ PASS = 「没有可探测的越界」，
     **不是**「这份文风画像抓对了」。

  ③ **这份产物整份进写作节点的 prompt，而且全链只有它没有章级闸门。**
     一处越界 = 全书每一章的 prompt 里都有它。实测代价：某本的 `voice.mode_variation`
     写了「…系统摊牌…」，而「系统」是读者第 14 章才知情的秘密的主体——
     **写第 1 章的闭卷写手当场读到全书最大的底**。

  ④ **本层是先校验后落盘，而且有两条不同的「不合格」路径**：有 `hard` 违规 ⇒ `.INVALID`；
     **阻断级判据【没跑成】** ⇒ 同样落 `.INVALID`，另记 `_provenance.gates_skipped`。
     第二条是这一层最值得照抄的设计：`name_leak` 是 `HARD` 但它要 stage2 的人物名表，
     并行跑时名表还不在 ⇒ **闸门缺料 ⇒ 状态是「未验」不是「合格」**，
     等上游落盘后 `--revalidate`（0 成本）补跑那几条，过了即提升。**钱一分没多花。**

⚠ **已裁撤**：`source_id` · `title`——**0 读者**。它们在本层唯一的作用是
**被 `_META_KEYS` 排除在扫描面之外**（`R-4.5b`）——一个字段存在的理由是
「记得把它从检查里排除掉」，那本身就说明它不该存在。
⇒ 不存它们，`R-4.5b` 从「一条要记得排除的规则」变成**结构上不可能**。
⚠ 但**别顺手把 `_META_KEYS` 整个删掉**：`measurable`（代码算的数字）与 `_provenance`
仍必须排除在扫描面之外。**排除表越短越好**——每一项都是
「一个不该被当成描述的东西却存在产物里」的记号。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    CACHE_POLICY, HOOK_KEYS, STAGE3_ATTEMPT_KINDS, VOICE_KEYS,
)
from contracts.schema.base import Fp10, StrictModel
from contracts.schema.common import LlmProvenance


# ─────────────────────────────────────────────────────────────────────────────
# 一、`voice{}` —— 声音内核，八维
# ─────────────────────────────────────────────────────────────────────────────

class Voice(StrictModel):
    """**八个键闭合**，全部自然语言描述，**全部必填非空**。

    `required` 是本层的 `HARD`：缺一维或多一个契约外字段都拦。
    ⚠ 「多一个契约外字段」这一半现在由 `extra='forbid'` 在**载体层**兑现——
    它比判据早一步，在构造那一刻就拦住了。
    """

    #: 叙述贴人物多近。prompt 给的档位词：紧贴内视 / 半抽离 / 冷眼旁观。37~51 字。
    #:
    #: ⚠⚠ **它就是视角（限知/全知）那一轴，这是有意的**：stage2 的 `grammatical_person`
    #: 只管**人称**，「限知 vs 全知」在全链**只有这里一个落点**，而且以档位词形态存在。
    #: ⚠ 因此 `STYLE_SYS` 那句「不要判定人称/视角类型」**必须拆成两半**：
    #: **视角解禁**（它是唯一落点）· **人称保留禁令**——本层是全书一份而人称是逐章变量，
    #: 实测 `FQ_皇帝亲自替我宫斗` ch1 第一人称、ch2~ch19 第三人称，
    #: 本层无论写哪个**对 18/19 章都是错的**，而这份描述整份进**每一章**的写手 prompt。
    #: 这不是「两个落点会漂」的一般担心，是**结构上必然错**。
    #: ⚠ 盘上 3/3 违反（三本都写了「第一人称」），**零守卫**——
    #: 这三本恰好全书单一人称所以没造成损失，但那是运气不是保证（`R-4.9` / `B-09`）。
    narrative_distance: str = Field(min_length=1)

    #: 语域与情绪基调（口语／网感／古白话；反讽／自嘲／毒舌／深情）。43~49 字。
    register_tone: str = Field(min_length=1)

    #: 内心戏比重与呈现方式。49~69 字。
    interiority: str = Field(min_length=1)

    #: 句段形态与节奏，**须与 `measurable` 自洽**。35~46 字。
    sentence_rhythm: str = Field(min_length=1)

    #: 对白呈现形式（引号体／无引号／短句对轰／夹旁白／特殊排版）。43~72 字。
    dialogue_presentation: str = Field(min_length=1)

    #: 环境与动作描写的浓淡。
    #: ⚠ 「三本都说『白描／极简／不铺排』= 低区分度」**不成立**：三本同题材同平台，
    #: 都这么说很可能就是真的——**没量跨书方差之前，三本说得像不构成质量结论**。
    description_density: str = Field(min_length=1)

    #: 章首怎么起、章尾怎么收的**惯例**，不是某一章的具体钩子。48~73 字。
    chapter_open_close: str = Field(min_length=1)

    #: 声音在不同情境下怎么变。**要求逐条写全、分号分开**，覆盖四类位置：
    #: 基础态／防御失效处／冲突顶点／换人说话的段落。156~233 字（明显长于其余七维）。
    #: ⚠ 它按定义遍历全书各阶段 ⇒ **必然携带后文**，所以 stage6 的下发面把它整维剥掉。
    mode_variation: str = Field(min_length=1)


assert set(Voice.model_fields) == set(VOICE_KEYS), \
    'Voice 的八维与 contracts.invariants.VOICE_KEYS 分家了——那张表是 prompt 的插值源'


# ─────────────────────────────────────────────────────────────────────────────
# 三、`hook_style{}` —— 开篇文案段的写法，七项
# ─────────────────────────────────────────────────────────────────────────────

class HookStyle(StrictModel):
    """**七个键闭合**，全部自然语言描述。

    ⚠⚠ **`hook_style | null` 这个类型已作废。** 契约与产地代码都写着「无文案段时为 `null`」，
    而**这条分支不可达**：认不出文案段的书已经在 stage1 入显式终态（`R-1.12`）。
    ⇒ schema 收窄成必填，并且**删掉产地那三条不可达分支**——
    留着它们就是**一条永不执行、也永不被验证的路**（没跑过的分支不是安全的分支）。

    ⚠ **它的消费方不是写作节点，是 stage6。** 写作节点的 prompt 里没有它
    （投影实测只发 `voice` + `surface_habits` + 形态档位）。
    stage6 把它整份 `json.dumps` 发过去，另有一条判据把 `end_question` 投影成
    「收尾形态」做确定性检测——**七项里唯一有下游判据的一项，其余六项零守卫。**

    ⚠ 它在必需顶层键里，与 `required` 判据**不重叠**：必需键查的是「顶层块在不在」
    （模型整块没产 ⇒ 解析层就失败，一次调用作废、可直接重试）；
    `required` 判据查的是「块里的七项齐不齐」（落 `.INVALID`）。**两层各查各的。**
    """

    person: str = Field(min_length=1)
    open_mode: str = Field(min_length=1)
    info_hiding: str = Field(min_length=1)
    twist: str = Field(min_length=1)
    end_question: str = Field(min_length=1)
    tone: str = Field(min_length=1)
    relation_to_body: str = Field(min_length=1)


assert set(HookStyle.model_fields) == set(HOOK_KEYS), \
    'HookStyle 的七项与 contracts.invariants.HOOK_KEYS 分家了'


# ─────────────────────────────────────────────────────────────────────────────
# 四、`measurable{}` —— ★ 代码算的，不是 LLM 产
# ─────────────────────────────────────────────────────────────────────────────

class ChapterCharStats(StrictModel):
    """章篇幅的三个数。盘上 min 369~1053 · avg 784.2~1347.9 · max 1141~1775。"""

    min: int = Field(ge=0)
    max: int = Field(ge=0)
    avg: float = Field(ge=0)


class PerChapterSentence(StrictModel):
    """章间句长形态差异。**`mode_variation` 的证据**：`max >= 1.5 * min` 就判
    「章间句长形态差异明显」。盘上 15.3~16.6 / 18.4~20.7，三本**都没到 1.5×**。
    """

    min: float = Field(ge=0)
    max: float = Field(ge=0)


class MeasurableBlock(StrictModel):
    """两块共有的九项。`body`（全书正文聚合）与 `hook`（开篇文案段）同形。

    **`R-4.2`：两块分开测、互不污染**——文案段的情绪浓度通常远高于正文。
    """

    #: **段内**字符数（行已 strip，**换行不计**）。body 9516~12131 ｜ hook 139~319。
    #: ⚠ 全链有**三个** `chars` 口径，别拿它跟另外两个比：
    #: 这一个**去空白** · `stage5.chNN.chars` **含空白** ·
    #: `stage7.book_txt.chars` **含空白且含章号行与块间分隔符**。
    chars: int = Field(ge=0)

    #: 段落 = 非空行。
    paragraphs: int = Field(ge=0)

    #: 按 `。！？!?…` 切，**下限 1**。
    sentences: int = Field(ge=1)

    avg_paragraph_chars: float = Field(ge=0)

    #: **段落分隔符**的平均占字。body 2.03~2.05 ｜ hook 1.75~1.87。
    #:
    #: ⚠⚠ **它必须存，因为它是一次跨口径运算的补丁**：`chars` / `avg_paragraph_chars`
    #: 数的是**段内**字符（不含换行），而下游的篇幅预算数的是 `len(整章文本)`、**含分隔符**。
    #: 两个口径差约 14%——拿 budget 直接除以 `avg_paragraph_chars` 求段落数，
    #: 会系统性多要一成半的段落（实测成稿 704 段 vs 原文 691 段，而成稿只有原文的 0.96 长）。
    #: ⚠ 它自己带一个**未修的 off-by-one**：分隔字符总数除以**段数**，
    #: 而分隔符只有「段数 − 1」个 ⇒ **段数 ≤ 3 的文本会被判反**。
    #: stage6 因此另起 `sep_band_of_text` 按字节取档位、**不读这个字段**。
    #: 不改分母是因为它被 stage3/4/5 一路消费，改了会动到所有书的所有产物指纹。
    paragraph_sep_chars: float = Field(ge=0)

    avg_sentence_chars: float = Field(ge=0)

    #: 引号跨度字符占比。body 0.277~0.358 ｜ hook 0.164~0.496。
    #: **无引号体时为 `null`**（键恒在）。
    dialogue_ratio: float | None

    #: `False` ⇒ 上一项不可信、**不进任何一致性判断**。
    #: ⚠ **盘上 3/3 恒 `True`，`False` 那一支零样本。**
    dialogue_ratio_applicable: bool

    #: 见过的引号体，按**实际成对跨度**判定（孤立一个引号不算）。
    #: 候选四种：直角引号 · 全角双引号 · 双层直角引号 · 半角双引号。
    #: ⚠ **盘上恒长度 1**，只见前两种——**后两种零样本**。
    quote_styles: list[str]


class MeasurableBody(MeasurableBlock):
    """`body` 独有的两项。"""

    chapter_chars: ChapterCharStats
    per_chapter_avg_sentence: PerChapterSentence


class Measurable(StrictModel):
    """★ **代码算的，不是 LLM 产。** body 14 项 + hook 9 项。

    ⚠ **档位词不是字段，是现算的**：`bands()` 是本块的纯函数，**不落盘**，两个消费方各算一次。
    ⇒ 盘上只有这一份原始数字，**删它的任何一项都会同时打掉两侧**。

    ⚠ **数字怎么下发，两侧故意不一样**（`R-4.3` / `R-4.3b`）：
    抽取侧（stage3 自己的 prompt）**只发档位词，一个数字都不发**——
    既给「权威数字」又禁止复述数字是一对**自相冲突的指令**，模型只能靠复述数字
    来证明自己遵守了自洽要求；写作侧发档位词 **＋ 3 个逐字写死的原始数值**
    （`avg_sentence_chars` · `avg_paragraph_chars` · 明喻每千字）。
    ⚠ 那三个数**逐字写死**：`body` 有 14 项，只写「写作侧可以发实测数值」，
    那 14 项里发哪几项就成了**自由变量**——同形的坑踩过一次
    （写法词表在 prompt 里手抄了一个子集，10 个词只提了 2 个）。

    ⚠ **逐章明细不落盘**：`measure()` 是逐章跑的，但只有聚合值 +
    `chapter_chars` + `per_chapter_avg_sentence` 进产物。逐章明细无消费方。
    """

    body: MeasurableBody
    hook: MeasurableBlock


# ─────────────────────────────────────────────────────────────────────────────
# 五、`_provenance`（本层特有的部分）
# ─────────────────────────────────────────────────────────────────────────────

class StyleAttempt(StrictModel):
    """一次尝试。盘上长度 1~2，共 4 条（3 `initial` + 1 返工）。

    ⚠ **本节点的真实调用数恒 ≥ 1，不可能靠缓存白跑**：表层习惯引原句 **5/5 本全犯**，
    而带反馈重抽那一次不读缓存。重跑路径恒为
    「首抽命中缓存 → 返回同一份违规稿 → 必然重抽 → 重抽不吃缓存」。报预算按「1~2 次」。
    """

    kind: Literal[STAGE3_ATTEMPT_KINDS]
    user_prompt: Fp10
    output: Fp10
    cache_policy: Literal[CACHE_POLICY]

    # ── 以下四个**只有返工那一条有**（盘上 1/4） ────────────────────────────

    input: Fp10 | None = None

    #: 被改写的字面清单的指纹。
    evidence: Fp10 | None = None

    evidence_count: int | None = None
    model: str | None = None
    code: str | None = None


class StyleProvenance(LlmProvenance):
    """`style_grounding._provenance`。

    `inputs` 恒三条：`stage1/main_text.json` · `stage1/hook_copy.json` ·
    `stage2/global_grounding.json`。
    ⚠ 第三条只被**校验期**用（取人物名表），**抽取本身不吃 stage2**——风格与情节正交。

    ⚠⚠ **这三个键（`attempts` / `revalidated` / `gates_skipped`）今天一个出口都没有**：
    实跑三本 `--check-only`，报告里一行都没有。而它们记的恰好是最该被人看见的三件事——
    「这份稿是第几次才产对的」「这份稿是跑出来的还是被重判提升上来的」
    「哪几条阻断级判据当时没跑成」。
    第三个尤其要紧：它是那整套设计的**唯一痕迹**，而「闸门没跑成」与「闸门跑过没事」
    正是 `Q-2` 要求必须能区分的两件事。⇒ 按 `Q-1` 的出口规则补（细则见 `stage2.md` §3.3）。

    ⚠ `gates_skipped` / `revalidated` 继承自 `Provenance`（`_common.md` §五），
    **不在这里重定义**——同一个语义两个落点必然分家。
    """

    attempts: list[StyleAttempt] = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 顶层
# ─────────────────────────────────────────────────────────────────────────────

class StyleGrounding(StrictModel):
    """`stage3/style_grounding.json`。"""

    voice: Voice

    #: **非空**（空数组是 `HARD`：「这本书一个写作小动作都没有？多半是没抽到」）。
    #: 盘上 5 / 5 / 6 条，单条长度 36~58。
    #: 每条 =「什么类别的词或句式 + 放在什么位置 + 造成什么效果」。
    #:
    #: ⚠ **这个字段有一条只属于它的 `HARD`**：`habit_quote`——**不许出现引号内的具体词例**
    #: （不论是否命中原文、也不论长短；纯结构模板除外）。理由是消费侧的：
    #: 写作节点把它当**必须照做的手法清单**，短词例与长原句一样会被当成口头禅反复复用。
    #: 它 5/5 本触发**不是判据错**——是模型确实 5/5 本都在举词例。
    #:
    #: ⚠ 它还是本层**唯一有定向返工通道**的字段（`HABIT_REPAIR_SYS`），
    #: 而**返工本身超范围了**（`R-4.10`，实测）：判据只要求删 53 字（8 处引号词例，
    #: 一个都没抓错），而返工**另外多删 42 字**（占删除总量 44%），包括
    #: `打脸`/`清脆感` 这类**通用写法描述**——正是这个字段该有的东西。
    #: ⇒ **移植时返工 prompt 要收窄成「只替换引号内的字面，其余一字不动」。**
    surface_habits: list[str] = Field(min_length=1)

    hook_style: HookStyle
    measurable: Measurable

    provenance: StyleProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _no_empty_surface_habit(self) -> StyleGrounding:
        if any(not h.strip() for h in self.surface_habits):
            raise ValueError('surface_habits 里有空条目——空条目会被下游当成一条要照做的手法')
        return self
