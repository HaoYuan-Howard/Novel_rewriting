#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage6.py —— 对应 `docs/schema/stage6.md`。开篇文案重构。
**全书级单次产出**——一次调用产三个候选，**人定稿**。

```
stage6/hook.json            LLM 产 + 确定性判据 —— 一本书一份，定长
stage6/_rounds/hook.rN.json 归档：上一轮的**整份拷贝**，形状与 hook.json 逐字段相同
```

【六条贯穿本层的事实】

  ① **判据表分成两半，边界是产物类，不是判据质量。** 文案是一个「全文改写」的产物类：
     查连续文本的字面探针在它身上**结构性失效**，查离散符号的探针仍然有效。
     两条 0 成本实测把这条边界钉死了：`secret_keywords` × 原作文案 = 0 命中
     （而書2 的原作文案确实把一条 secret 讲了出来）· 「文案引语必须能在正文里找到」
     曾是 HARD，恒等基线当场判死（两本原作文案共 7 条引语在**原作全书**里逐字命中 0 条）。

  ② **现役的剧透闸门是【人定稿】那一步，不是代码。** 这句话一直印在产物的三条 `unver` 里，
     别读成谦虚：**5 条 HARD 里没有一条在查「文案有没有替故事把话说完」。**

  ③ **三条 0 成本写盘路全部重盖来源账 ⇒ 伪造**，盘上有逐字节铁证：
     `_rounds/hook.r1.json` 的 `prompts.HOOK_SYS` 与正式产物**是两个值**，
     而三个候选的 `text` / `findings` **逐字相同**。
     ⇒ 新骨架由 `LlmProvenance.promoted()` 在**结构上**堵死（签名里没有 prompts/model/inputs）。

  ④ **`_provenance` 缺 `model` 与 `params`，而这是一个付费节点**——
     换了模型、换了 `--max-tokens`，产物里没有任何一处说得出来。
     ⇒ 本模块把两者定成**必填**；盘上那 6 份因此需要迁移（已登记进 `tests/corpus.py::ADDED`）。

  ⑤ **`--identity`（恒等基线）只有一半在工作。** 13 格里 **6 格构造上不可能非零**
     （5 条形态判据两侧是同一个式子 · `hook_downsend_leak` 根本不由候选判据产）。
     ⇒ **7 真 6 假，而报告必须把这 6 格标出来**，不许再拿它们当
     「判据没有误伤原作」的证据。
     ⚠ 那 5 条形态判据**是被修成恒等式的**：修 off-by-one 之前两侧用的是两把不同的尺子，
     恒等基线因此报过红并抓出了 bug；**修好之后它就不再携带信息了**。
     **同一个基线格，修 bug 前有判别力、修 bug 后没有。**

  ⑥ **定稿那一份与候选只差一个 `origin`** ⇒ 载体上就是**同一个模型加一个可选字段**，
     不是两份。`approved.text` 是拼进 `stage7/book.txt` 的那一份——**成书的第一段**。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from contracts.invariants import HOOK_APPROACHES, HOOK_APPROVED_APPROACHES
from contracts.schema.base import Fp10, StrictModel
from contracts.schema.common import Finding, LlmProvenance
from contracts.schema.stage5 import ContextSource, JudgedDraft

#: ⚠ 从 stage5 借的**不是拷贝，是同一个类**：`judged` 的形状与 stage5 逐字段相同
#: （同一套理由：成稿一律不走那条记账的读路，因为两级 `input_key` 会造出一条假的匹配边），
#: `sources.writer_context` 亦然。各定一份就是第二个落点。
_ = (ContextSource, JudgedDraft)


class Mech(StrictModel):
    """落盘前那次确定性改写的记账。

    ⚠⚠ **同一个字段，stage5 裁掉了它，本层留着——这不是不一致，是同一条判准的两个答案。**
    判准是**「有没有判据依赖这次改写的结果」**：

    | | stage5 | **stage6** |
    |---|---|---|
    | 有判据在断言归一后的属性吗 | **没有** | **有两条**：`hook_para_sep`（HARD，明写是归一的后置条件）· `hook_quote_style` |
    | 记账在盘上携带信息吗 | 31/31 **四项全 0** | **6/9 候选 `paras_fixed=1` · 2/9 `quotes_fixed` 非零** |
    | 处置 | 裁（换成报告一行） | **留 + 补出口** |

    `hook_para_sep` 是一条**被代码提前满足**的 HARD，它报 `[OK]` 时的全部信息量
    **都在 `paras_fixed` 那个数里**——6/9 非零说明归一真动过手，所以那盏绿灯是有理由的；
    `mech` 一裁，它就变成**一盏说不清为什么亮的绿灯**。

    ⚠ **`mech` 今天有 0 个读者**（产地写、API 打包、而前端全文 0 处渲染它）——
    **「有 API 字段」不等于「有出口」。** ⇒ 按 `Q-1` 补出口：进校验报告、空也要报、
    一行常驻非零才展开。

    ⚠ **已裁撤**：`quote_problems`（恒空）。它不是记账，**是一条结论**
    → 升成判据 `hook_quote_unbalanced`（与 stage5 的 `quote_unbalanced` 同型）。
    """

    #: **恒 0**（9 候选 + 3 定稿）。
    punct_fixed: int = Field(ge=0)

    #: 0~2（**2 个候选非零**）。
    quotes_fixed: int = Field(ge=0)

    #: 0~1（**6/9 候选 = 1**）。
    paras_fixed: int = Field(ge=0)


