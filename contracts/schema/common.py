#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common.py —— 对应 `docs/schema/_common.md`。所有产物共有的部分。

八份 stage 文档都写着「四个共有键留给 `_common.md`」。这里是它的机器形态。

【枚举一律从 `contracts.invariants` 取，不在本文件重抄】
写法是 `Literal[SEVERITY]`——用元组给 `Literal` 下标，运行时等价于逐个写字面量，
JSON Schema 里照样是个 `enum`（前端 TS 类型因此能生成），而**取值面仍然只有一份**。
⚠ 代价：静态类型检查器读不懂这种写法，它们会把这些字段看成 `str`。
这笔交换是划算的——手抄一份枚举正是本项目实测漂过两次的那个病。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypeVar

from pydantic import Field, RootModel, StringConstraints, field_validator, model_validator

from contracts.invariants import OWNER, PARTIAL_REWRITE_FLAGS, SEVERITY
from contracts.schema.base import Fp10, FrozenModel, Sha1, StrictModel

Severity = Literal[SEVERITY]
Owner = Literal[OWNER]
PartialRewrite = Literal[PARTIAL_REWRITE_FLAGS]
Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]

#: `promoted()` 的返回类型。**`model_copy` 保留具体类**，于是
#: `RoundLlmProvenance.promoted()` 仍是 `RoundLlmProvenance`（`round` 原样带下来——
#: 提升不是新的一轮）。写成 `-> Provenance` 会把这件事在类型上抹掉。
P = TypeVar('P', bound='Provenance')


def plain_report_text(value: Any) -> Any:
    """Remove report-author Markdown while preserving the instance text itself.

    Reports are rendered as plain text in every consumer.  Producers therefore do
    not get to smuggle presentation syntax into ``detail``/``note`` and depend on a
    Markdown renderer that some consumers do not have.  This intentionally removes
    only the formatting delimiters used by our own report prose; characters that can
    legitimately be part of quoted book evidence (``#``, brackets, underscores,
    asterisks, and so on) are left alone.
    """
    if not isinstance(value, str):
        return value
    return value.replace('**', '').replace('`', '').replace('~~', '')


# ─────────────────────────────────────────────────────────────────────────────
# 一、来源账（`_common.md` §一 ~ §三）
# ─────────────────────────────────────────────────────────────────────────────

class InputEdge(FrozenModel):
    """`_provenance.inputs` 的一条边。见 `_common.md` §三。

    ⚠ **两格，不是一格。** `Q-4` 明写「指纹是文件级的：改一个不相干的字段会让整份产物
    的指纹失配，把一批结论降级成『读数不可用』，且没有重新挂钩的路。
    **字段级指纹是一次契约改版，值得在新骨架里就做对。**」
    """

    #: 盘上字节的指纹 —— 「这份上游产物动过吗」。
    file: Fp10

    #: 本节点**真正读到的字段投影**的指纹 —— 「我依赖的那部分动过吗」。
    #:
    #: ⚠ **必填但可为 `null`**（没有默认值，所以「键不在」会被 `extra='forbid'` 之外的
    #: 必填校验挡住）。`null` 的含义是**「本节点没有声明自己读了哪些字段」**
    #: ⇒ 该条边的新鲜度是 `unknown`，**不是 `fresh`**。
    #: **建槽位，不填槽位**：八层不必同时交这份作业，投影按层逐步补。
    consumed: Fp10 | None


class Usage(FrozenModel):
    """这一份产物花了多少。见 `_common.md` §八。

    ⚠ **只存事实，不存价格**（`Q-10`：不显示预估花费——猜一个数写在界面上比不显示更坏）。
    """

    #: **真发出去**的调用数。
    calls_made: int = Field(ge=0)

    #: 命中缓存的次数（＝ 0 元）。
    #:
    #: ⚠ **不许与 `calls_made` 合并。** 只数真调用，会让「参数不变的重跑什么都没跑」
    #: （`Q-11` / `R-12.4` 要求**显式说出来**的那条语义）与「什么都没干」在报告上同形。
    calls_cached: int = Field(ge=0)

    #: 必填但可为 `null` —— `null` = 这次响应没给 usage，不是 0。
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None


