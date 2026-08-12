#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage1.py —— 对应 `docs/schema/stage1.md`。分章。**不调 LLM，也不切任何非正文。**

【本层与别层不同的两条，读模型前先知道】

  ① **承重的只有 `main_text.json` 与 `hook_copy.json` 的字节形态。**
     下游每一份产物记的来源指纹都是对它们算的；改动这两份（哪怕只是重排一个键）
     会让全部下游产物被整批报成「陈旧」——**那是一次假回归**。
     `partition_report.json` **不承重**：0 下游消费方，增删字段都不造成失配。

  ② **这两份产物在前端是只读的，stage1 切完就钉死。**
     分章算法是确定性的：切错了就是算法错了，去改算法、重跑，不在界面上手改数据。
     ⇒ 本层没有 `origin=human` 那套回写路径，也没有 `_provenance`。

【本层的样本面是全语料 163 本，不是盘上那 3 本】
`build_sections` / `build_report` 是纯函数，取样**不落盘** ⇒ 全语料扫一遍是 0 成本的。
所以本文件注释里的「实测」可信度**系统性高于** stage2~stage7 那几份。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel, model_validator

from contracts.invariants import MIN_CHAPTERS, STAGE1_WARNINGS
from contracts.schema.base import Sha1, StrictModel

#: 码点半开区间。两处用它：文案段与逐章。
Span = tuple[int, int]


class MainTextSection(StrictModel):
    """`main_text.json` 的一条。"""

    #: 形如 `sec_0001`，长度恒 8。
    section_id: str = Field(pattern=r'^sec_\d{4}$')

    #: **修正后的阅读序**：恒连续、无重复。**下游一律 key 在它上面。**
    chapter_ordinal: int = Field(ge=1)

    #: **原文标号，原样保留**，不修正。全语料 **28/163 本有重号**（单本最多 16 个）·
    #: **12/163 本有缺号**——「原样保留 + 另给一个修正序」这一对字段共存**就是记账**
    #: （`R-1.4`），派生的三个汇总由校验器现算、不落盘。
    #:
    #: ⚠ **必填非空，这是本 schema 对代码的一次收窄**：产地写的是
    #: `str(chapter_no) if chapter_no is not None else None`，类型上可空。
    #: 收窄有三重依据——结构上 `chapter_no` 恒由 `CHAPTER_RE` 匹配出的数字行解析得到
    #: （这条路上没有产生 `None` 的分支）· 全语料 **2559/2559 段实测非 `None`** ·
    #: 写盘前逐段断言。
    #: ⚠ **`None` 确实存在，只是不在这个字段上**：163 个 `hook_copy` 段的 `chapter_no`
    #: 全是 `None`，但 hook 段不进本产物。**「盘上见过 `None`」不是「这个字段可空」的证据。**
    source_chapter_label: str = Field(min_length=1)

    #: 章正文。
    text: str


class MainText(RootModel[Annotated[list[MainTextSection], Field(min_length=MIN_CHAPTERS)]]):
    """`stage1/main_text.json` —— 顶层是**数组**。

    ⚠ **长度恒 ≥ `MIN_CHAPTERS`(2)**（`R-1.13`）：只有一章的「书」不接受重构，入显式终态。
    全语料 2~39 章；**163 本里正好 10 本被拒收**（10670~28223 字），
    核过原文——它们确实只有一个 `1` 标记、之后再没有数字行，**真·未分章，不是漏切**。
    ⚠ 这条不带字数阈值：原型那条是「≤1 章且 >3000 字才告警」，而语料里最短的书也有
    8088 字，那个阈值**从来没起过作用**；留着它只会让判据看起来像标定过。
    """

    @model_validator(mode='after')
    def _ordinals_are_dense_from_one(self) -> MainText:
        """`chapter_ordinal` 从 1 起连续无重复（`R-1.4`，全语料 163/163 成立）。

        它是**修正序**，所以这条是产地的后置条件而不是对数据的期望：
        红了说明分章器坏了，不是说明这本书特别。
        """
        got = [s.chapter_ordinal for s in self.root]
        want = list(range(1, len(got) + 1))
        if got != want:
            raise ValueError(f'chapter_ordinal 不是从 1 起的连续序列：{got[:20]}…')
        return self


