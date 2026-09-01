#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage6.py —— 对应 `docs/schema/stage6.md`。开篇文案重构。
**全书级单次产出**——一次调用产三个候选，**人定稿**。

```
stage6/hook.json            LLM 产 + 确定性判据 —— 一本书一份，定长
stage6/_rounds/hook.rN.json 归档：上一轮的**整份拷贝**，形状与 hook.json 逐字段相同
```

【贯穿本层的事实】

  ① 原作文案、全书结构与正文 surface habits 都进入生成材料；剧透不是违规。
     语义硬边界是实质矛盾，质量由匿名六维审判排序。

  ② 人仍做最终定稿。当前校准 ``clear`` 可直接作为自动依据；缺审、过期或
     ``uncertain`` 只能由人提交 actor / reason / 当前文本完整 SHA-256 后，以
     ``human_verification`` 收口，不能冒充自动 PASS。当前或历史上与同一完整
     SHA-256 绑定的校准 ``FAIL`` 不可人工绕过，只能改字形成新主体后重新处理。

  ③ 0 成本写盘路通过 `rewrite_provenance` 或 `promoted()` 继承真实来源；
     `model` 与 `params` 对付费产物均为必填，不能由 0 成本路径现造。

  ⑤ **`--identity`（恒等基线）只有一半在工作。** 多个形态格构造上不可能非零，
     因为判定两侧最终都来自同一份原作文案度量。
     ⇒ **7 真 6 假，而报告必须把这 6 格标出来**，不许再拿它们当
     「判据没有误伤原作」的证据。
     ⚠ 那 5 条形态判据**是被修成恒等式的**：修 off-by-one 之前两侧用的是两把不同的尺子，
     恒等基线因此报过红并抓出了 bug；**修好之后它就不再携带信息了**。
     **同一个基线格，修 bug 前有判别力、修 bug 后没有。**

  ⑥ **定稿那一份与候选只差一个 `origin`** ⇒ 载体上就是**同一个模型加一个可选字段**，
     不是两份。`approved.text` 是拼进成书的那一份——**成书的第一段**。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    CACHE_POLICY,
    HOOK_APPROACHES,
    HOOK_RECIPE,
    HOOK_SLOTS,
)
from contracts.schema.base import Fp10, StrictModel
from contracts.schema.common import (
    Finding, HashBoundHumanApproval, LlmProvenance, Sha256,
)
from contracts.schema.evaluation import EvaluatorFingerprint, SemanticDecision
from contracts.schema.stage5 import ContextSource, JudgedDraft

#: ⚠ 从 stage5 借的**不是拷贝，是同一个类**：`judged` 的形状与 stage5 逐字段相同
#: （同一套理由：成稿一律不走那条记账的读路，因为两级 `input_key` 会造出一条假的匹配边），
#: `sources.writer_context` 亦然。
#: 各定一份就是第二个落点。
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
    """一条候选。恒 3 条——**身份是位置**（`c1`/`c2`/`c3`），策略只是它的一个属性。

    ⚠ 策略**可以重复**：当前配方 `HOOK_RECIPE` 就是 `payoff_preview` ×1 +
    `scene_punchline` ×2。这里不校验配方——校验开在**模型响应**那一侧
    （`HookResponse`），落盘载体要能读回**任何一次历史配方**产的存量。

    ⚠ **已裁撤**：`chars`（= 去空白的字数，盘上 9/9 与现算逐值相同，唯一读者是界面卡片）。
    ⚠⚠ **裁掉它之后，现算必须留在 API 层（Python，走同一个 `measure`）**：
    同一条候选实测 `measure(text)['chars'] = 253`（去空白，判据用的就是它）而
    `len(text) = 277`（界面若在 JS 里 `text.length` 会拿到这个）——差 24 字（**9.5%**）。
    下沉到 JS 的后果不是判据坏掉，是**同一页上人会看到两个字数**。
    """

    #: ⚠ 取值面是 `HOOK_APPROACHES`（含已退场的写法），**不是当前配方**：
    #: 存量产物里就有 `source_architecture`，载体收窄成配方会让它们当场拒读。
    approach: Literal[HOOK_APPROACHES]

    #: 文案本身。盘上**串长** 123~308（含段间分隔符）。
    #: ⚠ 存的是**归一之后**的字节。
    text: str = Field(min_length=1)

    mech: Mech

    #: 这一条候选的全部判定。盘上长度 2~3。
    findings: list[Finding]


