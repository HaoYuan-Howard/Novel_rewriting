#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage7.py —— 对应 `docs/schema/stage7.md`。人名替换与成书导出。**全链最后一层。**

```
stage7/name_map.json              LLM 产（两个键）+ 确定性展开 + 判据
stage7/_rounds/name_map.rN.json   归档：上一轮的整份拷贝
stage7/name_map_edit.json         ⚠ 不是节点产的，是【人产的输入】
stage7/book.txt                   ★ 交付物。纯文本，带不了 `_provenance`
```

【当前契约的关键边界】

  ① **模型只写两个键**（`surname_map` 与 `characters[]`），其余全是代码算的。
     ⇒ **本层的零消费审计比前几层可靠**：stage4 那类假阴（整份 dump 进 prompt，
     grep 报 0 下游而其实全在用）**在本层结构上不存在**——
     下发材料由 `build_material` 从 registry 现造，**它不读本产物的任何一个字段**。

  ② **中文没有词边界 —— 这是本节点全部的风险，也是三道防线的由来。**
     第一道（保留项进 alternation 当**恒等条目**）之所以必须是**结构性**的：
     实测某本 `char_011` 唯一的称呼是**单字「姐」**，而「姐姐」在成稿里有 4 处。
     「姐」是**单主**，多主判据抓不到它——只剩一道**词表**防线，
     而验收判据不该用词表。**把「姐姐」编进正则，这件事就与词表判得准不准无关了。**

  ③ `rn_new_collide` 校验模型人物决定；人工 `extra_subs` 的碰撞由
     `rn_new_collide_human` 单独报出，避免把人工确认过的同人映射误判为模型错误。

【判据 vs 载体：一条都不上移，而本层的两个理由叠加】
拒收 ⇒ 本层**唯一那次付费调用**整个作废（stage5 的理由）；
`--apply-edit` 收**完整决策集**，人直接改对，0 成本（stage6 的理由）。
⇒ 与 stage4 相反，本层**不给响应侧开 `extra='forbid'`**：模型多写一个键在这里不会坏事
（`build_name_map` 只挑它认识的键），而拒收要再花一次钱。**但不许静默——多出来的键要报一行。**
⚠ **唯一该归载体的是人写的编辑文件**（`NameMapEdit`）：拒收的代价是人改一行，不是再花一次钱。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import RESIDUAL_CONFIDENCE
from contracts.schema.base import Fp10, Sha1, StrictModel
from contracts.schema.common import (
    Finding, FreeProvenance, HashBoundHumanApproval, LlmProvenance, Sha256,
)
from contracts.schema.stage5 import ContextSource, JudgedDraft


class Alias(StrictModel):
    """一条别名映射。盘上 136 条，其中 **88 条 `old == new`**
    （＝这一条不换，**是决定不是错误**）。

    ⚠ **canonical 不得在这里再列一遍**——实测 registry 的 `address` 重复收了 canonical，
    模型于是同时交出 `new_canonical` 与一条同名 alias，被判成「一旧名两新名」。
    """

    old: str = Field(min_length=1)
    new: str = Field(min_length=1)


