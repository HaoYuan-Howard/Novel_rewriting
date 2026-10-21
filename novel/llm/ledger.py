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

⚠ **存量解码词表不动**：历史 Agent CLI 时代写入的 ``error_kind`` / channel /
``role`` 字符串仍须可读（C3 硬切执行面后勿删这些取值）；新写入不再产生 agent
CLI 会话行，但旧行不得因词表收缩而解码失败。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from contracts.schema import Usage
from novel import run_identity
from novel.appendlog import (
    append_line, append_line_under_process_lock, append_transaction, process_append_lock,
)
from novel.errors import ProvenanceRefused
from novel.observability import RequestProgress

#: 账本在 `<Output>/` 下的位置。跨书，所以不在任何一本书的目录里。
LEDGER_REL = ('_service', 'ledger.jsonl')

#: 采纳审计账：与成本账同目录、**分文件**。成本账答「花了多少」，这份答
#: 「内容闸怎么判的、最终 artifact 用的是哪一次响应、丢掉的那份答案留证在哪」。
#: 历史 `candidate_patch_applied` 行仍可读取；现役保留
#: `evidence_locator_rewrite`；需关联 wire 的行靠 `attempt_id` 与成本账相联，
#: 纯确定性 locator 改写则绑定最终 artifact 的完整 SHA-256。
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


def read_cost_snapshot(base: str | Path, *, readonly: bool = False) -> tuple[list[dict], bool]:
    """锁内读取成本账的已提交前缀，并单列未提交尾；坏完整行不容错。"""
    path = ledger_path(base)
    if not path.is_file():
        return [], False
    if readonly:
        # 审阅不创建锁文件；append-only 账只认读取时已提交的完整行前缀。
        raw = path.read_bytes()
    else:
        with process_append_lock(path):
            raw = path.read_bytes()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('成本账含重复 JSON 键')
            result[key] = value
        return result

    rows = []
    parts = raw.split(b'\n')
    for line in parts[:-1]:
        if not line.strip():
            continue
        row = json.loads(line, object_pairs_hook=unique)
        if not isinstance(row, dict):
            raise ValueError('成本账不是 JSON object')
        rows.append(row)
    return rows, bool(parts[-1])


def read_jsonl_rows(path: str | Path) -> list[dict[str, Any]]:
    """读服务 JSONL dict 行；只容忍被 kill 留下的最后一条半行。

    换行是提交标记；成本聚合和恢复消费者均不得把尚未追加完的行当成完整证据。
    """
    source = Path(path)
    if not source.is_file():
        return []
    lines = source.read_bytes().splitlines(keepends=True)
    rows: list[dict[str, Any]] = []
    for index, raw_line in enumerate(lines):
        complete = raw_line.endswith(b'\n')
        if not complete:
            if index == len(lines) - 1:
                # 换行是 append-only 行的提交标记；无论截断点是否恰好落在
                # 一个可解析 JSON 后面，未提交的尾都不能进入已记录事实。
                break
            raise ValueError(
                f'{source} 第 {index + 1} 行不是 LF 提交的完整事件')
        payload = raw_line[:-1] if complete else raw_line
        if payload.endswith(b'\r'):
            payload = payload[:-1]
        line = payload.decode('utf-8', errors='strict')
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # 有换行的行已经提交；即使它恰好是最后一行，也不能冒充 kill 尾。
            raise
        if not isinstance(row, dict):
            raise ValueError(
                f'{source} 第 {index + 1} 行不是 JSON object')
        rows.append(row)
    return rows


def audit_path(base: str | Path) -> Path:
    """`<Output>` → 采纳审计账路径。与 `ledger_path` 同目录，见 `AUDIT_REL`。"""
    return Path(base).joinpath(*AUDIT_REL)