class Candidate(StrictModel):
    """一条候选。恒 3 条（三种策略各一）。

    ⚠ **已裁撤**：`chars`（= 去空白的字数，盘上 9/9 与现算逐值相同，唯一读者是界面卡片）。
    ⚠⚠ **裁掉它之后，现算必须留在 API 层（Python，走同一个 `measure`）**：
    同一条候选实测 `measure(text)['chars'] = 253`（去空白，判据用的就是它）而
    `len(text) = 277`（界面若在 JS 里 `text.length` 会拿到这个）——差 24 字（**9.5%**）。
    下沉到 JS 的后果不是判据坏掉，是**同一页上人会看到两个字数**。
    """

    #: **唯一落点是 `HOOK_APPROACHES`**，prompt 那一份是插值来的。
    #: ⚠ 产地**不许 `.get` 兜底**：写歪的 approach 一律显式终态。
    approach: Literal[HOOK_APPROACHES]

    #: 文案本身。盘上**串长** 123~308（含段间分隔符）。
    #: ⚠ 存的是**归一之后**的字节。
    text: str = Field(min_length=1)

    mech: Mech

    #: 这一条候选的全部判定。盘上长度 2~3。
    findings: list[Finding]


class ApprovedHook(StrictModel):
    """定稿。**= 候选 + 一个 `origin`。**

    ⚠ **`approved.text` 进成书**（拼在 `book.txt` 最前面）。
    """

    #: **比候选多一个 `human_edit`。** 它答的是「这份人工稿是从哪一条改出来的」——
    #: **没有前身时才是 `human_edit`**。盘上 1/3 本是它（那本从没 `--approve` 过
    #: 就直接上了人工稿）。
    approach: Literal[HOOK_APPROVED_APPROACHES]

    text: str = Field(min_length=1)

    #: 盘上恒 `'human'`。**没有别的取值**——`auto_approved` 是**被裁决掉的东西**，
    #: 不是还没做的。
    origin: Literal['human']

    #: ⚠ `--approve` 时是**候选那一份的拷贝**；`--apply-edit`/`--recheck` 时是重新归一算出来的。
    mech: Mech

    #: ⚠ **三条写它的路都必须带**（`--approve` / `--apply-edit` / `--recheck`）：
    #: 定稿那一项要携带**它自己这份字节**的判定。只要有一条不带，
    #: 「这份没问题」与「这份没判过」在 JSON 里就长得一模一样。
    findings: list[Finding]


class HookSources(StrictModel):
    """⚠ 只有 `writer_context` 一个来源被这样记。stage1/2/3 三份上游的新鲜度靠
    `_provenance.inputs` 兜（只在界面上算），**两套机制并存**——移植时应当合成一套。
    """

    writer_context: ContextSource


class HookParams(StrictModel):
    """一次调用当时的旋钮。⚠ **原型完全没有这个键**（事实 ④）。"""

    max_tokens: int = Field(ge=1)

    #: A/B 分叉的目录后缀。⚠ **它不是「臂」**（见 `stage5.py` 事实 ①）。
    tag: str


class HookProvenance(LlmProvenance):
    """`hook.json._provenance`。

    `inputs` 恒四条：`stage1/hook_copy.json` · `stage2/global_grounding.json` ·
    `stage3/style_grounding.json` · `stage4/writer_context.json`。
    ⚠ `inputs` 里**没有成稿**——它们在 `judged` 里。

    ⚠ `prompts` 两个键：`HOOK_SYS`（**插值之后**的指纹，`HOOK_APPROACHES` 变了它就变）·
    `RULES`（判据表自身的指纹）。
    ⚠⚠ **判据表指纹统一叫 `RULES`**（`_common.md` §1.1）——原型在这一层叫 `RULES7`、
    在 stage7 叫 `RULES8`、在 stage5 叫 `RULES`，而它们是**同一件事**。

    ⚠ `prompts` / `code` 今天**0 个比对方**（只被界面原样显示），而事实③与 `code` 那两条
    证明**记的那个值本身会错** ⇒ 校验器要补一行「本产物的 prompt 指纹 vs 当前代码」。
    """

    #: ⚠ `model` 由 `LlmProvenance` 定成必填，本层**不重定义**（同一个语义两个落点必然分家）。
    #: 但要把「它盘上零样本」写在明处：本层是**付费节点**而原型没记 `model`，
    #: 换了模型产物里没有一处说得出来 ⇒ 3 份 `hook.json` + 3 份归档都要迁移，
    #: 迁移清单里单列一条（`tests/corpus.py::ADDED`）。
    params: HookParams