class NameDecision(StrictModel):
    """一个角色的改名决定。**模型写的那一半**（`old_canonical` 除外）。

    长度必须与 registry 一一对应——多一个少一个都是 `rn_llm_shape`（HARD）。
    """

    #: **必须 ∈ registry**（盘上恒 8 字符）。
    char_id: str = Field(min_length=1)

    #: **代码从 registry 回填**，不是模型写的。
    #: ⚠ `--apply-edit` 的 patch **显式禁止改它**（不在可编辑白名单里）。
    old_canonical: str = Field(min_length=1)

    #: `renamed=false` 时原样填旧名。`renamed=true` 时可因「描述 canonical + 带姓 alias」
    #: 保持 canonical，但至少一条 alias 必须真实变化；canonical/alias 全恒等由
    #: `rn_llm_shape`（HARD）拦住。盘上长度 1~8。
    new_canonical: str = Field(min_length=1)

    #: 盘上 32/75 为真。
    renamed: bool

    #: ⚠⚠ **这正是那条 Pydantic 默认值警告的实例**：
    #: 「没有这个字段」「字段是 `null`」「字段是空串」在盘上**同时存在**
    #: （`renamed=true` 时盘上有 `null` 9 个与 `''` 23 个两种「没有」），
    #: 而 `rn_no_reason` 只查 `renamed=false` 的那一侧 ⇒ **另一侧的两种拼法谁也没在看**。
    #: ⇒ 契约收窄成：**键恒在**，`renamed=true` 时**必须是 `null`**（不是空串、不是缺席）。
    #: 它是**代码回填**的位置，不是模型的自由度 ⇒ 归载体（下面那个 validator）。
    #:
    #: ⚠ **旧注释把这条契约写下了，但一直没有人实现**：`_build` 只是
    #: `c.get('not_renamed_reason')` 原样透传，于是盘上两本 flash 书
    #: **7/7 个 `renamed=true` 角色全是空串**。归一现在落在
    #: `novel/stages/stage7/rename.py::_build`（写之前先归零），载体在这里兜底。
    not_renamed_reason: str | None

    #: `old` 必须与该角色**实际下发**的 alias 精确一一对应：
    #: 不漏、不多、不重复、不串到别的 char_id。这是 `rn_llm_shape`
    #: 的判据语义，故意不收进 Pydantic：旧 INVALID 还要能进 0-LLM 编辑恢复口。
    aliases: list[Alias]

    #: 一句话说明新名为什么贴合。盘上 0~39 字；**空串 11 条，全部落在 `renamed=false`
    #: 的角色上**（那些角色的理由在 `not_renamed_reason` 里）。
    #: ⚠ **0 代码读者但不是孤儿**：界面渲染它，**它是人定稿时唯一的判断材料**。
    reason: str

    @model_validator(mode='after')
    def _reason_side_matches_renamed(self) -> NameDecision:
        """`renamed=true` 那一侧**只准是 `null`**。

        ⚠⚠ **只收这半边，另半边故意留给 info 判据。** 「`renamed=false` 却没写理由」
        由 `rn_no_reason`（info）报——把它也搬进载体，模型漏写一条理由就会让
        **本层唯一那次付费调用整个作废**，而本层的产品事实是「拒收＝再花一次钱」
        （见模块头「判据 vs 载体」那一段）。
        这半边不同：它**不是模型的自由度**，`_build` 落盘前一律归零 ⇒
        归一之后这个 validator 结构上不可能因模型的写法而红，
        它挡的是**人手写的编辑文件**和**代码自己写错**，两者的代价都是改一行。
        """
        if self.renamed and self.not_renamed_reason is not None:
            raise ValueError(
                f'{self.char_id}: renamed=true 时 not_renamed_reason 必须是 null，'
                f'收到 {self.not_renamed_reason!r}——'
                f'「不该改的理由」挂在一个改了名的角色上是自相矛盾，'
                f'而空串与 null 在盘上曾同时存在、谁也没在看')
        return self


class ExtraSub(StrictModel):
    """补充映射。盘上 4~12 条、共 21 条。

    ⚠⚠ **它是人产的输入直接落进产物，而全链没有一处校验它的形状**：
    合并函数把它原样传出、`build_name_map` 原样 `list(...)` 落盘，展开器只读三个键。
    **多写的键静默搭车进产物。** ⇒ 本类与 `NameMapEdit` 都要**用载体收**（`extra='forbid'`）。
    这一条**不违反「判据不上移」**：它挡的是**人手写的编辑文件**，
    拒收的代价是人改一行，不是再花一次钱。
    """

    old: str = Field(min_length=1)
    new: str = Field(min_length=1)

    #: 这条是**谁加的**。⚠ 旧注释写着「盘上恒 `'human'`」并预言
    #: 「`residual.suggest` 一旦有第二条来源，这个字段就是唯一能分开的地方」——
    #: **那第二条来源一直都在，只是记了假话**：`--adopt-residual high`
    #: （`novel/stages/stage7/rename.py::run_adopt_residual`）把探针算出来的
    #: `unit → suggest` 直接写进 `extra_subs`，却硬编码成 `'human'`。
    #: 实测某本 4 条 `孙家/孙府/宋家/宋府` 全是这么来的，产物里却说是人一条条敲的。
    #:
    #: · `human` —— 人在 `name_map_edit.json` 里手写的。
    #: · `adopted` —— 人跑 `--adopt-residual high` 批量采纳的探针高置信建议。
    #:
    #: ⚠ **两者在判定上同权，这是有意的**：`rn_new_collide_human` /
    #: `rn_shared_surface_human` 豁免的语义是「**有人看过并决定这么做**」与
    #: 「没人看过」必须分得开，而 `--adopt-residual` 是人显式敲的一条 CLI，
    #: 不是静默自动路。把 `adopted` 降回 HARD，唯一出路会变成撤销一个对的决定
    #: （正是 `rn_new_collide_human` 那条 note 记着的失败形状）。
    #: 这一格分开记的是**审计**，不是权限。
    origin: Literal['human', 'adopted']