class HookCopy(StrictModel):
    """`stage1/hook_copy.json` —— 开篇文案段（第一个正文章标记之前那段无标题内容）。

    ⚠⚠ **顶层不可为 `null`，「文案段为空」不是一个合法分支**（`R-1.12`）：
    认不出文案段时 stage1 直接入显式终态，**不写任何产物、后续 stage 一步都不跑**。

    理由不是「163 本都非空所以大概不会空」（那是拿样本当结论），是**下游三层都要它**：
    stage3 拿它产 `hook_style` 七项 · stage6 的三候选参照它的写法与收尾形态 ·
    stage7 成书的第一段就是它。
    ⇒ **带着一个 `null` 往下跑，等于把一次本该在 0 成本层红掉的失败推迟到花钱之后。**

    ⚠ 连带：`R-4.6`（stage3「无文案段时该字段为空并显式报警」）的分支因此**不可达**，
    stage3 的 `hook_style` 已相应收窄成必填。
    """

    #: 恒 `sec_0001`。
    section_id: str = Field(pattern=r'^sec_\d{4}$')

    #: 非空。全语料长度 57 ~ 414。
    text: str = Field(min_length=1)


class Stage1Warning(StrictModel):
    """一条可疑项。落点是 **S-13 校验报告里 `stage1` 那一份的 `info` 级结论**——
    `R-1.9`（不静默、不自动修正、不硬报错）靠这条路兑现，
    **写进文件没人看才是真的静默**。

    ⚠ **认告警一律用 `kind`，不许用措辞前缀。** 措辞不承重（逐字节等价那条验收
    不覆盖 `partition_report`），随时可改；拿字符串开头去认告警的代码
    会在第一次改文案时静默失效。
    """

    kind: Literal[STAGE1_WARNINGS]
    message: str = Field(min_length=1)