class Hook(StrictModel):
    """`stage6/hook.json` —— 顶层 **9 个键**，全部必填（`approved` 缺省 `null`，键恒在）。

    ⚠ **归档 `_rounds/hook.rN.json` 与它逐字段同形**（整份拷贝，不是差量、不是投影）——
    归档是「出事后唯一能回去的那一份」。盘上三份 `r1` 的 `approved` **全是 `null`**，
    这说明归档点选对了：换掉的是「谁被选中」这个决定，
    **而那正是下次调 prompt 时唯一的证据**。

    ⚠ **已裁撤四个**：
      · `title` · `source_id` —— 目录名是唯一落点。**第四次撞上同一把尺子**
        （stage1 ×1 · stage4 ×2 · stage5 ×2）。
      · `baselines`（`{判据: 'k/3'}`）—— 0 读者 + 100% 可从同文件的 `candidates[].findings`
        现算（盘上 3/3 逐值验过）+ **它自陈不是判据基线**（三个候选是同一次调用的相关三条）
        + **其中一格结构性说谎**（`hook_downsend_leak` 恒 `0/3`，因为它压根不由候选判据产）。
      · `counts` —— 0 读者 + 100% 现算 + **稀疏**：`Counter` 的 0 键会消失，
        盘上 1/3 本没有 `info` 键、3/3 本没有 `hard` 键——
        「没有这个键」与「值是 0」混掉，正是载体那条默认值警告要防的三态。
    """

    #: 下发范围＝`arc.conflict_stages[0]` 的**末章号**，至少 1。盘上 `1 / 2 / 2`。
    #:
    #: ⚠ **只数 `chapters`，绝不读 `stage` 那一列文字**——实测阶段名本身就是结局
    #: （「系统揭示与最终选择」）。
    #: ⚠ **它看起来能从 `judged` 推出来，不能**：盘上三本 `max(judged[].output_ordinal)`
    #: 恰好等于它，但那是因为开篇几章都有成稿。**缺章时 `judged` 会短一截**，
    #: 而本字段记的是**要过的范围**，不是**拿到的范围**。
    #: 两个量在正常路径上相等，**在异常路径上必须分得开**。
    lead_upto: int = Field(ge=1)

    #: 一句话，说明三个候选各自赌什么。盘上 89~144 字。
    #:
    #: ⚠ **人定稿时唯一的择优说明**（界面渲染它），而结构化调用点**曾漏把它声明为必需键**
    #: ——缺了它不会报错，产地存 `None`、CLI 与界面**静默不显示那一行**，
    #: 而本层的现役闸门恰恰就是人定稿。⇒ 必需顶层键应为 `('candidates', 'rationale')`。
    rationale: str = Field(min_length=1)

    #: 恒 **3** 条。
    candidates: list[Candidate] = Field(min_length=3, max_length=3)

    #: 缺省 `null`（键恒在）。**没有 auto_approved**（用户裁决）。
    approved: ApprovedHook | None

    sources: HookSources

    #: `{成稿相对路径: 引用}`。
    judged: dict[str, JudgedDraft]

    #: 书级结论。盘上恒 **4** 条（3 `unver` + 1 `metric`）。
    book: list[Finding]

    #: **恒等基线**：同一把尺子量原作文案段，分母恒 1。hard+info 每条一格，
    #: **减去 `hook_reuses_source`**（原作 vs 原作必然 100%，构造使然）⇒ **13 格**。
    #:
    #: ⚠⚠ **其中 6 格是恒等式**（事实⑤）——**报告必须标出来**，别当读数。
    #: ⚠ **它不裁**：它是「判据有没有把合法情况排除在外」的落点、有两个真读者，
    #: 而且**不可再生**（上游一变就不是当时那把尺子了）。
    #: ⚠ 对照 stage7：那一层的恒等基线**跑完把结论扔掉了**（只有 `print` + 返回码），
    #: 于是「跑过且逐字节无损」与「从没跑过」在盘上长得一模一样。
    #: **正因为本层有落点，才有人回头去读它，才判死过两条坏判据。**
    baselines_identity: dict[str, str]

    provenance: HookProvenance = Field(alias='_provenance')