class AppliedFace(StrictModel):
    """一个替换面的记账（逐章 / 文案）。

    ⚠ **`counts` 的口径不能用 `str.count`**：称呼之间大量嵌套，`count` 会把长称呼的字节
    重复计给短称呼（实测「阿禾」`count`=4 而独立命中只有 **1**，另外 3 次在「阿禾姊姊」里）。
    落盘的这份数来自**单次扫描的实际命中**，与下发给模型的 `aliases[].count` 同源。
    ⚠ **恒等条目不计数**（屏蔽不算替换）。
    """

    counts: dict[str, int]


class Applied(StrictModel):
    """三个替换面各自的记账。

    ⚠ **整块都是 `(成稿, subs, kept)` 的确定性函数**，可 0 成本重算。
    留着的理由不是「算不出来」，是它**记的是那一次替换的实况**——
    而成稿变了没有靠 `judged` 兜（`R-10.9`）。

    ⚠ **已裁撤**（2026-08-11 用户裁决，`stage7.md` §九 待决 ①②）：
      · `chapters{*}.chars_before` / `chars_after` —— 唯一读者是 `rn_report`（metric），
        而它要的两个总数可以直接写进 finding 的 `detail`。
        **替换不改篇幅**（3/3 都是 1.000×~1.001×），信息量本就接近 0。
      · `title` 整块 —— 盘上 3/3 **全空**，而且三个平行块**唯独它缺两个字段**。
    """

    #: 键是 `output_ordinal` 的字符串形式。
    chapters: dict[str, AppliedFace]

    hook: AppliedFace


class Residual(StrictModel):
    """一条漏网候选。盘上正式产物 19 条 + 归档 104 条。

    ⚠⚠ **`residual[]` 的顺序是承重的**（与 `subs` 相反）：排序键是
    `(confidence != 'high', -ratio, stem, where)`，设计口径是
    **「人从上往下读到覆盖率掉下去为止」**。
    ⇒ **schema 必须把顺序写成契约的一部分，不能当成装饰。**

    ⚠ **残留探针捡回来的是哪一类东西——这是一条被四处抄过的误读。**
    旧文档写着「她改了名而她的家人原样留着」，逐条复算**三处不成立**：
    是**他**不是她 · **是同一个人**不是家人 · 漏收的是**同一个角色的第二种写法**
    （原作自己把「颜」写成了「辕」，registry 里含「辕」的条目 0 条），
    **不是**「人物表漏收了一个角色」。
    **结论不变，改的是它捡回来的是哪一类东西**——两者的修法不同
    （前者补一条同目标映射，后者要补整个角色）。
    """

    #: 单个姓字。**取值面 = 「原文有据的姓」∪「`surname_map` 的键」**（`R-10.6`）——
    #: 少了后者有盲区。
    stem: str = Field(min_length=1)

    #: `ch<N>` ｜ `hook` ｜ `title`。
    where: str = Field(min_length=1)

    #: 命中处起 **3 个字**（恒 3 字）。
    hit: str = Field(min_length=1)

    #: 前后各 12 字（恒 24 字），换行已压成空格。**只影响可读性，不是门。**
    context: str

    confidence: Literal[RESIDUAL_CONFIDENCE]

    #: 建议替换对的左边（`沈家`）。没有下一个字时 `null`（键恒在）。
    unit: str | None

    #: 建议替换对的右边（`林家`）。`surname_map` 里查不到新姓时 `null`（键恒在）。
    #: **只为省一次敲字，不参与任何判定，绝不自动生效。**
    suggest: str | None