class PartitionReport(StrictModel):
    """`stage1/partition_report.json` —— **9 个字段**（原型 24 个）。

    删掉的 15 个全部可由 `main_text.json` / `hook_copy.json` / 目录名 / 原文现算。
    留下来的判准是**不可再生**。

    ⚠ 本产物**没有 `_provenance`**：它不承重、没有下游消费方，而它自己记的两个指纹
    已经把「照什么产的」答完了。
    """

    # ── 3.1 两个指纹 = `R-0.6` 的下半段 ──────────────────────────────────────

    #: **原始 txt** 的 sha1，从 stage0 透传。只用于追溯，**本层不判它**。
    source_sha1: Sha1

    #: **本层输入**（`stage0/clean_text.txt`）的 sha1。**[HARD]** 对不上即阻断（`R-1.3`）。
    #:
    #: F6 已用临时盘上产物与 `Test_data` 真实小说走通；原型存量
    #: `partition_report` 仍记的是旧摄入元数据，不能拿来冒充这一字段的历史样本。
    input_sha1: Sha1

    # ── 3.2 规模与分章 ──────────────────────────────────────────────────────

    #: 全语料 8088 ~ 41224。**[HARD]** 与复算的原文长度交叉核对（`R-1.3`）。
    total_code_points: int = Field(ge=1)

    #: `[0, body_limit)` = 文案 + 正文，即保留下来的部分。
    #:
    #: ⚠ 它与 `total_code_points` **不是为了算 `body_ratio`**——那条判据在 stage0
    #: （`R-0.9`），本层不剔除、切点只由完结标记决定 ⇒ `body_ratio` 恒 ≈1.0，
    #: 留在这里就是**一盏保证会亮的绿灯**。两个数留着是给 `R-1.3` 的交叉核对
    #: 与 `R-1.14` 的**三段式**记账用的。
    body_code_points: int = Field(ge=0)

    #: **≥ 2**（`R-1.13`）。全语料 2 ~ 39。**[HARD]** 必须等于 `main_text` 的长度（`R-1.5`）。
    #:
    #: ⚠⚠ **必须由分章那一趟独立数出来，不许写成 `len(sections)`** ——这是它存在的
    #: 唯一理由。`R-1.5` 防的是「两行式番外造出一个被跳过的空章」（认出 9 章、
    #: 只写进去 8 条）；取自 `len(main_text)` 的话这条判据就成了恒等式、
    #: **永远不可能红**，而它要抓的那个 bug 会原封不动地活下来。
    #: ⇒ 产地一侧的计数来源与写盘来源**必须是两条路**。
    num_chapters: int = Field(ge=MIN_CHAPTERS)

    #: 全语料 `True`×46 · `False`×117。**它是 `R-1.14` 的前件**：
    #: 三段式记账的第三段（切点之后、第一个非正文标记之前）**只在它为 `True` 时非空**。
    has_end_marker: bool

    # ── 3.4 可信度层 ────────────────────────────────────────────────────────

    #: **本层只剩一类**（`noisy_duplicate`）。⚠ **「只剩一类」不等于这一层变安全了**：
    #: `early_cut`/`drop_ratio`/`long_marker` 随剔除搬去了 stage0 ·
    #: 「只认出 ≤1 章却很长」升成了 `R-1.13` 显式终态 ·
    #: 「完结标记之后还有内容」升成了 `R-1.14` 的 HARD 判据。
    #: ⚠ 但本层现在**没有零样本的告警分支**：`noisy_duplicate` 有真实语料样本
    #: （`小三争夺战`：25 章，章号 1 出现 17 次）。
    warnings: list[Stage1Warning]

    # ── 3.5 对照屏用的两个区间 ──────────────────────────────────────────────
    #
    # **为什么不加进 `main_text.json`**：那是最自然的位置（span 是章的属性），
    # 但改 `main_text` 的字节形态会让全部下游产物被整批判成陈旧——那是一次假回归。
    # `partition_report` 不承重，加字段没有任何代价。
    #
    # **为什么它们不属于「能现算所以该删」**：重跑分章确实能算出同样的 span，
    # 但那样一来对照屏画的就是**分割器自己和自己对账**，看不出任何问题。
    # 拿**落盘时记下的** span 去比**此刻盘上的**原文，才是一次真的核对：
    # 原文被换过、被截断、编码变了，都会在屏上直接错位。

    #: 文案段在归一化原文上的码点半开区间。恒 `[0, x)`。F6 已临时跑盘。
    hook_span: Span

    #: 逐章。**长度恒 == `num_chapters`**，顺序即 `chapter_ordinal`。F6 已临时跑盘。
    chapter_spans: list[Span]

    @model_validator(mode='after')
    def _spans_are_consistent(self) -> PartitionReport:
        """三条后置条件。它们查的是**本产物内部**是否自洽——
        跨产物那几条（与 `main_text` 的长度、与盘上原文的复算值）归判据，不归载体。
        """
        if self.hook_span[0] != 0:
            raise ValueError(f'hook_span={self.hook_span} 不是从 0 起——文案段恒在最前')
        for name, (lo, hi) in [('hook_span', self.hook_span),
                               *((f'chapter_spans[{i}]', s)
                                 for i, s in enumerate(self.chapter_spans))]:
            if not 0 <= lo < hi:
                raise ValueError(f'{name}=({lo}, {hi}) 不是合法的半开区间')
        if len(self.chapter_spans) != self.num_chapters:
            raise ValueError(
                f'chapter_spans 有 {len(self.chapter_spans)} 条而 num_chapters='
                f'{self.num_chapters}——对照屏会按 span 画高亮，少一条就少画一章'
            )
        return self
