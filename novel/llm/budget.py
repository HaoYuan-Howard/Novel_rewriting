#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""供应商上下文预算的唯一登记表与请求前预算闸。

闸门计算的是供应商真正收到的聊天 prompt，而不是字符数：固定版本的 DeepSeek V4
tokenizer 加官方两消息 framing。它必须在缓存查找之前运行；否则一份已经存在的超限
缓存会让同一个非法请求在部分机器上通过、在另一台机器上上线失败。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from importlib import resources
from typing import Callable, Final, Sequence

from novel.errors import ContextBudgetRefused
from novel.llm.transport import ChatRequest


TOKENIZER_REVISION: Final = '0e1a0e5e52aea73055f50fef6f2423db370265b6'
TOKENIZER_SHA256: Final = '8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf'
TOKENIZER_RESOURCE: Final = 'resources/deepseek_v4_tokenizer.json'
TOKENIZER_LABEL: Final = f'deepseek-v4@{TOKENIZER_REVISION[:7]}'

_BOS: Final = '<｜begin▁of▁sentence｜>'
_USER: Final = '<｜User｜>'
_ASSISTANT: Final = '<｜Assistant｜>'
_THINKING_START: Final = '<think>'
_THINKING_END: Final = '</think>'
#: `max` 档的官方 effort 前缀。**低/高档不加文字前缀，只有 `max` 在 `_BOS` 之前加这一段。**
#:
#: ⚠⚠ 这是**发出去的字节**（服务端按 `reasoning_effort=max` 自己加，本函数复刻它只为
#: 把输入 token 数算准），凭记忆重写等于按另一个 prompt 估预算。它于 `4744479`
#: 随「零引用死代码」被删过一次，2026-08-21 为质量实验（FC-13：max 能不能切细拍）
#: **从 `4744479^:novel/llm/budget.py` 原样取回**，逐字节未改：476 字符 / 79 token。
#: ⚠ 启用它是**升档**：多 79 token 输入，且前缀明令写出完整推敲过程 ⇒ reasoning 会涨。
#: 它进的是 `rungs_from_effort` 的**覆盖梯**（max 起步、再降进 `LADDER`），
#: **不进 `LADDER` 本身**——那条梯扛的一类故障就是「推理吃光 max_tokens」，
#: 拿升档去救它是火上浇油。
_MAX_EFFORT_PREFIX: Final = (
    'Reasoning Effort: Absolute maximum with no shortcuts permitted.\n'
    'You MUST be very thorough in your thinking and comprehensively decompose the problem '
    'to resolve the root cause, rigorously stress-testing your logic against all potential '
    'paths, edge cases, and adversarial scenarios.\n'
    'Explicitly write out your entire deliberation process, documenting every intermediate '
    'step, considered alternative, and rejected hypothesis to ensure absolutely no assumption '
    'is left unchecked.\n\n'
)


@dataclass(frozen=True)
class ModelBudgetSpec:
    model: str
    context_limit: int
    max_output_tokens: int
    tokenizer_revision: str = TOKENIZER_REVISION


@dataclass(frozen=True)
class BudgetEstimate:
    model: str
    input_tokens: int
    max_tokens: int
    context_limit: int
    max_output_tokens: int
    tokenizer_revision: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.max_tokens


MODEL_BUDGETS: Final[dict[str, ModelBudgetSpec]] = {
    model: ModelBudgetSpec(model=model, context_limit=1_000_000,
                           max_output_tokens=384_000)
    for model in ('deepseek-v4-pro', 'deepseek-v4-flash')
}


def _refusal(message: str, *, model: str, input_tokens: int | None = None,
             max_tokens: int | None = None, spec: ModelBudgetSpec | None = None
             ) -> ContextBudgetRefused:
    return ContextBudgetRefused(
        message,
        model=model,
        input_tokens=input_tokens,
        max_tokens=max_tokens,
        context_limit=None if spec is None else spec.context_limit,
        max_output_tokens=None if spec is None else spec.max_output_tokens,
        tokenizer_revision=None if spec is None else spec.tokenizer_revision,
    )