class StemBaseline(StrictModel):
    """一个姓字的覆盖率。**残留为 0 时也必须输出。**

    ⚠ **分母 `total` 量在替换前**——比率的分母要是被测过程改不动的量。
    ⚠ **这个判别器会反向**：`ratio = (total − residual) / total`，
    **漏掉的形就是该姓的主用法时，分母里装的主要就是分子**，比率自我压低。
    ⇒ 两个信号打架时**以 `confidence` 为准**（结构信号，不受这个反馈回路影响）。
    """

    total: int = Field(ge=0)
    covered: int = Field(ge=0)
    residual: int = Field(ge=0)

    #: ⚠ **`total == 0` 时是 `null`，不是 `0.0`**（`Q-2` 的三态在这一格的样子）。
    #: 那种 stem 只可能来自 `surname_map` 的键而原文里一次都没出现过（模型写错了一个姓）。
    #: 压成 `0.0` 会被读成「覆盖率 0%」——**在这张按覆盖率排序的表上，那是最坏的读法**：
    #: 一个根本不存在的姓会被摆到「灾难性漏网」那一端。
    #: 盘上 11 份产物 0 处 `null`（三本的 stem 全部有据），**这一支是零样本分支**。
    ratio: float | None = Field(ge=0, le=1)


class ApprovedNameMap(HashBoundHumanApproval):
    """人工定稿；凭据绑定审批时看到的决策 surface。没有 auto approve。"""

    origin: Literal['human']
    #: 终审人实际看到的紧凑工作包；旧批准缺失时只保留历史可读性，不能视为
    #: 当前工作包已审。
    workpack_sha256: Sha256 | None = None


class BookTxt(StrictModel):
    """交付物的账。缺省 `null`（还没组装）。

    成书的字节形态（换行只有 LF · 章分隔＝**裸阿拉伯数字单独成行** ·
    文案在最前 · 没有任何审阅元信息 · 原子落盘）由写盘口那一层守，不在这里。
    """

    #: **回查盘上那份的唯一依据**——txt 带不了 `_provenance`。
    #:
    #: 40 位内容身份，回答「有人换过交付物吗」；可直接与 `sha1sum` 手工复核。
    sha1: Sha1

    #: 盘上 11105 / 11807 / 13308。
    #:
    #: ⚠⚠ **全链第三个 `chars` 口径**：stage3 `measurable` **去空白** ·
    #: stage5 `chNN.chars` **含空白** · 本字段**含空白，并且含章号行与全部块间分隔符**。
    #: 精确公式（实测三本逐字吻合）：
    #: `chars = len(文案) + Σlen(各章正文) + 2×(2n) + Σ位数(1..n) + 1`。
    #: ⇒ **别拿它和另外两个 `chars` 做任何比较**——三个量会**并排出现在同一个人的视野里**
    #: （stage5 页 / stage6 页 / stage7 页）。
    chars: int = Field(ge=0)

    chapters: int = Field(ge=1)

    # ⚠⚠ **`has_hook` 已裁**（2026-08-12 用户裁决，`stage7.md` §九 待决 ④）。
    #    骨架 A3 保留过它，理由是「先查再裁」——查的结果是**不可达**：
    #    `R-10.14` 规定「文案未定稿一律拒写 txt」，那道闸挡在前面
    #    ⇒ 结构上只剩「拒写」与「有文案」两种结局，`false` 这一支走不到。
    #    照 `op` 的先例（一个恒等于常量的字段）删掉。
    #    ⚠ 它同时是一次**产品语义**裁决，不只是清理：**「不允许出一本没有开篇文案的书」**。
    #    将来要允许，得改一次契约（加回字段 + 给 `stage7.assemble` 一条显式的无文案路
    #    + 配一份会红的样本），**这个代价是有意接下的**。


class IdentityBaseline(StrictModel):
    """恒等基线的结论（`R-10.7` / `R-10.7b`，**F2 新增**）。

    【为什么要有这个键】原型的 `--identity` 整个输出是 `print(...)` + 返回码
    ⇒ **「跑过且逐字节无损」与「从没跑过」在盘上长得一模一样**（`Q-2`）。
    而它是本层**最该先点的一条**：替换器在恒等下不逐字节守恒时，后面每一分钱都白花。

    ⚠ **不叫 `baselines_identity`**：本层已经有一个 `baselines`（姓字覆盖率，`stage7.md` §1.4），
    同名前缀会让两件毫无关系的事读起来像一族。

    ⚠ **不加「什么时候跑的」字段**——落点是作业事件流（`JobEvent`），
    产物里再记一份就是第二个落点（同 stage6 那次不给 `ApprovedHook` 加 `approved_at`）。
    """

    #: 占位符双射往返之后，正文 + 文案 + 书名**逐字节**等于原样。
    roundtrip_ok: bool

    #: 这一次基线量了几章。
    #: ⚠ **`roundtrip_ok=True` 而 `chapters=0` 是一句空话**——「什么都没量」也会返回 True。
    #: 报告必须把这个分母读出来（判据 31：绿灯要报出自己的分母）。
    chapters: int = Field(ge=0)

    #: 不守恒的位置（`ch<N>` ｜ `hook` ｜ `title`）。`roundtrip_ok` 为真时是空列表。
    mismatches: list[str]

    #: **它验不了的那三样**（`stage7.md` §十）：正向对真名安不安全（占位符天然无歧义，
    #: 真名不是）· 漏网覆盖率 · 新名好不好。
    #: ⚠ 它是常量而不是现算，**但必须跟结论存在一起**——分开放，下一个人就会把绿灯
    #: 读成「替换是安全的」。`R-10.7b` 要的就是这一条。
    blind: list[str] = Field(min_length=1)


