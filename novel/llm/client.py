#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client.py —— `ask_text` / `ask_json`。**重采与 Flash→Pro 兜底只有一份，两个入口共用。**

【内容恢复处理过三类实测故障；旧降档结论保留在这里，说明为什么现已取消】

  ① **答案流丢了。** DeepSeek 会把整段答案写进 `reasoning_content` 而 `content` 返回**空串**，
     `finish_reason='stop'`——**不是截断**。实测：模型把整章正文写进了推理里。
     ⚠⚠ **2026-08-22 起 `ask_json` 这一侧会把它接住**（`SALVAGE_ANSWER_FROM_REASONING`，
       用户裁决，历史见 `docs/DECISIONS.md` 2026-08-22）：`content` 空而 `reasoning` 那段**整体过得了同一套闸**
       （`json.loads` → `require` → `model_validate` → 调用点 `validator`）时**采纳它**，
       不再重采一次。依据是留证 5/5 份横跨两个 `json_mode` 臂——推理通道里那段整体就是
       一份合法 JSON，23 章齐全、括号配平、一个字推敲过程都没混进去，其中 1 份完全合
       当时的 Stage4 严格 JSON 契约。⇒ 老理由「里面混着推敲过程、救回来是半成品」**已证伪**，
       它的射程其实只到「从没测过」：空响应从前根本不落盘，没有任何人看得见那一段。
     ⚠ **`ask_text` 那一侧仍然不抠，而且这不是遗漏**：它一次都没量过，且**没有等价的
       客观闸**——JSON 那边「解析得动 + 合契约」是可判的证据，自然语言那边「这段是答案
       还是推敲」判不出来。两条边界与两条已知代价都写在 `SALVAGE_ANSWER_FROM_REASONING`。
     ⚠ **通用非 Stage2 路径里，捞不动时走法不变**：`_answer_lost` 与
       `_worth_resampling` 仍负责原来的内容采样策略；Stage2 的例外见本段末尾。
     ⇒ 2026-08-22 起它在账上**有自己的名字**（闸名 `answer_lost`，见 `REJECT_GATES`），
       且整份响应会落一份留证（`novel/paths.py::answer_lost_path`）。在此之前它与
       ②「JSON 被截断」共用 `parse` 一格，而这两类的处置正好相反。
     ⇒ 2026-08-20 起，通用非 Stage2 路径先**同档重采一次**
       （`MAX_SAMPLES_PER_RUNG`）；
       2026-08-24 起同档仍失败改由 Pro **保持同配置**兜底，不再自动降 effort；
     2026-08-22 起**除截断外的每一种拒绝**都走这条重采（见 `_worth_resampling`）。
     实测（flash 迁移，三本书）：`stage4.plan` 的 `high` 档空正文而 `reasoning_chars`
     上万、`finish_reason=stop`，旧阶梯把它当「这一档不行」直接降档 ⇒ 3 本里 2 本的
     计划**从未在 `high` 档被评估过**，一上来就掉进取证能力受损的 `low`。
     温度采样下同参数重发拿到的是新样本；换 Pro 换的是模型能力——两件事。
     ⚠ `prefer_content_over_reasoning_salvage=True` 的响应契约只把首份合法救回稿
       **延迟到同档第二采再裁决**：正常正文成功就采用正常正文；第二采仍不可用就采用
       已过完整契约的后备，不再进入 Pro 兜底。它只改变采纳顺序，不放宽任何一道 JSON 闸。

  ② **JSON 被截断。** `max_tokens` 在推理模型上**同时约束 reasoning + completion**。
     `reasoning_effort='high'` 时模型可能把大半预算花在思考上，正文写到一半就到顶了，
     于是**截断表现为 JSON 解析失败**（`finish_reason='length'`）。
     ⇒ 旧实现靠降 effort **给正文腾预算**，但也把异常静默改成了取证能力更弱的响应；
       现实现不再交换质量，直接让 Pro 以同 effort / thinking 接手。
     ⚠ 它仍是**唯一不做同档重采**的一类：同参数重采只会把推理再写满一遍。

  ③ **合法 JSON 但结构上是空的。** 这一类与①②**必须走同一条恢复链**（原型 F-125）。
     实测：某次规划调用拿回 `{"reconstruction_strategy": "…"}`——0 个输出章、正常结束、
     4477 token 里推理占 4317。它是合法 JSON，于是一路放行**并进了缓存**；
     旧下游把它摊成 **74 条**派生症状，盖住了「规划结果为空」这一句真正的事实。
     ⚠ 在通用非 Stage2 路径里，它与①同源（想得多、交得少），因此
       **也先同档重采**再由 Pro 兜底——
     `stage4.plan` 实测 `high` 档被拒后旧实现直接降档，白花掉整条阶梯再回 `high` 才成功。

⚠⚠ **原型把响应恢复在两个入口各写了一遍。** 两份逐行相同的东西必然分家——
本文件只有一份 `models_for()` 与默认配置，两个入口共用。

【模型兜底、内容恢复采样与传输层重试是三条独立的循环，不许合并】
模型兜底换的是 **Flash / Pro**，内容恢复采样仍占同一模型配置的一次样本额度，传输层重试扛的是
**网络**（429 / 5xx / 超时）。混成一条，一次网络抖动就会消耗内容采样预算；
而自动降低 effort 还会把显式失败洗成一份取证能力更差的“成功”响应。

通用非 Stage2 的受托 `--response-repair agent` 路径会改变后三次的 **user prompt**，
但不改变上述三层：Flash 第二采定向修复，Pro 首采约束重建，Pro 第二采再定向修复；
模型、effort、thinking、max_tokens 与采样上限均不变。普通 CLI 仍以原 prompt 独立重采。

⚠ **Stage2 `stage2-blueprint-v1` 是显式例外。** 合法 reasoning JSON 立即采用；
统一 batch 后最多一次 Flash 定向恢复、再最多一次 Pro constraint rebuild。若主模型本来
就是 Pro，只允许一次不同 request-mode 的 constraint rebuild；没有同动作 Pro 第二采，
且同一 run 已消费人工 workpack 后不得形成第二次人工暂停。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, replace
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import (
    Any, Callable, Final, Iterable, Iterator, Mapping, Sequence, TypeVar,
)
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from contracts.schema.orchestration import PipelineConcurrency

from novel import config
from novel.errors import (
    ContextBudgetRefused, ProvenanceRefused, ResponseRepairRequired, TerminalState,
)
from novel.llm.budget import BudgetChecker, enforce_request_budget
from novel.llm.cache import (
    CachePolicy, ResponseCache, cache_key, request_fingerprint,
)
from novel.llm.keepsake import write_answer_lost
from novel.llm import response_repair
from novel.llm.json_text import normalize_json_text as _strip_fences
from novel.llm.prompts import required_top_level
from novel.llm.transport import ChatRequest, ChatResponse, Transport, TransportError
from novel.llm.retry import RESOURCE_INTERRUPTED, TransportRetryPolicy
from novel.model_slots import model_slot_from_environment, pipeline_model_slot, ModelSlotUnavailable
from novel.observability import (
    AttemptHeartbeat, DEFAULT_HEARTBEAT_SECONDS, RequestProgress,
)
from novel.run import RunContext
from novel.work_units import budget_for, executor_scope, WorkUnitBudget, WorkUnitRefused, ContentReservation

#: 默认模型行为，只有一档。2026-08-24 用户裁决：**取消自动 effort / thinking 降级**。
#: Flash 的实跑问题主要是能力与稳定性不足；把 `high` 降成 `low` 会让失败变成一份
#: 取证能力更差、却可能通过结构闸的响应。默认因此保持原档位，Flash 同档重采仍失败
#: 就交给 Pro 的**同档位**兜底。Stage2 的内容恢复次数另由
#: ``stage2-blueprint-v1`` 收紧，不复用通用同档二采上限。显式 effort A/B 仍可从
#: `rungs_from_effort()` 选择一档，
#: 辅助调用也仍可显式传 `rungs=`；两者都不是异常驱动的静默降级。
DEFAULT_RUNGS: Final[tuple[tuple[str, bool], ...]] = (('high', True),)

#: 关思考阶梯：**单档**。`('low', False)` 是仓内唯一实测验证过的关思考档
#: （`novel/stages/stage6/hook.py::CANDIDATE_RETRY_RUNGS` 同款；这里曾指向
#: `stage5/write.py::TRIM_RUNGS`，那个常量随收缩臂整臂下线一起删了）；
#: 阶梯扛的两类故障全由推理流引起
#: （见模块 docstring ①②），关掉 thinking 后没有降级对象——复制同档当第二档
#: 等于把「重试」混进「阶梯」，而那两条循环不许合并（见上）。
#: 选用哪条由 `DEEPSEEK_THINKING` 决定（`novel/config.py::THINKING_MODES`）。
NOTHINK_RUNGS: Final[tuple[tuple[str, bool], ...]] = (('low', False),)

#: 模型级兜底。只允许 Flash → Pro；主模型本来就是 Pro（两个钉死节点或显式 A/B）时
#: 不再重复调用同一个模型。未知/实验模型也不被静默改成 Pro，避免把拼错的模型 id
#: 伪装成一次成功兜底；预算预检会在任何缓存/网络副作用之前拒绝它。
FLASH_MODEL: Final = 'deepseek-v4-flash'
PRO_FALLBACK_MODEL: Final = 'deepseek-v4-pro'


def models_for(model: str) -> tuple[str, ...]:
    """主模型及其显式兜底链。**模型选择只有这一处。**"""
    return (model, PRO_FALLBACK_MODEL) if model == FLASH_MODEL else (model,)

#: `ask_json` 还发不发 `response_format={'type':'json_object'}`。**发。**
#:
#: ⚠⚠ **2026-08-22 关过一次，当天改回来了——这一段历史留着是为了别再走一遍。**
#: 关掉的理由是「它买不到我们没有的东西，却疑似带来一个很贵的失败形态」，
#: 当时手上的实证是同场次 A/B 的 `off` 0/4 空 vs `on` 3/7 空（Fisher 单侧 p≈0.21，
#: 方向对但没到显著）。**改回来的理由是那点实证随后就蒸发了**：
#:
#: · 关掉之后又跑了两次 `stage4.plan` 全书整跑，**`json_mode=False` 照样出空正文**
#:   （留证 `1e9edb67` comp 216 = reas 216、只有 `reconstruction_strategy` 0 个输出章；
#:   留证 `98f93fa9` comp 9386 = reas 9386、完整 23 章）。累计 `off` **2/9 空（22%）**
#:   vs `on` 3/7（43%），Fisher 单侧 **p≈0.37 —— 完全不显著**。
#: · 而代价是**确定**的：`json_mode` 进 `cache_payload`（`transport.py::ChatRequest`）
#:   ⇒ 翻这一位让既有响应缓存整批够不着（翻的时候主仓 583 份）。
#:   **一边是没测出来的收益，一边是确定的代价** ⇒ 维持现状。
#:
#: ⚠ **下面这两条读数仍然成立，别连着一起撤回**：
#: · `json_object` **只保证「是合法 JSON」，不保证合乎 schema**——两臂都在同一种
#:   enum 越界上翻车（`G_TURN` / `R_SCENE` / 多出 `rationale_note`）。那是
#:   2026-08-22 内容闸拒因审计与批 4 前缀化的地盘，与这一位无关。
#: · 「答案写完了写进推理通道」**不是 `json_object` 的产物**：5 份留证横跨两个臂，
#:   5/5 都是完整 JSON、零推敲过程（`docs/DECISIONS.md` 2026-08-22）。
#:
#: ⇒ 要再关，先补足样本（两臂各 n≈8 才判得到 p<0.05），别再拿 n=4 那组数当依据。
JSON_OBJECT_MODE: Final = True

#: 正文空、而**推理通道里那一份整体过得了同一套闸**时，采不采纳它。**采纳**（2026-08-22 裁决）。
#:
#: ⚠⚠ **2026-08-22 之前这里的规矩是「不抠」，而那条规矩的理由已经被自己的留证推翻。**
#: 老理由是「reasoning 里混着推敲过程，救回来是半成品」——留证 **5/5 份横跨两个
#: `json_mode` 臂**：推理通道里那段**整体就是一份合法 JSON**，23 章齐全、括号配平、
#: **一个字推敲过程都没混进去**，其中 1 份**完全合当时的 Stage4 严格 JSON 契约**。
#: 也就是说：那一次丢掉的是一份**可用的计划**，然后又付了一次钱去重采。
#: 老理由的射程其实只到「从没测过」——空响应根本不落盘，没有任何人看得见那一段；
#: 是 2026-08-22 的留证（`novel/llm/keepsake.py`）第一次让它落了盘。
#:
#: 【采纳的边界，三条都承重】
#: ① **只在 `ask_json` 这一侧**。`ask_text` 一次都没量过，而它**没有等价的客观闸**：
#:    JSON 那边「解析得动 + 合契约」是可判的证据，自然语言那边「这段是答案还是推敲」
#:    判不出来。⇒ 结构上就不给 `ask_text` 开这条路（捞的代码只在 `ask_json` 里）。
#: ② **走完整的四道闸**（`json.loads` → `require` → `model_validate` → 调用点
#:    `validator`），不是「解析得动就用」。采纳进来的东西必须与正常路径的产物**同质**，
#:    否则这条路就是在契约上开一个后门。
#: ③ **只在 `_answer_lost` 为真时捞**（正文空**且非截断**）。截断那一半的 `reasoning`
#:    自己也是半截的，捞它只会把 ② 的处置（腾预算）搅进来。
#:
#: 【两条已知代价，认下来不掩盖】
#: · **捞成功的那一份仍然进不了缓存**：`ResponseCache.put` 拒写空正文（那是对的），
#:   而把捞出来的文本塞进 `content` 再存等于**改写供应商的响应** ⇒ 缓存里的东西
#:   与账本 `response_sha256` 分家。⇒ 不动普通缓存：通用节点只省**本次重采**；
#:   Stage2 会把通过全闸的章另写 validated checkpoint，同一 `run_id` 的 resume
#:   仍可零 wire 恢复，但它不冒充 cache hit，也不跨逻辑运行复用。
#: · **账上必须分得出来**：`gate_decision` 与 `answer_lost` 两种审计行都带 `salvaged`，
#:   否则事后答不了「这本书有几章的产物是从推理通道里捞出来的」。留证照写不误——
#:   这条路新、有产品语义争议，头几次跑必须能完整复核。
#:
#: ⚠ **翻这一位不会让缓存作废**（与 `JSON_OBJECT_MODE` 的关键区别）：它不进
#: `cache_payload`，因为它一个字都不改请求，只改我们怎么读回来的那一份。
#: ⚠ `_answer_lost` 的分类式不因 salvage 改变；通用非 Stage2 路径仍由
#: `_worth_resampling` 先同档重采再走 Flash→Pro。Stage2 则由
#: ``stage2-blueprint-v1`` 把无材料 answer_lost 直接送 Pro rebuild。
SALVAGE_ANSWER_FROM_REASONING: Final = True

#: 通用非 Stage2 内容/契约拒绝发生时，同一模型配置最多采几个样本。
#: **2 = 首发 + 一次内容恢复采样。**普通 CLI 第二采仍是原 prompt 独立重采；
#: 受托路径第二采显式消费前序失败证据，但不多占第三个样本。
#: Stage2 的 `stage2-blueprint-v1` 在 `ask_json` 内按失败类型进一步收紧该额度。
#:
#: ⚠ 这是「重采」不是「重试」：重试扛**网络**（`_send_attempts`，429/5xx/超时），
#: 重采换的是**同参数下的新样本**，兜底换的是**模型能力**——三条循环各管各的，不许合并
#: （合并一次网络抖动就会烧掉两份采样预算）。`tools/current_corpus_paid_preview.py`
#: 按这一格放大 wire 硬上限——改它必须同批改那边的口径。
#:
#: ⚠ 被内容闸拒绝的响应会 `evict`，真发循环也显式 `use_cache=False`，所以本地响应缓存
#: **不会**替后续重采买单。供应商输入前缀缓存可能命中，但不是本仓可保证的契约；
#: 每一采都必须按一次真实 wire 与最多一份完整输出计入预览上限。
MAX_SAMPLES_PER_RUNG: Final = 2

#: **服务端资源中断**。DeepSeek 在这一格里说的是「这次没做完，可以安全重试」。
#:
#: ⚠⚠ **它归传输层重试那条循环，不归内容闸**（三条循环各管各的，见模块 docstring 末段）。
#: 判据是「这一份是不是一次做完了的请求」，不是「这份内容能不能用」：
#: HTTP 是 200、`usage` 是真的、钱也花了，但服务端自己声明这一次被资源中断了 ⇒
#: 它与 429/5xx/超时同族（等一下再来），**不是** ①答案流丢失（换样本）、
#: 也不是 ②截断（腾预算）。
#: ⇒ 落 `_send_attempts` 的成功分支里退避重试：**不消耗采样预算**
#: （`MAX_SAMPLES_PER_RUNG`）、**不直接消耗模型兜底**、内容闸根本看不到它。
#: 走错循环的代价是不对称的：判成 `answer_lost` 的话，两次资源中断就能把一个付费节点
#: 推进 `low` 档（`stage4.plan` 在 low 上有已知劣化），而它一次模型行为问题都不是。
#:
#: ⚠ **本仓 880 行账本里出现 0 次**（`finish_reason` 只有 `stop` 814 / `length` 30 /
#: `None`+connection 36）⇒ 这一格是**照文档预备的**，不是从实测反推的。
#: 它便宜（一个常量 + 一个分支）而漏了很贵，所以先摆上。
#: ⚠ **不新增账本字段**：账本本来就有 `finish_reason` 列，`grep` 得出来；
#: 而 `will_retry` / `backoff_ms` 两列的语义与网络重试逐字相同。
# RESOURCE_INTERRUPTED 与退避决策由 retry.py 唯一登记。


def _worth_resampling(resp: ChatResponse) -> bool:
    """通用非 Stage2 路径是否再消费一个**同模型配置样本**。

    Stage2 不直接把本函数的答案当恢复策略；`ask_json.worth_content_recovery`
    会再按 gate、模型位置与 ``stage2-blueprint-v1`` 覆盖它。

    规则只有一条例外：**截断（`finish_reason='length'`）不重采。**
    那是模块 docstring ②——同参数重采大概率把推理再写满一遍；现由同配置的 Pro
    兜底，不再自动降低 effort。其余一律再采一次：①的答案流丢失、
    ③的空壳，以及 `parse` / `schema` / `validator` / `empty_text` 这些形状拒绝。

    ⚠ 这里只决定**额度与模型切换**，不决定下一份 prompt：普通 CLI 仍用原 prompt；
    受托路径由 `response_repair.recovery_prompt_variants` 在同一额度内构造定向修复/
    约束重建请求。把 prompt 选择塞回本谓词会让预算、跨 CLI fingerprint 与恢复状态分家。

    ⚠⚠ **这里把 ① 已经确立的结论推广到了 ③ 与内容闸拒，别按旧字面撤回。**
    2026-08-20 版只让 ①（`content` 空 + `stop`）重采，理由写着「非空正文被内容闸拒
    也不重采——那是形状/契约问题，同一参数大概率拿回同一份形状」。三条实测推翻了它：

    · **真发循环 `use_cache=False`** ⇒ 同档重采拿的是新样本，不是缓存里那一份。
      模块 docstring ① 自己就写着「温度采样下同参数重发拿到的是新样本」——
      那句话对 ③ 与内容闸拒同样成立，当时只是没推广过去。同档两次真实调用实测
      completion 29202 vs 33231（`stage4.plan`，2026-08-20），样本确实不同。
    · `stage4.plan` 盘上 5 次带失败的跑里 **3 次是 `high→low→low/nothink→high`**：
      整条阶梯走完**再重来**，而最后成功的那次仍是 `high`。既然第 4 次 high 能过，
      第 2 次 high 就能过——中间两档是白花。
    · 另 2 次以 `high→low` 收尾 ⇒ **最终计划产自降档档位**。那不是省钱是降质；
      当前由 `novel/llm/ledger.py::note_adoption` 的 `artifact_adoption` 事件记录最终采纳档位。

    盘上 22 次实付合计 completion 378 368，其中 **180 334（47.7%）** 花在没被采纳的档位上。

    ⚠ `MAX_SAMPLES_PER_RUNG=2` ⇒ 每档 wire 硬上限是两采，
    `tools/current_corpus_paid_preview.py` 从这个生产常量派生上限。
    """
    return resp.finish_reason != 'length'