class HumanApproval(StrictModel):
    """A human decision must always be attributable and explainable.

    ``min_length`` alone accepts whitespace-only strings.  These two fields are
    deliberately validated in the shared carrier so every approval surface has
    the same refusal boundary instead of relying on individual CLI call sites.
    """

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator('actor', 'reason')
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('人工凭据不能只含空白')
        return value


class HashBoundHumanApproval(HumanApproval):
    """A human decision bound to the exact subject that was reviewed."""

    subject_sha256: Sha256


class RevalidationAudit(HumanApproval):
    """Immutable facts captured while promoting an ``.INVALID`` artifact.

    Raw hashes expose byte-only edits (including formatting); canonical hashes
    and RFC 6901 pointers describe logical JSON changes.  Keeping both prevents
    either representation from silently standing in for the other.
    """

    baseline_locator_sha256: Sha256
    baseline_raw_sha256: Sha256
    candidate_raw_sha256: Sha256
    baseline_canonical_sha256: Sha256
    candidate_canonical_sha256: Sha256
    raw_changed: bool
    content_changed: bool
    edited_by_human: bool
    changed_json_pointers: tuple[str, ...]

    @model_validator(mode='after')
    def _hashes_and_diff_agree(self) -> 'RevalidationAudit':
        raw_changed = self.baseline_raw_sha256 != self.candidate_raw_sha256
        content_changed = (
            self.baseline_canonical_sha256 != self.candidate_canonical_sha256)
        if self.raw_changed != raw_changed:
            raise ValueError('raw_changed 必须与 baseline/candidate raw SHA-256 一致')
        if self.content_changed != content_changed:
            raise ValueError('content_changed 必须与 baseline/candidate canonical SHA-256 一致')
        if self.edited_by_human != content_changed:
            raise ValueError('edited_by_human 必须与 content_changed 一致')
        if not raw_changed and content_changed:
            raise ValueError('原始字节相同而 canonical JSON 不同，审计账自相矛盾')
        pointers = self.changed_json_pointers
        if pointers != tuple(sorted(set(pointers))):
            raise ValueError('changed_json_pointers 必须去重并按字典序排列')
        if any(pointer and not pointer.startswith('/') for pointer in pointers):
            raise ValueError('changed_json_pointers 必须是 RFC 6901 JSON Pointer')
        if content_changed != bool(pointers):
            raise ValueError('逻辑内容变化与 changed_json_pointers 是否为空必须一致')
        return self