class HookApprovalResolution(HashBoundHumanApproval):
    """A self-reported human approval bound to the exact approved bytes.

    ``actor`` is deliberately an asserted identity, not an authenticated
    principal.  The full SHA-256 is supplied by the caller and compared before
    any archive/write so a stale browser or CLI cannot approve different text.
    """

    #: 盘上恒 `'human_verification'`。**没有别的取值**——`calibrated_pass` 是**被裁决掉的
    #: 东西**（它只能由 evaluator profile 那套自动放行装置产生，而那套已整批下线），
    #: 不是还没做的。同 `ApprovedHook.origin` 的形状与理由。
    #: ⚠ 盘上不需要迁移：三本书的既有 approval 本来就全是 `human_verification`，
    #: 从来没有任何宿主产出过 `calibrated_pass`。
    basis: Literal['human_verification']
    #: 人当时读的是哪一份 semantic 审判。`None` = 定稿时盘上没有可用的当前审判
    #: （那时人是在**没有判官证据**的情况下拍板的，这两件事必须分得开）。
    semantic_review_seq: int | None = Field(default=None, ge=1)
    #: 终审人实际看到的紧凑工作包。旧盘没有这一格时仍可解码，但不能据此宣称
    #: ``already_current``；下一次明确批准会补上当前摘要。
    workpack_sha256: Sha256 | None = None
    #: 长期适用性只绑定人实际看到的内容/证据与显式审阅契约；代码、规则实现
    #: 与产物 provenance 属 validation/execution 身份，不得再级联撤销人工决定。
    review_identity_sha256: Sha256 | None = None


class ApprovedHook(StrictModel):
    """定稿。**= 候选 + 一个 `origin`。**

    ⚠ **`approved.text` 进成书**（拼在成书最前面）。
    """

    #: 它答的是「这份定稿是从**哪一条候选**来的」——`None` = 没有前身（人手写）。
    #:
    #: ⚠⚠ **这里曾是 `approach`（策略名 + 一个 `human_edit`）。** 那个写法把
    #: 「从哪条候选来」与「那条候选用的什么策略」压成了一格，而一旦同一种策略
    #: 采两条样本，它就**答不出前一个问题**了。⇒ 换成位置身份。
    #: ⚠ 策略名没有丢：它由 `candidates[slot].approach` **现算**得到
    #: （能 0 成本现算的字段不落盘，同 `Candidate.chars` 被裁掉的理由）。
    #: ⚠ `human_edit` 这个取值随之消失：「它是不是人写的」由 `origin` 回答、
    #: 「有没有前身」由 `slot is None` 回答，两件事本来就不该挤在一个枚举里。
    # ⚠ `None` 是一个**有意义的显式值**（人手写、无候选前身），不是缺省值。
    # 字段缺失表示 producer 没交代来源，必须拒读；否则“未知”会被洗成“没有前身”。
    slot: Literal[HOOK_SLOTS] | None

    text: str = Field(min_length=1)

    #: 盘上恒 `'human'`。**没有别的取值**——`auto_approved` 是**被裁决掉的东西**，
    #: 不是还没做的。
    origin: Literal['human']

    #: ⚠ `stage6.approve --pick` 时是**候选那一份的拷贝**；
    #: `--apply-edit`/`--recheck` 时是重新归一算出来的。
    mech: Mech

    #: ⚠ **三条写它的路都必须带**（`stage6.approve --pick` / `--apply-edit` / `--recheck`）：
    #: 定稿那一项要携带**它自己这份字节**的判定。只要有一条不带，
    #: 「这份没问题」与「这份没判过」在 JSON 里就长得一模一样。
    findings: list[Finding]

    #: 人工定稿不再是一个无主体的 bool：记自报身份、理由、判定基础，
    #: 并用完整 SHA-256 与真正进成书的字节绑定。
    resolution: HookApprovalResolution