def _answer_lost(text: str, resp: ChatResponse) -> bool:
    """这一份是不是模块 docstring ① 的形态：**正文空，而且不是截断。**

    ⚠ 它只决定**闸名**（`answer_lost` 而不是 `parse` / `empty_text`），
    **不决定走哪条路**——是否同档重采仍由 `_worth_resampling` 一家判。两个谓词都看
    `finish_reason` 却各自独立：这里问「这一份是什么」，那里问「下一步做什么」。
    ⇒ 2026-08-22 加这一格时判定式一行没动，走法与从前逐字相同。

    ⚠ 判的是 `!= 'length'` 而不是 `== 'stop'`：要分出来的对立面**只有截断**一个
    （②要腾预算，这一类要换样本）。`stop` 以外那些少见的收尾理由归到哪一边都是猜，
    而猜错的代价不对称——把一次非截断误记成截断，就又把 ① 塞回 `parse` 那一格了。

    ⚠ 传的是**已归一的正文**（`ask_text` 的 `resp.text` / `ask_json` 去掉围栏之后），
    不是 `resp.content`：两个入口对「空」的定义必须与各自闸判的那一份是同一个。
    """
    return not text and resp.finish_reason != 'length'


def rungs_from_effort(effort: str | None) -> tuple[tuple[str, bool], ...]:
    """节点显式选择的一种模型行为；异常不再把它自动改成另一档。

    ``None`` 与 ``high`` 都取默认 high；`low` / `max` 只用于显式节点配置或 A/B。
    `medium`/`xhigh` 虽然 API 收，但服务端映射回 `high`，仍关闭式拒绝，避免账面
    effort 与真实模型行为分叉。
    """
    if effort in (None, 'high'):
        return DEFAULT_RUNGS
    if effort == 'max':
        return (('max', True),)
    if effort != 'low':
        raise ValueError(
            f'节点 effort={effort!r}；可选 low / high / max / None。'
            f'⚠ `medium` 与 `xhigh` 会被服务端静默映射回 `high`——这里关闭式拒绝它们，'
            f'免得一个「降了档」的节点配置其实一档没降')
    return (('low', True),)

#: 默认 token 上限。**统一 64k**：`max_tokens` 在推理模型上同时约束 reasoning + completion，
#: 高预算防止长正文/推理被 API 默认上限**悄悄**截断。确需更小上限时显式传。
DEFAULT_MAX_TOKENS: Final = 64_000

#: 内容闸的机器可数名。**取值面只有这一份**，`llm_rung_rejected` 事件的 `gate` 取自它。
#: 与 `ask_json` docstring 里那三道一一对应，加上 `validator`（可选的第四道），
#: 以及正文为空时那两格——`answer_lost` 与 `empty_text`。
#:
#: ⚠⚠ **`answer_lost` 与 `parse` 曾经是同一格，而它们是两件事。**
#: 空正文照旧进 `json.loads` 必然抛 `JSONDecodeError` ⇒ 模块 docstring ①（答案写进了
#: 推理通道、非截断）与 ②（JSON 被截断）在账上塌成同一个 `parse`。而这两类的处置
#: **本来就相反**：①换新样本（同档重采），②不值得同参数重采——账上却分不出谁是谁，
#: 于是「这个节点到底在坏哪一种」只能靠人去翻 `finish_reason` 手数。
#: 实测代价（2026-08-22 复盘，四份账本 752 次 wire）：flash@high 418 次里 60 次是 ①、
#: pro 255 次里 0 次——一个只在**一个模型的一个档位**上出现的失败模式，
#: 在账面上和「JSON 被截断」长得一模一样。⇒ 按 `finish_reason` 劈开，两边各有名字。
#: ⚠ 劈的是**账**不是**判定**：`_worth_resampling` 一行没动（见 `_answer_lost`）。
#: ⚠⚠ **2026-08-22 起 `answer_lost` 有两种结局**（`SALVAGE_ANSWER_FROM_REASONING`）：
#: 推理通道那份过得了同一套闸 ⇒ **采纳**（审计账 `decision=accepted` + `gate=None` +
#: `salvaged=True`）；过不了 ⇒ 照旧按本表这一格拒。⇒ 数「答案流丢失害我们付了几次重采」
#: 要看 `salvaged` 那一位，光数这个闸名会把被救回来的那些次算成损失。
REJECT_GATES: Final[tuple[str, ...]] = (
    'answer_lost',  # ⓪ 正文空而**不是截断**：答案写进了 reasoning（模块 docstring ①）
    'parse',        # ① json.loads 失败——截断的典型形态（finish_reason='length'）
    'require',      # ② 空壳闸：顶层缺必需键，或值结构上是空的
    'schema',       # ③ model_validate：形状 / 类型 / extra='forbid'
    'validator',    # ④ 调用点自带的那一道（只有部分调用点传）
    'empty_text',   # ⑤ ask_text 专有：正文空**且是截断**（非截断那半归 ⓪）
)

#: 通用受托修复只覆盖「响应里的语义材料仍在，坏的是 JSON/外壳/形状」这一族。
#: `validator` 属于调用点内容契约，可能意味着错章、漏事实或幻觉；通用 agent 不许
#: 在没有 stage 专用编辑面的地方猜。截断与空响应则根本没有完整材料可修。
AGENT_REPAIR_GATES: Final[tuple[str, ...]] = (
    'answer_lost', 'parse', 'require', 'schema',
)


def _agent_repair_eligibility(gate: str, resp: ChatResponse) -> tuple[bool, str]:
    if gate not in AGENT_REPAIR_GATES:
        return False, f'{gate} 不属于通用 JSON/形状修复面'
    if resp.finish_reason == 'length':
        return False, '响应已截断，缺失尾部不能靠修 JSON 猜回'
    if gate == 'answer_lost' and not (resp.reasoning or '').strip():
        return False, '正文与推理通道都没有可修材料'
    if gate != 'answer_lost' and not (resp.content or '').strip():
        return False, '响应正文为空，没有可修材料'
    return True, '完整材料仍在；允许一次受托 JSON/形状修复并按原闸复验'


def _deterministic_json_candidate(text: str) -> str | None:
    """只修两种不改变语义叶子的 JSON 字节问题：UTF-8 BOM 与容器尾逗号。

    不抽取“看起来像 JSON”的子串、不改单引号、不补缺失值：那些都会需要猜模型
    到底想表达什么，必须留给受托 agent 并重新过内容闸。
    """
    source = text.removeprefix('\ufeff')
    out: list[str] = []
    in_string = False
    escaped = False
    index = 0
    changed = source != text
    while index < len(source):
        char = source[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == ',':
            look = index + 1
            while look < len(source) and source[look].isspace():
                look += 1
            if look < len(source) and source[look] in '}]':
                changed = True
                index += 1
                continue
        out.append(char)
        index += 1
    candidate = ''.join(out)
    if not changed:
        return None
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return candidate

#: 结构化拒因摘要最多列几格。摘要要能塞进**一行 JSON**（审计账是 append-only
#: 且要能 `grep`），而一份 23 章的坏计划可以同时踩上百个错。
REJECT_DETAIL_MAX: Final = 20


def _loc_path(loc: tuple[Any, ...]) -> str:
    """`ValidationError` 的 `loc` → 字段路径。**下标一律压成 `[]`。**

    `output_chapters.9.…` 与 `output_chapters.11.…` 是**同一个字段**的两次命中，
    分开记就答不了「是不是同一种错」——而那正是这份摘要唯一的用途
    （历史依据见 `docs/DECISIONS.md` 2026-08-22 的内容闸拒因审计）。
    """
    out = ''
    for part in loc:
        if isinstance(part, int):
            out += '[]'
        else:
            out = f'{out}.{part}' if out else str(part)
    return out


def _schema_detail(exc: ValidationError) -> list[dict[str, Any]]:
    """`ValidationError` → 按 **(字段路径, 错误类型)** 去重计数的摘要。

    ⚠⚠ **只取 `loc` 与 `type`；`input` 不取，`msg` 也不取。**
    `input` 是模型写出来的正文片段，而 `msg` 在好几种错误类型里会把它插值进去
    （`literal_error` 的 msg 里就带着 `input_value=...`）。这份摘要落在
    `_service/llm_audit.jsonl`——它与产物同一棵目录树，正文外发边界（`R-12.x`）
    管的是同一批字节。要看原句去看那一次的响应，不是来翻账。
    ⚠ `loc` 的末段可能是**模型自己编出来的键名**（`extra_forbidden` 那一支），
    那是字段名不是正文，而且它恰恰是这份摘要最想说的东西。

    ⚠ 排序按「命中多的在前」而不是按出现顺序：截断到 `REJECT_DETAIL_MAX` 时，
    留下的应该是**最能解释这一拒**的那几格。
    """
    tally: dict[tuple[str, str], int] = {}
    for err in exc.errors():
        key = (_loc_path(tuple(err.get('loc') or ())), str(err.get('type')))
        tally[key] = tally.get(key, 0) + 1
    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{'loc': loc, 'type': kind, 'count': n}
            for (loc, kind), n in ranked[:REJECT_DETAIL_MAX]]


def _parse_detail(exc: json.JSONDecodeError, content: str,
                  resp: ChatResponse) -> list[dict[str, Any]]:
    """`JSONDecodeError` → 「坏在哪一格、是不是被截断」。

    ⚠ 只取解析器自己那句固定措辞（`exc.msg`，例如 `Expecting ',' delimiter`）与位置，
    **不取正文**。
    ⚠⚠ `truncated` 取自 **`finish_reason`**，不是从 `pos` 推的——这两类的处置不同
    （截断不值得同配置重采；中间就坏了则新样本仍可能有用，见模块 docstring ②），
    所以这一格必须是权威信号。**曾经写成 `pos >= len - 1` 是错的**：
    `Unterminated string starting at` 的 `pos` 指向那个字符串的**起点**而不是文档末尾
    （实测 `{"voice": {"a": 1}, "surface_hab` ⇒ `pos=20`、`len=32`，最典型的截断
    却被判成「中间坏的」）。`pos` / `doc_chars` 仍然留着答「坏在哪」，但别拿它们判截断。
    """
    return [{
        'loc': '', 'type': 'json_decode', 'count': 1,
        'error': exc.msg, 'pos': exc.pos, 'doc_chars': len(content),
        'truncated': resp.finish_reason == 'length',
    }]


#: 响应拒因进事件时的截断长度。`ValidationError` 的文本可以有几千字，而观测事件是
#: **一行 JSON**、要能被作业日志与 `grep` 消化 ⇒ 截断并标记，不整段灌进去。
REJECT_REASON_MAX: Final = 400


M = TypeVar('M', bound=BaseModel)
TextValidator = Callable[[str, ChatResponse], str | None]
JsonValidator = Callable[[M, ChatResponse], str | None]


class LlmFailed(TerminalState):
    """主模型与允许的兜底模型都没拿到可用结果。**显式终态，绝不返回空稿。**

    ⚠⚠ 基类是 `TerminalState` 而不是 `NovelError`，而这一位承重：
    `novel/cli/main.py` 只捕 `TerminalState` / `RefusedByStructure` / `Exception`，
    挂在 `NovelError` 上就落最后那条兜底 ⇒ **exit 70 + 一段栈**，
    编排层与人都会读成「进程崩了，结论未知」。
    而它说的其实是「按设计这一步走不下去了」——`exit_codes.py` 的 `2`，不打栈。
    ⚠ 这不是把 bug 说成正常：真正未捕获的异常仍走 70 且栈留全，两条各有守卫。
    ⚠ `MissingApiKey`（`novel/llm/transport.py`）早就是同样的归属，本条与它对齐。
    ⚠ 归族改了**不影响 HTTP 那一侧**：`novel/api/errors.py` 是
    `STATUS.get(type(exc))` 精确类型查表、不走 MRO，`LlmFailed` 仍只登记在
    `UNREACHABLE_OVER_HTTP`（它只在子进程里抛，父进程只看得到退出码）。
    """


class _ContentRecoveryNeedsWire(Exception):
    """零模型恢复探查已经走完确定性步骤，剩余工作需要新内容请求。"""


def _attempt_id() -> str:
    """一次响应获取事件（一次真上线 attempt 或一次缓存命中）的联结键。

    只求进程内外唯一，不求可复算——它联结的是**账本行与审计行**，
    不是缓存键（那份身份归 `cache_key`，两者不许混）。
    """
    return uuid4().hex[:16]


def _response_sha256(resp: ChatResponse | None) -> str | None:
    """响应正文的内容身份。`None` = 这次 attempt 没拿回响应（传输层失败）。

    ⚠ 空 content 也算：空串的哈希把「拿回了空正文」与「没拿回」分开。
    """
    if resp is None:
        return None
    return hashlib.sha256((resp.content or '').encode('utf-8')).hexdigest()


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _checkpoint_request_binding(req: ChatRequest) -> dict[str, Any]:
    """不保存 prompt 正文，仍能逐字段重建并核验一次完整 ChatRequest。"""
    return {
        'request_model': req.model,
        'reasoning_effort': req.reasoning_effort,
        'thinking': req.thinking,
        'json_mode': req.json_mode,
        'max_tokens': req.max_tokens,
        'temperature': req.temperature,
        'system_sha256': _text_sha256(req.system),
        'user_sha256': _text_sha256(req.user),
        'request_fingerprint': request_fingerprint(req),
    }


def _is_empty(v: Any) -> bool:
    """这个顶层键的值算不算「结构上是空的」。**不是真值判断。**

    `None` / `''` / `[]` / `{}` / 空集合叫空；**`False` 与 `0` 不叫空**。

    ⚠ 原来这里写的是 `not data.get(k)`，于是一个**合法的 `False`**（或 `0`、
    或一个刻意为空的集合语义）会被报成「模型没给这个键」——
    白走四档阶梯、花四次钱，去救一个根本没坏的响应。
    ⚠ 反过来这道闸**不能删**：F-125 那个形态（`{"reconstruction_strategy": "…"}`，
    **0 个输出章**、正常结束、4477 token 里推理占 4317）是**合法 JSON**，
    `model_validate` 对一个 `list` 字段收到 `[]` 也照样放行。
    要让 pydantic 自己拦住它，响应模型得声明 `Field(min_length=1)`——
    而「记得声明」正是判据 39 已经证明过靠不住的那种纪律。
    """
    if v is None:
        return True
    return isinstance(v, (str, bytes, list, tuple, dict, set, frozenset)) and len(v) == 0


def judge_payload(
    text: str,
    resp: ChatResponse,
    *,
    require: Sequence[str],
    model_cls: type[M],
    validator: Callable[[M, ChatResponse], str | None] | None,
) -> tuple[M | None, str | None, str, list[dict[str, Any]] | None]:
    """三道闸（+ 调用点那道可选的第四道）判**一段已经归一过的正文**。

    ⚠ 它与 `inspect_response` 的分工是承重的：这里只回答「这段文本能不能用」，
    **不问它是从哪个通道来的**。⇒ 从 `content` 来的那份与从 `reasoning` 里
    捞出来的那份走的是**同一套闸、同一份 `require`、同一个 `model_cls`**
    （`SALVAGE_ANSWER_FROM_REASONING` 边界②）。两条路各写一遍判定，
    就是给捞出来的那份开了一个自己的契约。

    【它为什么是模块级的】判定面**只许有一份**，而消费方不止 `ask_json` 一个：
    响应修复、机械候选采纳及已保存工作单元回答恢复，都必须走同一套
    parse/require/schema/validator。
    它当初写成 `ask_json` 里的闭包，于是**函数外的任何恢复路都调不到它**——
    那正是「一次有效付费产出只能整本重来」的结构性原因。
    """
    why: str | None = None
    obj: M | None = None
    gate = 'parse'
    detail: list[dict[str, Any]] | None = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        why, detail = f'解析失败: {e}', _parse_detail(e, text, resp)
    else:
        missing = [k for k in require
                   if not isinstance(data, dict) or k not in data
                   or _is_empty(data[k])]
        if missing:
            why, gate = f'顶层缺必需键（或值为空）: {missing}', 'require'
            # 缺的是**顶层必需键**，键名来自 `model_cls` 的派生、不是模型正文。
            detail = [{'loc': k, 'type': 'missing_or_empty', 'count': 1}
                      for k in missing]
        else:
            try:
                obj = model_cls.model_validate(data)
            except ValidationError as e:
                why, gate = f'不合 {model_cls.__name__} 的契约: {e}', 'schema'
                detail = _schema_detail(e)
            else:
                if validator is not None:
                    why, gate = validator(obj, resp), 'validator'
                    # 调用点自带那道闸多数只交回一句人话 ⇒ 不编 detail。
                    # 例外：Stage4 validator 自带 typed witness（批次 1），
                    # 审计追加无正文的 code/chapter/fingerprint 读数。
                    typed = getattr(validator, 'typed_failures', ())
                    if typed:
                        detail = [f.audit_row() for f in typed]
    return obj, why, gate, detail