class HookConsumed(StrictModel):
    """本节点**真正消费**的文案投影。⚠ **盘上 1/3 本有**——后加的键。"""

    present: bool
    approved_text_sha1: Fp10


class NameMapSources(StrictModel):
    """形状与 stage6 逐字段相同。"""

    writer_context: ContextSource


class NameMapParams(StrictModel):
    """一次调用当时的生成上限。"""

    max_tokens: int = Field(ge=1)


class NameMapProvenance(LlmProvenance):
    """`name_map._provenance`。

    `inputs` 四条：`stage1/main_text.json` · `stage2/global_grounding.json` ·
    `stage4/plan_chapters.json` · `stage4/writer_context.json`。

    ⚠⚠ **这四条边今天一处都没在查**：复检的第 ① 步确实先报来源——但报的是
    `sources.writer_context.state`，也就是**上游的上游**。
    **洞在哪里**：`stage2/global_grounding.json`（整份人物表的来源）改了之后，
    若 ctx **没有**重建 ⇒ ctx 变 stale ⇒ 报出来（今天靠这条兜住）；
    若 ctx **重建过** ⇒ ctx 是 `fresh`，而本层记的那份 stage2 指纹已经过期，**没人看**。

    ⚠ `prompts` 两个键：`HOOK_SYS` 对应的本层是 `RENAME_SYS`，判据表指纹**统一叫 `RULES`**
    （原型这一层叫 `RULES8`）。
    ⚠ `code` 改成 `git describe --always --dirty` 是**全链一处**的改动：
    盘上 11 份产物 `code` 全是同一个短 sha，而同一批的 prompt 指纹取了**三个不同的值**
    ⇒ **代码确实变过三次，而按 git HEAD 算的 `code` 一个字没动。**
    """

    params: NameMapParams


class NameMapFreeProvenance(FreeProvenance):
    """没有模型基线时，由完整人工编辑直接产生的来源账。"""

    params: NameMapParams