def model_budget(model: str) -> ModelBudgetSpec:
    try:
        return MODEL_BUDGETS[model]
    except KeyError:
        known = '、'.join(sorted(MODEL_BUDGETS))
        raise _refusal(
            f'模型 {model!r} 没有已验证的上下文规格/tokenizer，预算预检关闭式拒绝。'
            f'行动：先在 MODEL_BUDGETS 登记供应商上限和固定 tokenizer；当前已登记：{known}。'
            '本版本不会猜测 token 比例、自动换模型或试着花钱碰运气。',
            model=model,
        ) from None


def _verified_tokenizer_bytes(data: bytes, *, expected_sha256: str = TOKENIZER_SHA256) -> bytes:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise _refusal(
            f'{TOKENIZER_LABEL} tokenizer 资源校验失败：expected_sha256={expected_sha256}，'
            f'actual_sha256={actual}。行动：恢复固定提交中的官方资源后重试；'
            '校验未恢复前不读取缓存、不发请求。',
            model='deepseek-v4-*',
        )
    return data


@lru_cache(maxsize=1)
def _tokenizer():
    """懒加载 optional 依赖；0 成本、未调用 LLM 的路径不要求安装它。"""
    try:
        raw = resources.files('novel.llm').joinpath(TOKENIZER_RESOURCE).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise _refusal(
            f'{TOKENIZER_LABEL} tokenizer 资源不可读（{type(exc).__name__}: {exc}）。'
            '行动：重新安装包含 novel.llm/resources 包数据的 llm 或 dev 依赖；'
            '资源恢复前不读取缓存、不发请求。',
            model='deepseek-v4-*',
        ) from exc
    _verified_tokenizer_bytes(raw)
    try:
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise _refusal(
            '精确 token 预算需要 optional 依赖 `tokenizers`。'
            '行动：安装项目的 `llm` 依赖组；未安装时不使用字符比例近似放行。',
            model='deepseek-v4-*',
        ) from exc
    try:
        return Tokenizer.from_str(raw.decode('utf-8'))
    except Exception as exc:  # tokenizers 的解析异常类型不属于稳定 API
        raise _refusal(
            f'{TOKENIZER_LABEL} tokenizer 无法解析（{type(exc).__name__}: {exc}）。'
            '行动：恢复固定资源或兼容的 tokenizers 版本；当前请求未产生副作用。',
            model='deepseek-v4-*',
        ) from exc


def _render_prompt(req: ChatRequest) -> str:
    """DeepSeek V4 官方 ``encode_messages`` 对 system + user 请求的精确子集。

    low/high 都采用官方 ``high`` framing（它们不增加文字前缀）；``max`` 在 `_BOS`
    之前多一段 `_MAX_EFFORT_PREFIX`（79 token）。JSON mode 是顶层 API 控制参数，
    不擅自把不可观测的服务端隐藏文字算进 prompt。

    ⚠ **未登记的档位一律关闭式拒绝**，不猜一个 framing 了事：prompt 是发出去的字节，
    算错 framing 就等于按另一个 prompt 估预算。守卫见 `tests/test_llm_budget.py`。
    ⚠ `medium` / `xhigh` 仍然拒绝，**而理由不是「API 不认」**——它们合法，只是被服务端
    静默映射回 `high`。放行等于让一个「降了档」的配置一档没降，而账本上分辨不出来
    （同 `novel/llm/client.py::rungs_from_effort` 那道闸，两处说的是同一件事）。
    ⚠ `max` 只在**不关思考**时有前缀：那段文字要求写出完整推敲过程，
    `thinking=False` 时没有推敲流可写 ⇒ 前缀无处落地，按无前缀算
    （与删它之前那一版的条件逐字相同：`req.thinking and effort == 'max'`）。
    """
    if req.reasoning_effort not in ('low', 'high', 'max'):
        raise _refusal(
            f'未知 reasoning_effort={req.reasoning_effort!r}，无法选择官方聊天 framing。'
            '行动：登记该档位的官方编码规则后再启用。',
            model=req.model,
            max_tokens=req.max_tokens,
            spec=MODEL_BUDGETS.get(req.model),
        )
    prefix = (_MAX_EFFORT_PREFIX
              if req.thinking and req.reasoning_effort == 'max' else '')
    tail = _THINKING_START if req.thinking else _THINKING_END
    return f'{prefix}{_BOS}{req.system}{_USER}{req.user}{_ASSISTANT}{tail}'