class Provenance(StrictModel):
    """产物的来源账。**付费层用 `LlmProvenance`，0 成本层用 `FreeProvenance`。**

    ⚠ 本类不直接用——`model` 这个键该不该在，是由**子类**回答的，见下面两个类。
    """

    #: 完整 git commit；脏树追加其内容摘要：`<commit>-dirty.<sha256[:16]>`。
    #:
    #: ⚠ 原来是 `git rev-parse --short HEAD`，**工作树脏的时候它照样给出一个看起来精确
    #: 的答案**：stage6 盘上有逐字节铁证——同一份 `_provenance` 里按内容算的 prompt 指纹
    #: 变了，而按 git HEAD 算的 `code` 一个字没动。只有 `-dirty` 也仍区分不了两棵脏树。
    code: str = Field(min_length=1)

    #: 全链根正文身份。所有带来源账的产物都必须声明自己最终建立在哪一版
    #: stage0/clean_text.txt 上；只比较中间边不足以发现根正文已换而中间产物未重建。
    root_clean_sha1: Sha1

    #: 这份产物照哪几份上游产的。键是 `<stage>/<文件名>`。
    #:
    #: ⚠ 键**不含书名**，而那不是笔误：原型的来源账是**进程级**的，
    #: 于是同一进程里读过 A 书再写 B 书会落下一条看不出来的假匹配边——
    #: 这正是 `R-11.3` 至今要求「一切写操作走子进程」的理由。
    #: 新骨架把来源账做成 run 级的实例字段，串扰在结构上不存在。
    inputs: dict[str, InputEdge]

    #: 见 `Usage`。可选：0 成本节点没有它。
    usage: Usage | None = None

    # ── 以下四个是**三态**字段：`None` 一律表示「键不在」，落盘时必须 exclude ──

    #: 这份稿是被 `--revalidate` / `--apply-edit` **提升**上来的。
    #:
    #: ⚠ `False` 与「键不在」**不是一回事**：前者是「跑出来的」，
    #: 后者是「这份产物太老，没记过这件事」（`Q-2` 的三态）。
    #: 它只回答「这份稿被重判提升过」，不把提升伪装成一轮新的人工稿。
    #: 是否有人改过业务内容由 ``revalidation_audit.edited_by_human`` 单独回答：
    #: 仅当去掉 ``_provenance`` 后的 canonical payload 变化才为真；纯排版或来源块变化
    #: 只会令 ``raw_changed`` 为真。
    revalidated: bool | None = None

    #: 阻断级判据里**没跑成**的那些。**只写在 `.INVALID` 上，且空列表也要写。**
    #:
    #: 空列表 = 这次没有闸门缺料；键不在 = 这份产物早于这套机制。
    #: 它是「闸门没跑成」这件事的**唯一痕迹**，而那与「闸门跑过没事」正是 `Q-2`
    #: 要求必须能区分的两件事。
    gates_skipped: list[str] | None = None

    #: `'human'` = `--apply-edit` 落成的**新一轮**人工稿。直接修改活的 `.INVALID`
    #: 再提升不会冒充新一轮，而由 ``revalidation_audit`` 留下 actor/reason 与内容 diff。
    origin: Literal['human'] | None = None

    #: 「这份 `inputs` 是本次重判时读的，**不是对历史来源的追认**」。
    input_basis: str | None = None

    #: ⭐ 这份产物被一条 0 成本路径**局部改写**过，值 = 那条路的 flag
    #: （`PARTIAL_REWRITE_FLAGS`）。见 `_common.md` §5.1b。
    #:
    #: 改完之后这一份是**混版的**：LLM 写的那些字段出自当初那次付费产出，
    #: 被改的那部分是**现在**从盘上重算的。三个记法各错一半：
    #: 沿用旧账 ⇒「这次重算的派生字段照哪份上游算的」永远说不出口 ·
    #: 整份覆盖 ⇒ 把 `prompts`/`model` 说成本次产的（伪造）·
    #: 借 `revalidated` ⇒ 那一条只说发生过提升；其 audit 比较的是 INVALID 创建基线与
    #: 人工提交面，不能替本次确定性派生字段重算声明新的输入依据。
    #:
    #: ⚠ **一个带值的键，不是多个布尔。** 否则 `--recheck` / `--renormalize`
    #: 各自还要再加一个键——同一件事会不断长出新落点。
    partial_rewrite: PartialRewrite | None = None

    #: ``write_invalid`` 保存的不可变原始快照定位键。只属于活的 `.INVALID`；
    #: 提升为正式产物后由 ``revalidation_audit`` 接过审计责任并清除此键。
    invalid_baseline_sha256: Sha256 | None = None

    #: ``--revalidate`` 的完整人工审计账。raw hash 记录任意字节变化；去掉来源块后的
    #: canonical hash / JSON Pointer 记录业务变化，并据此唯一派生 ``edited_by_human``。
    revalidation_audit: RevalidationAudit | None = None

    def to_disk(self) -> dict:
        """落盘形态：**本层 `None` 的键一个都不写**。

        这是三态能成立的前提——`revalidated: null` 与「没有 `revalidated` 这个键」
        在 JSON 里必须是两份不同的字节。

        ⚠⚠ **只剥这一层，不许递归**（2026-08-11 骨架 B1 修）。原来写的是
        `model_dump(exclude_none=True)`，而 `exclude_none` 是**全局**的，它会连着
        剥掉两处**必填但可为 `null`** 的键，于是**落下去的产物读不回来**：

          · `inputs[].consumed` —— 契约明写「必填但可为 `null`」，
            `null` = 本节点没声明读了哪些字段 ⇒ 该边 `unknown`。剥掉它，
            那条边在重新加载时是 `Field required`，整份产物加载失败。
          · `usage.{prompt,completion,reasoning}_tokens` —— `null` = 这次响应没给 usage，
            **不是 0**（`_common.md` §八）。同样剥掉、同样加载失败。

        ⚠ 这两处的共同点值得记：**「可为 null」的字段与「三态字段」长得一样，语义相反**。
        三态字段的 `None` 意思是「这个键不该在」，可空字段的 `None` 意思是
        「这个键在，值就是不知道」。一个全局开关同时管这两种，必然把其中一种搞错。
        ⇒ 剥的范围收成**本模型自己的字段**，嵌套模型各自负责（它们没有三态键）。

        这条今天才发作，是因为 A2/A3 只做过「盘上 → 模型」的加载体检，
        从来没有过一次「模型 → 盘上 → 模型」的往返。
        `novel/store.py::_verify_round_trip` 现在把这条往返钉在**落盘之前**。
        """
        dumped = self.model_dump(mode='json')
        return {k: v for k, v in dumped.items() if v is not None}

    def promoted(self: P, *, code: str, audit: RevalidationAudit) -> P:
        """把 `.INVALID` 稿的来源账带上来。`_common.md` §5.2。

        ⚠⚠ **签名里没有 `prompts` / `model` / `inputs` —— 这是本方法存在的全部意义。**

        原型的 `--revalidate` 之所以能**伪造**，是因为提升分支够得到那个会构造一份
        全新来源账的函数：它盖上当前的 SYS 指纹 + 当前 `model` + 当前 `inputs`，
        `round` 还 +1，而**没有任何一次新的落笔发生过**。
        三件事同时错：记了一个没产出它的 prompt；记了一个没写它的模型；
        把「重判提升」记成了「第 N 轮写作」。

        审计参数只接受已经校验过的 ``RevalidationAudit``；其中
        ``edited_by_human`` 只由不含 ``_provenance`` 的 canonical 业务 payload diff
        决定，纯排版只记 ``raw_changed``。仍然没有任何入口能替换 prompts / model /
        inputs，因而不会把重判伪造成一次新生成或新一轮人工稿。

        ⚠ `code` 与重验证审计账是本次动作新增的事实；生成来源的 prompts / model /
        inputs 与轮次仍然原样继承。

        ⚠⚠ **它挂在基类上，不挂在 `LlmProvenance` 上。** 原来挂在付费那一支，
        而 `store.promote_invalid` 拿到的 `src` 类型是 `Provenance`：
        `stage4.CtxProvenance` 与 `stage5.ReviewProvenance` 都是 `FreeProvenance` 的子类、
        都是**真实存在**的 0 成本产物，一样会被闸门拦下、一样要走提升路。
        提升它们会抛 `AttributeError`——而 `errors.py` 明写「不设默认异常，
        每一处终态都要选一个具体的类」，一条裸 `AttributeError` 连终态都不算。
        方法体里没有一处是付费那一支特有的，「没有参数就没有可以传错的东西」
        对 0 成本孪生同样成立。
        """
        return self.model_copy(update={
            'code': code,
            'revalidated': True,
            'gates_skipped': None,
            'invalid_baseline_sha256': None,
            'revalidation_audit': audit,
        })

    def partially_rewritten(self: P, *, by: str, code: str,
                            inputs: dict[str, InputEdge]) -> P:
        """一条 0 成本路径**只改了这份产物的一部分**。`_common.md` §5.1b。

        ⚠⚠ **签名里没有 `prompts` / `model`** —— 与 `promoted()` 同一条判准：
        本次一个请求都没发过，记上它们就是假账（`Q-5` 点名的那类伪造）。

        ⚠ 签名里**有** `inputs` 而 `promoted()` 没有，这不是不一致：
        提升路**没有读任何新东西**（它只是把一份已有的稿换个位置），
        而局部改写**真的重新读了上游**——记下这次读到的正是它的全部意义。
        沿用旧 `_provenance` 会让「这次重算的派生字段照哪份上游算的」永远说不出口。
        危险的那两个在两条路上都够不到。

        ⚠ 运行时那一侧再收一道：`inputs` 只能来自 `RunContext` 的来源账
        （`novel/run.py::RunContext.rewrite_provenance`），**调用方传不进去**。
        本方法留这个参数，是因为契约层不知道 `RunContext` 的存在（分层不许反向依赖）。

        `round` 不 +1 —— 它不是新的一轮写作。
        """
        if by not in PARTIAL_REWRITE_FLAGS:
            raise ValueError(
                f'{by!r} 不是一条「局部改写」路。取值面的唯一落点是 '
                f'`contracts.invariants.PARTIAL_REWRITE_FLAGS`：{PARTIAL_REWRITE_FLAGS}。'
                f'⚠ 一条会花钱的路不叫局部改写，那是一次新的产出，该记一份新的来源账'
            )
        return self.model_copy(update={'code': code, 'inputs': dict(inputs),
                                       'partial_rewrite': by})