class NameMap(StrictModel):
    """`stage7/name_map.json`。

    ⚠ **归档 `_rounds/name_map.rN.json` 与它逐字段同形**（实测 8 份归档与正式产物的
    顶层键集合完全相同）。**归档是本层 `confidence='high'` 分支的唯一样本来源。**

    ⚠ **已裁撤**：
      · `_problems` —— 加载回来 0 读者，而结论已经逐条翻进 `book[]` ⇒ **同一信息两个落点**。
      · `source_id` · `title` —— 目录名是唯一落点。⚠ 复检**不读 `title`**、
        自己重算了一遍（源码注「书名只认目录名，不认命令行怎么敲」）⇒ 同一件事两个落点。
      · `title_new`（待决①）· `applied.title` + `applied.chapters{*}.chars_*`（待决①②）·
        `_groups`（待决③）· `counts` —— 2026-08-11 用户裁决，理由见各自的落点。
      · `judged{*}.mode` —— Stage5 单臂折平后删除；章路径直接绑定
        canonical `stage5/chNN.json`，不保留目录臂字段。
    """

    #: **模型写的。** `{原姓: 新姓}`——本书**全部改名角色**的姓。
    #: 盘上 8/3/9 条，**新姓 ∩ 旧姓 = ∅、新姓无重复**（3/3）。
    #:
    #: ⚠⚠ **旧注释写的是「只覆盖【字面同姓且 ≥2 人】的族群」，那与代码从来不是一回事。**
    #: 族群（`novel/core/naming.py::family_groups`，判据确实是「字面同姓且 ≥2 人」）
    #: 只是这张表里**必须协调一致**的那一部分；`rn_surname_split` 实际 enforce 的是
    #: 「每条映射都要被某个**改名角色**真实使用」，**从不要求它属于某个族群**
    #: （那条 orphan 检查还显式允许「姓只由别名承载」）。
    #: 实测某本 `family_groups` 交出空族群表、模型仍给了 4 条**单人**映射，
    #: 全部合法通过——**盘上的事实一直是宽口径。**
    #:
    #: ⚠ **宽口径是对的，不要回头收窄。** `R-10.6` 的探针把本字段的**键**并进 `stems`
    #: （`novel/stages/stage7/rename.py::_build`），而 `attested_surname` 的五道过滤
    #: 必然漏掉真姓（实测两例：只由自造标签承载的姓、撞上亲属字表的姓）——
    #: 模型这张表是唯一能把它们捞回来的地方，注释里记着漏掉那两个姓的代价是
    #: **6 处旧姓活着进了交付的成书**。收窄这一格＝把那条兜底关掉。
    #:
    #: ⚠⚠ **调用点曾漏声明它是必需键，而它缺席会静默关掉一条 HARD**：
    #: `rn_surname_split` 恒不触发（查表全 `None` ⇒ 候选集恒空）·
    #: `R-10.6` 的探针盲区原样回来 · 亲属称呼判据变严（姓氏豁免失效，「孙大娘」被误判）。
    #: 三处都靠 `or {}` 兜底 ⇒ **一条违约数据的静默通行证。**
    surname_map: dict[str, str]

    #: **模型写的。** 长度 = registry 大小，盘上 12/15/48。
    characters: list[NameDecision] = Field(min_length=1)

    extra_subs: list[ExtraSub]

    #: 代码合成的最终替换表 `{old: new}`。历史三本盘上 15/24/33 条。
    #: ⚠ 落盘顺序是「长的在前」，**但替换器不依赖它**（自己按长度重排）⇒ **顺序是装饰**。
    subs: dict[str, str]

    #: **明确保留**的称呼——不改字节，但**必须进 alternation**（事实②第一道防线）。
    #: 盘上 11/27/59 条。
    #: ⚠ 盘上有**单字条目**（「姐」「哥」「妈」）：最短长度管的是**替换面**，
    #: 不管**屏蔽面**，这是对的。
    #: ⚠ 它可由 `characters` 重算，但它是**第一道防线的载体**，
    #: 重算等于把防线交给调用方 ⇒ **落盘**。
    #: ⚠ 它与 `subs` **保证不交**（换赢）。
    kept: list[str]

    applied: Applied

    #: 漏网候选。盘上 2/2/15 条。**顺序是承重的。**
    residual: list[Residual]

    #: `{姓字: 覆盖率}`。**残留为 0 时也必须输出。**
    baselines: dict[str, StemBaseline]

    #: 缺省 `null`（键恒在）。
    approved: ApprovedNameMap | None

    #: 缺省 `null`（还没组装）。
    book_txt: BookTxt | None

    #: `{成稿相对路径: 引用}`，盘上 31 份。
    judged: dict[str, JudgedDraft]

    #: `stage6/hook.json` 的**整文件**指纹，**只作审计**。
    hook_sha1: Fp10 | None

    #: 文案定稿文本的消费投影；`hook_sha1` 只作整文件审计。
    hook_consumed: HookConsumed

    sources: NameMapSources

    #: 书级结论。盘上恒 **7 条**（1 info + 3 metric + 3 unver），三本**规则集完全相同**。
    book: list[Finding]

    #: 恒等基线的结论（`R-10.7`）。**`null` = 当前流程从没跑过恒等基线**。
    #: ⚠ **键恒在**（`null` 与「键不在」是两件事，`Q-2`）——所以这里**不给默认值**。
    identity: IdentityBaseline | None

    provenance: NameMapProvenance | NameMapFreeProvenance = Field(alias='_provenance')


class NameDecisionResponse(StrictModel):
    """模型写的一条角色决定。**`old_canonical` 不在里面**——它由代码从 registry 回填。

    因此响应载体本身无法比较新旧字面；`_build` 回填之后由 `rn_llm_shape` 要求
    char_id 及逐角色 alias 与实际下发面精确对应，并要求 `renamed=true` 至少
    真实改变 visible canonical 或该角色收到的一条 alias，全恒等不能冒充改名。
    """

    char_id: str = Field(min_length=1)
    new_canonical: str = Field(min_length=1)
    renamed: bool
    not_renamed_reason: str | None
    aliases: list[Alias]
    reason: str