def _commander_call_row(
        *, run_id: str, session_run_id: str, sid: str,
        attempt_id: str, request_model: str, requested_effort: str,
        code: str, protocol_version: str,
        actual_model: str | None = None,
        actual_effort: str | None = None,
        provider_session_id: str | None = None,
        cli_version: str | None = None,
        usage: Mapping[str, int | None] | None = None,
        outcome: str, exit_code: int,
        error_kind: str | None = None,
        latency_ms: int | None = None,
        activity: str | None = None,
        response_sha256: str | None = None) -> dict[str, Any]:
    """构造一次整书 Commander 调用的成本事实；正常追加与恢复共用。

    Commander 不是 ``NODES`` 的成员，故意以 ``role='commander'``、``node=null``
    落在同一本成本账里；拿一个假 node 填空会污染完成度与按节点成本统计。它也不
    复用 ``CostLedger.record``：后者是一条节点产物生产线的 run 内累计器，而外层
    Commander 不铸造任何 artifact provenance。两种调用仍用同一个 append-only
    字节口，因此并发与半行恢复纪律不分叉。

    ``attempt_id`` 同时写入 agent session 的可得 turn，是两账的联结键。供应商没有
    回 provider session id 时仍诚实记一次调用，但 session audit 不得据此伪造 turn。
    实际 model/effort 没回显就保持 ``null``，绝不拿请求值补齐。

    ``latency_ms`` 是这一轮 Commander CLI 子进程从启动到退出的单调时钟墙钟，
    包含它内部全部模型往返与工具调用，不含之后的 execution drain 等待；进程
    没有启动时保持 ``null``。它与逐节点行的 ``latency_ms`` 同名同单位，供终审
    账本把 ``role:commander`` 的耗时单列，不能拿节点行的传输耗时冒充。
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
            ('cli_version', cli_version), ('error_kind', error_kind),
            ('activity', activity)):
        if value is not None and (
                not isinstance(value, str) or not value.strip()
                or any(ord(char) < 32 for char in value)):
            raise ProvenanceRefused(
                f'commander ledger {name} 必须是非空、无控制字符的字符串或 null')
    if latency_ms is not None and (
            isinstance(latency_ms, bool) or not isinstance(latency_ms, int)
            or latency_ms < 0):
        raise ProvenanceRefused('commander ledger latency_ms 必须是非负整数或 null')
    if response_sha256 is not None and (
            len(response_sha256) != 64
            or any(ch not in '0123456789abcdef' for ch in response_sha256)):
        raise ProvenanceRefused(
            'commander ledger response_sha256 必须是 64 位小写 SHA-256 或 null')

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
        # 可选的人读活动标签只为墙钟分段；成本身份仍是 role=commander，
        # 不能伪装成 NODES 中的一只生产 node。
        'activity': activity,
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
        'latency_ms': latency_ms,
        'error_kind': error_kind,
        'retryable': False,
        'will_retry': False,
        'backoff_ms': None,
        'attempt_id': attempt_id,
        'request_fingerprint': None,
        'response_sha256': response_sha256,
        'provider_session_id': provider_session_id,
        'protocol_version': protocol_version,
        'cli_version': cli_version,
        'chapter': None,
        'chapter_index': None,
        'chapter_total': None,
    }
    return row


def append_commander_call(base: str | Path, **original_kwargs: Any) -> None:
    """追加 Commander 调用；字段与验证只由同一行构造器定义。"""
    row = _commander_call_row(**original_kwargs)
    append_line(ledger_path(base), json.dumps(
        row, ensure_ascii=False, sort_keys=True, allow_nan=False))


def reconcile_commander_call(
        base: str | Path, *, read_only: bool = False,
        **original_kwargs: Any) -> None:
    """核验或补齐已保存的原 Commander 成本行，不改归属、不重复计费。

    同一 attempt 的原事实必须逐字段相等；只有追加时间不可从 receipt 重建，
    因此不参与比较。只读消费者遇到缺账必须拒绝，不能因读取动作创建成本事实。
    """
    expected = _commander_call_row(**original_kwargs)
    path = ledger_path(base)

    def check() -> bool:
        matches = [row for row in read_jsonl_rows(path)
                   if row.get('attempt_id') == expected['attempt_id']]
        if not matches:
            if read_only:
                raise ProvenanceRefused('原 Commander attempt 成本行缺失，须先恢复原账')
            return False
        wanted = {key: value for key, value in expected.items() if key != 'at'}
        if len(matches) != 1 or {
                key: value for key, value in matches[0].items() if key != 'at'} != wanted:
            raise ProvenanceRefused('原 Commander attempt 成本行与持久 receipt 不一致')
        return True

    if read_only:
        check()
        return
    with append_transaction(path):
        if check():
            return
        if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b'\n'):
            raise ProvenanceRefused('成本账有未提交尾行，拒绝在其后补写 Commander 事实')
        append_line_under_process_lock(path, json.dumps(
            expected, ensure_ascii=False, sort_keys=True, allow_nan=False))


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


def _evidence_locator_rewrite_id(
        *, run_id: str, node: str, purpose: str, artifact: str,
        artifact_sha256: str, claim_path: str,
        original_chapter_ordinal: int, resolved_chapter_ordinal: int,
        match_mode: str,
) -> str:
    """一次确定性 locator 跨章纠正的稳定身份；只绑定坐标事实与正式字节。"""
    canonical = json.dumps(
        [run_id, node, purpose, artifact, artifact_sha256, claim_path,
         original_chapter_ordinal, resolved_chapter_ordinal, match_mode],
        ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _human_typed_edit_id(
        *, run_id: str, sid: str, node: str, purpose: str,
        subject_sha256: str, result_sha256: str,
        actor: str, reason: str, chapters: Iterable[int],
) -> str:
    """零 wire typed edit 的稳定身份；正文与完整请求不进入服务审计账。"""
    canonical = json.dumps(
        [run_id, sid, node, purpose, subject_sha256, result_sha256,
         actor, reason, list(chapters)],
        ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _require_sha256(value: str, *, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)):
        raise ProvenanceRefused(f'{field} 必须是完整小写 SHA-256')
    return value


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

    #: 外层 AgentRun 的稳定身份。直接 CLI/job 与 producer run 相同；v2
    #: AgentRun 的节点账则用它把 epoch-scoped producer run 精确归回 parent。
    parent_run_id: str = ''

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

    #: ``chapter`` 不能覆盖 strategy/多相 logical request；断点恢复的
    #: 精确成本边界是 ``run_id + purpose``。这张表与全局/逐章
    #: counter 同源更新，使 ``path=None`` 的同进程运行也能按
    #: purpose 聚合，不需要从请求 identity 猜 token。这里仍把
    #: sid/node 收进 key，不许同一 run 里恰好同名的其他请求串账。
    _by_purpose: dict[tuple[str | None, str | None, str], _UsageCounter] = field(
        default_factory=dict, repr=False)

    #: 本 run 的 ``attempt_id → request_fingerprint`` 精确索引。
    #: 成本 JSONL 是跨 run 的账，不应为铸一份正在落盘的 provenance 再扫全文件；
    #: 且 ``path=None`` 的测试/缓存路径同样必须保留这条联结。
    _request_fingerprints: dict[str, str] = field(default_factory=dict, repr=False)

    #: 本 run 已记账 attempt 行的精确镜像（B1）：``usage_for_attempt_rows`` 在
    #: 本 run 内直接用它聚合；跨 run 的行从盘上 JSONL 按 attempt_id 精确取出。
    #: 与时间窗刻度无关——provenance 的成本只来自被显式引用的 attempt 行。
    _attempt_rows: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    #: 本 run 的 ``attempt_id → 实际响应模型`` 精确索引。与请求指纹同一条联结：
    #: 内容闸只记 attempt id，产物来源账据此反查最终采纳的是 Flash 还是 Pro，
    #: 不能从 client 的主模型配置猜。
    _request_models: dict[str, str] = field(default_factory=dict, repr=False)

    #: 与上面两张索引同源：跨 CLI 的响应修复恢复链需要把**原来已经花掉、但尚未被
    #: 产物认领**的 attempts 重新纳入本次 provenance 窗口。按 purpose 过滤，不能把
    #: 同一节点的另一章/另一相调用一起认领。
    _request_purposes: dict[str, str] = field(default_factory=dict, repr=False)
    _request_sids: dict[str, str | None] = field(default_factory=dict, repr=False)
    _request_nodes: dict[str, str | None] = field(default_factory=dict, repr=False)
    _request_chapters: dict[str, int | None] = field(default_factory=dict, repr=False)

    #: checkpoint 不能只凭请求指纹反推配置：指纹是结果身份，不说明当前运行到底
    #: 允许哪条模型链、哪档 effort/thinking。这里保留每次实际 ChatRequest 的非正文
    #: 参数，以及供应商响应 content 的既有 SHA 联结；跨进程恢复时从原 ledger 行重建。
    _request_specs: dict[str, dict[str, object]] = field(
        default_factory=dict, repr=False)
    _response_sha256s: dict[str, str | None] = field(
        default_factory=dict, repr=False)
    _work_unit_commits: dict[str, tuple[object, str]] = field(default_factory=dict, repr=False)

    #: Stage 2 会并发等待独立章节响应。计数更新与 JSONL 追加必须是同一个临界区，
    #: 否则两个成功回包可能互相覆盖内存计数，甚至把两行字节交错写坏。
    _lock: threading.RLock = field(default_factory=threading.RLock,
                                    repr=False, compare=False)

    def __post_init__(self) -> None:
        self.run_id = run_identity.check_run_id(self.run_id)
        self.parent_run_id = run_identity.check_run_id(
            self.parent_run_id or self.run_id)

    def bind_run_id(self, run_id: str) -> None:
        """把账本与 RunContext 的身份钉死；已绑到另一次跑则关闭式失败。"""
        value = run_identity.check_run_id(run_id)
        with self._lock:
            if self.run_id != value:
                raise ProvenanceRefused(
                    f'CostLedger run_id={self.run_id!r} 与 '
                    f'RunContext run_id={value!r} 不一致；拒绝串账')

    def bind_parent_run_id(self, parent_run_id: str) -> None:
        """把账本与外层 AgentRun 身份钉死；不允许跨 parent 串账。"""
        value = run_identity.check_run_id(parent_run_id)
        with self._lock:
            if self.parent_run_id != value:
                raise ProvenanceRefused(
                    f'CostLedger parent_run_id={self.parent_run_id!r} 与 '
                    f'RunContext parent_run_id={value!r} 不一致；拒绝串账')

    def _identity_fields(self) -> dict[str, str]:
        fields = {'run_id': self.run_id}
        if self.parent_run_id != self.run_id:
            fields['parent_run_id'] = self.parent_run_id
        return fields

    def record(self, *, node: str | None, sid: str | None, model: str, code: str,
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
                previous_sid = self._request_sids.get(attempt_id)
                previous_node = self._request_nodes.get(attempt_id)
                if ((attempt_id in self._request_sids and previous_sid != sid)
                        or (attempt_id in self._request_nodes and previous_node != node)):
                    raise ProvenanceRefused(
                        f'attempt_id={attempt_id} 同时指向两个 sid/node：'
                        f'{previous_sid}/{previous_node} / {sid}/{node}')
                self._request_sids[attempt_id] = sid
                self._request_nodes[attempt_id] = node
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
                self._attempt_rows[attempt_id] = {
                    'attempt_id': attempt_id, 'sid': sid, 'node': node,
                    'purpose': purpose, 'request_fingerprint': request_fingerprint,
                    'cached': cached,
                    'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens,
                    'reasoning_tokens': reasoning_tokens}

            if cached:
                self.calls_cached += 1
            else:
                self.calls_made += 1
            purpose_counter = (
                self._by_purpose.setdefault((sid, node, purpose), _UsageCounter())
                if purpose else None)
            if purpose_counter is not None:
                if cached:
                    purpose_counter.calls_cached += 1
                else:
                    purpose_counter.calls_made += 1
            for name, val in zip(TOKEN_KINDS,
                                 (prompt_tokens, completion_tokens, reasoning_tokens)):
                if val is not None:
                    setattr(self, name, getattr(self, name) + val)
                    self._seen[name] += 1
                    if purpose_counter is not None:
                        purpose_counter.tokens[name] += val
                        purpose_counter.seen[name] += 1

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
            row = self._record_row(
                node=node, sid=sid, model=model, code=code, cached=cached, cache_policy=cache_policy, prompt_tokens=prompt_tokens, prompt_cache_hit_tokens=prompt_cache_hit_tokens, prompt_cache_miss_tokens=prompt_cache_miss_tokens, completion_tokens=completion_tokens, reasoning_tokens=reasoning_tokens, purpose=purpose, outcome=outcome, error=error, status=status, reasoning_effort=reasoning_effort, thinking=thinking, json_mode=json_mode, max_tokens=max_tokens, finish_reason=finish_reason, attempt=attempt, max_attempts=max_attempts, latency_ms=latency_ms, error_kind=error_kind, retryable=retryable, will_retry=will_retry, backoff_ms=backoff_ms, attempt_id=attempt_id, request_fingerprint=request_fingerprint, response_sha256=response_sha256, request_model=request_model, progress=progress)
            self._append(row)

    def _record_row(self, *, node: str | None, sid: str | None, model: str, code: str,
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
               progress: RequestProgress | None = None) -> dict[str, Any]:
        row = {
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            **self._identity_fields(),
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
        return row

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

    def usage_for_attempt_rows(self, attempt_ids: Iterable[str], *, sid: str,
                               node: str | None) -> Usage | None:
        """按**精确 attempt 行**聚合用量（B1）；不依赖时间窗刻度与当前实例起点。

        provenance 的成本只来自被显式引用的 attempt 行（I-17）：本 run 已记账的
        行用内存镜像，其余（跨 run 免费采用的原购买行）从盘上 JSONL 按
        ``attempt_id`` 精确取出。每个命中行都复核 sid/node/请求指纹；没有盘上
        账本时缺行即关闭式拒绝，不按当前累计值猜。空集合返回 ``None``
        （这份产物没有可归属的模型调用，不是 0）。
        """
        wanted: list[str] = []
        for attempt_id in attempt_ids:
            if not isinstance(attempt_id, str) or not attempt_id:
                raise ProvenanceRefused('attempt usage 聚合含空 attempt_id')
            if attempt_id not in wanted:
                wanted.append(attempt_id)
        if not wanted:
            return None
        rows: dict[str, dict[str, Any]] = {}
        with self._lock:
            for attempt_id in wanted:
                row = self._attempt_rows.get(attempt_id)
                if row is not None:
                    rows[attempt_id] = row
        missing = [a for a in wanted if a not in rows]
        if missing:
            if self.path is None:
                raise ProvenanceRefused(
                    f'attempt {sorted(missing)} 不在本 run 成本账且账本无盘上路径；'
                    '拒绝按累计值或零猜它的成本')
            for row in read_jsonl_rows(self.path):
                attempt_id = row.get('attempt_id')
                if isinstance(attempt_id, str) and attempt_id in missing:
                    if attempt_id in rows:
                        raise ProvenanceRefused(
                            f'attempt {attempt_id} 在成本账重复出现')
                    rows[str(attempt_id)] = row
                    missing.remove(attempt_id)
        if missing:
            raise ProvenanceRefused(
                f'被引用的 attempt {sorted(missing)} 不在成本账；拒绝把成本记成零')
        counter = _UsageCounter()
        for attempt_id in wanted:
            row = rows[attempt_id]
            if row.get('sid') != sid or row.get('node') != node:
                raise ProvenanceRefused(
                    f'attempt {attempt_id} 属于 sid/node='
                    f'{row.get("sid")}/{row.get("node")}，与产物归属 {sid}/{node} 不一致；'
                    '拒绝串账')
            if row.get('cached') is True:
                counter.calls_cached += 1
            else:
                counter.calls_made += 1
            for key in TOKEN_KINDS:
                value = row.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    counter.tokens[key] += value
                    counter.seen[key] += 1
        return counter.usage()

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
                self._adopt_prior_row(attempt_id, row, sid=sid, node=node, purpose=purpose)

    def _adopt_prior_row(self, attempt_id: str, row: dict[str, object], *,
                         sid: str, node: str, purpose: str) -> None:
        """仅在来源身份全部验证后恢复内存计量，不追加费用行。"""
        fingerprint = str(row['request_fingerprint'])
        model = str(row.get('model') or '')
        self._request_fingerprints[attempt_id] = fingerprint
        self._request_models[attempt_id] = model
        self._request_purposes[attempt_id] = purpose
        self._request_sids[attempt_id] = sid
        self._request_nodes[attempt_id] = node
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
        purpose_counter = self._by_purpose.setdefault(
            (sid, node, purpose), _UsageCounter())
        if cached:
            purpose_counter.calls_cached += 1
        else:
            purpose_counter.calls_made += 1
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
                purpose_counter.tokens[key] += value
                purpose_counter.seen[key] += 1
                if counter is not None:
                    counter.tokens[key] += value
                    counter.seen[key] += 1

    def claim_work_unit_attempts(self, budget: Any, turn_id: str, *, purpose: str) -> None:
        """通过当前持久工作单元认领本账已有尝试，原费用/run身份保持原样。"""
        from novel.work_units import WorkUnitBudget
        if not isinstance(budget, WorkUnitBudget):
            raise ProvenanceRefused('本账恢复缺真实工作单元账户')
        bindings = budget.recovery_attempts(turn_id)
        wanted = {str(row['attempt_id']): row for row in bindings}
        if len(wanted) != len(bindings) or not wanted:
            raise ProvenanceRefused('本账恢复的尝试集合为空或不唯一')
        with self._lock:
            missing = set(wanted) - set(self._request_fingerprints)
            if not missing:
                return
            if self.path is None:
                raise ProvenanceRefused('本账恢复缺盘上成本账')
            with process_append_lock(self.path):
                raw = self.path.read_bytes()
            if raw and not raw.endswith(b'\n'):
                raise ProvenanceRefused('历史成本账末行未闭合')
            rows: dict[str, dict[str, object]] = {}
            for line in raw.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ProvenanceRefused('历史成本行不是对象')
                identity = row.get('attempt_id')
                if identity in wanted:
                    if identity in rows:
                        raise ProvenanceRefused('历史成本尝试身份重复')
                    rows[str(identity)] = row
            if set(rows) != set(wanted):
                raise ProvenanceRefused('业务账户与历史成本尝试集合不闭合')
            for identity, expected in wanted.items():
                row = rows[identity]
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ProvenanceRefused('历史尝试不属于当前工作单元绑定的来源')
                if not isinstance(row.get('purpose'), str) or not row['purpose']:
                    raise ProvenanceRefused('历史尝试缺调用目的')
            for identity in sorted(missing):
                row = rows[identity]
                self._adopt_prior_row(identity, row, sid=str(row['sid']),
                                      node=str(row['node']), purpose=str(row['purpose']))
            self._append_audit_idempotent({
                'kind': 'work_unit_adoption', 'sid': budget.identity['sid'],
                'node': budget.identity['node'], 'purpose': purpose,
                'work_unit_adoption_id': hashlib.sha256(
                    f'{budget.unit_id}:{turn_id}:{self.run_id}'.encode()).hexdigest(),
                'unit_id': budget.unit_id, 'content_turn_id': turn_id,
                'source_run_ids': sorted({str(row['run_id']) for row in bindings}),
                'attempt_ids': sorted(wanted),
            }, identity_key='work_unit_adoption_id')

    def reconcile_work_unit_attempt(self, budget: Any, turn_id: str,
                                    receipt: Mapping[str, Any]) -> None:
        """从 wire 后先保存的原始回执幂等补账，保留原 run、attempt 和未知用量。"""
        from novel.work_units import WorkUnitBudget
        if not isinstance(budget, WorkUnitBudget) or self.path is None:
            raise ProvenanceRefused('工作单元补账缺持久业务账户或成本账')
        frozen = budget.response_payload(turn_id).get('prepared_receipt')
        if frozen != dict(receipt) or receipt.get('kind') != 'executor-turn-receipt-v1':
            raise ProvenanceRefused('补账回执不是原始回答保存的精确对象')
        turn = budget.turn(turn_id)
        if receipt.get('run_id') != turn.run_id:
            raise ProvenanceRefused('补账不能更换原调用 run')
        arguments = dict(receipt['ledger_arguments'])
        identity = arguments.get('attempt_id')
        attempts = [row for row in turn.attempts if row.attempt_id == identity]
        if (len(attempts) != 1 or arguments.get('cached') is not False
                or arguments.get('sid') != budget.identity['sid']
                or arguments.get('node') != budget.identity['node']
                or arguments.get('request_fingerprint') != attempts[0].request_fingerprint):
            raise ProvenanceRefused('待补成本行与已预留实际尝试不一致')
        if arguments.get('progress') is not None:
            arguments['progress'] = RequestProgress(**arguments['progress'])
        source = CostLedger(path=None, run_id=turn.run_id,
                            parent_run_id=str(receipt['parent_run_id']))
        # record 的同一生成器保证当前写入和恢复没有第二套默认值或 token 规则。
        expected = source._record_row(**arguments)
        with self._lock:
            with process_append_lock(self.path):
                raw = self.path.read_bytes() if self.path.exists() else b''
                if raw and not raw.endswith(b'\n'):
                    raise ProvenanceRefused('成本账有未闭合尾行，禁止补写')
                rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
                if any(not isinstance(row, dict) for row in rows):
                    raise ProvenanceRefused('成本账含非对象记录')
                matches = [row for row in rows if row.get('attempt_id') == identity]
                if len(matches) > 1:
                    raise ProvenanceRefused('原尝试在成本账重复出现')
                if matches:
                    row = matches[0]
                    if {k: v for k, v in row.items() if k != 'at'} != {k: v for k, v in expected.items() if k != 'at'}:
                        raise ProvenanceRefused('原尝试成本行与冻结回执不一致')
                else:
                    row = expected
                    append_line_under_process_lock(self.path, json.dumps(row, ensure_ascii=False, sort_keys=True))
            if identity not in self._request_fingerprints:
                self._adopt_prior_row(str(identity), row, sid=str(row['sid']),
                                      node=str(row['node']), purpose=str(row['purpose']))

    def usage_for_attempt_identities(
            self,
            identities: Iterable[tuple[str, Mapping[str, object]]], *,
            sid: str, node: str,
    ) -> Usage | None:
        """只聚合指定 attempt 集合的用量，不依赖当前实例起点。

        ``identities`` 每项是 ``(purpose, request_identity)``；identity 必须
        来自 checkpoint 封存的 ``attempt_links``。本口用一本隔离的
        scratch ledger 按 purpose 从盘上账本 claim 这些行，再复用
        ``usage()`` 的 calls/cache/token-null 三态算法。因此：

        * 当前 ``CostLedger`` 已经记了什么、``RunContext`` 的 UsageMark
          在哪里，都不会改变结果；
        * 每个 attempt 的 run/sid/node/purpose/fingerprint/完整请求配置
          与 response SHA 都由既有 claim/identity 闭环逐项复验；
        * 不往账本追加任何行，也不污染当前实例计数器。

        没有盘上账本时无法从 identity 反推 token；非空集合必须
        关闭式拒绝，不能偷用当前累计值伪装精确聚合。
        """
        sealed: list[tuple[str, dict[str, object]]] = []
        seen_attempts: set[str] = set()
        for purpose, identity in identities:
            if not isinstance(purpose, str) or not purpose.strip():
                raise ProvenanceRefused(
                    'attempt usage 聚合的 purpose 必须是非空字符串')
            if not isinstance(identity, Mapping):
                raise ProvenanceRefused(
                    'attempt usage 聚合的 request identity 必须是 object')
            row = dict(identity)
            attempt_id = row.get('attempt_id')
            fingerprint = row.get('request_fingerprint')
            if (not isinstance(attempt_id, str) or not attempt_id
                    or attempt_id in seen_attempts
                    or not isinstance(fingerprint, str)
                    or len(fingerprint) != 64
                    or any(char not in '0123456789abcdef'
                           for char in fingerprint)):
                raise ProvenanceRefused(
                    'attempt usage 聚合含重复/不完整 attempt identity')
            seen_attempts.add(attempt_id)
            sealed.append((purpose, row))
        if not sealed:
            return None
        if self.path is None:
            raise ProvenanceRefused(
                'attempt usage 聚合需要盘上 ledger.jsonl 的 token 事实')

        scratch = CostLedger(path=self.path, run_id=self.run_id)
        by_purpose: dict[str, list[dict[str, object]]] = {}
        for purpose, identity in sealed:
            by_purpose.setdefault(purpose, []).append(identity)
        for purpose in sorted(by_purpose):
            rows = by_purpose[purpose]
            scratch.claim_prior_attempts(
                [str(row['attempt_id']) for row in rows],
                sid=sid, node=node, purpose=purpose,
                request_fingerprints=[
                    str(row['request_fingerprint']) for row in rows],
            )
        for purpose, identity in sealed:
            attempt_id = str(identity['attempt_id'])
            if scratch.request_identity_for_attempt(attempt_id) != identity:
                raise ProvenanceRefused(
                    f'{purpose}: attempt={attempt_id} 的 checkpoint identity '
                    '与盘上 ledger 不闭合')
        return scratch.usage()

    def usage_for_complete_purposes(
            self, purposes: Iterable[str], *, sid: str, node: str,
            required_identities: Iterable[
                tuple[str, Mapping[str, object]]
            ] = (),
    ) -> Usage | None:
        """聚合当前 run 中指定 logical purposes 的**全部** attempts。

        accepted checkpoint 只有在成功落下之后才能封存
        ``attempt_links``。如果一个 unit 在此前用尽轮次、进程退出，
        同 ``run_id/purpose`` 重启后的新 checkpoint 未必能列出那些
        旧失败行。仅求 checkpoint links 并集会给产物落一份稳定但
        偏小的 usage。

        本口以盘上成本账的 ``run_id + sid + node + purpose`` 为完整
        边界，发现每个 attempt，再交给隔离 scratch ledger 的既有
        ``claim_prior_attempts`` / ``usage`` 算法聚合。``required_identities``
        是 checkpoint 已封存的 links：它们必须逐项存在且完全相等，
        但不是完整性边界。这样既不丢无 checkpoint 失败轮，也不靠
        当前内存计数器猜历史。
        """
        wanted_purposes: set[str] = set()
        for purpose in purposes:
            if not isinstance(purpose, str) or not purpose.strip():
                raise ProvenanceRefused(
                    'complete purpose usage 的 purpose 必须是非空字符串')
            if purpose in wanted_purposes:
                raise ProvenanceRefused(
                    f'complete purpose usage 重复 purpose={purpose!r}')
            wanted_purposes.add(purpose)
        if not wanted_purposes:
            return None

        expected: dict[str, tuple[str, dict[str, object]]] = {}
        for purpose, identity in required_identities:
            if purpose not in wanted_purposes or not isinstance(identity, Mapping):
                raise ProvenanceRefused(
                    'checkpoint required identity 不属于 complete purposes')
            row = dict(identity)
            attempt_id = row.get('attempt_id')
            if (not isinstance(attempt_id, str) or not attempt_id
                    or attempt_id in expected):
                raise ProvenanceRefused(
                    'checkpoint required identities 含重复/缺失 attempt')
            expected[attempt_id] = (purpose, row)

        if self.path is None:
            # 没有盘上 JSONL 时，同进程的 record 仍保有两份同源
            # 事实：attempt 的精确 scope/identity，以及 scope 级原始计数。
            # 只有两者数量闭合时才能用后者结算 token；否则说明
            # 有一次调用没有可定位 attempt，不得猜它属于谁。
            with self._lock:
                discovered = {
                    attempt_id: purpose
                    for attempt_id, purpose in self._request_purposes.items()
                    if (purpose in wanted_purposes
                        and self._request_sids.get(attempt_id) == sid
                        and self._request_nodes.get(attempt_id) == node)
                }
                combined = _UsageCounter()
                for purpose in sorted(wanted_purposes):
                    counter = self._by_purpose.get((sid, node, purpose))
                    if counter is None:
                        continue
                    calls = counter.calls_made + counter.calls_cached
                    indexed = sum(
                        1 for item_purpose in discovered.values()
                        if item_purpose == purpose)
                    if calls != indexed:
                        raise ProvenanceRefused(
                            f'{purpose}: 内存 usage 有 {calls} 次调用但只有 '
                            f'{indexed} 个可定位 attempt')
                    combined.calls_made += counter.calls_made
                    combined.calls_cached += counter.calls_cached
                    for key in TOKEN_KINDS:
                        combined.tokens[key] += counter.tokens[key]
                        combined.seen[key] += counter.seen[key]
                for attempt_id in discovered:
                    # 与盘上分支一样，每个被纳入的 attempt 都必须能
                    # 还原完整请求 identity，不只是有一个计数。
                    self.request_identity_for_attempt(attempt_id)
                for attempt_id, (purpose, identity) in expected.items():
                    if (discovered.get(attempt_id) != purpose
                            or self.request_identity_for_attempt(attempt_id) != identity):
                        raise ProvenanceRefused(
                            f'{purpose}: checkpoint attempt={attempt_id} '
                            '与 complete purpose 内存账身份不闭合')
                return combined.usage()

        discovered: dict[str, tuple[str, str]] = {}
        try:
            with process_append_lock(self.path):
                raw_lines = self.path.read_text(encoding='utf-8').splitlines()
        except OSError as exc:
            raise ProvenanceRefused(
                f'读不了 complete purpose usage 所指成本账：{exc}') from exc
        for index, line in enumerate(raw_lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                if index == len(raw_lines) - 1:
                    continue
                raise ProvenanceRefused(
                    f'成本账中间第 {index + 1} 行损坏，'
                    '不能聚合 complete purpose usage') from exc
            if not isinstance(row, dict) or (
                    row.get('run_id') != self.run_id
                    or row.get('sid') != sid
                    or row.get('node') != node
                    or row.get('purpose') not in wanted_purposes):
                continue
            attempt_id = row.get('attempt_id')
            fingerprint = row.get('request_fingerprint')
            if (not isinstance(attempt_id, str) or not attempt_id
                    or attempt_id in discovered
                    or not isinstance(fingerprint, str)
                    or len(fingerprint) != 64
                    or any(char not in '0123456789abcdef'
                           for char in fingerprint)):
                raise ProvenanceRefused(
                    f'complete purpose usage 第 {index + 1} 行缺唯一 '
                    'attempt_id/request_fingerprint')
            discovered[attempt_id] = (str(row['purpose']), fingerprint)
        if not discovered:
            return None

        scratch = CostLedger(path=self.path, run_id=self.run_id)
        for purpose in sorted(wanted_purposes):
            attempts = sorted(
                attempt for attempt, (row_purpose, _fingerprint)
                in discovered.items() if row_purpose == purpose)
            if not attempts:
                continue
            scratch.claim_prior_attempts(
                attempts, sid=sid, node=node, purpose=purpose,
                request_fingerprints=[
                    discovered[attempt][1] for attempt in attempts],
            )

        for attempt_id, (purpose, identity) in expected.items():
            discovered_identity = scratch.request_identity_for_attempt(attempt_id)
            if (discovered.get(attempt_id, (None, None))[0] != purpose
                    or discovered_identity != identity):
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint attempt={attempt_id} '
                    '与 complete purpose ledger 身份不闭合')
        return scratch.usage()

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

        ⚠⚠ **`reject_detail` 答的是「拒在哪个字段」**（历史见 `docs/DECISIONS.md` 2026-08-22）：
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


    def note_evidence_locator_rewrite(
            self, *, sid: str, node: str, purpose: str, artifact: str,
            artifact_sha256: str, claim_path: str,
            original_chapter_ordinal: int, resolved_chapter_ordinal: int,
            match_mode: str) -> None:
        """记录一次已随正式/INVALID 字节落地的跨章唯一回取。

        locator 与正文 span（历史类型 SourceSpan，已随旧链 schema 删除）都属于
        小说正文，不进入 ``_service``；事件只保留
        claim 路径、前后章号、匹配模式及最终文件 SHA，足以复核确定性改写。
        """
        digest = _require_sha256(artifact_sha256, field='artifact_sha256')
        if not isinstance(claim_path, str) or not claim_path.strip():
            raise ProvenanceRefused('evidence locator rewrite claim_path 必须非空')
        for field, value in (
                ('original_chapter_ordinal', original_chapter_ordinal),
                ('resolved_chapter_ordinal', resolved_chapter_ordinal)):
            if (not isinstance(value, int) or isinstance(value, bool) or value < 1):
                raise ProvenanceRefused(f'{field} 必须是正整数')
        if original_chapter_ordinal == resolved_chapter_ordinal:
            raise ProvenanceRefused('evidence locator rewrite 必须实际改变章号')
        if match_mode not in ('exact', 'normalized'):
            raise ProvenanceRefused('evidence locator rewrite match_mode 非法')
        event_id = _evidence_locator_rewrite_id(
            run_id=self.run_id, node=node, purpose=purpose,
            artifact=artifact, artifact_sha256=digest,
            claim_path=claim_path,
            original_chapter_ordinal=original_chapter_ordinal,
            resolved_chapter_ordinal=resolved_chapter_ordinal,
            match_mode=match_mode)
        self._append_audit_idempotent({
            'kind': 'evidence_locator_rewrite',
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'evidence_locator_rewrite_id': event_id,
            'sid': sid,
            'node': node,
            'purpose': purpose,
            'artifact': artifact,
            'artifact_sha256': digest,
            'claim_path': claim_path,
            'original_chapter_ordinal': original_chapter_ordinal,
            'resolved_chapter_ordinal': resolved_chapter_ordinal,
            'match_mode': match_mode,
        }, identity_key='evidence_locator_rewrite_id')

    def bind_work_unit_commit(self, attempt_id: str, budget: object, turn_id: str) -> None:
        """真实请求的提交关联；缓存尝试不注册、不伪造内容消耗。"""
        with self._lock:
            self._work_unit_commits[attempt_id] = (budget, turn_id)

    def artifact_adoption_payload(self, *, sid: str, node: str, purpose: str,
                                  attempt_id: str, artifact: str, artifact_fingerprint: str,
                                  chapter: int | None = None) -> dict[str, Any]:
        """采用事件的唯一内容构造器，供写口与提交前完整闭集复核共享。"""
        return {
            **self._identity_fields(), 'kind': 'artifact_adoption',
            'adoption_id': _adoption_id(
                run_id=self.run_id, node=node, purpose=purpose, attempt_id=attempt_id,
                artifact=artifact, artifact_fingerprint=artifact_fingerprint, chapter=chapter),
            'sid': sid, 'node': node, 'purpose': purpose, 'attempt_id': attempt_id,
            'artifact': artifact, 'artifact_fingerprint': artifact_fingerprint, 'chapter': chapter,
        }

    def note_adoption(self, *, sid: str, node: str, purpose: str,
                      attempt_id: str, artifact: str,
                      artifact_fingerprint: str,
                      chapter: int | None = None) -> None:
        """正式产物落盘时逐 purpose 记「这份 artifact 采纳了哪一次响应」。

        「最终采纳档位」由 `artifact_adoption → attempt_id → 成本账行` 反查，
        不再从「最后一条 accepted」推测。本 run 没有采纳新响应的章
        （定向重生成合并进来的未动章）没有行——缺行即「沿用旧稿」。
        """
        payload = self.artifact_adoption_payload(
            sid=sid, node=node, purpose=purpose, attempt_id=attempt_id,
            artifact=artifact, artifact_fingerprint=artifact_fingerprint, chapter=chapter)
        self._append_audit_idempotent({
            **payload, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        }, identity_key='adoption_id')
        bound = self._work_unit_commits.get(attempt_id)
        if bound is not None:
            budget, turn_id = bound
            budget.mark_adopted(turn_id)
            budget.mark_artifact_committed(artifact, artifact_fingerprint)

    def note_aggregate_adoption(self, *, sid: str, node: str, artifact: str,
                                artifact_fingerprint: str, sources_sha256: str,
                                finalization_sha256: str) -> None:
        """确定性汇合的独立采用收据；不创造请求、费用或新的内容轮。"""
        payload = {
            'kind': 'deterministic_aggregate_adoption', 'run_id': self.run_id,
            'sid': sid, 'node': node, 'artifact': artifact,
            'artifact_fingerprint': artifact_fingerprint,
            'sources_sha256': _require_sha256(sources_sha256, field='sources_sha256'),
            'finalization_sha256': _require_sha256(
                finalization_sha256, field='finalization_sha256'),
        }
        digest = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()
        self._append_audit_idempotent({
            **payload, 'aggregate_adoption_id': digest,
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        }, identity_key='aggregate_adoption_id')

    def note_aggregate_recovery(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """记录实际免费补尾执行；不把新 epoch 伪装成原模型 producer。"""
        row = {**dict(payload), 'run_id': self.run_id, 'parent_run_id': self.parent_run_id}
        identity = hashlib.sha256(json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        self._append_audit_idempotent({
            **row, 'aggregate_recovery_id': identity,
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        }, identity_key='aggregate_recovery_id', require_equal=True)
        return {**row, 'aggregate_recovery_id': identity}

    def note_human_typed_edit(
            self, *, sid: str, node: str, purpose: str,
            subject_sha256: str, result_sha256: str,
            actor: str, reason: str, artifact: str,
            chapters: Iterable[int], summary: Mapping[str, int],
    ) -> None:
        """记录一次人工 typed edit 的身份与计数摘要，不复制业务正文。"""
        subject = _require_sha256(subject_sha256, field='subject_sha256')
        result = _require_sha256(result_sha256, field='result_sha256')
        actor = actor.strip()
        reason = reason.strip()
        if not actor or not reason:
            raise ProvenanceRefused('human typed edit 的 actor/reason 不能为空')
        chapter_rows = tuple(int(value) for value in chapters)
        if chapter_rows != tuple(sorted(set(chapter_rows))) \
                or any(value < 1 for value in chapter_rows):
            raise ProvenanceRefused('human typed edit 的 chapters 必须升序、唯一且 >=1')
        counts = {str(key): int(value) for key, value in summary.items()}
        if any(value < 0 for value in counts.values()):
            raise ProvenanceRefused('human typed edit 的 summary 计数不能为负')
        edit_id = _human_typed_edit_id(
            run_id=self.run_id, sid=sid, node=node, purpose=purpose,
            subject_sha256=subject, result_sha256=result,
            actor=actor, reason=reason, chapters=chapter_rows)
        self._append_audit_idempotent({
            'kind': 'human_typed_edit',
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'human_typed_edit_id': edit_id,
            'sid': sid,
            'node': node,
            'purpose': purpose,
            'subject_sha256': subject,
            'result_sha256': result,
            'actor': actor,
            'reason': reason,
            'artifact': artifact,
            'chapters': list(chapter_rows),
            'summary': dict(sorted(counts.items())),
        }, identity_key='human_typed_edit_id', require_equal=True)

    def _append_audit_idempotent(
            self, row: dict, *, identity_key: str,
            require_equal: bool = False) -> None:
        """带稳定身份的一次审计事件跨线程/进程只追加一行。"""
        identity = row.get(identity_key)
        if not isinstance(identity, str) or not identity:
            raise ProvenanceRefused(f'幂等审计事件缺稳定 {identity_key}')
        stamped = {**self._identity_fields(), **row}

        def same_event(existing: dict) -> bool:
            return ({key: value for key, value in existing.items() if key != 'at'}
                    == {key: value for key, value in stamped.items() if key != 'at'})

        with self._lock, _AUDIT_IDEMPOTENT_APPEND_LOCK:
            if self.path is None:
                for event in self.audit_events:
                    if event.get(identity_key) != identity:
                        continue
                    if require_equal and not same_event(event):
                        raise ProvenanceRefused(
                            f'{identity_key}={identity} 已绑定另一条审计事实')
                    return
                self.audit_events.append(stamped)
                return

            target = self.path.with_name(AUDIT_REL[-1])
            try:
                with process_append_lock(target):
                    raw = target.read_bytes() if target.is_file() else b''
                    # LF 是 append-only JSONL 的提交标记。无论末尾恰好是
                    # 可解析 JSON，还是 kill 留下的 partial，都不能在其后
                    # 补换行或续写；必须由更高层先裁决这个未提交尾。
                    if raw and not raw.endswith(b'\n'):
                        raise ProvenanceRefused(
                            'LLM audit 存在未闭合末行，禁止补齐或续写')

                    # path-backed ledger 的内存镜像不是真相源：即使它已见
                    # 这个 identity，也要先在同一进程锁下读完盘上尾。
                    # 尾部完整后仍保留原有内存幂等语义。
                    for event in self.audit_events:
                        if event.get(identity_key) != identity:
                            continue
                        if require_equal and not same_event(event):
                            raise ProvenanceRefused(
                                f'{identity_key}={identity} 已绑定另一条审计事实')
                        return

                    for index, encoded in enumerate(raw.split(b'\n')):
                        if not encoded.strip():
                            continue
                        try:
                            existing = json.loads(encoded.decode('utf-8'))
                        except (UnicodeError, json.JSONDecodeError) as exc:
                            raise ProvenanceRefused(
                                f'LLM audit 第 {index + 1} 行损坏，'
                                '不能在无法确认 adoption_id 时重复追加') from exc
                        if (isinstance(existing, dict)
                                and existing.get(identity_key) == identity):
                            if require_equal and not same_event(existing):
                                raise ProvenanceRefused(
                                    f'{identity_key}={identity} '
                                    '已由另一 target/action 使用')
                            return
                    append_line_under_process_lock(
                        target,
                        json.dumps(stamped, ensure_ascii=False, sort_keys=True))
            except OSError as exc:
                raise ProvenanceRefused(
                    f'LLM audit 读取或追加失败：{exc}') from exc
            self.audit_events.append(stamped)

    def _append_audit(self, row: dict) -> None:
        with self._lock:
            stamped = {**self._identity_fields(), **row}
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

    def commit_chapter(self, chapter: int, *, resume: bool = False) -> None:
        """把一章 usage 结算给一份产物 provenance。

        默认（``resume=False``）保持原守卫：一章只能被结算一次，第二次是
        双重 provenance。``resume=True`` 只服务 Stage5 same-run deterministic
        sweep 的有界重放——同 run、同 checkpoint、同候选重走提交链时，接续
        第一次已做的结算；它由重放边界显式传入，其余路径拿不到。
        """
        chapter = int(chapter)
        with self._lock:
            if chapter in self._claimed_chapters:
                if not resume:
                    raise ProvenanceRefused(
                        f'chapter {chapter} usage was already claimed by an artifact')
                return
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


def read_strict_jsonl_objects(path: Path, *, label: str) -> list[dict[str, Any]]:
    """恢复写口使用的 strict JSONL：无 LF 尾也是 unknown，而非可忽略事实。"""
    try:
        # 读侧与所有 append 写手共用同一把 OS 锁；否则恰好撞在一行 bytes
        # 已写、LF 尚未写的窗口，会把并发正常追加误判成 durable 坏尾。
        with process_append_lock(path):
            if not path.is_file():
                return []
            raw = path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(f'{label} 不可读：{exc}') from exc
    if raw and not raw.endswith(b'\n'):
        raise ProvenanceRefused(f'{label} 存在未闭合末行；恢复不得继续追加')

    def reject_constant(value: str) -> None:
        raise ValueError(f'JSON 不接受 {value}')

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'JSON object 含重复键 {key!r}')
            value[key] = item
        return value

    rows: list[dict[str, Any]] = []
    for index, encoded in enumerate(raw.split(b'\n')):
        if not encoded.strip():
            continue
        try:
            value = json.loads(
                encoded.decode('utf-8'), parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate)
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ProvenanceRefused(
                f'{label} 第 {index + 1} 行损坏：{exc}') from exc
        if not isinstance(value, dict):
            raise ProvenanceRefused(f'{label} 第 {index + 1} 行不是 JSON object')
        rows.append(value)
    return rows
