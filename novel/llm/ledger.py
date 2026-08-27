#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ledger.py —— 成本账。**原型完全没有这一层。**

【两份账，答的是两个问题】（`_common.md` §八）
  · **账本**（本模块）：`<Output>/_service/ledger.jsonl`，跨书跨时间，答「这周 / 这本书花了多少」；
  · **产物里那一份**（`_provenance.usage`）：答「**这一份**花了多少」——
    那是看返工回路边际成本时会问的，而账本按时间排，答不了。
  ⚠ token 数**不可再生**（`Q-3` 的判准）：响应一丢就永远算不回来了，所以两份都必须落盘。

【为什么是 append-only JSONL】它**永不需要读-改-写**：
并发安全（两个节点同时跑不会互相截断）· 被 kill 不损坏（最多留半行，前面的行照样可读）·
可以直接 `grep`。一个「统计文件」如果需要先读进来改再写回去，
那它在被 kill 的那一刻就是全损——而这层账正是为了记录**已经花掉的钱**。

⚠⚠ **只存事实，不存价格**（`Q-10`：不显示预估花费——「猜一个数写在界面上比不显示更坏」）。
本模块存 token 数与调用数，**不存钱**。要出价钱是另一件事，要配一张带日期的费率表。
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from contracts.schema import Usage
from novel import run_identity
from novel.appendlog import (
    append_line, append_line_under_process_lock, process_append_lock,
)
from novel.errors import ProvenanceRefused
from novel.observability import RequestProgress

#: 账本在 `<Output>/` 下的位置。跨书，所以不在任何一本书的目录里。
LEDGER_REL = ('_service', 'ledger.jsonl')

#: 采纳审计账：与成本账同目录、**分文件**。成本账答「花了多少」，这份答
#: 「内容闸怎么判的、最终 artifact 用的是哪一次响应、丢掉的那份答案留证在哪」
#: （`gate_decision` / `artifact_adoption` / `answer_lost` 三种行，
#: 靠 `attempt_id` 与成本账的 wire 行相联）。
#: ⚠ 不并进 `ledger.jsonl`：既有读者按「行=一次调用」聚合
#: （`tools/ab_compare.py::cost_table` 把没有 `cached` 键的行当 wire 行数，
#: `tools/observe_provider_limits.py` 拿行数对 wire attempts）——审计行混进去
#: 会把判定事件算成调用数。
AUDIT_REL = ('_service', 'llm_audit.jsonl')

#: token 三项的名字。**一处落点**——三个计数器、三个「见过几次」、`Usage` 的三个键
#: 都从它派生，免得哪天加一项时漏掉其中一处。
TOKEN_KINDS = ('prompt_tokens', 'completion_tokens', 'reasoning_tokens')

# ``CostLedger`` 是每 run 一个实例，但同一进程可以有多本账指向
# 同一 audit JSONL。带稳定身份事件的「查 id + 追加」必须跨实例也是一个临界区。
_AUDIT_IDEMPOTENT_APPEND_LOCK = threading.Lock()


def combine_usage(*parts: Usage | None) -> Usage | None:
    """合并共同产出一份制品的多段用量；任一段未知则总量未知。

    ``usage=None`` 不是“确定花了 0”，而是旧产物没记或本段无法结算。把其余段
    单独写成总量会得到一个看似精确但必然偏小的数字。
    """
    if not parts or any(part is None for part in parts):
        return None
    known = tuple(part for part in parts if part is not None)
    tokens = {
        key: (sum(getattr(part, key) for part in known)
              if all(getattr(part, key) is not None for part in known) else None)
        for key in TOKEN_KINDS
    }
    return Usage(
        calls_made=sum(part.calls_made for part in known),
        calls_cached=sum(part.calls_cached for part in known),
        **tokens,
    )

# 一次合成跑会在同一个进程里造多个账本实例，不同小说则是不同的 CLI 进程。
# 两种并发都由 `novel/appendlog.py` 的那一把追加口盖住（线程锁 + 操作系统文件锁），
# 本模块因此不再自己持锁——那把锁现在还给 `jobs.jsonl` / `queues.jsonl` /
# `agent_progress.jsonl` 共用，不是各抄一份。


def ledger_path(base: str | Path) -> Path:
    """`<Output>` → 账本路径。**唯一落点**——账本只有一个，别按书分账。"""
    return Path(base).joinpath(*LEDGER_REL)


def audit_path(base: str | Path) -> Path:
    """`<Output>` → 采纳审计账路径。与 `ledger_path` 同目录，见 `AUDIT_REL`。"""
    return Path(base).joinpath(*AUDIT_REL)