class FreeProvenance(Provenance):
    """0 成本节点的来源账。**没有 `model` 这个字段。**

    ⚠ 这不是「`model` 可以是 `null`」，是**结构上不存在**——配合 `extra='forbid'`，
    一份 0 成本产物里出现 `model` 键会当场校验失败。
    理由是**假账**：`--apply-edit` 落的是人手写的稿、`review_<n>.json` 是 0 成本算出来的，
    两者都没有任何模型参与。记一个 `model` 上去就是宣称「某个模型产了它」。
    """

    #: 0 成本节点**只在有尺子时**有 —— 典型是 `RULES`（判据表自身的指纹）。
    prompts: dict[str, Fp10] | None = None


class LlmProvenance(Provenance):
    """付费节点的来源账。`prompts` 与 `model` 都是必填。"""

    #: 插值**之后**的 system prompt 指纹。
    #:
    #: ⚠ 存渲染结果不存模板：一个模板 + 一份枚举表 = 一个 prompt，
    #: **枚举表变了而模板没变，发出去的东西就变了**。
    #: ⚠ 判据表自身的指纹**统一叫 `RULES`**——原型有 `RULES`/`RULES7`/`RULES8` 三个名字，
    #: 而它们是同一件事（`Q-6`：同一个语义必须叫同一个名字）。
    prompts: dict[str, Fp10] = Field(min_length=1)

    model: str = Field(min_length=1)

    def to_human_edit(self, *, code: str, input_basis: str | None = None) -> FreeProvenance:
        """`--apply-edit` 落人工稿。`_common.md` §5.3 —— 原型这一份是对的。

        继承上一轮的 `inputs`（改的是同一份上游下的稿）· **丢掉 `prompts` 与 `model`**
        （这一稿不是任何 prompt / 模型产的，记了就是假账）· 记 `origin='human'`。
        """
        return FreeProvenance(
            code=code,
            root_clean_sha1=self.root_clean_sha1,
            inputs=self.inputs,
            origin='human',
            input_basis=input_basis,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 一之二、轮次账 —— **stage5 专有**（`_common.md` §六）
# ─────────────────────────────────────────────────────────────────────────────
#
# 放子类不放基类：这四个键在另外七层里**恒缺席**，而恒缺席的字段是噪声——
# 同 `op` 那一族的下场（一个常量伪装成 artifact 字段）。
#
# ⚠ 盘上实测确认它有**两种**形状：付费产出的那一份带 `prompts`/`model`，
#   而 `--apply-edit` 落的人工稿带 `origin`/`input_basis` 却**没有** `prompts`/`model`，
#   同时仍然带着 `round` 与 `revision_of`（盘上 1 份，键组合逐字为
#   `['code','input_basis','inputs','origin','revision_of','round']`）。
#   ⇒ 轮次账必须能与**两种**来源账各组合一次，所以它是 mixin 不是子类。


class _RoundFields(StrictModel):
    """轮次账的四个键。不单独使用——与两种来源账各组合一次。"""

    #: ⚠ **盘上见过的最大轮次 + 1**，含 `.INVALID` 与 `_rounds/`；
    #: **不是 `len(glob) + 1`**——后者在归档缺号时会重号覆盖。
    round: int = Field(ge=1)

    #: 上一轮**正文**的指纹。
    #: ⚠ 用正文的指纹而不是整份文件的：来源账/`warns`/归一这类 0 成本元数据修复
    #: 会改整份文件的指纹，而**写手一个字都没再写**——拿整份指纹去推收敛计数，
    #: 等于把一次重判记成「写手又试了一次」。
    revision_of: str | None = None

    #: 触发这一轮的报告指纹。
    review: str | None = None

    #: 这一轮针对性下发了哪几条，形如 `[[判据, 凭据键], …]`。
    #: ⚠ **只存引用不存渲染后的指令文本**——指令由判据表生成，存第二份就会跟着表漂。
    fixes: list[tuple[str, str]] | None = None


class RoundLlmProvenance(LlmProvenance, _RoundFields):
    """stage5 逐章成稿（模型产的那一份）。"""


class RoundFreeProvenance(FreeProvenance, _RoundFields):
    """stage5 逐章成稿（`--apply-edit` 落的人工稿）。盘上 1/31。"""


# ─────────────────────────────────────────────────────────────────────────────
# 二、结论（`_common.md` §七）—— 全链一份，14 个字段
# ─────────────────────────────────────────────────────────────────────────────

class WontFixNote(FrozenModel):
    """一条结论上挂着的「这条我不修」。`why` 必填——写不出理由的豁免，
    与「没看过」在盘上没有区别。

    ⚠ **它是结论上的标记，不是那份账本**——账本是下面的 `WontFix`，
    落在 `<stage>/wontfix.json`。两者的字段刻意不同：账本要能反查（带 `rule` /
    `evidence_key` / `detail`），标记只要能在报告和界面上说清「有人看过并决定不修」。
    """

    why: str = Field(min_length=1)

    #: **人做这个决定时看的是哪一版结论。**形如 `review_4` / `semantic_2:9f1c2ab0d3`。
    #:
    #: ⚠⚠ **它是审计账，不参与豁免的匹配**（2026-08-15 裁决）。
    #: 匹配只按身份 `(层, 章|book, 判据, 证据键)` ——层由**文件路径**承载
    #: （`<stage>/wontfix.json`），其余三段是键。
    #: 把审判版本也塞进匹配键的后果很具体：`stage6.semantic`
    #: 是**付费**节点、重跑是常态，而一次不改变任何结论的重审会把
    #: 「有人看过并决定不修」这条账**整批清空** ⇒ 等于没有豁免。
    #: ⚠ 反过来，失效由**证据自身**保证而不是靠版本：两条语义判据的证据
    #: 都是当前候选文案/正文的连续原字节，文本一改证据就变、键就失配。
    at: str = Field(min_length=1)


class WontFixEntry(StrictModel):
    """账本里的一条「这条我不修」。**盘上零样本**（没有一本有 `wontfix.json`）。

    键与**收敛键同构**（`<章|book>|<rule>|<evidence_key>`）——
    只按 `rule` 记会把「换了个位置的同类问题」一并豁免掉，**那不是人做的决定**。

    三条约束：
      · **`why` 必填**——写不出理由的豁免，与「没看过」在盘上没有区别；
      · **不给不存在的结论发豁免**——该层当前结论池里没有那条 ⇒ 显式终态；
      · **豁免不删结论**——照常出现在报告与界面上，只是不进反哺、不占「待人工」，并单独计数。

    ⚠ **它从 `stage5.py` 搬到本模块，是因为语义结论的豁免闭环把它变成了跨层机制**
    （2026-08-15 裁决）：stage5 与 stage6 各有一份 `<stage>/wontfix.json`，
    形状必须只有一份——同一件事的第 N 个落点必然分家。
    """

    output_ordinal: int | None
    rule: str = Field(min_length=1)
    evidence_key: str
    why: str = Field(min_length=1)
    detail: str

    #: **人做这个决定时看的是哪一版结论**（同 `WontFixNote.at`：审计账，不参与匹配）。
    at: str = Field(min_length=1)


class WontFix(RootModel[dict[str, WontFixEntry]]):
    """`<stage>/wontfix.json` —— **顶层就是那张映射**（`{收敛键: 条目}`），没有包一层。

    ⚠ **层由文件路径承载，不进键**：身份的完整形态是 `(层, 章|book, 判据, 证据键)`，
    而每层各有自己的一份账 ⇒ 键里再写一遍层名就是同一个信息的第二个落点。
    """

    @model_validator(mode='after')
    def _key_agrees_with_the_entry(self) -> WontFix:
        """键是收敛键 `<章|book>|<rule>|<evidence_key>` —— 它与条目里的字段必须一致。

        ⚠ 不一致时豁免会挂在另一条结论上，而**豁免不删结论**、报告照常打印它，
        于是「人豁免过」与「人没看过」在界面上同形。
        """
        for key, e in self.root.items():
            head = 'book' if e.output_ordinal is None else str(e.output_ordinal)
            want = f'{head}|{e.rule}|{e.evidence_key}'
            if key != want:
                raise ValueError(f'wontfix 键 {key!r} 与条目算出来的收敛键 {want!r} 对不上')
        return self


class Finding(FrozenModel):
    """一条结论。stage5 / stage6 / stage7 **逐字段相同**。

    ⚠ 原型里它有 **5 份拷贝、3 种字段签名**，而「同一件事的第 N 个落点必然分家」。

    ⚠ **不要直接构造它。** 走判据注册表的工厂：`sev` / `item` / `owner` / `actionable`
    从注册表取，调用方只给 `rule` + `detail` + `evidence`
    （以及规则明确声明的带值修复实参）。
    这样一条 finding **不可能与判据表说的不一致**。
    """

    #: 逐章结论所属输出章序；书级结论显式为 ``None``。
    output_ordinal: int | None = Field(default=None, ge=1)

    rule: str = Field(min_length=1)
    sev: Severity
    item: str = Field(min_length=1)
    detail: str = Field(min_length=1)

    #: **机器可比**的凭据（不是给人读的散文）。
    evidence: list[str]

    #: 本次命中为 `Rule.fix` 补出的带值实参；键是 flag，值是已经解析好的 CLI 字符串。
    #: 路由仍只在 `Rule.fix`，这里绝不重复 node 或无值 flag。
    fix_args: dict[str, str] = Field(default_factory=dict)

    #: `'|'.join(evidence)`。**收敛键 = (章, rule, evidence_key)**。
    evidence_key: str

    #: **`None` ⇔ 它不是结论**（`metric` / `unver`）。
    #: 这条不变量在并表时抓出过一个 `sev` 写错的判据。
    owner: Owner | None

    #: **蕴含 `owner == 'stage5'`**。⚠ 它不等于「会进反哺」——还要过准入。
    actionable: bool

    #: 这一章的**源文**也触发了同一条 ⇒ 这条结论不携带信息。
    identity_hit: bool

    #: 书级：这条判据在这本书上**零判别力**。
    identity_dead: bool | None = None

    converge: str
    unresolved_rounds: int = Field(ge=0)
    wontfix: WontFixNote | None = None

    @field_validator('detail', mode='before')
    @classmethod
    def _detail_is_plain_text(cls, value):
        """报告契约传纯文本，不把显示职责塞回 finding producer。"""
        return plain_report_text(value)

    @model_validator(mode='after')
    def _postconditions(self) -> Finding:
        """三条后置条件。它们是**载体**在守，不是判据——因为 finding 由我们自己的代码
        构造，拒收的代价是当场崩（一个 bug），不是再花一次钱。
        """
        expect = '|'.join(self.evidence)
        if self.evidence_key != expect:
            raise ValueError(
                f'{self.rule}: evidence_key={self.evidence_key!r} 不等于凭据的连接 {expect!r}。'
                f'收敛键靠它算，对不上会让「同一个问题」被数成两次'
            )
        is_conclusion = self.sev in ('hard', 'info')
        if is_conclusion != (self.owner is not None):
            raise ValueError(
                f'{self.rule}: sev={self.sev!r} 与 owner={self.owner!r} 对不上。'
                f'`owner is None` ⇔ 它不是结论（metric/unver）——'
                f'这条不变量抓出过一个 sev 写错的判据'
            )
        if self.actionable and self.owner != 'stage5':
            raise ValueError(
                f'{self.rule}: actionable=True 但 owner={self.owner!r}。'
                f'根因不在写作层的条目，重写一百遍也修不好'
            )
        bad_args = {k: v for k, v in self.fix_args.items()
                    if not k.startswith('--') or not isinstance(v, str) or not v.strip()}
        if bad_args:
            raise ValueError(
                f'{self.rule}: fix_args 必须是「--flag → 非空 CLI 值」，实得 {bad_args!r}')
        return self