class NameMapResponse(StrictModel):
    """**模型写的那两个键**，`ask_json(model_cls=...)` 收的就是它。

    ⚠⚠ **它与 `NameMap` 不是同一个东西，那正是它存在的理由。** 产物里另外
    十几个顶层键——`subs` / `kept` / `applied` / `residual` / `baselines` /
    `book` / `book_txt` / `judged` / `identity` / `_provenance`——**一个字都不经模型**。

    ⚠⚠ **`require` 从本模型的必需顶层键派生 ⇒ 两个键一个都漏不掉。**
    原型的调用点写的是 `require=('characters',)`，**漏了 `surname_map`**。
    漏传不报错，它只是让三处 `or {}` 兜底同时生效，而那三处的后果各不相同：

    | 后果 | 机制 |
    |---|---|
    | `rn_surname_split`（HARD）**恒不触发** | 查表全 `None` ⇒ 候选集恒空 ⇒ 条件恒假 |
    | `R-10.6` 的探针盲区**原样回来** | `stems` 少了 `surname_map` 的键那一半 |
    | `rn_kinship_touched` **变严** | 姓氏豁免失效 ⇒ 「孙大娘」这类重新被误判 |

    **一条违约数据的静默通行证。** 派生之后这件事**结构上不可能再发生**。
    """

    #: `{原姓: 新姓}`——本书**全部改名角色**的姓；口径与去向见 `NameMap.surname_map`。
    #: 【姓氏族群】先排除完整纯称谓/位号，再按有据姓氏聚类；表中原姓是其中
    #: **一个都不能漏**的子集（同族必须共姓）。
    #: ⚠ **必需键**——见上面那张表。空表是合法的（这本书没有一个改名角色带姓），
    #: 而**键不在**不是（那说明模型没照契约答）。
    surname_map: dict[str, str]

    #: 长度必须与 registry 一一对应，多一个少一个都是 `rn_llm_shape`。
    characters: list[NameDecisionResponse] = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 三、`stage7/name_map_edit.json` —— 人产的输入，也是契约
# ─────────────────────────────────────────────────────────────────────────────

class CharacterPatch(StrictModel):
    """按 `char_id` **浅合并**的一条补丁。盘上 2/3 份有，共 13 条。

    ⭐ **`character_patches` 是全链唯一把「定向返工只许改判据点名的部分」做成数据结构的地方。**
    其他生成面至多在 prompt 里写一句叮嘱，
    **没有任何东西查「其余一字不动」**。这里不一样：不在 patch 里的角色**在结构上改不到**，
    而 `old_canonical` 被显式挡在白名单外。

    它的由来也值得记：旧接口只有完整决策集，实测 48 人的映射表里唯一那条 HARD
    只涉及一个角色的一条重复 alias，却要人复制其余 47 人的决定——
    **0 成本人工出路名存实亡**。
    ⇒ 移植时照搬这个形状，并把它当成 `R-10.15` 的实现范例。
    """

    #: 必须 ∈ 现有 `characters`；**重复即报错**。
    char_id: str = Field(min_length=1)

    # ── 可编辑的五个键。⚠ **`old_canonical` 不在里面**（它由 registry 派生）──────

    renamed: bool | None = None
    new_canonical: str | None = None
    not_renamed_reason: str | None = None
    aliases: list[Alias] | None = None
    reason: str | None = None


class NameMapEdit(StrictModel):
    """`stage7/name_map_edit.json` —— **不是节点产的，是人产的输入**，
    被 `--apply-edit` 当**决策集**吃掉 ⇒ 它的形状同样要定死。盘上 3 份，三本都用过这条路。

    ⚠ **这是本层唯一该归载体的东西**：拒收的代价是人改一行，不是再花一次钱。
    """

    #: **完整决策集**。盘上零样本（三本用的都是 patch 那条路）；
    #: **盘上没有旧映射时它是必填**——否则显式终态。
    characters: list[NameDecision] | None = None

    character_patches: list[CharacterPatch] | None = None

    #: 缺席 = 沿用盘上那份。盘上 1/3 份有。
    surname_map: dict[str, str] | None = None

    #: 缺席 = 沿用盘上那份。盘上 3/3 份有，共 21 条。
    extra_subs: list[ExtraSub] | None = None