def append_commander_call(
        base: str | Path, *, run_id: str, session_run_id: str, sid: str,
        attempt_id: str, request_model: str, requested_effort: str,
        code: str, protocol_version: str,
        actual_model: str | None = None,
        actual_effort: str | None = None,
        provider_session_id: str | None = None,
        cli_version: str | None = None,
        usage: Mapping[str, int | None] | None = None,
        outcome: str, exit_code: int,
        error_kind: str | None = None) -> None:
    """追加一次整书 Commander 调用的成本事实。

    Commander 不是 ``NODES`` 的成员，故意以 ``role='commander'``、``node=null``
    落在同一本成本账里；拿一个假 node 填空会污染完成度与按节点成本统计。它也不
    复用 ``CostLedger.record``：后者是一条节点产物生产线的 run 内累计器，而外层
    Commander 不铸造任何 artifact provenance。两种调用仍用同一个 append-only
    字节口，因此并发与半行恢复纪律不分叉。

    ``attempt_id`` 同时写入 agent session 的可得 turn，是两账的联结键。供应商没有
    回 provider session id 时仍诚实记一次调用，但 session audit 不得据此伪造 turn。
    实际 model/effort 没回显就保持 ``null``，绝不拿请求值补齐。
    """
    run_id = run_identity.check_run_id(run_id)
    session_run_id = run_identity.check_run_id(session_run_id)
    for name, value in (
            ('sid', sid), ('attempt_id', attempt_id),
            ('request_model', request_model),
            ('requested_effort', requested_effort), ('code', code),
            ('protocol_version', protocol_version), ('outcome', outcome)):
        if not isinstance(value, str) or not value.strip():
            raise ProvenanceRefused(f'commander ledger {name} 必须是非空字符串')
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ProvenanceRefused('commander ledger exit_code 必须是整数')
    if outcome not in ('ok', 'error'):
        raise ProvenanceRefused('commander ledger outcome 只接受 ok/error')
    for name, value in (
            ('actual_model', actual_model), ('actual_effort', actual_effort),
            ('provider_session_id', provider_session_id),
            ('cli_version', cli_version), ('error_kind', error_kind)):
        if value is not None and (
                not isinstance(value, str) or not value.strip()
                or any(ord(char) < 32 for char in value)):
            raise ProvenanceRefused(
                f'commander ledger {name} 必须是非空、无控制字符的字符串或 null')

    usage_values: dict[str, int | None] = {
        'prompt_tokens': None,
        'prompt_cache_hit_tokens': None,
        'prompt_cache_miss_tokens': None,
        'completion_tokens': None,
        'reasoning_tokens': None,
        'total_tokens': None,
    }
    if usage is not None:
        if not isinstance(usage, Mapping):
            raise ProvenanceRefused('commander ledger usage 必须是 mapping 或 null')
        extra = set(usage) - set(usage_values)
        if extra:
            raise ProvenanceRefused(
                f'commander ledger usage 含未知字段：{sorted(extra)}')
        for name in usage_values:
            value = usage.get(name)
            if (value is not None
                    and (isinstance(value, bool) or not isinstance(value, int)
                         or value < 0)):
                raise ProvenanceRefused(
                    f'commander ledger usage.{name} 必须是非负整数或 null')
            usage_values[name] = value

    row = {
        'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'run_id': run_id,
        'session_run_id': session_run_id,
        'role': 'commander',
        'sid': sid,
        'node': None,
        'model': actual_model,
        'request_model': request_model,
        'code': code,
        'cached': False,
        'cache_policy': 'commander-agent-session',
        **usage_values,
        'purpose': 'commander',
        'outcome': outcome,
        'error': error_kind,
        'status': None,
        'exit_code': exit_code,
        'reasoning_effort': requested_effort,
        'actual_reasoning_effort': actual_effort,
        'thinking': None,
        'json_mode': None,
        'max_tokens': None,
        'finish_reason': None,
        'attempt': 1,
        'max_attempts': 1,
        'latency_ms': None,
        'error_kind': error_kind,
        'retryable': False,
        'will_retry': False,
        'backoff_ms': None,
        'attempt_id': attempt_id,
        'request_fingerprint': None,
        'response_sha256': None,
        'provider_session_id': provider_session_id,
        'protocol_version': protocol_version,
        'cli_version': cli_version,
        'chapter': None,
        'chapter_index': None,
        'chapter_total': None,
    }
    append_line(
        ledger_path(base),
        json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _adoption_id(*, run_id: str, node: str, purpose: str, attempt_id: str,
                 artifact: str, artifact_fingerprint: str,
                 chapter: int | None) -> str:
    """一次正式采纳的稳定身份；用数组避免字段分隔符义。"""
    canonical = json.dumps(
        [run_id, node, purpose, attempt_id, artifact,
         artifact_fingerprint, chapter],
        ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _repair_event_id(
        *, run_id: str, node: str, purpose: str, attempt_id: str,
        action: str, decision: str, actor: str | None, gate: str | None,
        candidate_sha256: str | None,
) -> str:
    """不可变 repair 终局事件身份；outcome 落盘后的 replay 可安全补账。"""
    canonical = json.dumps(
        [run_id, node, purpose, attempt_id, action, decision,
         actor, gate, candidate_sha256],
        ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _gate_decision_id(
        *, run_id: str, sid: str, node: str, purpose: str, attempt_id: str,
        source: str, decision: str, gate: str | None,
        reasoning_effort: str | None, thinking: bool | None,
        rung_index: int | None, sample: int | None, resample: bool,
        reject_detail: list[dict[str, object]] | None, salvaged: bool,
        request_mode: str, recovery_from_attempts: Iterable[str],
        chapter: int | None,
) -> str:
    """一次内容闸判定的稳定身份；供 checkpoint 崩溃补尾去重。"""
    canonical = json.dumps({
        'run_id': run_id, 'sid': sid, 'node': node, 'purpose': purpose,
        'attempt_id': attempt_id, 'source': source, 'decision': decision,
        'gate': gate, 'reasoning_effort': reasoning_effort,
        'thinking': thinking, 'rung_index': rung_index, 'sample': sample,
        'resample': resample, 'reject_detail': reject_detail,
        'salvaged': salvaged, 'request_mode': request_mode,
        'recovery_from_attempts': list(recovery_from_attempts),
        'chapter': chapter,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class UsageMark:
    """账本的一把**刻度**：结算「上一份产物之后又花了多少」时的起点。

    ⚠ `seq` 让它**一次性**：同一把刻度结算两次 = 两份产物认领同一批 token，
    而那正是本次要修掉的那个病的镜像。见 `CostLedger.commit`。
    """

    seq: int
    calls_made: int
    calls_cached: int
    tokens: tuple[int, ...]
    #: 每一项**被响应报过几次**。差值为 0 ⇒ 这段窗口里没人报过它 ⇒ `null`，不是 `0`。
    seen: tuple[int, ...]


@dataclass
class _UsageCounter:
    calls_made: int = 0
    calls_cached: int = 0
    tokens: dict[str, int] = field(
        default_factory=lambda: {key: 0 for key in TOKEN_KINDS})
    seen: dict[str, int] = field(
        default_factory=lambda: {key: 0 for key in TOKEN_KINDS})

    def usage(self) -> Usage | None:
        if not self.calls_made and not self.calls_cached:
            return None
        values = {
            key: self.tokens[key] if self.seen[key] else None
            for key in TOKEN_KINDS
        }
        return Usage(calls_made=self.calls_made,
                     calls_cached=self.calls_cached, **values)


@dataclass
class CostLedger:
    """一次 run 的成本账：往盘上追加行，同时在内存里攒出**每一份产物**的 `usage`。

    ⚠⚠ **`calls_made` 与 `calls_cached` 是两个计数器，不许合并。**
    只数真调用，会让「参数不变的重跑什么都没跑」（`Q-11` / `R-12.4` 要求**显式说出来**
    的那条语义）与「什么都没干」在报告上**同形**。

    ⚠⚠ **本类的计数器是 run 累计的，而 `_provenance.usage` 问的是「这一份花了多少」。**
    两者原来是同一个数：`RunContext.llm_provenance` 直接写 `ledger.usage()`。
    于是一次跑产 N 份产物时，第 2 份带着第 1 份的账，第 3 份带着前两份的，
    一路累加——而返工回路的边际成本正是要从这一格里读的。
    ⇒ 结算走 `snapshot()` / `usage_since(mark)` 的**差值**；`usage()`（无参）仍是 run 总量。
    """

    path: Path | None = None            # `None` = 不落盘（测试 / `--dry-run`），仍然计数

    #: 本账本实例所属的 run。自己新建时也必须有身份；RunContext
    #: 在任何记账前再用 ``bind_run_id`` 核对，防止一本账被两次跑共用。
    run_id: str = field(default_factory=run_identity.resolve_run_id)

    calls_made: int = 0                 # **真发出去**的调用数
    calls_cached: int = 0               # 命中缓存的次数（＝ 0 元）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    #: 每一项**被响应报过几次**。
    #:
    #: ⚠⚠ 原来这里是**一个** `_saw_token_counts: bool`，三项共用。于是一次只给了
    #: `prompt_tokens` 的响应（DeepSeek 实测有）会把另外两项写成 `0`——
    #: 而契约明写「`null` = 这次响应**没给** usage，**不是 0**」（`_common.md` §八）。
    #: 一个全局开关同时管三件事，必然把其中两件搞错。⇒ 一项一个计数。
    _seen: dict[str, int] = field(default_factory=lambda: {k: 0 for k in TOKEN_KINDS},
                                  repr=False)

    #: 刻度的世代号。每 `commit` 一次 +1，于是旧刻度立刻作废。
    _seq: int = field(default=0, repr=False)

    #: 采纳审计行的内存镜像。`path=None`（`--dry-run` / 测试）时盘上没有
    #: `llm_audit.jsonl`，判定与采纳仍要能被断言——这份镜像就是那条路。
    audit_events: list[dict] = field(default_factory=list, repr=False)

    # Concurrent chapter writers cannot use one moving usage mark: completion
    # order would make one chapter claim another chapter's calls. Requests already
    # carry RequestProgress, so aggregate and settle them by chapter instead.
    _by_chapter: dict[int, _UsageCounter] = field(
        default_factory=dict, repr=False)
    _claimed_chapters: set[int] = field(default_factory=set, repr=False)

    #: 本 run 的 ``attempt_id → request_fingerprint`` 精确索引。
    #: 成本 JSONL 是跨 run 的账，不应为铸一份正在落盘的 provenance 再扫全文件；
    #: 且 ``path=None`` 的测试/缓存路径同样必须保留这条联结。
    _request_fingerprints: dict[str, str] = field(default_factory=dict, repr=False)

    #: 本 run 的 ``attempt_id → 实际响应模型`` 精确索引。与请求指纹同一条联结：
    #: 内容闸只记 attempt id，产物来源账据此反查最终采纳的是 Flash 还是 Pro，
    #: 不能从 client 的主模型配置猜。
    _request_models: dict[str, str] = field(default_factory=dict, repr=False)

    #: 与上面两张索引同源：跨 CLI 的响应修复恢复链需要把**原来已经花掉、但尚未被
    #: 产物认领**的 attempts 重新纳入本次 provenance 窗口。按 purpose 过滤，不能把
    #: 同一节点的另一章/另一相调用一起认领。
    _request_purposes: dict[str, str] = field(default_factory=dict, repr=False)
    _request_chapters: dict[str, int | None] = field(default_factory=dict, repr=False)

    #: checkpoint 不能只凭请求指纹反推配置：指纹是结果身份，不说明当前运行到底
    #: 允许哪条模型链、哪档 effort/thinking。这里保留每次实际 ChatRequest 的非正文
    #: 参数，以及供应商响应 content 的既有 SHA 联结；跨进程恢复时从原 ledger 行重建。
    _request_specs: dict[str, dict[str, object]] = field(
        default_factory=dict, repr=False)
    _response_sha256s: dict[str, str | None] = field(
        default_factory=dict, repr=False)

    #: Stage 2 会并发等待独立章节响应。计数更新与 JSONL 追加必须是同一个临界区，
    #: 否则两个成功回包可能互相覆盖内存计数，甚至把两行字节交错写坏。
    _lock: threading.RLock = field(default_factory=threading.RLock,
                                    repr=False, compare=False)

    def bind_run_id(self, run_id: str) -> None:
        """把账本与 RunContext 的身份钉死；已绑到另一次跑则关闭式失败。"""
        value = run_identity.check_run_id(run_id)
        with self._lock:
            if self.run_id != value:
                raise ProvenanceRefused(
                    f'CostLedger run_id={self.run_id!r} 与 '
                    f'RunContext run_id={value!r} 不一致；拒绝串账')

    def record(self, *, node: str, sid: str, model: str, code: str,
               cached: bool, cache_policy: str,
               prompt_tokens: int | None = None,
               prompt_cache_hit_tokens: int | None = None,
               prompt_cache_miss_tokens: int | None = None,
               completion_tokens: int | None = None,
               reasoning_tokens: int | None = None,
               purpose: str = '',
               outcome: str = 'ok', error: str | None = None,
               status: int | None = None,
               reasoning_effort: str | None = None,
               thinking: bool | None = None,
               json_mode: bool | None = None,
               max_tokens: int | None = None,
               finish_reason: str | None = None,
               attempt: int | None = None,
               max_attempts: int | None = None,
               latency_ms: int | None = None,
               error_kind: str | None = None,
               retryable: bool | None = None,
               will_retry: bool | None = None,
               backoff_ms: int | None = None,
               attempt_id: str | None = None,
               request_fingerprint: str | None = None,
               response_sha256: str | None = None,
               request_model: str | None = None,
               progress: RequestProgress | None = None) -> None:
        """记**一次上线**（或一次缓存命中）。`cached=True` 表示这一次是缓存命中（0 元）。

        ⚠ **缓存命中也要记一行。** 不记的话，「这次跑读了 31 份缓存」与
        「这次跑什么都没做」在账本上长得一模一样，而这两件事的下一步动作完全不同。

        ⚠⚠ **失败的那几次也要记，而且计进 `calls_made`。** 原来只在整轮重试**成功之后**
        记一行：传输层实际发了 3 次而账上只有 1 次，整轮失败则一行都没有——
        于是「限流限了半天」在账本上与「一次就过」长得一模一样，而那两件事的下一步完全不同。
        `calls_made` 的契约原文是「**真发出去**的调用数」，一次 429 确实上过线，
        一次超时对面很可能已经生成并计费了。
        ⇒ 逐次记，`outcome` 那一列把成功与失败分开（离线 `grep` 得出来）。
        ⚠ **不给 `Usage` 加 `calls_failed`**：那是一次契约改版，会让盘上 79 份存量产物
        全都缺一个必填键，而这里要答的问题 JSONL 已经答得了。
        """
        with self._lock:
            if (request_fingerprint is not None
                    and (len(request_fingerprint) != 64
                         or any(ch not in '0123456789abcdef'
                                for ch in request_fingerprint))):
                raise ProvenanceRefused(
                    'request_fingerprint 必须是完整的小写 SHA-256（64 hex）；'
                    '32 字符 cache key 只用于定位既有缓存，不能冒充请求来源身份')
            if attempt_id is not None and request_fingerprint is not None:
                previous = self._request_fingerprints.get(attempt_id)
                if previous is not None and previous != request_fingerprint:
                    raise ProvenanceRefused(
                        f'attempt_id={attempt_id} 同时指向两个请求指纹：'
                        f'{previous} / {request_fingerprint}')
                self._request_fingerprints[attempt_id] = request_fingerprint
            if attempt_id is not None:
                previous_model = self._request_models.get(attempt_id)
                if previous_model is not None and previous_model != model:
                    raise ProvenanceRefused(
                        f'attempt_id={attempt_id} 同时指向两个模型：'
                        f'{previous_model!r} / {model!r}')
                self._request_models[attempt_id] = model
                previous_purpose = self._request_purposes.get(attempt_id)
                if previous_purpose is not None and previous_purpose != purpose:
                    raise ProvenanceRefused(
                        f'attempt_id={attempt_id} 同时指向两个 purpose：'
                        f'{previous_purpose!r} / {purpose!r}')
                self._request_purposes[attempt_id] = purpose
                self._request_chapters[attempt_id] = (
                    progress.chapter if progress is not None else None)
                spec: dict[str, object] = {
                    'request_model': request_model or model,
                    'reasoning_effort': reasoning_effort,
                    'thinking': thinking,
                    'json_mode': json_mode,
                    'max_tokens': max_tokens,
                }
                previous_spec = self._request_specs.get(attempt_id)
                if previous_spec is not None and previous_spec != spec:
                    raise ProvenanceRefused(
                        f'attempt_id={attempt_id} 同时指向两份 ChatRequest 配置：'
                        f'{previous_spec!r} / {spec!r}')
                self._request_specs[attempt_id] = spec
                previous_response = self._response_sha256s.get(attempt_id)
                if (attempt_id in self._response_sha256s
                        and previous_response != response_sha256):
                    raise ProvenanceRefused(
                        f'attempt_id={attempt_id} 同时指向两个 response_sha256：'
                        f'{previous_response!r} / {response_sha256!r}')
                self._response_sha256s[attempt_id] = response_sha256

            if cached:
                self.calls_cached += 1
            else:
                self.calls_made += 1
            for name, val in zip(TOKEN_KINDS,
                                 (prompt_tokens, completion_tokens, reasoning_tokens)):
                if val is not None:
                    setattr(self, name, getattr(self, name) + val)
                    self._seen[name] += 1

            if progress is not None:
                counter = self._by_chapter.setdefault(
                    progress.chapter, _UsageCounter())
                if cached:
                    counter.calls_cached += 1
                else:
                    counter.calls_made += 1
                for name, val in zip(
                        TOKEN_KINDS,
                        (prompt_tokens, completion_tokens, reasoning_tokens)):
                    if val is not None:
                        counter.tokens[name] += val
                        counter.seen[name] += 1

            if self.path is None:
                return
            row = {
                'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'run_id': self.run_id,
                'sid': sid, 'node': node, 'model': model, 'code': code,
                'cached': cached, 'cache_policy': cache_policy,
                'prompt_tokens': prompt_tokens,
                # 供应商侧 prompt cache 与本地 `cached` 是两件事。必有两列；
                # `null` 表示响应没给，不能伪装成确定命中 0 token。
                'prompt_cache_hit_tokens': prompt_cache_hit_tokens,
                'prompt_cache_miss_tokens': prompt_cache_miss_tokens,
                'completion_tokens': completion_tokens,
                'reasoning_tokens': reasoning_tokens,
                'purpose': purpose,
                'outcome': outcome, 'error': error, 'status': status,
                'reasoning_effort': reasoning_effort,
                'thinking': thinking,
                'json_mode': json_mode,
                'max_tokens': max_tokens,
                'finish_reason': finish_reason,
                'attempt': attempt,
                'max_attempts': max_attempts,
                'latency_ms': latency_ms,
                'error_kind': error_kind,
                'retryable': retryable,
                'will_retry': will_retry,
                'backoff_ms': backoff_ms,
                # 采纳审计的联结键：`llm_audit.jsonl` 的 gate_decision / adoption 行
                # 靠 `attempt_id` 指回这一行。`None` = 旧调用点没传（形状不破）。
                'attempt_id': attempt_id,
                'request_fingerprint': request_fingerprint,
                'response_sha256': response_sha256,
                'request_model': request_model or model,
                'chapter': progress.chapter if progress else None,
                'chapter_index': progress.index if progress else None,
                'chapter_total': progress.total if progress else None,
            }
            self._append(row)

    def request_fingerprint_for_attempt(self, attempt_id: str) -> str:
        """精确反查一次已记账请求；缺联结是来源账结构错误，不做估算。"""
        with self._lock:
            value = self._request_fingerprints.get(attempt_id)
            if value is None:
                raise ProvenanceRefused(
                    f'accepted attempt_id={attempt_id!r} 在本 run 的成本账里没有 '
                    'request_fingerprint；拒绝按默认 effort/thinking 估算')
            return value

    def model_for_attempt(self, attempt_id: str) -> str:
        """精确反查一次已记账请求的实际响应模型；缺联结时关闭式失败。"""
        with self._lock:
            value = self._request_models.get(attempt_id)
            if value is None:
                raise ProvenanceRefused(
                    f'accepted attempt_id={attempt_id!r} 在本 run 的成本账里没有 model；'
                    '拒绝按 client 主模型估算')
            return value

    def attempt_ids_for(self, *, purpose: str,
                        request_fingerprints: Iterable[str]) -> tuple[str, ...]:
        """本逻辑请求已经发生过的全部成本行；供修复状态封存，顺序不承载语义。"""
        allowed = set(request_fingerprints)
        with self._lock:
            return tuple(sorted(
                attempt_id for attempt_id, fingerprint
                in self._request_fingerprints.items()
                if fingerprint in allowed
                and self._request_purposes.get(attempt_id) == purpose))

    def attempt_ids_for_purpose(self, purpose: str) -> tuple[str, ...]:
        """本 run 中一个逻辑 purpose 的全部 wire/cache attempts，含传输层重试。"""
        with self._lock:
            return tuple(sorted(
                attempt_id for attempt_id, item_purpose
                in self._request_purposes.items()
                if item_purpose == purpose))

    def request_identity_for_attempt(self, attempt_id: str) -> dict[str, object]:
        """checkpoint 所需的 ledger 侧完整请求配置与响应内容身份。"""
        with self._lock:
            fingerprint = self._request_fingerprints.get(attempt_id)
            model = self._request_models.get(attempt_id)
            spec = self._request_specs.get(attempt_id)
            if fingerprint is None or model is None or spec is None:
                raise ProvenanceRefused(
                    f'attempt_id={attempt_id!r} 在本 run 成本账缺请求配置联结')
            return {
                'attempt_id': attempt_id,
                'request_fingerprint': fingerprint,
                'model': model,
                **spec,
                'response_sha256': self._response_sha256s.get(attempt_id),
            }

    def claim_prior_attempts(self, attempt_ids: Iterable[str], *, sid: str,
                             node: str, purpose: str,
                             request_fingerprints: Iterable[str]) -> None:
        """把前一次 CLI 已记账、尚未产出制品的调用纳入本次 provenance 窗口。

        **不追加第二行成本账。** 原 wire 行已经是费用事实；这里仅恢复内存计数与
        attempt 联结，使修复后落下的产物能够如实认领首发/重采的全部 token。
        若 path=None，同进程没有跨 CLI 可恢复材料，非空输入关闭式拒绝。
        """
        wanted = set(attempt_ids)
        if not wanted:
            return
        allowed_fp = set(request_fingerprints)
        with self._lock:
            missing = wanted - set(self._request_fingerprints)
            if not missing:
                return
            if self.path is None:
                raise ProvenanceRefused(
                    f'响应修复要恢复 {len(missing)} 条 prior attempts，但成本账没有盘上路径')

            rows: dict[str, dict[str, object]] = {}
            try:
                with process_append_lock(self.path):
                    raw_lines = self.path.read_text(encoding='utf-8').splitlines()
            except OSError as exc:
                raise ProvenanceRefused(f'读不了响应修复所指成本账 {self.path}: {exc}') from exc
            for index, line in enumerate(raw_lines):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    if index == len(raw_lines) - 1:
                        continue
                    raise ProvenanceRefused(
                        f'成本账中间第 {index + 1} 行损坏，不能恢复响应修复来源') from exc
                attempt_id = row.get('attempt_id') if isinstance(row, dict) else None
                if attempt_id in missing:
                    rows[str(attempt_id)] = row

            absent = missing - set(rows)
            if absent:
                raise ProvenanceRefused(
                    f'响应修复引用的 prior attempts 不在成本账：{sorted(absent)}')

            for attempt_id in sorted(missing):
                row = rows[attempt_id]
                if ('run_id' in row and row.get('run_id') != self.run_id):
                    raise ProvenanceRefused(
                        f'prior attempt {attempt_id} 属于 run_id='
                        f'{row.get("run_id")!r}，当前 run_id={self.run_id!r}；'
                        '新账只允许同 run resume，只有历史缺字段行按 legacy 放行')
                if (row.get('sid') != sid or row.get('node') != node
                        or row.get('purpose') != purpose
                        or row.get('request_fingerprint') not in allowed_fp):
                    raise ProvenanceRefused(
                        f'prior attempt {attempt_id} 不属于当前请求：'
                        f'{row.get("sid")}/{row.get("node")}/{row.get("purpose")}/'
                        f'{row.get("request_fingerprint")}')
                fingerprint = str(row['request_fingerprint'])
                model = str(row.get('model') or '')
                self._request_fingerprints[attempt_id] = fingerprint
                self._request_models[attempt_id] = model
                self._request_purposes[attempt_id] = purpose
                chapter_raw = row.get('chapter')
                chapter = chapter_raw if isinstance(chapter_raw, int) else None
                self._request_chapters[attempt_id] = chapter
                self._request_specs[attempt_id] = {
                    'request_model': str(row.get('request_model') or model),
                    'reasoning_effort': row.get('reasoning_effort'),
                    'thinking': row.get('thinking'),
                    'json_mode': row.get('json_mode'),
                    'max_tokens': row.get('max_tokens'),
                }
                raw_response_sha = row.get('response_sha256')
                self._response_sha256s[attempt_id] = (
                    str(raw_response_sha) if isinstance(raw_response_sha, str) else None)

                cached = row.get('cached') is True
                if cached:
                    self.calls_cached += 1
                else:
                    self.calls_made += 1
                counter = (self._by_chapter.setdefault(chapter, _UsageCounter())
                           if chapter is not None else None)
                if counter is not None:
                    if cached:
                        counter.calls_cached += 1
                    else:
                        counter.calls_made += 1
                for key in TOKEN_KINDS:
                    value = row.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        setattr(self, key, getattr(self, key) + value)
                        self._seen[key] += 1
                        if counter is not None:
                            counter.tokens[key] += value
                            counter.seen[key] += 1

    def _append(self, row: dict) -> None:
        """一行 JSON，**按字节追加**（换行与两把锁都在 `appendlog.append_line` 里）。

        ⚠ 二进制模式不是洁癖：Windows 上文本模式会把 `\\n` 写成 `\\r\\n`，
        于是同一份账本在两台机器上是两种字节，`grep`/`wc -l` 之外的任何逐字节比对都会打架。
        """
        assert self.path is not None
        append_line(self.path, json.dumps(row, ensure_ascii=False, sort_keys=True))

    # ── 采纳审计：内容闸判定与最终采纳（`llm_audit.jsonl`）────────────────

    def note_gate(self, *, sid: str, node: str, purpose: str, attempt_id: str,
                  source: str, decision: str, gate: str | None = None,
                  reasoning_effort: str | None = None,
                  thinking: bool | None = None,
                  rung_index: int | None = None,
                  sample: int | None = None,
                  resample: bool = False,
                  reject_detail: list[dict[str, object]] | None = None,
                  salvaged: bool = False,
                  request_mode: str = 'original',
                  recovery_from_attempts: Iterable[str] = (),
                  chapter: int | None = None) -> None:
        """内容闸对**一次已获取的响应**（wire 或缓存）的判定，各记一行。

        ⚠ 成本账那一行是**传输层**的结论（`outcome=ok` 哪怕正文随即被闸拒）——
        「最终采纳档位」原来只能靠「同 purpose 最后一条 ok 行」猜，并发、缓存重放
        与中断恢复下都不可靠。⇒ `decision=accepted/rejected` 在这里显式落账，
        `attempt_id` 指回成本账的那一行。

        ⚠⚠ **`reject_detail` 答的是「拒在哪个字段」**（历史见 `docs/BACKEND_CONTRACT.md` §十 2026-08-22）：
        `[{loc, type, count}, …]`，按 (字段路径, 错误类型) 去重、下标压成 `[]`。
        在它之前，这一行只有 `gate` 四个字，而拒因只进 stdout 事件——
        2026-08-22 一次整跑被超时杀掉之后，两次 `schema` 拒**再也查不出拒在哪**。
        ⚠ 它**不带模型正文**：`input` 不取、`msg` 也不取（`msg` 会把 `input` 插值进去）。
        这份账与产物同一棵目录树，正文外发边界管的是同一批字节。
        ⚠ `accepted` 的行恒 `None`——「过了」没有拒因，写个空列表会让统计多一格。

        ⚠⚠ **`salvaged` 答的是「这份正文从哪个通道来」**
        （`novel/llm/client.py::SALVAGE_ANSWER_FROM_REASONING`）：`True` = `content`
        是空的、这一份是从 `reasoning` 里捞回来并过了同一套闸的。
        它与 `gate` 正交：捞成功的那一行是 `decision=accepted` + `gate=None` +
        `salvaged=True`。**不落这一位，「有几章的产物不是走正常通道产出的」事后就答不了。**

        `request_mode` / `recovery_from_attempts` 回答另一根轴：本次请求是原始采样、
        定向修复还是约束重建，以及它显式消费了哪些失败 attempt。只留策略与联结键，
        不把动态 prompt 或失败正文复制进 `_service/` 审计账。
        """
        recovery = list(recovery_from_attempts)
        gate_decision_id = _gate_decision_id(
            run_id=self.run_id, sid=sid, node=node, purpose=purpose,
            attempt_id=attempt_id, source=source, decision=decision, gate=gate,
            reasoning_effort=reasoning_effort, thinking=thinking,
            rung_index=rung_index, sample=sample, resample=resample,
            reject_detail=reject_detail, salvaged=salvaged,
            request_mode=request_mode, recovery_from_attempts=recovery,
            chapter=chapter)
        self._append_audit_idempotent({
            'kind': 'gate_decision',
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'gate_decision_id': gate_decision_id,
            'sid': sid, 'node': node, 'purpose': purpose,
            'attempt_id': attempt_id,
            'source': source,                  # cache | wire
            'decision': decision,              # accepted | rejected
            'gate': gate,                      # REJECT_GATES 之一；accepted 时 None
            'reasoning_effort': reasoning_effort,
            'thinking': thinking,
            'rung_index': rung_index,
            'sample': sample,
            'resample': resample,
            # 后继请求是否显式消费了前序失败证据；只记策略与联结键，不记 prompt/正文。
            'request_mode': request_mode,
            'recovery_from_attempts': recovery,
            'reject_detail': reject_detail,   # [{loc, type, count}, …]；accepted 时 None
            'salvaged': salvaged,              # True = 正文捞自推理通道，不是 content
            'chapter': chapter,
        }, identity_key='gate_decision_id')

    def note_answer_lost(self, *, sid: str, node: str, purpose: str,
                         attempt_id: str, source: str,
                         reasoning_effort: str | None = None,
                         thinking: bool | None = None,
                         json_mode: bool | None = None,
                         rung_index: int | None = None,
                         sample: int | None = None,
                         resample: bool = False,
                         salvaged: bool = False,
                         finish_reason: str | None = None,
                         content_chars: int = 0,
                         reasoning_chars: int = 0,
                         dump: str | None = None,
                         dump_error: str | None = None,
                         chapter: int | None = None) -> None:
        """「答案流丢失」各记一行：**指针与读数进账，正文进留证文件。**

        它与同一次拒绝的 `gate_decision` 行不重复，各答一个问题：那一行答
        「哪一道闸判的、下一步是重采还是模型兜底」；这一行答**「证据在哪」**——
        `dump` 是相对**书目录**的路径（`novel/paths.py::answer_lost_rel`），
        因为那份推理里装的是这本书的正文，跟书走而不是跟服务走。

        ⚠ **token 数不抄在这里。** `completion_tokens` / `reasoning_tokens` 归成本账
        （同一个 `attempt_id` 那一行），一个数不该有第二个声称拥有它的地方；
        留证文件里另存一份供应商原样的 `usage`，那是**证据**不是账。
        ⚠ `dump=None` + `dump_error` = 证据没写成（盘满 / 路径超长 / 权限）。
        留证失败**不许**让一次已经付过钱的调用抛掉 ⇒ 失败也要留下这一行说清楚。

        ⚠⚠ **`salvaged=True` 的这一行说的不是「丢了」而是「接住了」**：那一份被
        `SALVAGE_ANSWER_FROM_REASONING` 捞出来、过了同一套闸、并且**被采纳了**。
        ⇒ 数「答案流丢失害我们付了多少次重采」时要**排除**这一位为真的行，
        否则会把这条路救回来的那些次算成损失。
        """
        self._append_audit({
            'kind': 'answer_lost',
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'sid': sid, 'node': node, 'purpose': purpose,
            'attempt_id': attempt_id,
            'source': source,                  # cache | wire
            'reasoning_effort': reasoning_effort,
            'thinking': thinking,
            'json_mode': json_mode,
            'rung_index': rung_index,
            'sample': sample,
            'resample': resample,
            'salvaged': salvaged,              # True = 捞回来并采纳了，不是丢了
            'finish_reason': finish_reason,
            'content_chars': content_chars,
            'reasoning_chars': reasoning_chars,
            'dump': dump,                      # 相对书目录；None = 没写成
            'dump_error': dump_error,
            'chapter': chapter,
        })

    def note_response_repair(
            self, *, sid: str, node: str, purpose: str, attempt_id: str,
            action: str, decision: str, actor: str | None = None,
            gate: str | None = None, candidate_sha256: str | None = None,
            dump: str | None = None, dump_error: str | None = None,
            model: str | None = None, rung_index: int | None = None,
            sample: int | None = None, chapter: int | None = None) -> None:
        """一次拒绝是否进入修复、由谁提交、同闸复验结果如何。

        正文只在书内隔离目录；这行只留联结、决定与哈希。`decision=accepted` 也不把
        原 `gate_decision(rejected)` 改写成通过——通过的是 agent 候选，不是供应商原响应。
        """
        row = {
            'kind': 'response_repair',
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'sid': sid, 'node': node, 'purpose': purpose,
            'attempt_id': attempt_id,
            'action': action,              # offered | agent | declined | deterministic | ineligible
            'decision': decision,          # pending | accepted | rejected | declined | skipped
            'actor': actor,
            'gate': gate,
            'candidate_sha256': candidate_sha256,
            'dump': dump,
            'dump_error': dump_error,
            'model': model,
            'rung_index': rung_index,
            'sample': sample,
            'chapter': chapter,
        }
        if decision in ('accepted', 'rejected', 'declined') and action in (
                'agent', 'deterministic', 'declined'):
            repair_event_id = _repair_event_id(
                run_id=self.run_id, node=node, purpose=purpose,
                attempt_id=attempt_id, action=action, decision=decision,
                actor=actor, gate=gate, candidate_sha256=candidate_sha256)
            row['repair_event_id'] = repair_event_id
            self._append_audit_idempotent(
                row, identity_key='repair_event_id')
            return
        self._append_audit(row)

    def note_adoption(self, *, sid: str, node: str, purpose: str,
                      attempt_id: str, artifact: str,
                      artifact_fingerprint: str,
                      chapter: int | None = None) -> None:
        """正式产物落盘时逐 purpose 记「这份 artifact 采纳了哪一次响应」。

        「最终采纳档位」由 `artifact_adoption → attempt_id → 成本账行` 反查，
        不再从「最后一条 accepted」推测。本 run 没有采纳新响应的章
        （定向重生成合并进来的未动章）没有行——缺行即「沿用旧稿」。
        """
        adoption_id = _adoption_id(
            run_id=self.run_id, node=node, purpose=purpose,
            attempt_id=attempt_id, artifact=artifact,
            artifact_fingerprint=artifact_fingerprint, chapter=chapter)
        self._append_audit_idempotent({
            'kind': 'artifact_adoption',
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'adoption_id': adoption_id,
            'sid': sid, 'node': node, 'purpose': purpose,
            'attempt_id': attempt_id,
            'artifact': artifact,
            'artifact_fingerprint': artifact_fingerprint,
            'chapter': chapter,
        }, identity_key='adoption_id')

    def _append_audit_idempotent(self, row: dict, *, identity_key: str) -> None:
        """带稳定身份的一次审计事件跨线程/进程只追加一行。"""
        identity = row.get(identity_key)
        if not isinstance(identity, str) or not identity:
            raise ProvenanceRefused(f'幂等审计事件缺稳定 {identity_key}')
        stamped = {'run_id': self.run_id, **row}
        with self._lock, _AUDIT_IDEMPOTENT_APPEND_LOCK:
            if any(event.get(identity_key) == identity
                   for event in self.audit_events):
                return
            if self.path is None:
                self.audit_events.append(stamped)
                return

            target = self.path.with_name(AUDIT_REL[-1])
            with process_append_lock(target):
                if target.is_file():
                    lines = target.read_text(encoding='utf-8').splitlines()
                    for index, line in enumerate(lines):
                        if not line.strip():
                            continue
                        try:
                            existing = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ProvenanceRefused(
                                f'LLM audit 第 {index + 1} 行损坏，'
                                '不能在无法确认 adoption_id 时重复追加') from exc
                        if (isinstance(existing, dict)
                                and existing.get(identity_key) == identity):
                            return
                append_line_under_process_lock(
                    target,
                    json.dumps(stamped, ensure_ascii=False, sort_keys=True))
            self.audit_events.append(stamped)

    def _append_audit(self, row: dict) -> None:
        with self._lock:
            stamped = {'run_id': self.run_id, **row}
            self.audit_events.append(stamped)
            if self.path is None:
                return
            append_line(self.path.with_name(AUDIT_REL[-1]),
                        json.dumps(stamped, ensure_ascii=False, sort_keys=True))

    # ── 结算：run 总量 vs **这一份** ──────────────────────────────────────

    def snapshot(self) -> UsageMark:
        """当前刻度。**一份产物的账 = 两把刻度之间的差值。**"""
        with self._lock:
            return UsageMark(
                seq=self._seq, calls_made=self.calls_made, calls_cached=self.calls_cached,
                tokens=tuple(getattr(self, k) for k in TOKEN_KINDS),
                seen=tuple(self._seen[k] for k in TOKEN_KINDS),
            )

    def usage_since(self, mark: UsageMark) -> Usage | None:
        """从 `mark` 到现在花了多少。**纯算，一个字节都不动**（包括刻度本身）。

        ⚠ 与 `commit` 分成两步是有原因的：铸造来源账时校验可能失败
        （`StyleProvenance.attempts` 之类的后置条件），而**校验失败不许把刻度推走**——
        推走了那段窗口的账就再也没有产物认领它，Σ 不变量当场破。
        ⇒ 先纯算、再构造（可能抛）、构造成功了才 `commit`。
        """
        with self._lock:
            return self._usage_between(mark)

    def commit(self, mark: UsageMark) -> UsageMark:
        """把刻度推到现在，返回**下一把**。

        ⚠⚠ **同一把刻度不许提交两次。** 提交两次 = 两份产物各自认领同一批 token，
        而那正是本次要修掉的那个病（run 累计写进按份计的字段）的镜像形态：
        一个数被两份产物同时声称是自己的。它是显式终态，不是一笔悄悄重复的账。
        """
        with self._lock:
            if mark.seq != self._seq:
                raise ProvenanceRefused(
                    f'这把刻度是第 {mark.seq} 代，账本已经走到第 {self._seq} 代——'
                    f'同一把刻度结算了两次，等于两份产物各自认领同一批 token。'
                    f'⇒ 一份产物一次结算（`RunContext` 的两个来源账铸造口各调一次）')
            self._seq += 1
            return self.snapshot()

    def chapter_usage(self, chapter: int) -> Usage | None:
        with self._lock:
            counter = self._by_chapter.get(int(chapter))
            return counter.usage() if counter is not None else None

    def commit_chapter(self, chapter: int) -> None:
        chapter = int(chapter)
        with self._lock:
            if chapter in self._claimed_chapters:
                raise ProvenanceRefused(
                    f'chapter {chapter} usage was already claimed by an artifact')
            self._claimed_chapters.add(chapter)

    def usage(self) -> Usage | None:
        """**整个 run** 花了多少。⚠ 进 `_provenance.usage` 的**不是**这一份，是 `usage_since` 的差值。

        一次调用都没有 ⇒ `None`（键不在）。
        """
        with self._lock:
            return self._usage_between(None)

    def _usage_between(self, mark: UsageMark | None) -> Usage | None:
        """`mark`（`None` = run 起点）到现在的用量。**三态在这里兑现。**

        ⚠ `None` 与「全 0 的 usage」不是一回事：前者是「这份产物没有任何模型参与」
        （0 成本节点该是这个），后者是「跑过，而它一次都没命中也没发出去」——
        后者不该发生，发生了要看得见。

        ⚠ token 三项是 `int | null`，**`null` = 这次响应没给 usage，不是 0**
        （`_common.md` §八）。DeepSeek 实测会有不带 usage 的响应，
        而把它记成 0 会让「没给」与「真的没花」混掉。
        ⇒ 判据是「**这段窗口里**这一项被报过几次」，一项一判：
          只给了 `prompt_tokens` 的那次响应，不许让另外两项从 `null` 变成 `0`。
        """
        made = self.calls_made - (mark.calls_made if mark else 0)
        cached = self.calls_cached - (mark.calls_cached if mark else 0)
        if not made and not cached:
            return None
        vals = {}
        for i, k in enumerate(TOKEN_KINDS):
            seen = self._seen[k] - (mark.seen[i] if mark else 0)
            vals[k] = (getattr(self, k) - (mark.tokens[i] if mark else 0)) if seen else None
        return Usage(calls_made=made, calls_cached=cached, **vals)