class HookSources(StrictModel):
    """⚠ 只有 `writer_context` 一个来源被这样记。stage1/2/3 三份上游的新鲜度靠
    `_provenance.inputs` 兜（只在界面上算），**两套机制并存**——移植时应当合成一套。
    """

    writer_context: ContextSource


class HookParams(StrictModel):
    """初始生成与单候选自动重试当时的输出上限。"""

    max_tokens: int = Field(ge=1)
    #: 动态 JSON 的排版实验臂。默认值兼容现役 pretty 字节；现役 producer
    #: 仍须显式写入，不能靠默认值让正式产物无法区分付费 A/B。
    prompt_json_layout: Literal['pretty-v1', 'compact-v1'] = 'pretty-v1'
    candidate_retry_max_tokens: int = Field(default=24000, ge=1)
    # ``0`` is the neutral migration value for v2 artifacts: those artifacts
    # predate candidate retrying and therefore must not claim that a retry was
    # configured.  Current producers still use the default of exactly one.
    candidate_retry_limit: int = Field(default=1, ge=0, le=1)
    candidate_retry_effort: Literal['low'] = 'low'
    candidate_retry_thinking: Literal[False] = False


class HookCandidateRetry(StrictModel):
    """一条机械 HARD 候选的一次定向重试账。

    ⚠ 记的是**哪一条候选**被重试了（位置身份），不是「哪种策略」——后者由
    `candidates[slot].approach` 现算，而同一种策略采两条样本时它答不出前一个问题。
    """

    slot: Literal[HOOK_SLOTS]
    attempt: int = Field(default=1, ge=1, le=1)
    failure_items: list[str] = Field(min_length=1)
    failure_evidence: Fp10
    user_prompt: Fp10
    output: Fp10 | None
    model: str = Field(min_length=1)
    cache_policy: Literal[CACHE_POLICY]
    before_hard: int = Field(ge=1)
    after_hard: int | None = Field(default=None, ge=0)
    status: Literal[
        'accepted', 'hard_not_reduced', 'duplicate', 'wrong_approach', 'request_failed',
    ]
    reason: str | None = Field(default=None, min_length=1)
    accepted: bool

    @model_validator(mode='after')
    def _status_matches_payload(self) -> 'HookCandidateRetry':
        if self.accepted != (self.status == 'accepted'):
            raise ValueError('candidate retry 的 accepted 必须且只能对应 status=accepted')
        failed_before_candidate = self.status in ('wrong_approach', 'request_failed')
        if failed_before_candidate != (self.after_hard is None):
            raise ValueError(
                'wrong_approach/request_failed 没有可归入目标候选的 after_hard；'
                '其余状态必须记录重判后的 HARD 数')
        if (self.status == 'request_failed') != (self.output is None):
            raise ValueError('只有 request_failed 没有可校验响应 output')
        if (self.status == 'accepted') != (self.reason is None):
            raise ValueError('accepted 不写失败 reason；其余状态必须说明未采纳原因')
        return self


class HookProvenance(LlmProvenance):
    """`hook.json._provenance`。

    `inputs` 恒三条：`stage1/hook_copy.json` · `stage2/global_grounding.json` ·
    `stage4/writer_context.json`；测量契约并入第一条的 consumed 指纹。
    ⚠ `inputs` 里**没有成稿**——它们在 `judged` 里。

    ⚠ `prompts` 常驻 `HOOK_SYS`（**插值之后**）与 `RULES`；发生脏候选重试时再有
    `HOOK_RETRY_SYS`。缺第三键表示当轮没有重试，不表示漏记。
    ⚠⚠ **判据表指纹统一叫 `RULES`**（`_common.md` §1.1）——原型在这一层叫 `RULES7`、
    在 stage7 叫 `RULES8`、在 stage5 叫 `RULES`，而它们是**同一件事**。

    ⚠ `prompts` / `code` 今天**0 个比对方**（只被界面原样显示），而事实③与 `code` 那两条
    证明**记的那个值本身会错** ⇒ 校验器要补一行「本产物的 prompt 指纹 vs 当前代码」。
    """

    #: `model` 由 `LlmProvenance` 定成必填，本层不重复定义。
    params: HookParams

    #: 每条初始脏候选恰好一次定向重试。默认空 = **这一跑没有重试过任何候选**，
    #: 是产地不写这一格时的中性值。（曾写作「兼容旧产物」，而迁移机制已整批删除。）
    candidate_retries: list[HookCandidateRetry] = Field(default_factory=list)


