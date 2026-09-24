"""迁移期直连请求边界：请求身份缓存、原响应先落盘、形状错误免费处理（F5 改版）。

【缓存先于授权】
`_answered_row` 先查同请求身份的已购响应（免费采用），缺失时才检查授权并购买
一次——工具层不得在缓存命中之前拒绝未授权的重放（F5 修复「未授权先于缓存
拒绝」）。新调用需要显式授权；未授权抛 `PurchaseRequired`（ValueError 子类，
消息含 --yes 与外发许可提示）。

【两种模式】
`ask`（JSON 模式）：响应解码 + validate，行为不变。`ask_text`（文本模式）：返回
`(text, errors)`，失败算 realization——超长/格式类失败由调用方在同一输入下
有限重写，不重购上游。`ask_answered` 额外透出 finish_reason 与请求身份（正文
章级回执绑定与截断判定需要）。两者的付费、缓存与账务路径共用 `_answered_row`：
先落原响应再验证，失败不得触发第二次调用。

【新调用额度】
`book_call_ceiling` 是本次运行的显式调用上限：在准入锁内核对新购次数（
`runtime/admission.json` 按准入凭据累计），pending/unknown 不释放额度，缓存
采用不耗额度。成本唯一权威仍是 `<base>/_service/ledger.jsonl`；admission 只
是闸门计数，不是第二份成本账。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import NamedTuple

from contracts.schema.str06 import STR06_RC_VERSION
from novel.appendlog import process_append_lock
from novel.llm.json_text import normalize_json_text
from novel.llm.ledger import CostLedger, read_cost_snapshot
from novel.llm.transport import ChatRequest, Transport
from novel.store import Str06Repository

_ADMISSION_REL = 'runtime/admission.json'


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


class PurchaseRequired(ValueError):
    """缓存未命中且未授权新调用：停在该 owner，报告需要授权。"""

    def __init__(self, owner: str):
        super().__init__(
            f'{owner} 无同请求身份结果且未授权新调用；如需继续请同时提供 '
            '--yes 与正文外发许可（--allow-source-export）')
        self.owner = owner


class TextAnswer(NamedTuple):
    """文本请求的完整已回答行（章级回执绑定与截断判定用）。"""

    text: str | None
    finish_reason: str | None
    identity: str
    cached: bool
    errors: list[str]


class PaidRequestSession:
    def __init__(self, repository: Str06Repository, ledger: CostLedger,
                 transport: Transport, *, yes: bool = False, allow_source_export: bool = False,
                 provider: str, book_call_ceiling: int | None = None):
        if ledger.path is None:
            raise ValueError('付费请求边界需要持久化成本账')
        self.repository, self.ledger, self.transport = repository, ledger, transport
        self.yes, self.allow_source_export, self.provider = yes, allow_source_export, provider
        self.book_call_ceiling = book_call_ceiling

    def ask(self, owner: str, request: ChatRequest, validate) -> dict:
        row = self._answered_row(owner, request)
        content = row.get('content') or ''
        try:
            data = json.JSONDecoder(object_pairs_hook=_unique).decode(normalize_json_text(content))
            errors = validate(data) if isinstance(data, dict) else ['顶层必须为 object']
        except (ValueError, TypeError, KeyError) as exc:
            data, errors = None, [f'响应解码/形状失败：{exc}']
        self.repository.write(f'runtime/validation_{row["identity"]}.json',
                              {'owner': owner, 'errors': errors})
        if errors:
            raise ValueError(f'{owner} 校验失败；原响应已保存，免费修复，不重问整份 JSON：{errors}')
        return data

    def ask_text(self, owner: str, request: ChatRequest) -> tuple[str | None, list[str]]:
        """文本模式：返回 (text, errors)。errors 非空 = realization 失败（免费重写）。"""
        answered = self.ask_answered(owner, request)
        return answered.text, answered.errors

    def ask_answered(self, owner: str, request: ChatRequest) -> TextAnswer:
        """文本模式全量应答：finish_reason 与请求身份透出（章级回执绑定需要）。"""
        row = self._answered_row(owner, request)
        text = (row.get('content') or '').strip() or None
        errors = [] if text is not None else ['响应为空']
        self.repository.write(f'runtime/validation_{row["identity"]}.json',
                              {'owner': owner, 'mode': 'text', 'errors': errors})
        return TextAnswer(text=text, finish_reason=row.get('finish_reason'),
                          identity=row['identity'], cached=row.get('cached', False),
                          errors=errors)

    def _new_calls(self) -> int:
        """本次运行已消耗的新调用数（准入凭据；缓存采用不计入）。"""
        try:
            return int(self.repository.read(_ADMISSION_REL).get('new_calls', 0))
        except FileNotFoundError:
            return 0

    def _charge_admission(self) -> int:
        """新购应答后原子累计准入计数（pending/unknown 不释放额度）。"""
        count = self._new_calls() + 1
        self.repository.write(_ADMISSION_REL, {'new_calls': count})
        return count

    def _answered_row(self, owner: str, request: ChatRequest) -> dict:
        """取得已回答的原响应行：缓存命中免费采用，缺失时按授权购买一次。"""
        identity = digest({'owner': owner, 'provider': self.provider, 'request': request.cache_payload()})
        rel = f'runtime/request_{identity}.json'
        path = self.repository.path(rel)
        with process_append_lock(path.with_suffix('.lock')):
            if read_cost_snapshot(self.ledger.path.parent.parent)[1]:
                raise ValueError('成本账有未提交尾，先恢复，不签发新调用')
            if path.exists():
                row = self.repository.read(rel)
                if row['identity'] != identity:
                    raise ValueError('请求身份损坏，拒绝采用或重购')
                if row['status'] != 'answered':
                    raise ValueError(f'{owner} 原请求为 {row["status"]}；先恢复，不自动重复付费')
                if digest(row.get('content')) != row.get('content_sha256'):
                    raise ValueError('已购响应内容损坏，拒绝采用或重购')
                row['cached'] = True
                self._record(owner, request, row, cached=True)
                return row
            if not self.yes or not self.allow_source_export:
                raise PurchaseRequired(owner)
            if self.book_call_ceiling is not None and self._new_calls() >= self.book_call_ceiling:
                raise ValueError(
                    f'{owner}：本次运行新调用已达 --book-call-ceiling={self.book_call_ceiling}；'
                    '停止生成，不静默超限')
            row = {'identity': identity, 'status': 'pending', 'attempt_id': uuid.uuid4().hex,
                   'provider': self.provider, 'request': request.cache_payload()}
            self.repository.write(rel, row)
            started = time.monotonic()
            try:
                response = self.transport.send(request)
            except Exception as exc:
                row.update(status='unknown', error_kind=type(exc).__name__,
                           latency_ms=int((time.monotonic() - started) * 1000))
                self.repository.write(rel, row)
                self._record(owner, request, row, cached=False)
                raise
            row.update(status='answered', content=response.content, content_sha256=digest(response.content),
                       reasoning=response.reasoning,
                       usage=response.usage, model=response.model or request.model,
                       finish_reason=response.finish_reason,
                       latency_ms=int((time.monotonic() - started) * 1000))
            # 在任何 parse/shape validation 前保存完整原响应，失败不得触发第二次调用。
            self.repository.write(rel, row)
            self._record(owner, request, row, cached=False)
            self._charge_admission()
            return row

    def _record(self, owner, request, row, *, cached):
        # raw→ledger 中断时，免费重放先补原始账，不能把已付费变成缓存零成本。
        rows, partial = read_cost_snapshot(self.ledger.path.parent.parent)
        if partial:
            raise ValueError('成本账有未提交尾，拒绝追加或采用')
        if cached and not any(r.get('attempt_id') == row['attempt_id'] for r in rows):
            self._record(owner, request, row, cached=False)
        usage = row.get('usage') or {}
        self.ledger.record(
            node=None, sid='str06', model=row.get('model', request.model),
            request_model=request.model, code=STR06_RC_VERSION, attempt=1, max_attempts=1,
            will_retry=False,
            cached=cached, cache_policy='request-identity', purpose=owner,
            attempt_id=uuid.uuid4().hex if cached else row['attempt_id'],
            request_fingerprint=row['identity'], outcome='ok' if row['status'] == 'answered' else 'error',
            error_kind=row.get('error_kind'), reasoning_effort=request.reasoning_effort,
            thinking=request.thinking, json_mode=request.json_mode, max_tokens=request.max_tokens,
            finish_reason=row.get('finish_reason'), latency_ms=0 if cached else row.get('latency_ms'),
            response_sha256=(hashlib.sha256((row.get('content') or '').encode('utf-8')).hexdigest()
                             if row['status'] == 'answered' else None),
            **{k: 0 if cached else usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens',
                                        'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')})


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'响应 JSON 重复键：{key}')
        result[key] = value
    return result