def count_input_tokens(req: ChatRequest) -> int:
    """按固定 tokenizer 与官方消息 framing 返回精确输入 token 数。"""
    model_budget(req.model)  # 未知模型必须在加载资源之前拒绝
    return len(_tokenizer().encode(_render_prompt(req), add_special_tokens=False).ids)


def validate_budget(estimate: BudgetEstimate, *, node: str, purpose: str) -> BudgetEstimate:
    """验证已经计算出的预算；独立函数让等界语义不依赖巨型测试字符串。"""
    if estimate.max_tokens > estimate.max_output_tokens:
        raise _refusal(
            f'{node}/{purpose}: 请求预算预检拒绝。计算口径={TOKENIZER_LABEL} 官方消息 framing；'
            f'input_tokens={estimate.input_tokens}，declared_max_tokens={estimate.max_tokens}，'
            f'total={estimate.total_tokens}，context_limit={estimate.context_limit}，'
            f'max_output_tokens={estimate.max_output_tokens}。声明输出已超过供应商单次输出上限。'
            f'行动：把 max_tokens 降到 {estimate.max_output_tokens} 以下；本版本不截断、'
            '不分块聚合、不自动换模型。',
            model=estimate.model,
            input_tokens=estimate.input_tokens,
            max_tokens=estimate.max_tokens,
            spec=ModelBudgetSpec(estimate.model, estimate.context_limit,
                                 estimate.max_output_tokens, estimate.tokenizer_revision),
        )
    if estimate.total_tokens > estimate.context_limit:
        allowed_input = estimate.context_limit - estimate.max_tokens
        raise _refusal(
            f'{node}/{purpose}: 请求预算预检拒绝。计算口径={TOKENIZER_LABEL} 官方消息 framing；'
            f'input_tokens={estimate.input_tokens}，declared_max_tokens={estimate.max_tokens}，'
            f'total={estimate.total_tokens}，context_limit={estimate.context_limit}，'
            f'max_output_tokens={estimate.max_output_tokens}。'
            f'行动：缩短整本正文/提示，使 input_tokens <= {allowed_input}，即完整输入 + '
            f'{estimate.max_tokens} 声明输出不超过 {estimate.context_limit}；本版本不截断、'
            '不分块聚合、不自动换模型。',
            model=estimate.model,
            input_tokens=estimate.input_tokens,
            max_tokens=estimate.max_tokens,
            spec=ModelBudgetSpec(estimate.model, estimate.context_limit,
                                 estimate.max_output_tokens, estimate.tokenizer_revision),
        )
    return estimate


TokenCounter = Callable[[ChatRequest], int]
BudgetChecker = Callable[..., BudgetEstimate]


def enforce_request_budget(requests: Sequence[ChatRequest], *, node: str, purpose: str,
                           counter: TokenCounter = count_input_tokens) -> BudgetEstimate:
    """在一条降级阶梯开始前验证所有不同 framing，返回最坏一档的预算。"""
    if not requests:
        raise ValueError(f'{node}/{purpose}: 预算预检收到空请求组')
    estimates: list[BudgetEstimate] = []
    seen: set[tuple[str, str, bool, int]] = set()
    for req in requests:
        spec = model_budget(req.model)
        key = (req.model, req.reasoning_effort, req.thinking, req.max_tokens)
        if key in seen:
            continue
        seen.add(key)
        estimates.append(BudgetEstimate(
            model=req.model,
            input_tokens=counter(req),
            max_tokens=req.max_tokens,
            context_limit=spec.context_limit,
            max_output_tokens=spec.max_output_tokens,
            tokenizer_revision=spec.tokenizer_revision,
        ))
    worst = max(estimates, key=lambda item: item.total_tokens)
    for estimate in estimates:
        validate_budget(estimate, node=node, purpose=purpose)
    return worst