class Hook(StrictModel):
    """`stage6/hook.json` —— 顶层 **7 个键**，全部必填（`approved` 缺省 `null`，键恒在）。

    ⚠ **归档 `_rounds/hook.rN.json` 与它逐字段同形**（整份拷贝，不是差量、不是投影）——
    归档是「出事后唯一能回去的那一份」。盘上三份 `r1` 的 `approved` **全是 `null`**，
    这说明归档点选对了：换掉的是「谁被选中」这个决定，
    **而那正是下次调 prompt 时唯一的证据**。

    ⚠ **已裁撤四个**：
      · `title` · `source_id` —— 目录名是唯一落点。**第四次撞上同一把尺子**
        （stage1 ×1 · stage4 ×2 · stage5 ×2）。
      · `baselines`（`{判据: 'k/3'}`）—— 0 读者 + 100% 可从同文件的 `candidates[].findings`
        现算（盘上 3/3 逐值验过）+ **它自陈不是判据基线**（三个候选是同一次调用的相关三条）
        + 历史上还曾混入不由候选文本产生的书级格，现已退役。
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

    provenance: HookProvenance = Field(alias='_provenance')


class HookCandidateResponse(StrictModel):
    """模型写的一条候选。**只有两个键**——`mech` 与 `findings` 是代码算的。"""

    approach: Literal[HOOK_APPROACHES]
    text: str = Field(min_length=1)


class HookCandidateRetryResponse(HookCandidateResponse):
    """单候选修复响应，同时重写解释当前整组三候选的 rationale。"""

    rationale: str = Field(min_length=1)


class HookResponse(StrictModel):
    """**模型写的那两个键**，`ask_json(model_cls=...)` 收的就是它。

    ⚠⚠ **它与 `Hook` 不是同一个东西，那正是它存在的理由。** 产物还有 `judged`
    （来源账）· `mech` / `findings`
    （确定性算的）· `_provenance`——**一个字都不经模型**。

    ⚠⚠ **`require` 从本模型的必需顶层键派生 ⇒ 两个键一个都漏不掉。**
    `rationale` 是**人定稿时唯一的择优说明**（界面渲染它），而原型的结构化调用点
    **曾漏把它声明为必需键**——缺了它不会报错，产地存 `None`、
    CLI 与界面**静默不显示那一行**，而本层的现役闸门恰恰就是人定稿。
    派生之后这件事**结构上不可能再发生**。
    """

    candidates: list[HookCandidateResponse] = Field(min_length=3, max_length=3)
    rationale: str = Field(min_length=1)

    @model_validator(mode='after')
    def _matches_the_current_recipe(self) -> 'HookResponse':
        """按**逐槽配方**校验，不按策略去重。

        ⚠⚠ 这里曾经写的是「每种策略恰好一次」（集合相等 + 无重复）。那条断言把
        「取值面」与「这一次要产哪几条」当成同一件事，于是同一种策略采两条样本
        **在校验器层面就不可能**。现在配方允许重复，但身份又由列表位置承载，
        因此必须逐槽比较：同一种策略可以占两槽，模型却不能把 c1/c2 对调后
        再让位置身份替错误顺序背书。
        """
        approaches = [c.approach for c in self.candidates]
        if tuple(approaches) != HOOK_RECIPE:
            raise ValueError(
                f'candidates 必须按槽位顺序符合当前配方 {HOOK_RECIPE}，实得 {approaches}')
        texts = [c.text.strip() for c in self.candidates]
        if len(set(texts)) != len(texts):
            raise ValueError('三个候选文本不得重复')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# `stage6/semantic_<n>.json` —— 当前质量审判
# ─────────────────────────────────────────────────────────────────────────────


class HookSemanticResponse(StrictModel):
    """匿名主编的一次响应：实质矛盾审查 + 吸引力评分与排序。"""

    reviews: list['HookCandidateReviewResponse'] = Field(min_length=1)
    ranking: list[str] = Field(min_length=1)


class HookContradiction(StrictModel):
    """会改变读者对身份、关系、关键因果或故事结果理解的一处矛盾。"""

    assertion: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class HookQualityScores(StrictModel):
    """质量六维；总分由代码按固定权重计算，不接收模型自报总分。"""

    read_on_desire: float = Field(ge=0, le=10)
    body_voice_match: float = Field(ge=0, le=10)
    concrete_scene_and_specificity: float = Field(ge=0, le=10)
    curiosity_and_payoff_promise: float = Field(ge=0, le=10)
    hook_craft: float = Field(ge=0, le=10)
    synopsis_penalty: float = Field(ge=0, le=10)


class HookCandidateReviewResponse(StrictModel):
    """模型对一个匿名文本标签的审阅；证据随后由代码回查原文。"""

    label: str = Field(min_length=1)
    coverage: Literal['complete', 'uncertain']
    uncertainty_reason: str | None = None
    contradictions: list[HookContradiction]
    quality: HookQualityScores
    concise_reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _uncertainty_is_explicit(self) -> 'HookCandidateReviewResponse':
        if self.coverage == 'uncertain' and not self.uncertainty_reason:
            raise ValueError('coverage=uncertain 必须说明 uncertainty_reason')
        if self.coverage == 'complete' and self.uncertainty_reason:
            raise ValueError('coverage=complete 不应携带 uncertainty_reason')
        return self


class HookCandidateReview(HookCandidateReviewResponse):
    """落盘审阅 = 模型响应 + 代码绑定的文案内容指纹。"""

    text_sha1: Fp10


class HookSemanticReview(StrictModel):
    """`stage6/semantic_<n>.json` —— 付费文案质量审判的可审计落盘形态。

    ⚠ **候选文本绑定键是 `judged_texts`（label → 文本内容 sha1），不绑 hook.json 整文件**：
    `--pick` 只改 `approved` 键、不换任何文本字节，整文件指纹绑死会让定稿动作把
    语义结论误判过期——`R-9.17` 教训的同型。`hook_file_sha1` 只作审计。

    判官同时看见 Stage5 开篇成稿。它们是被审判的参考主体，不是普通
    `inputs`；因此必须与 Stage6 hook 一样用 `JudgedDraft` 的消费投影单独绑定。
    否则正文改字后，旧语义审判仍可能被当成 current。
    """

    judged_texts: dict[str, Fp10] = Field(min_length=1)
    hook_file_sha1: Fp10
    # 排序标签使用产物里的真实标签；请求时会换成 T1/T2… 匿名标签。
    reviews: list[HookCandidateReview] = Field(min_length=1)
    ranking: list[str] = Field(min_length=1)
    #: 两名盲评 judge 各自的原始排序；``ranking`` 是代码按合并决议
    #: 与平均分确定性派生的顺序。
    judge_rankings: dict[Literal['A', 'B'], list[str]]
    #: 每份去重文本一条；真正的 PASS/FAIL/UNVER 只从这里读，
    #: 不从某一名 judge 的 ``reviews`` 倒推。
    decisions: list[SemanticDecision] = Field(min_length=1)
    evaluator_fingerprint: EvaluatorFingerprint
    #: `{stage5/chNN.json: 当时实际下发给 A/B 判官的成稿引用}`。
    #: 整文件 sha1 只作审计；过期判定只比 `consumed.text_sha1`。
    judged: dict[str, JudgedDraft]
    provenance: LlmProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _double_blind_surface_is_closed(self) -> 'HookSemanticReview':
        labels = {review.label for review in self.reviews}
        if set(self.judged_texts) != labels:
            raise ValueError('judged_texts 与 reviews 必须恰好覆盖同一批标签')
        if any(self.judged_texts[review.label] != review.text_sha1
               for review in self.reviews):
            raise ValueError('judged_texts 与 reviews 的文本 sha1 绑定不一致')
        if set(self.judge_rankings) != {'A', 'B'}:
            raise ValueError('judge_rankings 必须恰有 A/B 两份')
        for judge, ranking in self.judge_rankings.items():
            if len(ranking) != len(set(ranking)) or set(ranking) != labels:
                raise ValueError(f'judge {judge} ranking 未恰好覆盖合并审阅标签')
        subjects = [decision.subject_key for decision in self.decisions]
        if len(subjects) != len(set(subjects)) or len(subjects) != len(self.reviews):
            raise ValueError('decisions 必须与去重文本一一对应')
        if any(decision.ability != 'reference_boundary' for decision in self.decisions):
            raise ValueError('Stage6 decisions 只能使用 reference_boundary 能力')
        if any({judge.judge for judge in decision.judges} != {'A', 'B'}
               for decision in self.decisions):
            raise ValueError('每条 Stage6 decision 必须恰含 A/B 两名盲评 judge')
        for decision in self.decisions:
            refs = [ref for judge in decision.judges for ref in judge.evidence]
            if any(ref.subject_sha256 != decision.subject_key
                   for ref in [*refs, *decision.evidence]):
                raise ValueError('Stage6 decision 的全部证据必须绑定它自己的完整 subject hash')
        # ⚠ 这里曾有一支 `if decision.automated:`（自动决议只能是 PASS/FAIL，且必须 A/B
        #   同结论 + 各自证据闭合）与它的 `elif ... != 'UNVER': raise` 对偶。自动放行门
        #   下线后 `SemanticDecision.verdict` 在类型上就只有 `UNVER`，两支都不可达。
        #   **不要**在这里补一条「decisions 必须全 UNVER」——那等于把刚删掉的校验器
        #   换个地方再写一遍，而类型已经把它守住了。
        return self


class HookEdit(StrictModel):
    """`stage6/hook_edit.json` —— 人手改好的**一条**文案文本（`--apply-edit` 的决策集）。

    `candidate` 指明改哪条候选（**位置身份** `c1`/`c2`/`c3`）。`None` 只表示
    「修改当前定稿的候选前身」：运行时把归一后文本写回原 slot、清掉旧批准，再要求
    刷新 hash-bound workpack 并重新批准；旧定稿没有候选前身时必须显式给 slot。
    ⚠ 它曾经填策略名。同一种策略采两条样本时策略名不再唯一 ⇒ 选不中那一条。
    ⚠ 只收文本：`mech` / `findings` 由 `_normalize_and_judge` 重算——
    人工稿与生成稿走同一条归一+判定，没有免检写口。
    """

    candidate: Literal[HOOK_SLOTS] | None = None
    text: str = Field(min_length=1)
    actor: str | None = None
    reason: str | None = None
    subject_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _formal_edit_is_an_explicit_bound_resolution(self) -> 'HookEdit':
        audit = (self.actor, self.reason, self.subject_sha256)
        if self.candidate is None:
            if any(value is None or not value.strip() for value in audit):
                raise ValueError('修改既有定稿必须显式给 actor、reason 和 subject_sha256')
            # subject 绑定的是进入正式产物的归一后文本。归一口径依赖本书原作文案的
            # 引号与分段，schema 单独拿到 edit 时算不出来；由 run_apply_edit 在写盘前
            # 用同一条 normalize_hook 链校验，不能在这里误绑 raw upload 字节。
        elif any(value is not None for value in audit):
            raise ValueError('修改候选尚未定稿，不应伪造人工 resolution')
        return self