class LlmClient:
    """一次跑的 LLM 入口。**缓存口径与成本账都从 `RunContext` 来**，不各自持有一份。"""

    def __init__(self, ctx: RunContext, transport: Transport, *,
                 cache: ResponseCache | None = None, model: str = '',
                 thinking_mode: str = config.DEFAULT_THINKING_MODE,
                 effort: str | None = None,
                 max_retries: int = config.DEFAULT_MAX_RETRIES,
                 backoff_base: float = config.DEFAULT_BACKOFF_BASE,
                 concurrency: int = config.DEFAULT_CONCURRENCY,
                 concurrency_policy: PipelineConcurrency | None = None,
                 warmup: bool = True,
                 enable_model_fallback: bool = True,
                 sleep: Callable[[float], None] = time.sleep,
                 heartbeat_interval: float = DEFAULT_HEARTBEAT_SECONDS,
                 clock: Callable[[], float] = time.perf_counter,
                 budget_checker: BudgetChecker = enforce_request_budget) -> None:
        # ⚠ 四个默认值**引用** `novel/config.py`，不在这里再写一份字面量。
        #   那个文件抬头就写着「默认值都在这里，别在调用点再写一份」，而这里原来写了
        #   第二份（`3` / `2.0` / `2` / `'deepseek-v4-pro'`）。两份数分家时不会报错——
        #   它只是让「这次跑到底用的哪个并发度」有两个都说得通的答案。
        self.ctx = ctx
        self.transport = transport
        self.cache = cache if cache is not None else ResponseCache()
        self.model = model or config.DEFAULT_MODEL
        # 产品调用默认启用唯一的 Flash→Pro 模型链。单模型供应商探针与 A/B 工具
        # 必须显式关闭，否则一次兜底会把它们正在测量的模型变量换掉。
        self._models = (models_for(self.model) if enable_model_fallback
                        else (self.model,))
        # 取值闸放构造期（同下面的 concurrency 闸）：手搭一个 client 也拦得住。
        if thinking_mode not in config.THINKING_MODES:
            raise ValueError(
                f'DEEPSEEK_THINKING（thinking_mode）={thinking_mode!r}：'
                f'思考模式只有 {config.THINKING_MODES}')
        self.thinking_mode = thinking_mode
        #: 默认模型行为。思考开关选择唯一档位；显式 `rungs` 的优先级不受它影响。
        self._rungs = (NOTHINK_RUNGS if thinking_mode == 'off'
                       else rungs_from_effort(effort))
        from contracts.invariants import WORK_UNIT_MAX_DELIVERY_ATTEMPTS
        if (not isinstance(max_retries, int) or isinstance(max_retries, bool)
                or not 0 <= max_retries < WORK_UNIT_MAX_DELIVERY_ATTEMPTS):
            raise ValueError('max_retries 必须在 0..3；每内容请求网络尝试最多 4 次')
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleep
        if heartbeat_interval < 0:
            raise ValueError(f'heartbeat_interval 不能为负数，实得 {heartbeat_interval}')
        self.heartbeat_interval = heartbeat_interval
        self._clock = clock
        # 预算闸在两个公开入口、任何缓存读之前运行。测试可注入一把合成的精确计数尺，
        # 但生产默认只有 ``enforce_request_budget`` 这一处供应商规格权威。
        self._budget_checker = budget_checker
        #: 并发闸。⚠ 它守的不是速率，是**钱**：一次配置错误（prompt 漂了）在被人看见之前
        #: 能烧掉多少，与并发度成正比。见 `novel/config.py::DEFAULT_CONCURRENCY`。
        if not 1 <= concurrency <= config.MAX_CONCURRENCY:
            raise ValueError(
                f'concurrency 必须在 1..{config.MAX_CONCURRENCY}，实得 {concurrency!r}')
        self.concurrency = concurrency
        self.concurrency_policy = concurrency_policy or PipelineConcurrency()
        self._gate = threading.Semaphore(self.concurrency)
        #: 首发独占闸。⚠ **与 `_gate` 是两件事**：`_gate` 守的是**并发度**（也就是钱），
        #: 首发顺序闸默认开；Stage6 双判官按登记并发，其余例外只供测试。
        self.warmup = warmup
        self._warmup_lock = threading.Lock()
        self._warmed = False
        self._warm_done = threading.Event()
        #: 每个 purpose 最终被采纳的那次响应（`attempt_id`）。落盘方
        #: （`extract.py` 等）写 `artifact_adoption` 行时从这里反查——
        #: 一个 run 里一个 purpose 只问一次，后采纳者覆盖前者即是正确语义。
        self._adopt_lock = threading.Lock()
        self._accepted: dict[str, str] = {}
        #: 章节检查点所需的 accepted-channel 原文与精确成本联结。它只在内存里活到
        #: producer 把验证后的 payload 原子写进按书隔离的
        #: `_service/runs/<sid>/stage2/`；不进普通 cache 或正式产物。
        self._accepted_checkpoint: dict[str, dict[str, Any]] = {}
        self._accepted_checkpoints_by_attempt: dict[str, dict[str, Any]] = {}
        self._conversation_requests: dict[str, ChatRequest] = {}
        self._work_requests: dict[str, tuple[WorkUnitBudget, ContentReservation]] = {}
        self._producer_cache_receipts: dict[str, dict[str, Any]] = {}
        self._content_local = threading.local()

    @property
    def _purpose_progress(self) -> dict[str, RequestProgress | None]:
        """同 purpose 的跨章并发必须保留各自的请求工作单元。"""
        progress = getattr(self._content_local, 'purpose_progress', None)
        if progress is None:
            progress = {}
            self._content_local.purpose_progress = progress
        return progress

    def _chat_request(self, *, purpose: str, **kwargs: Any) -> ChatRequest:
        """所有预检、缓存、wire 和采纳身份共用完整多轮消息。"""
        raw = ChatRequest(**kwargs)
        seed = request_fingerprint(raw)
        scope = executor_scope(self.ctx.node, purpose, self._purpose_progress.get(purpose))
        conversation_key = scope + ':' + seed
        with self._adopt_lock:
            prior = self._conversation_requests.get(conversation_key)
        if prior is not None:
            return prior
        budget = budget_for(self.ctx, scope=scope)
        original = budget.response_for_logical_request(seed, include_completed=True)
        if original is not None:
            # 重验旧响应时按原多轮请求复算，不能把后来的轮次加进它的输入。
            saved = budget.request_payload(original[0].turn_id)
            messages = saved.get('request', {}).get('messages', [])
            if not isinstance(messages, list) or len(messages) < 2:
                raise WorkUnitRefused('历史请求缺完整多轮消息')
            history = tuple((row['role'], row['content']) for row in messages[1:-1])
            request = ChatRequest(**kwargs, history=history)
            if request_fingerprint(request) != budget.turn(original[0].turn_id).request_sha256:
                raise WorkUnitRefused('历史完整请求与当前逻辑请求不一致')
        else:
            history = budget.conversation_history(system=raw.system, logical_request_sha256=seed)
            request = ChatRequest(**kwargs, history=history)
        with self._adopt_lock:
            self._conversation_requests[conversation_key] = request
        return request

    def mark_checkpointed(self, attempt_id: str) -> None:
        """producer 已验证并落下 checkpoint 后关闭恢复中的候选，缓存无需记内容账。"""
        bound = self._work_requests.get(attempt_id)
        if bound is not None:
            budget, reservation = bound
            budget.mark_adopted(reservation.turn_id)

    def record_candidate_feedback(self, purpose: str, feedback: dict[str, Any]) -> None:
        """生产者明确要求同单元继续修订时，保留前候选及拒因，不返还额度。"""
        attempt_id = self.accepted_attempt(purpose)
        bound = self._work_requests.get(attempt_id)
        if bound is not None:
            budget, reservation = bound
            budget.save_feedback(reservation.turn_id, feedback)

    @contextmanager
    def replay_content_progress(
            self, *, scope: str = 'book', verified_turn_ids: frozenset[str] = frozenset(),
    ) -> Iterator[None]:
        """重放持久业务序列；已采纳轮仅接受 producer 复核过的提交尾窗证明。"""
        budget = budget_for(self.ctx, scope=scope)
        for turn_id in verified_turn_ids:
            budget.turn(turn_id)
            budget.response_payload(turn_id)
            if budget.producer_recovery(turn_id) is None:
                raise ProvenanceRefused('已采纳响应重放缺 producer 提交证明')
        previous = getattr(self._content_local, 'replay_turn_ids', frozenset())
        previous_required = getattr(self._content_local, 'required_replay_turn_ids', frozenset())
        self._content_local.replay_turn_ids = budget.unfinished_content_ids() | verified_turn_ids
        self._content_local.required_replay_turn_ids = verified_turn_ids
        try:
            yield
        finally:
            self._content_local.replay_turn_ids = previous
            self._content_local.required_replay_turn_ids = previous_required

    def _restore_work_response(self, req: ChatRequest, *, purpose: str,
                               progress: RequestProgress | None
                               ) -> tuple[ChatResponse, str] | None:
        budget = budget_for(self.ctx, scope=executor_scope(self.ctx.node, purpose, progress))
        raw = replace(req, history=())
        allowed_completed = getattr(self._content_local, 'replay_turn_ids', frozenset())
        pending = budget.response_for_logical_request(
            request_fingerprint(raw), include_completed=bool(allowed_completed))
        if pending is None:
            return None
        reservation, saved = pending
        prior_attempt = str(saved['attempt_id'])
        if prior_attempt in self._work_requests:
            return None
        if budget.turn(reservation.turn_id).state != 'delivered' and reservation.turn_id not in allowed_completed:
            return None
        original = budget.request_payload(reservation.turn_id)
        messages = original.get('request', {}).get('messages', [])
        if not isinstance(messages, list) or len(messages) < 2:
            raise WorkUnitRefused('历史响应缺完整原多轮消息')
        history = tuple((str(row['role']), str(row['content'])) for row in messages[1:-1])
        original_request = replace(req, history=history)
        if request_fingerprint(original_request) != budget.turn(reservation.turn_id).request_sha256:
            raise WorkUnitRefused('原多轮工作包与内容请求指纹不一致')
        scope = executor_scope(self.ctx.node, purpose, progress)
        with self._adopt_lock:
            self._conversation_requests[scope + ':' + request_fingerprint(raw)] = original_request
        receipt = saved.get('prepared_receipt')
        if receipt is not None:
            self.ctx.ledger.reconcile_work_unit_attempt(budget, reservation.turn_id, receipt)
            budget.record_attempt(reservation.turn_id, attempt_id=prior_attempt, outcome='ok',
                                  request_fingerprint=request_fingerprint(original_request))
        self.ctx.ledger.claim_work_unit_attempts(budget, reservation.turn_id, purpose=purpose)
        response = ChatResponse(**saved['response'])
        self._work_requests[prior_attempt] = (budget, reservation)
        self.ctx.ledger.bind_work_unit_commit(prior_attempt, budget, reservation.turn_id)
        self.ctx.observe('work_unit_response_restored', purpose=purpose,
                         unit_id=budget.unit_id, attempt_id=prior_attempt)
        return response, prior_attempt

    # ── 两个入口 ──────────────────────────────────────────────────────────

    def _active_rungs_for(
        self, rungs: tuple[tuple[str, bool], ...] | None, *, purpose: str,
    ) -> tuple[tuple[str, bool], ...]:
        """选择唯一显式模型配置；多档输入关闭式拒绝，防止降级机制借参数复活。"""
        active = self._rungs if rungs is None else tuple(rungs)
        if not active:
            raise ValueError(f'{self.ctx.node}/{purpose}: rungs 不能为空')
        if len(active) != 1:
            raise ValueError(
                f'{self.ctx.node}/{purpose}: rungs 只允许一个显式配置，实得 {active!r}；'
                '2026-08-24 已取消异常驱动的 effort / thinking 自动降级')
        return active

    def ask_text(self, system: str, user: str, *, purpose: str,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 validator: TextValidator | None = None,
                 rungs: tuple[tuple[str, bool], ...] | None = None,
                 progress: RequestProgress | None = None,
                 cache_policy: CachePolicy | None = None) -> str:
        """纯文本调用。默认保持一个 effort / thinking 档位；Flash 失败时由 Pro
        以同档位兜底。**全空或全被调用点拒绝则抛**。

        `validator` 是纯文本响应声明「可用」含义的边界：返回 `None` 表示可用，
        返回人话理由表示这一份不可用、应驱逐缓存并重采/兜底。它只该判断响应是否完整，
        不能拿文风偏好触发付费重采样。

        `rungs` 只给确有不同成本语义的辅助调用显式选择**一个**配置；空或多档
        都是调用错误，不能伪装成「模型失败」或借参数恢复自动降级。
        """
        self._purpose_progress[purpose] = progress
        active_rungs = self._active_rungs_for(rungs, purpose=purpose)
        active_models = self._models
        self._preflight(system, user, models=active_models, rungs=active_rungs,
                        json_mode=False,
                        max_tokens=max_tokens, purpose=purpose)
        policy = cache_policy or self.ctx.cache
        tried: list[str] = []

        # 先横扫主模型与兜底模型的全部已验证缓存，再发任何新请求。否则盘上已有合格
        # Pro 兜底缓存时，仍会因为 Flash miss 重新付一次钱。
        for model_index, model in enumerate(active_models):
            for i, (effort, thinking) in enumerate(active_rungs):
                got = self._read_cached(
                    system, user, model=model, effort=effort, thinking=thinking,
                    json_mode=False, max_tokens=max_tokens, purpose=purpose,
                    progress=progress, cache_policy=policy)
                if got is None:
                    continue
                resp, key, attempt_id = got
                text = resp.text
                why = None if text else '正文为空'
                gate = 'answer_lost' if _answer_lost(text, resp) else 'empty_text'
                if why is None and validator is not None:
                    why = validator(text, resp)
                    gate = 'validator'
                if why is None:
                    if model_index > 0:
                        self._observe_model_fallback(
                            purpose=purpose,
                            from_model=active_models[model_index - 1],
                            to_model=model, reason='cached_fallback',
                            progress=progress)
                    self._settle_gate(
                        purpose=purpose, attempt_id=attempt_id, source='cache',
                        accepted=True, gate=None, rungs=active_rungs, index=i,
                        progress=progress)
                    return text
                self.cache.evict(key)
                self._settle_gate(
                    purpose=purpose, attempt_id=attempt_id, source='cache',
                    accepted=False, gate=gate, rungs=active_rungs, index=i,
                    progress=progress)
                self._observe_rung_rejected(
                    purpose=purpose, attempt_id=attempt_id, model=model,
                    models=active_models, model_index=model_index,
                    rungs=active_rungs, index=i, source='cache', gate=gate,
                    why=why, resp=resp, json_mode=False, progress=progress)
                tried.append(
                    f'model={model} effort={effort} think={thinking} [缓存] '
                    f'finish={resp.finish_reason} reasoning_len={len(resp.reasoning or "")} '
                    f'拒绝={why}')

        for model_index, model in enumerate(active_models):
            transport_failure: TransportError | LlmFailed | None = None
            for i, (effort, thinking) in enumerate(active_rungs):
                # 自由文本被拒后先同档重采一次；截断除外。默认只有一个模型行为档位，
                # 因而该档耗尽后进入 Pro 兜底，不会自动降低 effort。
                for sample in range(1, MAX_SAMPLES_PER_RUNG + 1):
                    try:
                        resp, key, _cached, attempt_id = self._call_keyed(
                            system, user, model=model, effort=effort,
                            thinking=thinking, json_mode=False,
                            max_tokens=max_tokens, purpose=purpose, use_cache=False,
                            progress=progress, cache_policy=policy)
                    except (TransportError, LlmFailed) as exc:
                        transport_failure = exc
                        break
                    text = resp.text
                    why = None if text else '正文为空'
                    gate = 'answer_lost' if _answer_lost(text, resp) else 'empty_text'
                    if why is None and validator is not None:
                        why = validator(text, resp)
                        gate = 'validator'
                    if why is None:
                        self._keep(key, resp, cache_policy=policy)
                        self._settle_gate(
                            purpose=purpose, attempt_id=attempt_id, source='wire',
                            accepted=True, gate=None, rungs=active_rungs, index=i,
                            sample=sample, progress=progress)
                        return text
                    self.cache.evict(key)
                    resample = sample < MAX_SAMPLES_PER_RUNG and _worth_resampling(resp)
                    self._settle_gate(
                        purpose=purpose, attempt_id=attempt_id, source='wire',
                        accepted=False, gate=gate, rungs=active_rungs, index=i,
                        sample=sample, resample=resample, progress=progress)
                    self._observe_rung_rejected(
                        purpose=purpose, attempt_id=attempt_id, model=model,
                        models=active_models, model_index=model_index,
                        rungs=active_rungs, index=i, source='wire', gate=gate,
                        why=why, resp=resp, json_mode=False, resample=resample,
                        sample=sample, progress=progress)
                    tried.append(
                        f'model={model} effort={effort} think={thinking}'
                        f'{"（重采）" if sample > 1 else ""} '
                        f'finish={resp.finish_reason} reasoning_len={len(resp.reasoning or "")} '
                        f'拒绝={why}')
                    if not resample:
                        break
                if transport_failure is not None:
                    break
            if transport_failure is not None:
                tried.append(f'model={model} 传输/资源重试耗尽：{transport_failure}')
                # 4xx 等不可重试错误说明请求/授权本身有问题，换模型只会再犯一次。
                if model_index == 0 \
                        and isinstance(transport_failure, TransportError) \
                        and not transport_failure.retryable:
                    raise transport_failure
            if model_index + 1 < len(active_models):
                self._observe_model_fallback(
                    purpose=purpose, from_model=model,
                    to_model=active_models[model_index + 1],
                    reason=('transport_exhausted' if transport_failure is not None
                            else 'content_rejected'), progress=progress)
        raise LlmFailed(
            f'{self.ctx.node}/{purpose}: 主模型与兜底模型 '
            f'{" → ".join(active_models)} 均仍然拿不到正文（没有可用响应）。\n  '
            + '\n  '.join(tried)
            + '\n⚠ `reasoning_len` 大而正文为空 = 模型不是没干活，是答案流丢了；'
              '**不从 reasoning 里抠答案**，那里面混着推敲过程，救回来的是半成品')

    def ask_json(self, system: str, user: str, *, model_cls: type[M], purpose: str,
                 require: tuple[str, ...] | None = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 validator: JsonValidator | None = None,
                 rungs: tuple[tuple[str, bool], ...] | None = None,
                 progress: RequestProgress | None = None,
                 cache_policy: CachePolicy | None = None,
                 prefer_content_over_reasoning_salvage: bool = False,
                 recovery_policy: str | None = None) -> M:
        """结构化调用。默认与 `ask_text` 使用同一档模型行为与 Flash→Pro 兜底；
        只有收缩/裁决这类成本语义明确不同的辅助调用可显式收窄 `rungs`。
        解析失败与「结构上是空的」同等对待。

        ⚠⚠ **`model_cls` 是必填的，而 `require` 默认从它派生。**
        原型的 `require=()` 默认值正是那三个漏声明的调用点得以存在的原因——
        **漏传不报错，只是静默停掉一条 HARD 闸门**（判据 39）。派生之后它**结构上不可能忘**。

        显式传 `require` **只许收窄**（子集）：放宽等于绕开上面那条，
        而绕开会长成「这个调用点特殊」的注释，然后没人再回来看它。

        ⚠⚠ **返回的是校验过的模型实例，不是原始 dict。** 原来 `model_cls` 只被拿去
        派生 `require`，**一次 `model_validate` 都没跑过**：于是 `voice='not-a-dict'`、
        `surface_habits=[1]` 这种一路放行，而 `extra='forbid'` 那类载体守卫
        **一次都没响过**。`store.py` 那条主杠杆写着「只有 `store.read` 返回带类型的模型
        ⇒ 绕过意味着同时放弃校验和类型」——`ask_json` 是不可信数据进运行时的**另一扇门**，
        它得守同一条。校验完再退回 dict 等于让调用方读**未校验**的那一份
        （pydantic 会强制转换、填默认值、走别名），那就是同一份响应的两种表示。

        【一档之内三道闸，顺序是挑过的】
          ① `json.loads`      —— 截断就长这样（`finish_reason='length'`）；
          ② **空壳闸**（`_is_empty`）—— F-125，合法 JSON 但结构上是空的；
          ③ `model_validate`  —— 形状/类型/`extra='forbid'`。
        ②在③之前：`require` 是按**盘上键名**（alias）定义的，而且这条路要报得出
        「缺的是哪一个顶层键」——那句话归②，不归 pydantic 的错误文本。
        通用非 Stage2 路径对三道闸采用同一处置：驱逐缓存 + 必要时消费同配置
        恢复采样 + Pro 兜底。Stage2 传入 ``stage2-blueprint-v1`` 后按失败类型改走
        人工 batch / Flash targeted / Pro constraint rebuild，但仍复用同一组三道闸。
        """
        self._purpose_progress[purpose] = progress
        active_rungs = self._active_rungs_for(rungs, purpose=purpose)
        if recovery_policy not in (None, 'stage2-blueprint-v1'):
            raise ValueError(f'{purpose}: 未登记的内容恢复策略 {recovery_policy!r}')
        active_models = self._models
        derived = required_top_level(model_cls)
        if require is None:
            require = derived
        elif extra := sorted(set(require) - set(derived)):
            raise ValueError(
                f'{purpose}: require={sorted(require)} 里的 {extra} 不在 '
                f'{model_cls.__name__} 的必需顶层键 {sorted(derived)} 里。'
                f'显式传只许**收窄**——放宽等于给这个调用点开一个「它特殊」的口子')

        policy = cache_policy or self.ctx.cache
        self._preflight(system, user, models=active_models, rungs=active_rungs,
                        json_mode=JSON_OBJECT_MODE,
                        max_tokens=max_tokens, purpose=purpose)

        repair_chain_id: str | None = None
        legacy_repair_chain_id: str | None = None
        original_repair_request_fingerprints: tuple[str, ...] = ()
        if getattr(self.ctx, 'repair_rejected_responses', False):
            repair_requests = tuple(
                self._chat_request(purpose=purpose,
                    model=model, system=system, user=user,
                    reasoning_effort=effort, thinking=thinking,
                    json_mode=JSON_OBJECT_MODE, max_tokens=max_tokens)
                for model in active_models
                for effort, thinking in active_rungs)
            original_repair_request_fingerprints = tuple(
                request_fingerprint(req) for req in repair_requests)
            chain_args = dict(
                sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                code=self.ctx.code, system=system, user=user,
                models=active_models, rungs=active_rungs,
                json_mode=JSON_OBJECT_MODE, max_tokens=max_tokens)
            repair_chain_id = response_repair.chain_fingerprint(
                **chain_args, run_id=self.ctx.run_id)
            # 旧 rejection 的 chain 身份没有 run_id。只兼容其中同样没有
            # ``run_id`` 字段的真正 legacy 行；过渡版本写出的异 run 新行绝不能
            # 混进当前恢复链，否则一次放弃的 run 会永久锁死相同 prompt。
            legacy_repair_chain_id = response_repair.chain_fingerprint(**chain_args)

        def wire_history_before(
                records: list[response_repair.RepairRecord], *,
                model_index: int, rung_index: int, sample: int,
        ) -> list[response_repair.RepairRecord]:
            """只把更早的真 wire 拒绝编进后继请求；毒缓存不是一次新模型判断。"""
            out: list[response_repair.RepairRecord] = []
            current = (model_index, rung_index, sample)
            for record in records:
                rejection = record.rejection
                prior_sample = rejection.get('sample')
                if (rejection.get('source') != 'wire'
                        or not isinstance(prior_sample, int)):
                    continue
                prior = (
                    int(rejection.get('model_index') or 0),
                    int(rejection.get('rung_index') or 0),
                    prior_sample,
                )
                if prior < current:
                    out.append(record)
            return sorted(out, key=lambda record: (
                int(record.rejection.get('model_index') or 0),
                int(record.rejection.get('rung_index') or 0),
                int(record.rejection.get('sample') or 0),
                record.attempt_id,
            ))

        def recovery_variants_for(
                records: list[response_repair.RepairRecord], *,
                model_index: int, rung_index: int, sample: int,
        ) -> tuple[response_repair.RecoveryPrompt, ...]:
            history = wire_history_before(
                records, model_index=model_index,
                rung_index=rung_index, sample=sample)
            if not history:
                return ()
            # 通用非 Stage2 链：Flash/主模型第二采修最新候选；切到下一模型的
            # 首采按全部失败史重建，该模型自己的第二采再回到定向修复。
            # Stage2 policy 会在预算层截掉 Pro 第二采，但仍复用本函数构造前两种 prompt。
            pro_primary_rebuild = (
                len(active_models) == 1
                and active_models[0] == PRO_FALLBACK_MODEL
                and model_index == 0 and sample > 1)
            strategy = (
                'constraint_rebuild'
                if pro_primary_rebuild or (
                    sample == 1 and any(
                        int(record.rejection.get('model_index') or 0) < model_index
                        for record in history))
                else 'targeted_repair')
            return response_repair.recovery_prompt_variants(
                user, history, strategy=strategy)

        def request_shape(
                *, model: str, effort: str, thinking: bool, request_user: str,
        ) -> ChatRequest:
            return self._chat_request(purpose=purpose,
                model=model, system=system, user=request_user,
                reasoning_effort=effort, thinking=thinking,
                json_mode=JSON_OBJECT_MODE, max_tokens=max_tokens)

        def gate_payload(text: str, resp: ChatResponse) -> tuple[
                M | None, str | None, str, list[dict[str, Any]] | None]:
            """本次调用的四道闸；实现是模块级的 `judge_payload`，这里只绑参数。"""
            return judge_payload(
                text, resp, require=require, model_cls=model_cls,
                validator=validator)

        def inspect_response(resp: ChatResponse) -> tuple[
                M | None, str | None, str, str, list[dict[str, Any]] | None, bool]:
            """⚠ 第四个返回值是**闸名**（`REJECT_GATES` 之一），不是给人读的。
            `why` 那句话事后要靠人读才能归类，而恢复链统计要能机器数。

            ⚠⚠ 第五个是**结构化拒因摘要**，进审计账（`gate_decision.reject_detail`）。
            它与 `why` 的分工是承重的：`why` 只进 stdout 事件，而 stdout **会随进程
            一起没**——2026-08-22 那次整跑被超时杀掉之后，账上只剩 `gate: schema`
            四个字，两次拒到底拒在哪个字段**再也查不出来**（2026-08-22 的内容闸拒因
            审计记的正是这个洞）。⇒ 能机器数的那一半必须落账。

            ⚠⚠ 第六个是 `salvaged`：**这一份正文是从推理通道里捞回来的**
            （`SALVAGE_ANSWER_FROM_REASONING`）。它一路带到 `_settle_gate` 与留证，
            因为事后必须答得了「这本书有几章的产物不是从 `content` 来的」——
            答不了的话，这条路就是在产物里掺了一种看不见的东西。
            """
            content = _strip_fences(resp.content)
            if content:
                obj, why, gate, detail = gate_payload(content, resp)
                return obj, why, content, gate, detail, False
            # ⓪ 空正文若照旧往下走，`json.loads('')` 必然抛 ⇒ 落进 `parse`，
            # 把「答案流丢了」与「JSON 被截断」记成同一格（见 `REJECT_GATES`）。
            # ⚠ 先劈开再解析，而**不是**事后按 `finish_reason` 改写闸名：
            # 那样一来 `parse` 这一格的含义会取决于读的人记不记得改写规则。
            lost = _answer_lost(content, resp)
            salvage_note = ''
            if lost and SALVAGE_ANSWER_FROM_REASONING:
                # ⚠ 捞的是**同一套闸**，不是「解析得动就用」（见
                #   `SALVAGE_ANSWER_FROM_REASONING` 边界②）。
                salvaged_text = _strip_fences(resp.reasoning)
                if salvaged_text:
                    obj, why, _gate, _detail = gate_payload(salvaged_text, resp)
                    if why is None:
                        # ⚠ 闸名仍报 `answer_lost`：这一次**确实**是答案流丢了，
                        #   只是我们把它接住了。采纳与否由 `why is None` 决定，
                        #   而 `salvaged` 那一位让账上分得出这份产物的来路。
                        return obj, None, salvaged_text, 'answer_lost', None, True
                    # ⚠ 捞不动**不改这一次的拒因**：事实仍是「正文为空」。
                    #   捞的拒因只附一句给现场看，不进 `reject_detail`——
                    #   那一格答的是「这一份被拒在哪个字段」，而被拒的是空正文。
                    salvage_note = f'；推理通道那份也不合用（{why[:60]}）'
            return None, '正文为空' + salvage_note, content, (
                'answer_lost' if lost else 'parse'), (
                None if lost
                else [{'loc': '', 'type': 'empty_body_truncated', 'count': 1}]), False

        errors: list[str] = []

        def human_batch_already_resolved() -> bool:
            if recovery_policy != 'stage2-blueprint-v1':
                return False
            from novel.llm import stage2_runs
            return stage2_runs.human_batch_resolved(
                self.ctx.novel_dir, self.ctx.run_id)

        def worth_content_recovery(
                resp: ChatResponse, *, gate: str, model_index: int,
        ) -> bool:
            """Stage2 只留 Flash 定向恢复 + Pro 重建各一次；其余节点保持旧策略。"""
            if recovery_policy != 'stage2-blueprint-v1':
                return _worth_resampling(resp)
            # 显式把主模型配置成 Pro 时没有 Flash→Pro 的第二个 model slot；首采
            # 被拒后仍保留一次**同 Pro、不同 request mode** 的 constraint rebuild。
            # 这不是同配置重采，answer_lost/length 也直接进入这一动作。
            if (len(active_models) == 1
                    and active_models[0] == PRO_FALLBACK_MODEL
                    and model_index == 0):
                return True
            if resp.finish_reason == 'length' or gate == 'answer_lost':
                return False
            # Pro 的第一次已经是 constraint rebuild；再采一份同动作只会重付。
            return model_index == 0

        # 每份 rejection 的实际请求不能信盘上自报；repair_records 会用当前
        # prompt/模型链/档位重建并核验后，把唯一 ChatRequest 留在这里供 agent /
        # deterministic 采纳铸 checkpoint。
        verified_repair_requests: dict[str, ChatRequest] = {}

        def repair_records() -> list[response_repair.RepairRecord]:
            """读取并核验本逻辑请求的跨 CLI 状态，同时恢复未认领的成本窗口。"""
            if repair_chain_id is None:
                return []
            records = response_repair.records_for_chain(
                self.ctx.novel_dir, repair_chain_id)
            if (legacy_repair_chain_id is not None
                    and legacy_repair_chain_id != repair_chain_id):
                records.extend(
                    record for record in response_repair.records_for_chain(
                        self.ctx.novel_dir, legacy_repair_chain_id)
                    if 'run_id' not in record.rejection)
            records.sort(key=lambda record: (
                int(record.rejection.get('model_index') or 0),
                -1 if record.rejection.get('sample') is None
                else int(record.rejection['sample']),
                record.attempt_id,
            ))
            attempt_ids: set[str] = set()
            allowed_fingerprints = set(original_repair_request_fingerprints)
            seen_failure_actions: set[tuple[str, str]] = set()
            for record in records:
                rejection = record.rejection
                signature = rejection.get('failure_signature')
                action = rejection.get('next_recovery_action')
                if isinstance(signature, str) and isinstance(action, str):
                    key = (signature, action)
                    if key in seen_failure_actions:
                        raise TerminalState(
                            f'{self.ctx.node}/{purpose}: 相同失败签名重复执行恢复动作 '
                            f'{action}；run={self.ctx.run_id} 拒绝继续白烧')
                    seen_failure_actions.add(key)
                model_index = rejection.get('model_index')
                rung_index = rejection.get('rung_index')
                expected_chain_id = (
                    legacy_repair_chain_id
                    if 'run_id' not in rejection else repair_chain_id)
                if (rejection.get('sid') != self.ctx.sid
                        or rejection.get('node') != self.ctx.node
                        or rejection.get('purpose') != purpose
                        or rejection.get('code') != self.ctx.code
                        or rejection.get('chain_id') != expected_chain_id
                        or rejection.get('response_model') != model_cls.__name__):
                    raise TerminalState(
                        f'响应修复状态 {record.root} 不属于当前请求/代码/响应契约；'
                        '拒绝把旧候选接到新 prompt 上')
                if ('run_id' in rejection
                        and rejection.get('run_id') != self.ctx.run_id):
                    raise TerminalState(
                        f'响应修复状态 {record.root} 属于 run_id='
                        f'{rejection.get("run_id")!r}，当前={self.ctx.run_id!r}；'
                        '必须显式复用原 run_id')
                if (not isinstance(model_index, int) or isinstance(model_index, bool)
                        or not 0 <= model_index < len(active_models)
                        or rejection.get('model') != active_models[model_index]
                        or not isinstance(rung_index, int) or isinstance(rung_index, bool)
                        or not 0 <= rung_index < len(active_rungs)):
                    raise TerminalState(
                        f'响应修复状态 {record.root} 的模型/档位身份不合法')
                sample = rejection.get('sample')
                if (sample is not None
                        and (not isinstance(sample, int) or isinstance(sample, bool)
                             or not 1 <= sample <= MAX_SAMPLES_PER_RUNG)):
                    raise TerminalState(
                        f'响应修复状态 {record.root} 的 sample={sample!r} 不合法')
                effort, thinking = active_rungs[rung_index]
                expected: list[tuple[str, tuple[str, ...], ChatRequest]] = []
                variants: tuple[response_repair.RecoveryPrompt, ...] = ()
                if isinstance(sample, int):
                    variants = recovery_variants_for(
                        records, model_index=model_index,
                        rung_index=rung_index, sample=sample)
                    expected.extend((
                        variant.request_mode,
                        variant.from_attempt_ids,
                        request_shape(
                            model=active_models[model_index], effort=effort,
                            thinking=thinking, request_user=variant.user),
                    ) for variant in variants)
                    if variants:
                        expected.append((
                            'original:budget_fallback',
                            variants[0].from_attempt_ids,
                            request_shape(
                                model=active_models[model_index], effort=effort,
                                thinking=thinking, request_user=user),
                        ))
                if not variants or 'request_mode' not in rejection:
                    expected.append((
                        'original', (), request_shape(
                            model=active_models[model_index], effort=effort,
                            thinking=thinking, request_user=user)))
                actual_fp = rejection.get('request_fingerprint')
                actual_mode = rejection.get('request_mode', 'original')
                raw_from = rejection.get('recovery_from_attempts', [])
                if (not isinstance(raw_from, list)
                        or not all(isinstance(item, str) for item in raw_from)):
                    raise TerminalState(
                        f'响应修复状态 {record.root} 缺合法 recovery_from_attempts')
                actual_from = tuple(raw_from)
                # 旧 v1 记录没有 request_mode/recovery_from_attempts；它只可能来自
                # 上一版的独立重采，按 original 兼容读取。新记录则必须逐字匹配
                # 由不可变前序失败重建出的请求，不能信盘上自报的 fingerprint。
                matches = [request for mode, from_ids, request in expected
                           if actual_mode == mode and actual_from == from_ids
                           and actual_fp == request_fingerprint(request)]
                if len(matches) != 1:
                    raise TerminalState(
                        f'响应修复状态 {record.root} 的后继请求身份无法由前序失败重建；'
                        '拒绝把旧候选/成本接到当前链上')
                verified_repair_requests[record.attempt_id] = matches[0]
                allowed_fingerprints.add(str(actual_fp))
                ids = rejection.get('attempt_ids')
                if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                    raise TerminalState(f'响应修复状态 {record.root} 缺合法 attempt_ids')
                attempt_ids.update(ids)
            self.ctx.ledger.claim_prior_attempts(
                attempt_ids, sid=self.ctx.sid, node=self.ctx.node,
                purpose=purpose,
                request_fingerprints=allowed_fingerprints)
            return records

        def request_for_sample(
                *, model: str, model_index: int, rung_index: int,
                effort: str, thinking: bool, sample: int,
        ) -> tuple[str, str, tuple[str, ...]]:
            """选择本采的真实 user prompt；每个动态变体在上线/读缓存前精确预算。"""
            if repair_chain_id is None:
                return user, 'original', ()
            variants = recovery_variants_for(
                repair_records(), model_index=model_index,
                rung_index=rung_index, sample=sample)
            if not variants:
                return user, 'original', ()
            rejected_levels: list[str] = []
            for variant in variants:
                try:
                    self._preflight(
                        system, variant.user, models=(model,),
                        rungs=((effort, thinking),), json_mode=JSON_OBJECT_MODE,
                        max_tokens=max_tokens, purpose=purpose)
                except ContextBudgetRefused:
                    rejected_levels.append(variant.detail_level)
                    continue
                self.ctx.observe(
                    'llm_response_recovery_prepared', purpose=purpose,
                    model=model, sample=sample,
                    strategy=variant.strategy,
                    detail_level=variant.detail_level,
                    from_attempts=len(variant.from_attempt_ids),
                    budget_rejected_levels=rejected_levels,
                    chapter=progress.chapter if progress else None)
                return (variant.user, variant.request_mode,
                        variant.from_attempt_ids)
            # 原请求已在本逻辑调用开头连同 Flash/Pro 一起精确预检过。极限长书连
            # 最小诊断都装不下时，宁可保留旧的独立采样能力，也不能在首采已经付费后
            # 因动态恢复包超限把整条链锁死；这一退化在事件与 gate 审计中都显式可见。
            from_ids = variants[0].from_attempt_ids
            self.ctx.observe(
                'llm_response_recovery_prepared', purpose=purpose,
                model=model, sample=sample,
                strategy='original', detail_level='budget_fallback',
                from_attempts=len(from_ids),
                budget_rejected_levels=rejected_levels,
                chapter=progress.chapter if progress else None)
            return user, 'original:budget_fallback', from_ids

        def raise_pending(record: response_repair.RepairRecord) -> None:
            candidate = response_repair.candidate_path(record)
            original = record.root / response_repair.ORIGINAL_FILE
            base = self.ctx.novel_dir.parent
            hint = (
                'python -X utf8 -u -m novel.cli response-repair '
                f'{self.ctx.sid} --base "{base}" --attempt {record.attempt_id} '
                '--actor agent:novel-pipeline-operator --reason "说明修复依据"')
            raise ResponseRepairRequired(
                f'{self.ctx.node}/{purpose}: 一份已付费响应被 '
                f'{record.rejection.get("gate")} 闸拒绝，下一采尚未发出。\n'
                f'  原响应：{original}\n'
                f'  固定候选面：{candidate}\n'
                '先只修 JSON/封装/形状，不猜缺失事实；提交后重跑原节点同闸复验。'
                '若判断不可安全修复，用同一提交命令追加 `--decline`。重跑进入下一采时，'
                '模型会收到这轮不可变失败证据并定向恢复，而不是失忆重来。',
                attempt_id=record.attempt_id,
                candidate_path=str(candidate), original_path=str(original), hint=hint)

        def resolve_record(record: response_repair.RepairRecord) -> tuple[bool, M | None]:
            """消费一次不可变 agent 决定；`True` 表示候选通过并已采纳。"""
            if not record.eligible:
                return False, None
            resolution = record.resolution
            if resolution is None:
                raise_pending(record)
            assert resolution is not None
            action = resolution.get('action')
            if action == 'decline':
                if record.outcome is None:
                    response_repair.write_outcome(
                        record, decision='declined', gate=record.rejection.get('gate'),
                        detail=record.rejection.get('reject_detail'),
                        why='受托 agent 判定不可在不发明内容的前提下安全修复')
                self.ctx.ledger.note_response_repair(
                    sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                    attempt_id=record.attempt_id, action='declined',
                    decision='declined', actor=str(resolution.get('actor') or ''),
                    gate=str(record.rejection.get('gate') or ''),
                    model=str(record.rejection.get('model') or ''),
                    rung_index=record.rejection.get('rung_index'),
                    sample=record.rejection.get('sample'),
                    chapter=record.rejection.get('chapter'))
                return False, None
            if action not in ('submit', 'deterministic') or 'candidate' not in resolution:
                raise TerminalState(
                    f'{record.root / response_repair.RESOLUTION_FILE} 的 action/candidate 不合法')
            if record.outcome is not None and record.outcome.get('decision') == 'rejected':
                # outcome 是先于 audit 落盘的完成事实。若进程恰在二者之间崩溃，
                # resume 也必须以稳定 event_id 补回最终判定，不能因已有 rejected
                # outcome 走快速分支而永久留下审计断口。
                self.ctx.ledger.note_response_repair(
                    sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                    attempt_id=record.attempt_id,
                    action=('deterministic' if action == 'deterministic' else 'agent'),
                    decision='rejected',
                    actor=str(resolution.get('actor') or ''),
                    gate=str(record.outcome.get('gate') or ''),
                    candidate_sha256=str(resolution.get('candidate_sha256') or ''),
                    model=str(record.rejection.get('model') or ''),
                    rung_index=record.rejection.get('rung_index'),
                    sample=record.rejection.get('sample'),
                    chapter=record.rejection.get('chapter'))
                errors.append(
                    f'agent 修复 attempt={record.attempt_id} 已被 '
                    f'{record.outcome.get("gate")} 闸拒绝：{record.outcome.get("why")}')
                return False, None

            candidate_text = json.dumps(
                resolution['candidate'], ensure_ascii=False, separators=(',', ':'))
            raw_response = record.rejection.get('response')
            if not isinstance(raw_response, dict):
                raise TerminalState(f'{record.root} 缺供应商原响应信封')
            repaired_response = ChatResponse(
                content=candidate_text,
                reasoning=raw_response.get('reasoning'),
                finish_reason=raw_response.get('finish_reason'),
                usage=raw_response.get('usage'),
                model=str(raw_response.get('model') or record.rejection.get('model') or ''))
            obj, why, gate, detail = gate_payload(candidate_text, repaired_response)
            decision = 'accepted' if why is None else 'rejected'
            response_repair.write_outcome(
                record, decision=decision, gate=None if why is None else gate,
                detail=None if why is None else detail, why=why)
            self.ctx.ledger.note_response_repair(
                sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                attempt_id=record.attempt_id,
                action='deterministic' if action == 'deterministic' else 'agent',
                decision=decision,
                actor=str(resolution.get('actor') or ''),
                gate=None if why is None else gate,
                candidate_sha256=str(resolution.get('candidate_sha256') or ''),
                model=str(record.rejection.get('model') or ''),
                rung_index=record.rejection.get('rung_index'),
                sample=record.rejection.get('sample'),
                chapter=record.rejection.get('chapter'))
            if why is not None:
                errors.append(
                    f'agent 修复 attempt={record.attempt_id} 仍被 {gate} 闸拒绝：{why}')
                return False, None
            assert obj is not None
            accepted_request = verified_repair_requests.get(record.attempt_id)
            if accepted_request is None:
                repair_records()
                accepted_request = verified_repair_requests.get(record.attempt_id)
            if accepted_request is None:
                raise TerminalState(
                    f'{record.root}: 无法重建 agent 候选所属的实际 ChatRequest')
            self._remember_json_acceptance(
                purpose=purpose, attempt_id=record.attempt_id,
                raw_accepted_text=candidate_text,
                normalized_text=candidate_text,
                channel=('deterministic' if action == 'deterministic' else 'agent'),
                salvaged=False,
                recovery_mode=('deterministic' if action == 'deterministic'
                               else 'agent_submit'),
                request=accepted_request,
                request_mode=str(record.rejection.get('request_mode') or 'original'),
                recovery_from_attempts=tuple(
                    str(item) for item in
                    record.rejection.get('recovery_from_attempts', [])
                    if isinstance(item, str)),
                contributing_attempts=(
                    str(item) for item in record.rejection.get('attempt_ids', [])
                    if isinstance(item, str)),
            )
            return True, obj

        def offer_repair(*, resp: ChatResponse, attempt_id: str,
                         request_fp: str, model: str, model_index: int,
                         rung_index: int, sample: int | None, source: str,
                         request_mode: str,
                         recovery_from_attempts: tuple[str, ...],
                         gate: str, why: str,
                         detail: list[dict[str, Any]] | None,
                         content: str,
                         would_resample: bool) -> response_repair.RepairRecord | None:
            """保存所有拒绝；只有严格可修的一族会让原节点停在下一采之前。"""
            if repair_chain_id is None:
                return None
            eligible, eligibility_reason = _agent_repair_eligibility(gate, resp)
            if eligible and human_batch_already_resolved():
                eligible = False
                eligibility_reason = (
                    f'run={self.ctx.run_id} 已消费唯一人工 workpack；'
                    '后继失败只能升级到登记的自动恢复动作或终态')
            failure_signature = response_repair.failure_signature(
                request_fingerprint=request_fp, model=model,
                reasoning_effort=active_rungs[rung_index][0],
                thinking=active_rungs[rung_index][1], request_mode=request_mode,
                finish_reason=resp.finish_reason, gate=gate,
                reject_detail=detail, why=why)
            pro_primary_rebuild = (
                would_resample and len(active_models) == 1
                and active_models[0] == PRO_FALLBACK_MODEL
                and model_index == 0)
            next_recovery_action = (
                'human_batch' if eligible else
                'pro_constraint_rebuild' if pro_primary_rebuild else
                'flash_targeted_repair' if would_resample else
                'pro_constraint_rebuild' if model_index + 1 < len(active_models)
                else 'terminal')
            original = (_strip_fences(resp.reasoning) if gate == 'answer_lost'
                        else content)
            candidate: Any | None = None
            try:
                candidate = json.loads(original)
            except (json.JSONDecodeError, TypeError):
                pass
            existing_records = repair_records()
            known_fingerprints = {
                str(record.rejection.get('request_fingerprint'))
                for record in existing_records
                if isinstance(record.rejection.get('request_fingerprint'), str)
            }
            known_fingerprints.update(original_repair_request_fingerprints)
            known_fingerprints.add(request_fp)
            dump, dump_error = response_repair.keep_rejection(
                self.ctx.novel_dir, chain_id=repair_chain_id,
                attempt_id=attempt_id, original=original, candidate=candidate,
                payload={
                    'sid': self.ctx.sid, 'node': self.ctx.node,
                    'run_id': self.ctx.run_id,
                    'purpose': purpose, 'code': self.ctx.code,
                    'request_fingerprint': request_fp,
                    'attempt_ids': list(self.ctx.ledger.attempt_ids_for(
                        purpose=purpose,
                        request_fingerprints=known_fingerprints)),
                    'model': model, 'model_index': model_index,
                    'rung_index': rung_index, 'sample': sample, 'source': source,
                    'request_mode': request_mode,
                    'recovery_from_attempts': list(recovery_from_attempts),
                    'would_resample': would_resample,
                    'failure_signature': failure_signature,
                    'next_recovery_action': next_recovery_action,
                    'gate': gate, 'why': why[:4000], 'reject_detail': detail,
                    'repair_eligible': eligible,
                    'eligibility_reason': eligibility_reason,
                    'response_model': model_cls.__name__,
                    'required_top_level': list(require),
                    'response_schema': model_cls.model_json_schema(by_alias=True),
                    'chapter': progress.chapter if progress else None,
                    'response': {
                        'content': resp.content, 'reasoning': resp.reasoning,
                        'finish_reason': resp.finish_reason,
                        'usage': resp.usage, 'model': resp.model,
                        'sha256': _response_sha256(resp),
                    },
                })
            self.ctx.ledger.note_response_repair(
                sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                attempt_id=attempt_id,
                action='offered' if eligible else 'ineligible',
                decision=('pending' if eligible and dump is not None else 'skipped'),
                gate=gate, dump=dump, dump_error=dump_error,
                model=model, rung_index=rung_index, sample=sample,
                chapter=progress.chapter if progress else None)
            if dump is None:
                return None
            return next(
                record for record in repair_records()
                if record.attempt_id == attempt_id)

        def accept_deterministic_repair(
                record: response_repair.RepairRecord | None, *,
                resp: ChatResponse, gate: str, content: str) -> M | None:
            """机械候选必须直接通过**完整四道闸**；否则不消耗 agent 的一次额度。"""
            if record is None or gate not in ('parse', 'answer_lost'):
                return None
            source = (_strip_fences(resp.reasoning) if gate == 'answer_lost'
                      else content)
            candidate_text = _deterministic_json_candidate(source)
            if candidate_text is None:
                return None
            obj, why, _candidate_gate, _detail = gate_payload(candidate_text, resp)
            if why is not None or obj is None:
                return None
            candidate = json.loads(candidate_text)
            sealed = response_repair.submit_deterministic(
                record, candidate, rule='json-bytes-v1')
            response_repair.write_outcome(
                sealed, decision='accepted', gate=None, detail=None, why=None)
            resolution = sealed.resolution or {}
            self.ctx.ledger.note_response_repair(
                sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                attempt_id=record.attempt_id, action='deterministic',
                decision='accepted', actor=str(resolution.get('actor') or ''),
                candidate_sha256=str(resolution.get('candidate_sha256') or ''),
                model=str(record.rejection.get('model') or ''),
                rung_index=record.rejection.get('rung_index'),
                sample=record.rejection.get('sample'),
                chapter=record.rejection.get('chapter'))
            accepted_request = verified_repair_requests.get(record.attempt_id)
            if accepted_request is None:
                repair_records()
                accepted_request = verified_repair_requests.get(record.attempt_id)
            if accepted_request is None:
                raise TerminalState(
                    f'{record.root}: 无法重建 deterministic 候选所属的实际 ChatRequest')
            normalized_candidate = json.dumps(
                candidate, ensure_ascii=False, separators=(',', ':'))
            self._remember_json_acceptance(
                purpose=purpose, attempt_id=record.attempt_id,
                raw_accepted_text=normalized_candidate,
                normalized_text=normalized_candidate,
                channel='deterministic', salvaged=False,
                recovery_mode='deterministic',
                request=accepted_request,
                request_mode=str(record.rejection.get('request_mode') or 'original'),
                recovery_from_attempts=tuple(
                    str(item) for item in
                    record.rejection.get('recovery_from_attempts', [])
                    if isinstance(item, str)),
                contributing_attempts=(
                    str(item) for item in record.rejection.get('attempt_ids', [])
                    if isinstance(item, str)),
            )
            return obj

        # 同 ask_text：主模型或兜底模型任一档已有合格缓存时，本次必须是 0 次真请求。
        for model_index, model in enumerate(active_models):
            for i, (effort, thinking) in enumerate(active_rungs):
                got = self._read_cached(
                    system, user, model=model, effort=effort, thinking=thinking,
                    json_mode=JSON_OBJECT_MODE, max_tokens=max_tokens,
                    purpose=purpose, progress=progress, cache_policy=policy)
                if got is None:
                    continue
                resp, key, attempt_id = got
                obj, why, content, gate, detail, salvaged = inspect_response(resp)
                head = (f'model={model} effort={effort} think={thinking} [缓存]'
                        f'{"（捞自推理通道）" if salvaged else ""} '
                        f'finish={resp.finish_reason} clen={len(content)}')
                if why is None:
                    if model_index > 0:
                        self._observe_model_fallback(
                            purpose=purpose,
                            from_model=active_models[model_index - 1],
                            to_model=model, reason='cached_fallback',
                            progress=progress)
                    self._settle_gate(
                        purpose=purpose, attempt_id=attempt_id, source='cache',
                        accepted=True, gate=None, rungs=active_rungs, index=i,
                        salvaged=salvaged, progress=progress)
                    if salvaged:
                        self._keep_answer_lost(
                            purpose=purpose, attempt_id=attempt_id, source='cache',
                            effort=effort, thinking=thinking,
                            json_mode=JSON_OBJECT_MODE, index=i, sample=None,
                            resample=False, resp=resp, progress=progress,
                            salvaged=True)
                    self._remember_json_acceptance(
                        purpose=purpose, attempt_id=attempt_id,
                        raw_accepted_text=(
                            (resp.reasoning or '') if salvaged else (resp.content or '')),
                        normalized_text=content,
                        channel='reasoning' if salvaged else 'content',
                        salvaged=salvaged, recovery_mode='original',
                        request=request_shape(
                            model=model, effort=effort, thinking=thinking,
                            request_user=user),
                        request_mode='original')
                    return obj
                self.cache.evict(key)
                repair_record = offer_repair(
                    resp=resp, attempt_id=attempt_id,
                    request_fp=self.ctx.ledger.request_fingerprint_for_attempt(attempt_id),
                    model=model, model_index=model_index, rung_index=i,
                    sample=None, source='cache', gate=gate, why=why,
                    request_mode='original', recovery_from_attempts=(),
                    detail=detail, content=content, would_resample=False)
                self._settle_gate(
                    purpose=purpose, attempt_id=attempt_id, source='cache',
                    accepted=False, gate=gate, rungs=active_rungs, index=i,
                    detail=detail, progress=progress)
                self._observe_rung_rejected(
                    purpose=purpose, attempt_id=attempt_id, model=model,
                    models=active_models, model_index=model_index,
                    rungs=active_rungs, index=i, source='cache', gate=gate,
                    why=why, resp=resp, json_mode=JSON_OBJECT_MODE,
                    progress=progress)
                errors.append(f'{head} {why}')
                repaired = accept_deterministic_repair(
                    repair_record, resp=resp, gate=gate, content=content)
                if repaired is not None:
                    return repaired

        # 缓存拒绝不消耗 wire 样本；先横扫完所有模型缓存，避免明明已有合格 Pro 缓存
        # 却停下来修一份 Flash 毒缓存。没有合格缓存时才消费隔离决定。
        if repair_chain_id is not None:
            for record in repair_records():
                if record.rejection.get('source') != 'cache':
                    continue
                accepted, repaired = resolve_record(record)
                if accepted:
                    assert repaired is not None
                    return repaired

        for model_index, model in enumerate(active_models):
            transport_failure: TransportError | LlmFailed | None = None
            for i, (effort, thinking) in enumerate(active_rungs):
                # 通用非 Stage2 链被拒后先同档重采一次（截断除外），再进 Pro，
                # 且不自动降低 effort。Stage2 policy 在下面按失败类型/签名收紧：
                # batch 后最多一次 targeted Flash + 一次 Pro rebuild；answer_lost、
                # length 与 dynamic validator 都按各自登记动作升级，不套这句顺序。
                first_sample = 1
                if repair_chain_id is not None:
                    prior = [
                        record for record in repair_records()
                        if record.rejection.get('source') == 'wire'
                        and record.rejection.get('model') == model
                        and record.rejection.get('rung_index') == i
                    ]
                    samples = [record.rejection.get('sample') for record in prior]
                    if (any(not isinstance(sample_, int) for sample_ in samples)
                            or sorted(samples) != list(range(1, len(samples) + 1))):
                        raise TerminalState(
                            f'{self.ctx.node}/{purpose}: 响应修复采样序列不闭合：'
                            f'model={model} samples={samples}')
                    for record in prior:
                        accepted, repaired = resolve_record(record)
                        if accepted:
                            assert repaired is not None
                            return repaired
                        if not record.eligible:
                            errors.append(
                                f'model={model} sample={record.rejection.get("sample")} '
                                f'{record.rejection.get("gate")} 不可通用修复：'
                                f'{record.rejection.get("why")}')
                    first_sample = (len(prior) + 1
                                    if (not prior
                                        or prior[-1].rejection.get('would_resample') is True)
                                    else MAX_SAMPLES_PER_RUNG + 1)
                deferred_salvage: tuple[
                    M, ChatResponse, str, str, int,
                    str, str, tuple[str, ...],
                ] | None = None

                def adopt_deferred(index: int = i, effort: str = effort,
                                   thinking: bool = thinking) -> M:
                    """采用同档后备并把账结清。**两个采用点共用这一处。**"""
                    assert deferred_salvage is not None
                    (obj_, resp_, key_, attempt_, sample_, _request_user,
                     request_mode_, recovery_from_) = deferred_salvage
                    self._keep(key_, resp_, cache_policy=policy)
                    self._settle_gate(
                        purpose=purpose, attempt_id=attempt_, source='wire',
                        accepted=True, gate=None, rungs=active_rungs, index=index,
                        sample=sample_, salvaged=True,
                        request_mode=request_mode_,
                        recovery_from_attempts=recovery_from_, progress=progress)
                    self._keep_answer_lost(
                        purpose=purpose, attempt_id=attempt_, source='wire',
                        effort=effort, thinking=thinking,
                        json_mode=JSON_OBJECT_MODE, index=index, sample=sample_,
                        resample=False, resp=resp_, progress=progress,
                        salvaged=True)
                    self._remember_json_acceptance(
                        purpose=purpose, attempt_id=attempt_,
                        raw_accepted_text=resp_.reasoning or '',
                        normalized_text=_strip_fences(resp_.reasoning),
                        channel='reasoning', salvaged=True,
                        recovery_mode=request_mode_,
                        request=request_shape(
                            model=model, effort=effort, thinking=thinking,
                            request_user=_request_user),
                        request_mode=request_mode_,
                        recovery_from_attempts=recovery_from_,
                        contributing_attempts=recovery_from_)
                    return obj_

                for sample in range(first_sample, MAX_SAMPLES_PER_RUNG + 1):
                    if deferred_salvage is None:
                        (request_user, request_mode,
                         recovery_from_attempts) = request_for_sample(
                            model=model, model_index=model_index, rung_index=i,
                            effort=effort, thinking=thinking, sample=sample)
                    else:
                        # 这里只是在为已经过完整契约的 reasoning 救回稿争取正常
                        # content；必须复用同一请求，不能把它误改成另一份恢复任务。
                        request_user = deferred_salvage[5]
                        request_mode = deferred_salvage[6]
                        recovery_from_attempts = deferred_salvage[7]
                    # 动态恢复 prompt 有自己的缓存键。上一轮若已拿到合格响应、随后在
                    # 产物落盘前崩溃，重跑必须能回放它，不能因初始横扫只认识原 prompt
                    # 而把同一份定向修复再付一次钱。
                    if request_mode != 'original':
                        cached = self._read_cached(
                            system, request_user, model=model, effort=effort,
                            thinking=thinking, json_mode=JSON_OBJECT_MODE,
                            max_tokens=max_tokens, purpose=purpose,
                            progress=progress, cache_policy=policy)
                        if cached is not None:
                            cached_resp, cached_key, cached_attempt = cached
                            (cached_obj, cached_why, cached_content, cached_gate,
                             cached_detail, cached_salvaged) = inspect_response(cached_resp)
                            if cached_why is None:
                                assert cached_obj is not None
                                self._settle_gate(
                                    purpose=purpose, attempt_id=cached_attempt,
                                    source='cache', accepted=True, gate=None,
                                    rungs=active_rungs, index=i, sample=sample,
                                    salvaged=cached_salvaged,
                                    request_mode=request_mode,
                                    recovery_from_attempts=recovery_from_attempts,
                                    progress=progress)
                                if cached_salvaged:
                                    self._keep_answer_lost(
                                        purpose=purpose, attempt_id=cached_attempt,
                                        source='cache', effort=effort,
                                        thinking=thinking,
                                        json_mode=JSON_OBJECT_MODE, index=i,
                                        sample=sample, resample=False,
                                        resp=cached_resp, progress=progress,
                                        salvaged=True)
                                self._remember_json_acceptance(
                                    purpose=purpose, attempt_id=cached_attempt,
                                    raw_accepted_text=(
                                        (cached_resp.reasoning or '')
                                        if cached_salvaged
                                        else (cached_resp.content or '')),
                                    normalized_text=cached_content,
                                    channel=('reasoning' if cached_salvaged
                                             else 'content'),
                                    salvaged=cached_salvaged,
                                    recovery_mode=request_mode,
                                    request=request_shape(
                                        model=model, effort=effort,
                                        thinking=thinking,
                                        request_user=request_user),
                                    request_mode=request_mode,
                                    recovery_from_attempts=recovery_from_attempts,
                                    contributing_attempts=recovery_from_attempts)
                                return cached_obj
                            self.cache.evict(cached_key)
                            cached_record = offer_repair(
                                resp=cached_resp, attempt_id=cached_attempt,
                                request_fp=self.ctx.ledger.request_fingerprint_for_attempt(
                                    cached_attempt),
                                model=model, model_index=model_index, rung_index=i,
                                sample=sample, source='cache',
                                request_mode=request_mode,
                                recovery_from_attempts=recovery_from_attempts,
                                gate=cached_gate, why=cached_why,
                                detail=cached_detail, content=cached_content,
                                would_resample=(
                                    sample < MAX_SAMPLES_PER_RUNG
                                    and worth_content_recovery(
                                        cached_resp, gate=cached_gate,
                                        model_index=model_index)))
                            cache_pending = (
                                cached_record is not None and cached_record.eligible
                                and cached_record.resolution is None)
                            cache_resample = (
                                not cache_pending
                                and sample < MAX_SAMPLES_PER_RUNG
                                and worth_content_recovery(
                                    cached_resp, gate=cached_gate,
                                    model_index=model_index))
                            self._settle_gate(
                                purpose=purpose, attempt_id=cached_attempt,
                                source='cache', accepted=False, gate=cached_gate,
                                rungs=active_rungs, index=i, sample=sample,
                                resample=cache_resample, detail=cached_detail,
                                request_mode=request_mode,
                                recovery_from_attempts=recovery_from_attempts,
                                progress=progress)
                            self._observe_rung_rejected(
                                purpose=purpose, attempt_id=cached_attempt,
                                model=model, models=active_models,
                                model_index=model_index, rungs=active_rungs,
                                index=i, source='cache', gate=cached_gate,
                                why=cached_why, resp=cached_resp,
                                json_mode=JSON_OBJECT_MODE,
                                resample=cache_resample, sample=sample,
                                request_mode=request_mode,
                                recovery_from_attempts=recovery_from_attempts,
                                progress=progress)
                            errors.append(
                                f'model={model} sample={sample} request={request_mode} '
                                f'[缓存] {cached_why}')
                            repaired = accept_deterministic_repair(
                                cached_record, resp=cached_resp,
                                gate=cached_gate, content=cached_content)
                            if repaired is not None:
                                return repaired
                            if cache_pending:
                                assert cached_record is not None
                                raise_pending(cached_record)
                    try:
                        resp, key, _cached, attempt_id = self._call_keyed(
                            system, request_user, model=model, effort=effort,
                            thinking=thinking, json_mode=JSON_OBJECT_MODE,
                            max_tokens=max_tokens, purpose=purpose, use_cache=False,
                            progress=progress, cache_policy=policy)
                    except (TransportError, LlmFailed) as exc:
                        # 手里已有一份过完整契约的响应，就不能因争取正常 content 的
                        # 第二采没打通而扔掉它；没有后备时才进入模型兜底。
                        if deferred_salvage is not None:
                            self.ctx.observe(
                                'reasoning_salvage_adopted_after_transport_failure',
                                purpose=purpose, model=model,
                                reasoning_effort=effort, thinking=thinking,
                                sample=sample)
                            return adopt_deferred()
                        transport_failure = exc
                        break
                    obj, why, content, gate, detail, salvaged = inspect_response(resp)
                    head = (f'model={model} effort={effort} think={thinking}'
                            f'{"（重采）" if sample > 1 else ""}'
                            f' request={request_mode}'
                            f'{"（捞自推理通道）" if salvaged else ""} '
                            f'finish={resp.finish_reason} clen={len(content)}')
                    if why is None:
                        assert obj is not None
                        if (salvaged and prefer_content_over_reasoning_salvage
                                and sample < MAX_SAMPLES_PER_RUNG):
                            # 合法救回稿先作同档后备，只多争取一次正常 content；
                            # 第二份仍不可用时采用后备，不进入 Pro 兜底。
                            deferred_salvage = (
                                obj, resp, key, attempt_id, sample,
                                request_user, request_mode,
                                recovery_from_attempts)
                            continue
                        if deferred_salvage is not None:
                            (_old_obj, old_resp, old_key, old_attempt_id, old_sample,
                             _old_request_user, old_request_mode,
                             old_recovery_from) = deferred_salvage
                            self.cache.evict(old_key)
                            self._settle_gate(
                                purpose=purpose, attempt_id=old_attempt_id,
                                source='wire', accepted=False, gate='answer_lost',
                                rungs=active_rungs, index=i, sample=old_sample,
                                resample=True, request_mode=old_request_mode,
                                recovery_from_attempts=old_recovery_from,
                                progress=progress)
                            self._observe_rung_rejected(
                                purpose=purpose, attempt_id=old_attempt_id,
                                model=model, models=active_models,
                                model_index=model_index, rungs=active_rungs,
                                index=i, source='wire', gate='answer_lost',
                                why=('正文为空；合法推理通道救回稿已保留为后备，'
                                     '同档正常正文优先且已取得'),
                                resp=old_resp, json_mode=JSON_OBJECT_MODE,
                                resample=True, sample=old_sample,
                                request_mode=old_request_mode,
                                recovery_from_attempts=old_recovery_from,
                                progress=progress)
                        self._keep(key, resp, cache_policy=policy)
                        self._settle_gate(
                            purpose=purpose, attempt_id=attempt_id, source='wire',
                            accepted=True, gate=None, rungs=active_rungs, index=i,
                            sample=sample, salvaged=salvaged,
                            request_mode=request_mode,
                            recovery_from_attempts=recovery_from_attempts,
                            progress=progress)
                        if salvaged:
                            self._keep_answer_lost(
                                purpose=purpose, attempt_id=attempt_id,
                                source='wire', effort=effort, thinking=thinking,
                                json_mode=JSON_OBJECT_MODE, index=i, sample=sample,
                                resample=False, resp=resp, progress=progress,
                                salvaged=True)
                        self._remember_json_acceptance(
                            purpose=purpose, attempt_id=attempt_id,
                            raw_accepted_text=(
                                (resp.reasoning or '') if salvaged
                                else (resp.content or '')),
                            normalized_text=content,
                            channel='reasoning' if salvaged else 'content',
                            salvaged=salvaged, recovery_mode=request_mode,
                            request=request_shape(
                                model=model, effort=effort, thinking=thinking,
                                request_user=request_user),
                            request_mode=request_mode,
                            recovery_from_attempts=recovery_from_attempts,
                            contributing_attempts=recovery_from_attempts)
                        return obj
                    self.cache.evict(key)
                    repair_record = (None if deferred_salvage is not None else offer_repair(
                        resp=resp, attempt_id=attempt_id,
                        request_fp=self.ctx.ledger.request_fingerprint_for_attempt(attempt_id),
                        model=model, model_index=model_index, rung_index=i,
                        sample=sample, source='wire',
                        request_mode=request_mode,
                        recovery_from_attempts=recovery_from_attempts,
                        gate=gate, why=why,
                        detail=detail, content=content,
                        would_resample=(sample < MAX_SAMPLES_PER_RUNG
                                        and worth_content_recovery(
                                            resp, gate=gate,
                                            model_index=model_index))))
                    repair_pending = (
                        repair_record is not None and repair_record.eligible
                        and repair_record.resolution is None)
                    resample = (not repair_pending
                                and sample < MAX_SAMPLES_PER_RUNG
                                and worth_content_recovery(
                                    resp, gate=gate,
                                    model_index=model_index))
                    self._settle_gate(
                        purpose=purpose, attempt_id=attempt_id, source='wire',
                        accepted=False, gate=gate, rungs=active_rungs, index=i,
                        sample=sample, resample=resample, detail=detail,
                        request_mode=request_mode,
                        recovery_from_attempts=recovery_from_attempts,
                        progress=progress)
                    self._observe_rung_rejected(
                        purpose=purpose, attempt_id=attempt_id, model=model,
                        models=active_models, model_index=model_index,
                        rungs=active_rungs, index=i, source='wire', gate=gate,
                        why=why, resp=resp, json_mode=JSON_OBJECT_MODE,
                        resample=resample, sample=sample, progress=progress,
                        request_mode=request_mode,
                        recovery_from_attempts=recovery_from_attempts,
                        fallback_salvage=deferred_salvage is not None)
                    errors.append(f'{head} {why}')
                    repaired = accept_deterministic_repair(
                        repair_record, resp=resp, gate=gate, content=content)
                    if repaired is not None:
                        return repaired
                    if deferred_salvage is not None:
                        return adopt_deferred()
                    if repair_pending:
                        assert repair_record is not None
                        raise_pending(repair_record)
                    if not resample:
                        break
                if transport_failure is not None:
                    break
            if transport_failure is not None:
                errors.append(f'model={model} 传输/资源重试耗尽：{transport_failure}')
                if model_index == 0 \
                        and isinstance(transport_failure, TransportError) \
                        and not transport_failure.retryable:
                    raise transport_failure
            if model_index + 1 < len(active_models):
                self._observe_model_fallback(
                    purpose=purpose, from_model=model,
                    to_model=active_models[model_index + 1],
                    reason=('transport_exhausted' if transport_failure is not None
                            else 'content_rejected'), progress=progress)
        raise LlmFailed(
            f'{self.ctx.node}/{purpose}: 主模型与兜底模型 '
            f'{" → ".join(active_models)} 均未拿到可用 JSON。\n  '
            + '\n  '.join(errors)
            + f'\n⚠ 必需顶层键来自 {model_cls.__name__}（判据 39：每个结构化调用点'
              f'声明【全部】必需顶层键，而这一条现在由派生保证）')

    def _observe_model_fallback(self, *, purpose: str, from_model: str,
                                to_model: str, reason: str,
                                progress: RequestProgress | None) -> None:
        """模型链切换的唯一事件出口；不携带 prompt 或响应正文。"""
        self.ctx.observe(
            'llm_model_fallback', purpose=purpose,
            from_model=from_model, to_model=to_model, reason=reason,
            chapter=progress.chapter if progress else None)

    def _observe_rung_rejected(self, *, purpose: str, attempt_id: str,
                               model: str, models: tuple[str, ...],
                               model_index: int,
                               rungs: tuple[tuple[str, bool], ...], index: int,
                               source: str, gate: str, why: str,
                               resp: ChatResponse, json_mode: bool,
                               resample: bool = False,
                               sample: int | None = None,
                               request_mode: str = 'original',
                               recovery_from_attempts: tuple[str, ...] = (),
                               progress: RequestProgress | None = None,
                               fallback_salvage: bool = False) -> None:
        """响应被拒时留下【为什么】以及下一步是重采、显式 rung 还是模型兜底。

        ⚠⚠ 它补的是一个实测踩过的观测洞：内容闸判定不可用时，这里只做
        `cache.evict` + 降一档，**既不产事件也不写日志**；而账本那一行记的是
        **传输层**的结论（`outcome=ok` / `finish_reason=stop` / `will_retry=false`），
        读起来像「这一次很成功」。⇒ 2026-08-19 复盘 `stage4.plan` 那次降档时，
        理由在三处同时不在：账本只有传输层、降档路径不出声、被拒的那一档响应
        随即被 `ResponseCache.evict` 从盘上删掉；`errors` / `tried` 只有在
        **所有档全灭**时才随 `LlmFailed` 浮出来，而降档成功那一路根本走不到。
        ⇒ 这是**模型响应层**的判定，它得有自己的出口，不能借账本那一行。

        ⚠ `resample=True` 表示这一拒的下一步是**同档重采**（`MAX_SAMPLES_PER_RUNG`），
        此时 `next_effort` / `next_thinking` 指回**同一档**——事件必须说真话，
        否则「重采」会被统计口径读成「切换配置或模型」。
        `fallback_salvage=True` 则表示下一步采用已通过完整契约的同档后备；这时
        `next_*` 为空但 `exhausted=False`，不能谎报成“下一档”或“阶梯耗尽”。

        ⚠ 事件里**不带响应正文**：被拒的那份可能是半截 JSON 或整段推敲过程，
        灌进作业日志既没用又可能把正文写进不该写的地方。只带闸名、截断后的理由
        与几个长度读数——够回答「哪一道闸、拒的是什么形状」。

        ⚠⚠ **`answer_lost` 那一格另留一份证据，而事件本身仍然一个字正文都不带。**
        「推理通道里到底装了什么」这个问题事件答不了，而它正是这一类的全部争议：
        模型是没干活，还是答案写好了走错了通道。两者在事件里长得一样
        （`content_chars=0` + 一个大 `reasoning_chars`）。⇒ 整份响应落一份留证文件
        （`_keep_answer_lost`），指针进审计账，正文只在那份文件里、且跟着书走。
        在此之前这份证据**从来没落过盘**：`ResponseCache.put` 拒写空正文
        （那是对的——缓存下来只会让重试每次命中同一个空壳），于是空响应一被 `evict`
        就彻底没了，只剩 token 体积可以拿来猜。
        """
        effort, thinking = rungs[index]
        if fallback_salvage:
            nxt = None
        elif resample:
            nxt = (model, effort, thinking)
        elif model_index + 1 < len(models):
            nxt = (models[model_index + 1], *rungs[0])
        else:
            nxt = None
        self.ctx.observe(
            'llm_rung_rejected',
            purpose=purpose,
            model=model,
            reasoning_effort=effort,
            thinking=thinking,
            json_mode=json_mode,
            source=source,                    # cache | wire —— 缓存那一趟也会拒
            gate=gate,                        # REJECT_GATES 之一，机器可数
            reason=why[:REJECT_REASON_MAX],
            reason_truncated=len(why) > REJECT_REASON_MAX,
            finish_reason=resp.finish_reason,
            content_chars=len(resp.content or ''),
            reasoning_chars=len(resp.reasoning or ''),
            resample=resample,                # True = 下一步同档重采，不切模型
            request_mode=request_mode,
            recovery_from_attempts=list(recovery_from_attempts),
            fallback_salvage=fallback_salvage,  # True = 不切模型，采用已验过的后备
            next_model=None if nxt is None else nxt[0],
            next_effort=None if nxt is None else nxt[1],
            next_thinking=None if nxt is None else nxt[2],
            model_fallback=nxt is not None and nxt[0] != model,
            # 最后一个模型的最后配置也被拒，才算整条恢复链耗尽。
            exhausted=nxt is None and not fallback_salvage,
        )
        if gate == 'answer_lost':
            self._keep_answer_lost(
                purpose=purpose, attempt_id=attempt_id, source=source,
                effort=effort, thinking=thinking, json_mode=json_mode,
                index=index, sample=sample, resample=resample,
                resp=resp, progress=progress)

    def _keep_answer_lost(self, *, purpose: str, attempt_id: str, source: str,
                          effort: str, thinking: bool, json_mode: bool,
                          index: int, sample: int | None, resample: bool,
                          resp: ChatResponse,
                          progress: RequestProgress | None,
                          salvaged: bool = False) -> None:
        """留证 + 记指针。**正文只进留证文件，账上只有指针与长度读数。**

        留证落在**书目录**下（`novel/paths.py::answer_lost_path`）而不是跨书的
        `_service/`：那份 `reasoning` 里装的是这本书的正文——答案本来就该写进
        `content`，只是走错了通道。⇒ 跟书走，删书即删证据。

        ⚠ 留一份供应商原样的 `usage`：`reasoning_tokens == completion_tokens`
        是这一类的**服务端自证**（答案整份记在了推理侧），而它与 `reasoning` 的
        字数一起才说得清「模型是没干活还是走错通道」。
        ⚠ token 数不抄进审计账（那是成本账那一行的，见 `note_answer_lost`）。

        ⚠⚠ **`salvaged=True` 时这一份被采纳了，留证照写不误。**
        那时它已经不是「丢失的证据」而是**被采纳产物的来源**——更该留：这条路新、
        有产品语义争议，头几次跑必须能逐字复核「捞回来的到底是什么」。
        ⇒ 判断一次 `answer_lost` 是丢了还是接住了，看这一位，不看有没有这行。
        """
        payload: dict[str, Any] = {
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'sid': self.ctx.sid, 'node': self.ctx.node, 'purpose': purpose,
            'attempt_id': attempt_id, 'source': source,
            'reasoning_effort': effort, 'thinking': thinking, 'json_mode': json_mode,
            'rung_index': index, 'sample': sample, 'resample': resample,
            'chapter': progress.chapter if progress else None,
            'salvaged': salvaged,
            'finish_reason': resp.finish_reason,
            'model': resp.model,
            'usage': resp.usage,
            'content': resp.content,
            'reasoning': resp.reasoning,
        }
        dump, dump_error = write_answer_lost(
            self.ctx.novel_dir, attempt_id, payload)
        self.ctx.ledger.note_answer_lost(
            sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
            attempt_id=attempt_id, source=source,
            reasoning_effort=effort, thinking=thinking, json_mode=json_mode,
            rung_index=index, sample=sample, resample=resample,
            salvaged=salvaged,
            finish_reason=resp.finish_reason,
            content_chars=len(resp.content or ''),
            reasoning_chars=len(resp.reasoning or ''),
            dump=dump, dump_error=dump_error,
            chapter=progress.chapter if progress else None)

    def _settle_gate(self, *, purpose: str, attempt_id: str, source: str,
                     accepted: bool, gate: str | None,
                     rungs: tuple[tuple[str, bool], ...], index: int,
                     sample: int | None = None, resample: bool = False,
                     detail: list[dict[str, Any]] | None = None,
                     salvaged: bool = False,
                     request_mode: str = 'original',
                     recovery_from_attempts: tuple[str, ...] = (),
                     progress: RequestProgress | None) -> None:
        """内容闸的判定落**审计账**（`gate_decision` 行），采纳时同时登记
        `purpose → attempt_id`，供落盘方写 `artifact_adoption` 行时反查。

        与 `_observe_rung_rejected` 的分工：那条是给**看现场的人**的 stdout 事件
        （带被拒理由的截断文本）；这条是给**离线归因**的账（机器可联结）。

        ⚠⚠ **`detail` 是「拒在哪个字段」那一半，它必须在这里而不是只在事件里。**
        2026-08-22 那次整跑被超时杀掉之后，stdout 连同 `why` 一起没了，账上只剩
        `gate: schema` 四个字——两次拒到底拒在哪个字段**再也查不出来**
        （历史见 `docs/DECISIONS.md` 2026-08-22）。⇒ 能机器数的那一半落账，人读的那一半留给事件。
        ⚠ 它**只带 `loc` + `type` + 计数**，不带模型正文（见 `_schema_detail`）。

        ⚠⚠ **`salvaged` 说的是这份正文的来路**（`SALVAGE_ANSWER_FROM_REASONING`）：
        `True` = 它是从推理通道里捞回来的，不是从 `content` 来的。这一位必须落账——
        否则「这本书有几章的产物不是走正常通道产出的」事后答不了，而那正是这条路
        全部的产品语义争议所在。
        """
        effort, thinking = rungs[index]
        self.ctx.ledger.note_gate(
            sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
            attempt_id=attempt_id, source=source,
            decision='accepted' if accepted else 'rejected',
            gate=None if accepted else gate,
            reasoning_effort=effort, thinking=thinking, rung_index=index,
            sample=sample, resample=resample,
            reject_detail=None if accepted else detail,
            salvaged=salvaged,
            request_mode=request_mode,
            recovery_from_attempts=recovery_from_attempts,
            chapter=progress.chapter if progress else None)
        bound = self._work_requests.get(attempt_id)
        if bound is not None and not accepted:
            bound[0].mark_rejected(bound[1].turn_id, reason_code=gate or 'content_rejected')
        if accepted:
            with self._adopt_lock:
                self._accepted[purpose] = attempt_id

    def accepted_attempt(self, purpose: str) -> str | None:
        """这个 purpose 最终被采纳的响应的 `attempt_id`；本 run 没采纳过则 `None`。"""
        with self._adopt_lock:
            return self._accepted.get(purpose)

    def json_request_policy(
            self, phases: Iterable[tuple[str, type[BaseModel], int, bool]], *,
            recovery_policy: str,
    ) -> dict[str, Any]:
        """给 producer 一份可在扇出前绑定的完整 JSON 请求策略。"""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for phase, response_model, max_tokens, prefer_content in phases:
            if not phase or phase in seen:
                raise ValueError(f'JSON request policy phase 重复或为空：{phase!r}')
            if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) \
                    or max_tokens <= 0:
                raise ValueError(f'{phase}: max_tokens 必须是正整数')
            seen.add(phase)
            schema = response_model.model_json_schema(by_alias=True)
            schema_sha = hashlib.sha256(json.dumps(
                schema, ensure_ascii=False, sort_keys=True,
                separators=(',', ':')).encode('utf-8')).hexdigest()
            rows.append({
                'phase': phase,
                'response_model': response_model.__name__,
                'response_schema_sha256': schema_sha,
                'max_tokens': max_tokens,
                'prefer_content_over_reasoning_salvage': bool(prefer_content),
            })
        if not rows or not recovery_policy:
            raise ValueError('JSON request policy 必须含 phase 与 recovery_policy')
        return {
            'model_chain': list(self._models),
            'rungs': [[effort, thinking] for effort, thinking in self._rungs],
            'json_mode': JSON_OBJECT_MODE,
            'temperature': None,
            'recovery_policy': recovery_policy,
            'phases': rows,
        }

    def _remember_json_acceptance(
            self, *, purpose: str, attempt_id: str,
            raw_accepted_text: str, normalized_text: str,
            channel: str, salvaged: bool, recovery_mode: str,
            request: ChatRequest, request_mode: str,
            recovery_from_attempts: Iterable[str] = (),
            contributing_attempts: Iterable[str] = (),
    ) -> None:
        """保留验证后 checkpoint 的原通道字节、解析文本与完整成本/请求联结。"""
        if channel not in ('content', 'reasoning', 'agent', 'deterministic'):
            raise ProvenanceRefused(f'{purpose}: accepted channel={channel!r} 不合法')
        if request_fingerprint(request) \
                != self.ctx.ledger.request_fingerprint_for_attempt(attempt_id):
            raise ProvenanceRefused(
                f'{purpose}: accepted ChatRequest 与 ledger attempt={attempt_id} 不一致')
        attempts = sorted(
            set(self.ctx.ledger.attempt_ids_for_purpose(purpose))
            | set(contributing_attempts) | {attempt_id})
        links = [self.ctx.ledger.request_identity_for_attempt(item) for item in attempts]
        accepted_identity = self.ctx.ledger.request_identity_for_attempt(attempt_id)
        payload = {
            'attempt_id': attempt_id,
            'request_fingerprint': accepted_identity['request_fingerprint'],
            'model': accepted_identity['model'],
            'ledger_response_sha256': accepted_identity['response_sha256'],
            'accepted_channel': channel,
            # `accepted_text` 保留供应商/agent 通道的原字节；真正送进 json.loads 的
            # 围栏剥离文本另存，二者不能再混成一格。
            'accepted_text': raw_accepted_text,
            'normalized_text': normalized_text,
            'accepted_request': _checkpoint_request_binding(request),
            'model_chain': list(self._models),
            'rungs': [[effort, thinking] for effort, thinking in self._rungs],
            'attempt_links': links,
            'recovery_mode': recovery_mode,
            'request_mode': request_mode,
            'recovery_from_attempts': list(recovery_from_attempts),
            'salvaged': bool(salvaged),
        }
        with self._adopt_lock:
            self._accepted[purpose] = attempt_id
            self._accepted_checkpoint[purpose] = payload
            self._accepted_checkpoints_by_attempt[attempt_id] = deepcopy(payload)
        bound = self._work_requests.get(attempt_id)
        if bound is not None:
            bound[0].save_recovery(bound[1].turn_id, payload)

    def save_producer_recovery(self, attempt_id: str, payload: dict[str, Any]) -> bool:
        """只将真实模型轮次绑定 producer 提交目标；缓存不制造内容消耗。"""
        bound = self._work_requests.get(attempt_id)
        if bound is None:
            return False
        bound[0].save_producer_recovery(bound[1].turn_id, payload)
        return True

    def producer_response_receipt(self, purpose: str, *, attempt_id: str | None = None) -> dict[str, Any]:
        """封存完成批次的原响应；缓存快照保留原身份，不依赖缓存继续存在。"""
        if attempt_id is None:
            checkpoint = self.accepted_checkpoint(purpose)
            attempt_id = str(checkpoint['attempt_id'])
        else:
            if attempt_id not in self.ctx.ledger.attempt_ids_for_purpose(purpose):
                raise ProvenanceRefused(f'{purpose}: 指定贡献不属于原请求的成本来源')
            bound = self._work_requests.get(attempt_id)
            if bound is not None:
                checkpoint = bound[0].recovery_proof(bound[1].turn_id)
            else:
                with self._adopt_lock:
                    saved = self._accepted_checkpoints_by_attempt.get(attempt_id)
                    if saved is None:
                        raise ProvenanceRefused(f'{purpose}: 指定缓存贡献没有原接受快照')
                    checkpoint = deepcopy(saved)
            if checkpoint['attempt_id'] != attempt_id:
                raise ProvenanceRefused(f'{purpose}: 指定贡献与接受快照不一致')
        bound = self._work_requests.get(attempt_id)
        if bound is not None:
            budget, reservation = bound
            turn = budget.turn(reservation.turn_id)
            return {
                'origin': 'wire', 'purpose': purpose, 'checkpoint': checkpoint,
                'unit_id': budget.unit_id, 'turn_id': turn.turn_id,
                'response_sha256': turn.response_sha256,
                'request_sha256': turn.request_sha256,
            }
        raw = self._producer_cache_receipts.get(attempt_id)
        if raw is None:
            raise ProvenanceRefused(f'{purpose}: 完成批次缺原 wire/cache 响应证明')
        return {'origin': 'cache', 'purpose': purpose, 'checkpoint': checkpoint,
                **raw}

    def restore_json_response(
            self, system: str, user: str, *, model_cls: type[M], purpose: str,
            max_tokens: int = DEFAULT_MAX_TOKENS,
            validator: Callable[[M, ChatResponse], str | None] | None = None,
    ) -> M | None:
        """并行消费者先恢复每个已保存回答；此口绝不发送新请求或新增缓存身份。"""
        budget = budget_for(self.ctx, scope=executor_scope(self.ctx.node, purpose, None))
        latest = budget.latest_turn()
        if latest is not None and latest.state in ('reserved', 'unknown') \
                and latest.response_sha256 is None:
            raise WorkUnitRefused('原请求没有完整回答，须先明确原尝试终态')
        if budget.latest_pending_response() is None:
            return None
        previous = getattr(self._content_local, 'recover_without_wire', False)
        self._content_local.recover_without_wire = True
        try:
            return self.ask_json(system, user, model_cls=model_cls, purpose=purpose,
                                 max_tokens=max_tokens, validator=validator)
        except _ContentRecoveryNeedsWire:
            # 解析错误等真实内容反馈已按原通路保存；统一预验结束后才允许 worker
            # 使用同一角色的剩余预算纠正，不能在另一角色旧证据尚未核验时发请求。
            return None
        finally:
            self._content_local.recover_without_wire = previous

    def producer_cost_receipts(self, purposes: Iterable[str]) -> list[dict[str, Any]]:
        """封存完成组真实成本行，包含缓存与未被最终采用的已付费尝试。"""
        from novel.llm.ledger import read_jsonl_rows

        selected = set(purposes)
        wanted = {attempt for purpose in selected
                  for attempt in self.ctx.ledger.attempt_ids_for_purpose(purpose)}
        if self.ctx.ledger.path is None or not wanted:
            raise ProvenanceRefused('完成组缺持久成本来源')
        rows = [row for row in read_jsonl_rows(self.ctx.ledger.path)
                if row.get('attempt_id') in wanted]
        if len(rows) != len(wanted) or {row.get('attempt_id') for row in rows} != wanted:
            raise ProvenanceRefused('完成组成本来源缺失或重复')
        for row in rows:
            identity = self.ctx.ledger.request_identity_for_attempt(str(row['attempt_id']))
            if (row.get('sid') != self.ctx.sid or row.get('node') != self.ctx.node
                    or row.get('purpose') not in selected
                    or any(row.get(key) != value for key, value in identity.items())):
                raise ProvenanceRefused('完成组成本行与当前接受身份不一致')
        return rows

    @staticmethod
    def verify_producer_responses(
            ctx: RunContext, receipts: Iterable[Mapping[str, Any]], costs: list[dict[str, Any]],
    ) -> dict[str, tuple[WorkUnitBudget, str]]:
        """验证已保存组的原 cache/wire 证据，不加载缓存、不发请求、不重铸费用。"""
        from novel.llm.ledger import CostLedger, ledger_path, read_jsonl_rows
        from novel.run_identity import check_run_id

        # queue 的 readonly_ctx 故意没有写账路径；验证仍只认 base 的权威账。
        cost_path = ledger_path(ctx.novel_dir.parent)
        wanted = {str(row['attempt_id']): row for row in costs}
        actual = [row for row in read_jsonl_rows(cost_path)
                  if row.get('attempt_id') in wanted]
        if (len(wanted) != len(costs) or len(actual) != len(wanted)
                or any(wanted[str(row['attempt_id'])] != row for row in actual)):
            raise ProvenanceRefused('完成组原成本行缺失、重复或漂移')
        bindings: dict[str, tuple[WorkUnitBudget, str]] = {}
        seen: set[str] = set()
        ledgers: dict[str, CostLedger] = {}
        for receipt in receipts:
            checkpoint = receipt['checkpoint']
            attempt_id = str(checkpoint['attempt_id'])
            purpose = str(receipt['purpose'])
            row = wanted.get(attempt_id)
            if (attempt_id in seen or row is None
                    or row.get('sid') != ctx.sid or row.get('node') != ctx.node
                    or row.get('purpose') != purpose):
                raise ProvenanceRefused('完成组接受来源重复或不属于原节点请求')
            seen.add(attempt_id)
            if receipt['origin'] == 'wire':
                budget = budget_for(ctx, scope=executor_scope(ctx.node, purpose, None))
                turn_id = str(receipt['turn_id'])
                turn = budget.turn(turn_id)
                raw = budget.response_payload(turn_id)
                request = budget.request_payload(turn_id)['request']
                if (receipt['unit_id'] != budget.unit_id
                        or turn.response_sha256 != receipt['response_sha256']
                        or turn.request_sha256 != receipt['request_sha256']
                        or raw['attempt_id'] != attempt_id or row.get('cached') is not False
                        or checkpoint != budget.recovery_proof(turn_id)):
                    raise ProvenanceRefused('完成组原 wire 回答或接受证明漂移')
                response = ChatResponse(**raw['response'])
                bindings[attempt_id] = (budget, turn_id)
            elif receipt['origin'] == 'cache' and row.get('cached') is True:
                request = receipt['request']
                response = ChatResponse(**receipt['response'])
            else:
                raise ProvenanceRefused('完成组 cache/wire 来源类别漂移')
            messages = request['messages']
            req = ChatRequest(
                **{key: value for key, value in request.items() if key != 'messages'},
                system=messages[0]['content'], user=messages[-1]['content'],
                history=tuple((item['role'], item['content']) for item in messages[1:-1]))
            channel = checkpoint['accepted_channel']
            direct = channel in ('content', 'reasoning')
            repaired = receipt['origin'] == 'wire' and channel in ('agent', 'deterministic')
            accepted = response.content if channel == 'content' else response.reasoning
            if (not (direct or repaired)
                    or (direct and checkpoint['accepted_text'] != (accepted or ''))
                    or _response_sha256(response) != row.get('response_sha256')
                    or request_fingerprint(req) != row.get('request_fingerprint')
                    or checkpoint['request_fingerprint'] != row.get('request_fingerprint')):
                raise ProvenanceRefused('完成组原请求、回答与成本指纹不闭合')
            try:
                run_id = check_run_id(row['run_id'])
                parent = check_run_id(row.get('parent_run_id', run_id))
            except ValueError as exc:
                raise ProvenanceRefused('完成组原成本 run/parent 身份非法') from exc
            ledger = ledgers.get(run_id)
            if ledger is None:
                ledger = CostLedger(path=cost_path, run_id=run_id, parent_run_id=parent)
                ledgers[run_id] = ledger
            elif ledger.parent_run_id != parent:
                raise ProvenanceRefused('完成组原成本 parent 不唯一')
            ledger.claim_prior_attempts([attempt_id], sid=ctx.sid, node=ctx.node,
                                        purpose=purpose,
                                        request_fingerprints=[str(row['request_fingerprint'])])
            if ledger.request_identity_for_attempt(attempt_id) not in checkpoint['attempt_links']:
                raise ProvenanceRefused('完成组接受证明与原成本身份不一致')
        return bindings

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]:
        """返回刚通过完整响应闸的原文/attempt 联结；缺一格就不允许伪造检查点。"""
        with self._adopt_lock:
            value = self._accepted_checkpoint.get(purpose)
            if value is None:
                raise ProvenanceRefused(
                    f'{purpose}: accepted response 没有 checkpoint channel/raw-text 联结')
            return {
                **value,
                'attempt_links': [dict(row) for row in value['attempt_links']],
            }

    def restore_checkpoint_acceptance(
            self, purpose: str, record: Mapping[str, Any], *,
            system: str | None = None, user: str | None = None,
            max_tokens: int | None = None,
            recovery_policy: str | None = None,
            rungs: tuple[tuple[str, bool], ...] | None = None,
    ) -> None:
        """按当前完整 ChatRequest/恢复协议复算 checkpoint，再恢复 adoption 索引。"""
        attempt_id = str(record.get('attempt_id') or '')
        links = record.get('attempt_links')
        if not isinstance(links, list) or not links:
            raise ProvenanceRefused(f'{purpose}: checkpoint 缺 attempt_links')
        accepted_text = record.get('accepted_text')
        normalized_text = record.get('normalized_text')
        channel = str(record.get('accepted_channel') or '')
        if (not isinstance(accepted_text, str)
                or not isinstance(normalized_text, str) or channel not in (
                'content', 'reasoning', 'agent', 'deterministic')):
            raise ProvenanceRefused(f'{purpose}: checkpoint accepted channel/raw-text 不合法')
        response_model = str(record.get('model') or '')
        if not response_model:
            raise ProvenanceRefused(f'{purpose}: checkpoint 缺供应商实际响应 model')
        if system is None or user is None or max_tokens is None:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 恢复缺当前 system/user/max_tokens，'
                '不能核验实际 ChatRequest')
        if recovery_policy not in (None, 'stage2-blueprint-v1'):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 使用未登记恢复策略 {recovery_policy!r}')
        active_rungs = self._active_rungs_for(rungs, purpose=purpose)
        expected_chain = list(self._models)
        expected_rungs = [[effort, thinking] for effort, thinking in active_rungs]
        if (record.get('model_chain') != expected_chain
                or record.get('rungs') != expected_rungs):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 模型链/effort/thinking 与当前配置漂移；'
                f'盘上={record.get("model_chain")!r}/{record.get("rungs")!r}，'
                f'当前={expected_chain!r}/{expected_rungs!r}')
        binding = record.get('accepted_request')
        if not isinstance(binding, dict):
            raise ProvenanceRefused(f'{purpose}: checkpoint 缺完整 accepted_request 配置')
        request_model = str(binding.get('request_model') or '')
        effort = binding.get('reasoning_effort')
        thinking = binding.get('thinking')
        if request_model not in self._models:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted_request.request_model='
                f'{request_model!r} 不在当前模型链 {self._models!r}')
        if (not isinstance(effort, str)
                or not isinstance(thinking, bool)
                or (effort, thinking) not in active_rungs
                or binding.get('json_mode') != JSON_OBJECT_MODE
                or not isinstance(binding.get('max_tokens'), int)
                or isinstance(binding.get('max_tokens'), bool)
                or binding.get('max_tokens') != max_tokens
                or binding.get('temperature') is not None):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 的 model/effort/thinking/json_mode/max_tokens '
                '与当前将发送的请求配置不同')

        request_mode = str(record.get('request_mode') or '')
        raw_from = record.get('recovery_from_attempts')
        if (not isinstance(raw_from, list)
                or not all(isinstance(item, str) for item in raw_from)):
            raise ProvenanceRefused(f'{purpose}: checkpoint 缺 recovery_from_attempts')
        recovery_from = tuple(raw_from)
        request_user = user
        if request_mode == 'original':
            if recovery_from:
                raise ProvenanceRefused(
                    f'{purpose}: original checkpoint 不应携带恢复来源 attempts')
        elif request_mode == 'original:budget_fallback':
            if recovery_policy != 'stage2-blueprint-v1' or not recovery_from:
                raise ProvenanceRefused(
                    f'{purpose}: budget fallback checkpoint 缺已登记恢复链')
        else:
            try:
                strategy, _detail = request_mode.split(':', 1)
            except ValueError:
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint request_mode={request_mode!r} 不合法') from None
            if (recovery_policy != 'stage2-blueprint-v1'
                    or strategy not in response_repair.RECOVERY_STRATEGIES
                    or not recovery_from):
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint 恢复 prompt 身份不完整')
            chain_id = response_repair.chain_fingerprint(
                sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                code=self.ctx.code, system=system, user=user,
                models=self._models, rungs=active_rungs,
                json_mode=JSON_OBJECT_MODE, max_tokens=max_tokens,
                run_id=self.ctx.run_id)
            records = response_repair.records_for_chain(self.ctx.novel_dir, chain_id)
            by_attempt = {item.attempt_id: item for item in records}
            if set(by_attempt).intersection(recovery_from) != set(recovery_from):
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint 恢复 prompt 所指 attempts 不在当前 repair chain')
            selected = tuple(by_attempt[item] for item in recovery_from)
            variants = response_repair.recovery_prompt_variants(
                user, selected, strategy=strategy)
            matches = [variant for variant in variants
                       if variant.request_mode == request_mode
                       and variant.from_attempt_ids == recovery_from]
            if len(matches) != 1:
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint 恢复 prompt 无法由当前失败证据唯一重建')
            request_user = matches[0].user

        expected_request = self._chat_request(purpose=purpose,
            model=request_model, system=system, user=request_user,
            reasoning_effort=str(effort), thinking=bool(thinking),
            json_mode=JSON_OBJECT_MODE, max_tokens=max_tokens)
        expected_binding = _checkpoint_request_binding(expected_request)
        if binding != expected_binding:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted_request 指纹/prompt/config 与当前重建结果不同')
        accepted_ledger = self.ctx.ledger.request_identity_for_attempt(attempt_id)
        if response_model != str(accepted_ledger.get('model') or ''):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 实际响应 model 与 ledger 不闭合')
        ledger_request = {
            key: accepted_ledger.get(key)
            for key in ('request_model', 'reasoning_effort', 'thinking',
                        'json_mode', 'max_tokens', 'request_fingerprint')
        }
        binding_request = {
            key: binding.get(key)
            for key in ('request_model', 'reasoning_effort', 'thinking',
                        'json_mode', 'max_tokens', 'request_fingerprint')
        }
        if ledger_request != binding_request:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted_request 与 ledger ChatRequest 不闭合')
        if record.get('ledger_response_sha256') \
                != accepted_ledger.get('response_sha256'):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint ledger_response_sha256 与成本账不同')
        self._remember_json_acceptance(
            purpose=purpose, attempt_id=attempt_id,
            raw_accepted_text=accepted_text,
            normalized_text=normalized_text, channel=channel,
            salvaged=record.get('salvaged') is True,
            recovery_mode=str(record.get('recovery_mode') or 'checkpoint'),
            request=expected_request, request_mode=request_mode,
            recovery_from_attempts=recovery_from,
            contributing_attempts=(
                str(row.get('attempt_id') or '') for row in links
                if isinstance(row, dict)),
        )

    def accepted_request_fingerprint(self, purpose: str) -> str | None:
        """最终采纳响应的实际请求指纹；由 attempt 联结成本账，不按默认档位重算。"""
        attempt_id = self.accepted_attempt(purpose)
        if attempt_id is None:
            return None
        return self.ctx.ledger.request_fingerprint_for_attempt(attempt_id)

    def accepted_model(self, purpose: str) -> str | None:
        """最终采纳响应的实际模型；由 attempt 联结成本账，不按主模型估算。"""
        attempt_id = self.accepted_attempt(purpose)
        if attempt_id is None:
            return None
        return self.ctx.ledger.model_for_attempt(attempt_id)

    def accepted_models(self, *purposes: str) -> tuple[str, ...]:
        """给聚合产物返回去重、稳定排序的实际采纳模型集合。

        不传 purpose 时覆盖本 client 本 run 已采纳的全部 purpose。传入的 purpose
        若没有采纳响应则跳过——定向重生成合并进来的未动章正是这个形状。
        """
        if purposes:
            selected = purposes
        else:
            with self._adopt_lock:
                selected = tuple(self._accepted)
        models = {
            model
            for purpose in selected
            if (model := self.accepted_model(purpose)) is not None
        }
        return tuple(sorted(models))

    # ── 一次调用：缓存 → 传输层重试 → 记账 ────────────────────────────────

    def _preflight(self, system: str, user: str, *,
                   models: tuple[str, ...],
                   rungs: tuple[tuple[str, bool], ...], json_mode: bool,
                   max_tokens: int, purpose: str) -> None:
        """把主模型与兜底模型一次验完；返回前没有缓存、账本、网络或文件副作用。"""
        self.ctx.require_observed_roots_current()
        requests = tuple(
            self._chat_request(purpose=purpose,
                model=model,
                system=system,
                user=user,
                reasoning_effort=effort,
                thinking=thinking,
                json_mode=json_mode,
                max_tokens=max_tokens,
            )
            for model in models
            for effort, thinking in rungs
        )
        self._budget_checker(requests, node=self.ctx.node, purpose=purpose)

    def _read_cached(self, system: str, user: str, *, model: str,
                     effort: str, thinking: bool,
                     json_mode: bool, max_tokens: int, purpose: str,
                     progress: RequestProgress | None,
                     cache_policy: CachePolicy | None = None,
                     ) -> tuple[ChatResponse, str, str] | None:
        """只读一个档位的缓存；miss 绝不顺手上线。"""
        if (getattr(self._content_local, 'required_replay_turn_ids', frozenset())
                or getattr(self._content_local, 'recover_without_wire', False)):
            # producer 已证明本次必须采用某个原响应；缓存不能改写该响应的身份。
            return None
        policy = cache_policy or self.ctx.cache
        if not policy.read:
            return None
        req = self._chat_request(purpose=purpose, model=model, system=system, user=user,
                          reasoning_effort=effort, thinking=thinking,
                          json_mode=json_mode, max_tokens=max_tokens)
        key = cache_key(req)
        budget = budget_for(self.ctx, scope=executor_scope(self.ctx.node, purpose, progress))
        pending = budget.latest_pending_response()
        if pending is not None and str(pending[1].get('attempt_id')) not in self._work_requests:
            # 原回答优先走真实恢复链；不以一条 cache attempt 覆盖原调用的提交身份。
            return None
        hit = self.cache.get(key)
        if hit is None:
            return None
        attempt_id = self._record_cache_hit(
            req, hit, purpose=purpose, progress=progress, cache_policy=policy)
        return hit, key, attempt_id

    def _call_keyed(self, system: str, user: str, *, model: str,
                    effort: str, thinking: bool,
                    json_mode: bool, max_tokens: int, purpose: str,
                    use_cache: bool = True,
                    progress: RequestProgress | None = None,
                    cache_policy: CachePolicy | None = None,
                    ) -> tuple[ChatResponse, str, bool, str]:
        """发一次（或读一次缓存）。返回 `(响应, 缓存键, 是不是缓存来的, attempt_id)`。

        ⚠⚠ **它不再写缓存。** 原来这里拿到响应就 `put`，而调用方**还没判断它可不可用**：
        于是四档阶梯各自拿回一份截断 JSON 时，盘上留下的是**四份毒缓存**——
        此后一次完全健康的重跑会 0 次真调用、以一模一样的方式失败，
        不带 `--refresh` 永远走不出来。
        ⇒ 写缓存的时机挪到「判定可用之后」，由两个入口各自调 `_keep`。
        `cached` 这一格是给它们用的：读回来的那一份不必再写一遍。

        两个公开入口会先用 `_read_cached` 横扫全部档位；只有没有合格缓存时才以
        `use_cache=False` 调这里。保留 `use_cache=True` 仅维持本私有帮助函数的完整语义，
        不能拿它恢复“某档 miss 就立刻上线”的旧顺序。
        """
        req = self._chat_request(purpose=purpose, model=model, system=system, user=user,
                          reasoning_effort=effort, thinking=thinking,
                          json_mode=json_mode, max_tokens=max_tokens)
        key = cache_key(req)
        policy = cache_policy or self.ctx.cache

        restored = self._restore_work_response(req, purpose=purpose, progress=progress)
        if restored is not None:
            response, attempt_id = restored
            required = getattr(self._content_local, 'required_replay_turn_ids', frozenset())
            if required and self._work_requests[attempt_id][1].turn_id not in required:
                raise ProvenanceRefused('producer 恢复命中了未经提交证明授权的原响应')
            frozen = self._chat_request(purpose=purpose, model=model, system=system, user=user,
                                        reasoning_effort=effort, thinking=thinking,
                                        json_mode=json_mode, max_tokens=max_tokens)
            return response, cache_key(frozen), False, attempt_id
        if getattr(self._content_local, 'required_replay_turn_ids', frozenset()):
            raise ProvenanceRefused('producer 恢复没有匹配当前请求配置的原响应，禁止新增模型调用')
        if getattr(self._content_local, 'recover_without_wire', False):
            raise _ContentRecoveryNeedsWire()
        budget = budget_for(self.ctx, scope=executor_scope(self.ctx.node, purpose, progress))
        fingerprint = request_fingerprint(req)
        previous_response = budget.latest_pending_response()
        if previous_response is not None and str(previous_response[1].get('attempt_id')) in self._work_requests:
            # 同一存活 producer 已明确进入下一次内容采样；原候选继续留证，
            # 真正重启时则必须先通过上面的原请求恢复。
            budget.mark_rejected(previous_response[0].turn_id, reason_code='continued_content_work')

        if use_cache and policy.read:
            hit = self.cache.get(key)
            if hit is not None:
                # ⚠ **缓存命中也要记一行。** 不记的话，「这次跑读了 31 份缓存」与
                #   「这次跑什么都没做」在账本上长得一模一样，而这两件事的下一步完全不同。
                attempt_id = self._record_cache_hit(
                    req, hit, purpose=purpose, progress=progress,
                    cache_policy=policy)
                return hit, key, True, attempt_id

        seed = request_fingerprint(ChatRequest(
            model=model, system=system, user=user, reasoning_effort=effort,
            thinking=thinking, json_mode=json_mode, max_tokens=max_tokens))
        reservation = budget.reserve(
            fingerprint, run_id=self.ctx.run_id,
            request_payload={'system': system, 'user': user,
                             'logical_request_sha256': seed, 'request': req.cache_payload()})
        self._content_local.current = (budget, reservation)
        try:
            resp, attempt_id = self._send_with_retry(
                req, purpose=purpose, progress=progress, cache_policy=policy)
            self._work_requests[attempt_id] = (budget, reservation)
            self.ctx.ledger.bind_work_unit_commit(attempt_id, budget, reservation.turn_id)
            budget.finish(reservation.turn_id, outcome='delivered')
        except ModelSlotUnavailable as exc:
            budget.finish(reservation.turn_id, outcome='failed', reason_code='model_slot_not_started')
            raise LlmFailed(f'模型调用未开始：{exc}') from exc
        except TransportError:
            budget.finish(reservation.turn_id, outcome='failed', reason_code='transport_failed')
            raise
        except BaseException:
            budget.finish(reservation.turn_id, outcome='unknown', reason_code='content_interrupted')
            raise
        finally:
            self._content_local.current = None
        return resp, key, False, attempt_id

    def _keep(self, key: str, resp: ChatResponse, *,
              cache_policy: CachePolicy | None = None) -> None:
        """把一份**已经判定可用**的响应写进缓存。全类唯一的写口。"""
        policy = cache_policy or self.ctx.cache
        if policy.write:
            self.cache.put(key, resp)      # 空 content 由 `put` 自己再拒一道

    def _send_with_retry(self, req: ChatRequest, *, purpose: str,
                         progress: RequestProgress | None = None,
                         cache_policy: CachePolicy | None = None,
                         ) -> tuple[ChatResponse, str]:
        """**传输层**重试。与同档重采、模型兜底是独立循环（见模块 docstring 末尾）。

        ⚠ 只重试 `retryable` 的（429 / 5xx / 超时）。4xx 里其余的是**我们自己发错了**，
        重试只会把同一个错误再犯三遍，而每一遍都可能计费。

        ⚠⚠ **每一次上线都记一行**，成功与失败都记。原来只在整轮成功之后记一次：
        传输层实际发了 3 次而账上只有 1 次，整轮失败则一行都没有——
        于是「限流限了半天」与「一次就过」在账本上同形，
        而「彻底失败」与「什么都没干」也同形。这一层是**唯一**知道自己发了几次的地方。

        ⚠ 本方法是重试的**唯一所有者**：SDK 那一层已经 `max_retries=0`
        （见 `transport.py::_sdk`），不然两层各退各的避，一次调用能打出 12 次 HTTP。

        ⚠ 首发独占闸包在最外层（`_warmup_gate`）：它守的是**顺序**，而重试守的是**网络**。
        与模型兜底同理，**三条不许合并**——合并之后一次限流退避会把整批章都堵在闸里。
        """
        with self._warmup_gate():
            return self._send_attempts(
                req, purpose=purpose, progress=progress, cache_policy=cache_policy)

    @contextmanager
    def _warmup_gate(self) -> Iterator[None]:
        """**首发独占**：第一次真上线跑完，其余线程才放行。一个 client 只发生一次。

        【它省的是什么】供应商的上下文缓存按**字节前缀**命中，而那份前缀要等一次请求
        **完成**之后才写得进去。逐章扇出（`stage2.blueprint` / `stage5.write`，默认并发 32）
        把 N 章**同时**发出去时，N 份请求共享的那一大段 system 前缀**谁也命中不了谁**——
        实测 `stage2/prompts.py::BLUEPRINT_SYS` 4073 token、`stage5` 的臂 SYS 约 1900 token，
        一本 20 章的书因此白付 19 份。⇒ 拿第一发的串行，换后面 N−1 发的前缀命中。

        ⚠⚠ **`finally` 里无条件放行。** 首发走完整条响应恢复链仍然失败（或抛任何异常）时，
        其余线程必须继续跑：不放行就是整批死等一个已经死掉的请求，而那比多花一次钱坏得多。
        ⚠ **只有真上线才消耗名额**：本地缓存命中走 `_read_cached`，根本进不到这里——
        一次全命中的重跑不会因为这道闸而串行。
        ⚠ 单次调用的节点（`stage2.global` / `stage4.plan` / `stage6.hook` / `stage7.rename`）
        本来就只发一次，这道闸对它们是恒等的。
        ⚠ **不给它 CLI flag**：用户已裁决默认接受这一次串行的墙钟，而一个恒真的 flag 是误导
        （同 `cache.py::CachePolicy.disabled` 那条）。构造参数留着只为让测试关掉它。
        """
        # 双盲 A/B 的现役契约要求同时发起独立请求；不能为前缀缓存把 B 挡在 A
        # 完成之后。其余节点继续沿用原首发独占策略，不增加额外 warmup wire。
        if not self.warmup or self.ctx.node == 'stage6.semantic':
            yield
            return
        with self._warmup_lock:
            first = not self._warmed
            self._warmed = True             # 认领首发；后来者一律等
        if not first:
            self._warm_done.wait()
            yield
            return
        try:
            yield
        finally:
            self._warm_done.set()

    def _send_attempts(self, req: ChatRequest, *, purpose: str,
                       progress: RequestProgress | None = None,
                       cache_policy: CachePolicy | None = None,
                       ) -> tuple[ChatResponse, str]:
        """重试循环本体。**语义全部写在 `_send_with_retry` 的 docstring 里**，
        拆出来只是为了让首发闸不必把整个循环缩进一层。
        返回 `(响应, 成功那次 attempt 的 attempt_id)`——每次上线各有自己的 id，
        失败行也带（账本行与审计行靠它相联）。"""
        last: TransportError | None = None
        policy = TransportRetryPolicy(self.max_retries, self.backoff_base)
        max_attempts = policy.max_attempts
        for attempt_index in range(max_attempts):
            attempt = attempt_index + 1
            attempt_id = _attempt_id()
            content = getattr(self._content_local, 'current', None)
            if content is not None:
                content[0].record_attempt(
                    content[1].turn_id, attempt_id=attempt_id, outcome='started',
                    request_fingerprint=request_fingerprint(req))
            backoff_seconds: float | None = None
            with self._gate:                       # 并发闸：守的是钱不是速率
                heartbeat = AttemptHeartbeat(
                    self.ctx.observe, purpose=purpose, attempt=attempt,
                    max_attempts=max_attempts, progress=progress,
                    interval=self.heartbeat_interval, clock=self._clock)
                heartbeat.start()
                started = self._clock()
                try:
                    # 等待总槽不是供应商调用耗时；每次实际上线才开始计时。
                    with model_slot_from_environment(), pipeline_model_slot(
                            self.ctx.novel_dir, 'deepseek',
                            per_book=self.concurrency_policy.per_book,
                            per_provider=self.concurrency_policy.per_provider):
                        started = self._clock()
                        resp = self.transport.send(req)
                except ModelSlotUnavailable as exc:
                    if content is not None:
                        content[0].record_attempt(
                            content[1].turn_id, attempt_id=attempt_id, outcome='not_started')
                    heartbeat.finish(outcome='error', error_kind='model_slot_not_started')
                    raise
                except TransportError as e:
                    if content is not None:
                        content[0].record_attempt(content[1].turn_id, attempt_id=attempt_id, outcome='error')
                    last = e
                    latency_ms = max(0, round((self._clock() - started) * 1000))
                    backoff_seconds = policy.delay(attempt=attempt, retryable=e.retryable)
                    will_retry = backoff_seconds is not None
                    try:
                        self._record_wire(
                            req, None, error=e, purpose=purpose, attempt=attempt,
                            max_attempts=max_attempts, latency_ms=latency_ms,
                            will_retry=will_retry,
                            backoff_ms=(round(backoff_seconds * 1000)
                                        if backoff_seconds is not None else None),
                            attempt_id=attempt_id,
                            progress=progress, cache_policy=cache_policy)
                    finally:
                        heartbeat.finish(outcome='error', error_kind=e.kind)
                    if not will_retry:
                        raise
                except BaseException:
                    heartbeat.finish(outcome='error', error_kind='unexpected')
                    raise
                else:
                    latency_ms = max(0, round((self._clock() - started) * 1000))
                    # ⚠ 服务端资源中断走**本循环**（退避重试），不交给内容闸——见
                    #   `RESOURCE_INTERRUPTED`。记账走成功分支（`usage` 是真的、
                    #   钱花了），靠 `finish_reason` 列分辨，不新增字段。
                    interrupted = resp.finish_reason == RESOURCE_INTERRUPTED
                    backoff_seconds = policy.delay(attempt=attempt, retryable=interrupted)
                    will_retry = backoff_seconds is not None
                    try:
                        self._record_wire(
                            req, resp, error=None, purpose=purpose, attempt=attempt,
                            max_attempts=max_attempts, latency_ms=latency_ms,
                            will_retry=will_retry,
                            backoff_ms=(round(backoff_seconds * 1000)
                                        if backoff_seconds is not None else None),
                            attempt_id=attempt_id, progress=progress,
                            cache_policy=cache_policy)
                        if content is not None:
                            content[0].record_attempt(
                                content[1].turn_id, attempt_id=attempt_id,
                                outcome='interrupted' if interrupted else 'ok',
                                request_fingerprint=request_fingerprint(req))
                    finally:
                        heartbeat.finish(outcome='ok')
                    if not interrupted:
                        return resp, attempt_id
                    if not will_retry:
                        raise LlmFailed(
                            f'{self.ctx.node}/{purpose}: 服务端资源中断'
                            f'（`finish_reason={RESOURCE_INTERRUPTED}`），'
                            f'{max_attempts} 次上线都没做完。'
                            '⚠ 这几次**已经计费**（账本同 `attempt_id` 那几行带 usage）——'
                            '与连接故障那种「没到达模型、不计费」不是一回事。'
                            '等一下同参数重跑即可：逐章节点天然增量，只补缺的那些。')
            # ⚠ 睡在信号量**外面**：在闸里睡会把整跑串行化（并发闸守的是钱，不是速率）。
            assert backoff_seconds is not None
            self._sleep(backoff_seconds)
        raise last if last else LlmFailed('unreachable')

    def _record_wire(self, req: ChatRequest, resp: ChatResponse | None, *,
                     error: TransportError | None, purpose: str,
                     attempt: int, max_attempts: int, latency_ms: int,
                     will_retry: bool, backoff_ms: int | None,
                     attempt_id: str,
                     progress: RequestProgress | None,
                     cache_policy: CachePolicy | None = None) -> None:
        """一次**真上线**记一行。失败那次 token 三项全 `None`——它没拿回任何读数。"""
        u = (resp.usage if resp else None) or {}
        policy = cache_policy or self.ctx.cache
        arguments = dict(
            attempt_id=attempt_id,
            request_fingerprint=request_fingerprint(req),
            response_sha256=_response_sha256(resp),
            request_model=req.model,
            node=self.ctx.node, sid=self.ctx.sid,
            model=(resp.model if resp else '') or req.model,
            code=self.ctx.code, cached=False, cache_policy=policy.name,
            prompt_tokens=u.get('prompt_tokens'),
            prompt_cache_hit_tokens=u.get('prompt_cache_hit_tokens'),
            prompt_cache_miss_tokens=u.get('prompt_cache_miss_tokens'),
            completion_tokens=u.get('completion_tokens'),
            reasoning_tokens=(u.get('reasoning_tokens')
                              or (u.get('completion_tokens_details') or {}).get('reasoning_tokens')),
            purpose=purpose,
            outcome='ok' if error is None else 'error',
            error=None if error is None else str(error),
            status=None if error is None else error.status,
            reasoning_effort=req.reasoning_effort,
            thinking=req.thinking,
            json_mode=req.json_mode,
            max_tokens=req.max_tokens,
            finish_reason=None if resp is None else resp.finish_reason,
            attempt=attempt,
            max_attempts=max_attempts,
            latency_ms=latency_ms,
            error_kind=None if error is None else error.kind,
            retryable=None if error is None else error.retryable,
            will_retry=will_retry,
            backoff_ms=backoff_ms,
            progress=progress,
        )

        content = getattr(self._content_local, 'current', None)
        if (content is not None and resp is not None and error is None
                and resp.finish_reason != RESOURCE_INTERRUPTED):
            # 完整回答必须先带原 run/attempt 的成本回执持久化。这样进程死在
            # append 前后都能精确补原账，不再次外发，也不把费用归到恢复 run。
            content[0].save_response(content[1].turn_id, {
                'attempt_id': attempt_id,
                'response': asdict(resp),
                'prepared_receipt': {
                    'kind': 'executor-turn-receipt-v1',
                    'run_id': self.ctx.run_id,
                    'parent_run_id': self.ctx.parent_run_id,
                    'ledger_arguments': {
                        **arguments,
                        'progress': None if progress is None else asdict(progress),
                    },
                    'request_payload': req.cache_payload(),
                },
            })
        self.ctx.ledger.record(**arguments)

    def _record_cache_hit(self, req: ChatRequest, resp: ChatResponse, *,
                          purpose: str, progress: RequestProgress | None,
                          cache_policy: CachePolicy | None = None) -> str:
        """一次缓存命中记一行（0 元）。**不记的话，「读了 31 份缓存」与「什么都没做」同形。**

        返回本次命中事件的 `attempt_id`——缓存来的响应同样要过内容闸、
        同样可能被采纳，审计账的联结键不因来源而缺席。
        """
        attempt_id = _attempt_id()
        u = resp.usage or {}
        policy = cache_policy or self.ctx.cache
        self.ctx.ledger.record(
            attempt_id=attempt_id,
            request_fingerprint=request_fingerprint(req),
            response_sha256=_response_sha256(resp),
            request_model=req.model,
            node=self.ctx.node, sid=self.ctx.sid, model=resp.model or req.model,
            code=self.ctx.code, cached=True, cache_policy=policy.name,
            prompt_tokens=u.get('prompt_tokens'),
            completion_tokens=u.get('completion_tokens'),
            reasoning_tokens=(u.get('reasoning_tokens')
                              or (u.get('completion_tokens_details') or {}).get('reasoning_tokens')),
            purpose=purpose,
            reasoning_effort=req.reasoning_effort,
            thinking=req.thinking,
            json_mode=req.json_mode,
            max_tokens=req.max_tokens,
            finish_reason=resp.finish_reason,
            progress=progress,
        )
        self._producer_cache_receipts[attempt_id] = {
            'response': asdict(resp), 'request': req.cache_payload(),
        }
        budget_for(self.ctx, scope=executor_scope(self.ctx.node, purpose, progress)).note_processing(
            run_id=self.ctx.run_id, input_sha256=request_fingerprint(req),
            kind='cached', evidence_sha256=_response_sha256(resp))
        return attempt_id
