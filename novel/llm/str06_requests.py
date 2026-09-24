"""迁移期直连请求边界：请求身份缓存、原响应先落盘、形状错误免费处理（F5 改版；
F7a-2 接正式工作单元、整书总闸与 ledger 归属）。

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

【正式设施接入（F7a-2）】
每次**新购**在首 wire 前经正式 `WorkUnitBudget.reserve` 取内容轮：单元内容上限
（候选节点表的 `executor_content_limit`）与整书付费内容调用总闸
（`_service/runs/<sid>/book_budget.json`，跨全部工作单元合账、跨重启不重置）
在同一把锁序内结算——不建第二本预算账（I-17）。`book_call_ceiling` 仍是本次
运行的显式上限，与持久总闸叠加生效。响应被产物采纳（repository 写盘铸
LlmProvenance）时轮次转 `adopted`；未被采纳的 `delivered` 轮在下一次 reserve
前关闭为 `failed`（重试／崩溃重做让位）；`reserved`／`unknown` 轮不自动关闭、
不自动重购，交恢复入口对账（Q-13）。ledger 行带真实候选节点 id 与 sid，
`run_id` 绑本次执行；成本唯一权威仍是 `<base>/_service/ledger.jsonl`。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, NamedTuple

from contracts.schema.str06 import STR06_RC_VERSION
from novel.appendlog import process_append_lock
from novel.llm.json_text import normalize_json_text
from novel.llm.ledger import CostLedger, read_cost_snapshot
from novel.llm.transport import ChatRequest, Transport
from novel.store import Str06Repository
from novel.work_units import WorkUnitBudget

_ADMISSION_REL = 'runtime/admission.json'

#: F7a 过渡期 owner 名 → （候选节点 id, 工作单元 scope）。F7b 拆 owner runner
#: 后各 owner 自持身份，本表随之删除（删除条件 STR-06-F7b）。
STR06_OWNER_UNITS: dict[str, tuple[str, str]] = {
    'book': ('str06.stage2.source', 'strategy'),
    'names': ('str06.stage2.names', 'book'),
    'preserve': ('str06.stage4.preserve', 'book'),
    'design': ('str06.stage4.body_design', 'book'),
    'bind': ('str06.stage4.bind', 'book'),
    'hook_design': ('str06.stage4.hook_design', 'book'),
    'hook_writer': ('str06.stage5.hook', 'book'),
    'naming': ('str06.stage7.naming', 'book'),
}


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
                 provider: str, book_call_ceiling: int | None = None, sid: str = '',
                 adopt_roots: tuple[Path, ...] = ()):
        if ledger.path is None:
            raise ValueError('付费请求边界需要持久化成本账')
        self.repository, self.ledger, self.transport = repository, ledger, transport
        self.yes, self.allow_source_export, self.provider = yes, allow_source_export, provider
        self.book_call_ceiling = book_call_ceiling
        # 账本唯一落点 <base>/_service/ledger.jsonl ⇒ base 取其两级父目录。
        self.base = ledger.path.parent.parent
        self.sid = sid
        self.run_id = ledger.run_id
        self.adopt_roots = tuple(Path(root) for root in adopt_roots)
        self._units: dict[str, WorkUnitBudget] = {}

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

    # ── 正式设施：工作单元与归属 ────────────────────────────────────────────

    def _owner_unit(self, owner: str) -> tuple[str, str]:
        match = re.fullmatch(r'ch(\d+)', owner)
        if match:
            return 'str06.stage2.source', f'chapter:{int(match.group(1))}'
        match = re.fullmatch(r'body_ch(\d+)', owner)
        if match:
            return 'str06.stage5.body', f'chapter:{int(match.group(1))}'
        try:
            return STR06_OWNER_UNITS[owner]
        except KeyError:
            raise ValueError(f'未登记付费 owner 的候选节点归属：{owner}')

    def _unit_for(self, owner: str) -> WorkUnitBudget:
        unit = self._units.get(owner)
        if unit is not None:
            return unit
        node, scope = self._owner_unit(owner)
        unit = WorkUnitBudget(self.base, self.sid, node, 'executor', scope)
        self._units[owner] = unit
        return unit

    def _close_unadopted(self, unit: WorkUnitBudget) -> None:
        """上一次已购响应未被任何产物采纳（重试／崩溃重做）：关闭该轮让位新尝试。

        只关闭 `delivered`（响应已落盘、无产物采纳）；`mark_rejected` 保留原
        回答、只关闭自动重放，不返还轮次（对齐正式 client 的
        `continued_content_work` 语义）。`reserved`／`unknown` 原样保留——
        pending／unknown 不自动重购，交恢复入口对账（Q-13）。
        """
        turn = unit.latest_turn()
        if turn is not None and turn.state == 'delivered':
            unit.mark_rejected(turn.turn_id, reason_code='superseded_unadopted')

    def _answered_row(self, owner: str, request: ChatRequest) -> dict:
        """取得已回答的原响应行：缓存命中免费采用，缺失时按授权购买一次。"""
        identity = digest({'owner': owner, 'provider': self.provider, 'request': request.cache_payload()})
        rel = f'runtime/request_{identity}.json'
        path = self.repository.path(rel)
        with process_append_lock(path.with_suffix('.lock')):
            if read_cost_snapshot(self.base)[1]:
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
                self._note_adoption(owner, request, row)
                return row
            adopted = self._adopted_row(owner, request, identity)
            if adopted is not None:
                return adopted
            if not self.yes or not self.allow_source_export:
                raise PurchaseRequired(owner)
            if self.book_call_ceiling is not None and self._new_calls() >= self.book_call_ceiling:
                raise ValueError(
                    f'{owner}：本次运行新调用已达 --book-call-ceiling={self.book_call_ceiling}；'
                    '停止生成，不静默超限')
            unit = self._unit_for(owner)
            self._close_unadopted(unit)
            # 首 wire 前取正式内容轮：单元上限 + 整书总闸同一把锁序结算。
            reservation = unit.reserve(identity, run_id=self.run_id)
            row = {'identity': identity, 'status': 'pending', 'attempt_id': uuid.uuid4().hex,
                   'provider': self.provider, 'request': request.cache_payload(),
                   'unit_id': unit.unit_id, 'turn_id': reservation.turn_id}
            self.repository.write(rel, row)
            started = time.monotonic()
            try:
                response = self.transport.send(request)
            except Exception as exc:
                row.update(status='unknown', error_kind=type(exc).__name__,
                           latency_ms=int((time.monotonic() - started) * 1000))
                self.repository.write(rel, row)
                # 传输状态未知：轮次记 unknown，不释放额度、不自动重购。
                unit.finish(reservation.turn_id, outcome='unknown',
                            reason_code=type(exc).__name__)
                self._record(owner, request, row, cached=False)
                raise
            row.update(status='answered', content=response.content, content_sha256=digest(response.content),
                       reasoning=response.reasoning,
                       usage=response.usage, model=response.model or request.model,
                       finish_reason=response.finish_reason,
                       latency_ms=int((time.monotonic() - started) * 1000))
            # 在任何 parse/shape validation 前保存完整原响应，失败不得触发第二次调用。
            self.repository.write(rel, row)
            # 完整回答同步进工作单元账（内容寻址）：请求行是候选链的 per-owner
            # 证据，本 blob 是正式工作单元账户的响应证据——同字节同 hash，都不是
            # 业务状态源（成本权威唯一是 ledger）。delivered 轮必须有响应证据。
            unit.save_response(reservation.turn_id, {
                'attempt_id': row['attempt_id'],
                'response': {'content': response.content,
                             'reasoning': response.reasoning,
                             'usage': response.usage,
                             'model': response.model,
                             'finish_reason': response.finish_reason},
                'request_payload': request.cache_payload()})
            unit.finish(reservation.turn_id, outcome='delivered')
            self._record(owner, request, row, cached=False)
            self._charge_admission()
            self._note_adoption(owner, request, row, unit=unit,
                                turn_id=reservation.turn_id)
            return row

    def _adopted_row(self, owner: str, request: ChatRequest, identity: str) -> dict | None:
        """登记的显式历史采用（F7a-3）：从注册源根取同请求身份的已购结果。

        逐个注册源核对四件事，任一不符**明确阻断**（不静默跳过、不补购）：
        ① 请求身份逐字相等；② 响应字节完整（content_sha256）；③ 原状态为
        answered；④ 原成本关联存在——源根 ledger 里有同 attempt_id＋同
        request_fingerprint 的真实付费行（付费状态未知 = 拒绝采用）。
        采用不复制响应字节进本根（不冒充 cache hit）：本根只落采用记录与一行
       零 token 的 cached ledger 行，历史实付留在源根账内，不重复计费。
        """
        rel = f'runtime/request_{identity}.json'
        for root in self.adopt_roots:
            source = root / rel
            if not source.is_file():
                continue
            row = Str06Repository.read_external(source)
            if row.get('identity') != identity:
                raise ValueError(
                    f'{owner} 采用源 {root} 的请求身份损坏，拒绝采用或重购')
            if row.get('status') != 'answered':
                raise ValueError(
                    f'{owner} 采用源 {root} 的原请求为 {row.get("status")}；'
                    '付费状态未知，拒绝采用')
            if digest(row.get('content')) != row.get('content_sha256'):
                raise ValueError(
                    f'{owner} 采用源 {root} 的已购响应内容损坏，拒绝采用或重购')
            rows, partial = read_cost_snapshot(root.parent)
            if partial:
                raise ValueError(f'采用源 {root} 的成本账有未提交尾，先恢复再采用')
            original = [r for r in rows
                        if r.get('attempt_id') == row.get('attempt_id')
                        and r.get('request_fingerprint') == identity]
            if len(original) != 1 or original[0].get('cached') or \
                    original[0].get('outcome') != 'ok':
                raise ValueError(
                    f'{owner} 采用源 {root} 缺原成本关联（attempt_id＋指纹无唯一'
                    '真实付费行）；付费状态未知，拒绝采用')
            usage = row.get('usage') or {}
            self.repository.write(f'runtime/adopt_{owner}.json', {
                'owner': owner, 'mode': 'historical-adoption',
                'source_root': str(root.resolve()), 'source_rel': rel,
                'sha256': Str06Repository.file_sha256(source),
                'identity': identity, 'attempt_id': row['attempt_id'],
                'provider': row.get('provider'), 'model': row.get('model'),
                'historical_usage': {
                    'prompt_tokens': original[0].get('prompt_tokens'),
                    'completion_tokens': original[0].get('completion_tokens'),
                    'reasoning_tokens': original[0].get('reasoning_tokens')},
                'new_paid_calls': 0, 'cost_link': 'ledger-row', 'errors': []})
            self._record_adoption(owner, request, identity, row)
            self._note_adoption(owner, request, row)
            return {**row, 'cached': True}
        return None

    def bind_wire_adoption(self, owner: str, identity: str, wire_path: Path, *,
                           allow_patch: bool = False) -> dict:
        """wire 采用的身份／成本绑定（F7a-3）：返回并入采用记录的字段。

        证据分两轮扫描，逐档下降但都如实登记：
        · 第一轮（`ledger-row`）：任一注册源根有同请求身份的已购行，成本关联
          唯一且真实；wire 字节与其响应内容相等——身份、响应、成本三方绑定；
        · 第二轮（`batch-documented`）：源根 `adopt_<owner>.json` 记录与本 wire
          字节一致（采用链可验），原购买根的逐请求账未持久（F2b 批次证据），
          成本只到批次口径；
        · 都没有（`unverified`）：仅形状校验，采用记录如实标注。
        `allow_patch` 接受 owner patch 链：wire 与已购内容不一致但源根采用记录
          字节一致，且调用方已核验 patch note 的 base 身份（修订来源保留）。
        """
        wire_sha = Str06Repository.file_sha256(wire_path)
        for root in self.adopt_roots:
            source = root / 'runtime' / f'request_{identity}.json'
            if not source.is_file():
                continue
            row = Str06Repository.read_external(source)
            if row.get('identity') != identity:
                raise ValueError(f'{owner} 采用源 {root} 的请求身份损坏，拒绝采用')
            if row.get('status') != 'answered':
                raise ValueError(
                    f'{owner} 采用源 {root} 的原请求为 {row.get("status")}；'
                    '付费状态未知，拒绝采用')
            if digest(row.get('content')) != row.get('content_sha256'):
                raise ValueError(f'{owner} 采用源 {root} 的已购响应内容损坏，拒绝采用')
            rows, partial = read_cost_snapshot(root.parent)
            if partial:
                raise ValueError(f'采用源 {root} 的成本账有未提交尾，先恢复再采用')
            original = [r for r in rows
                        if r.get('attempt_id') == row.get('attempt_id')
                        and r.get('request_fingerprint') == identity]
            if len(original) != 1 or original[0].get('cached') or                     original[0].get('outcome') != 'ok':
                raise ValueError(
                    f'{owner} 采用源 {root} 缺原成本关联；付费状态未知，拒绝采用')
            if _same_json_payload(wire_path, row.get('content') or ''):
                return {'mode': 'historical-adoption', 'cost_link': 'ledger-row',
                        'source_root': str(root.resolve()),
                        'source_rel': f'runtime/request_{identity}.json',
                        'identity': identity, 'attempt_id': row['attempt_id'],
                        'provider': row.get('provider'), 'model': row.get('model'),
                        'historical_usage': {
                            'prompt_tokens': original[0].get('prompt_tokens'),
                            'completion_tokens': original[0].get('completion_tokens'),
                            'reasoning_tokens': original[0].get('reasoning_tokens')}}
            recorded = self._adopt_record(root, owner)
            if allow_patch and recorded is not None and recorded.get('sha256') == wire_sha:
                return {'mode': 'historical-adoption', 'cost_link': 'ledger-row',
                        'owner_patch': True,
                        'source_root': str(root.resolve()),
                        'source_rel': f'runtime/request_{identity}.json',
                        'identity': identity, 'attempt_id': row['attempt_id'],
                        'provider': row.get('provider'), 'model': row.get('model'),
                        'historical_usage': {
                            'prompt_tokens': original[0].get('prompt_tokens'),
                            'completion_tokens': original[0].get('completion_tokens'),
                            'reasoning_tokens': original[0].get('reasoning_tokens')}}
            if recorded is None or recorded.get('sha256') != wire_sha:
                raise ValueError(
                    f'{owner} wire 与采用源 {root} 的已购响应内容不一致，且无匹配的 '
                    'owner patch 采用链，拒绝采用')
        for root in self.adopt_roots:
            recorded = self._adopt_record(root, owner)
            if recorded is not None and recorded.get('sha256') == wire_sha:
                return {'mode': 'historical-adoption', 'cost_link': 'batch-documented',
                        'source_root': str(root.resolve()),
                        'source_rel': f'runtime/adopt_{owner}.json',
                        'adoption_chain_verified': True}
        return {'mode': 'wire-adoption', 'cost_link': 'unverified'}

    @staticmethod
    def _adopt_record(root: Path, owner: str) -> dict | None:
        path = root / 'runtime' / f'adopt_{owner}.json'
        if not path.is_file():
            return None
        return Str06Repository.read_external(path)

    def _record_adoption(self, owner, request, identity, row) -> None:
        """采用在候选根账内只记一行零 token 的 cached 行（新 attempt_id）。

        不走 `_record` 的 cached 回填分支：那一条服务本根已购响应的免费重放，
        会把历史 token 抄进候选根账——同一批 token 两处各声称一次。
        """
        node, _scope = self._owner_unit(owner)
        self.ledger.record(
            node=node, sid=self.sid, model=row.get('model', request.model),
            request_model=request.model, code=STR06_RC_VERSION, attempt=1, max_attempts=1,
            will_retry=False,
            cached=True, cache_policy='historical-adoption', purpose=owner,
            attempt_id=uuid.uuid4().hex,
            request_fingerprint=identity,
            outcome='ok' if row.get('status') == 'answered' else 'error',
            prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)

    def _record(self, owner, request, row, *, cached):
        # raw→ledger 中断时，免费重放先补原始账，不能把已付费变成缓存零成本。
        rows, partial = read_cost_snapshot(self.base)
        if partial:
            raise ValueError('成本账有未提交尾，拒绝追加或采用')
        if cached and not any(r.get('attempt_id') == row['attempt_id'] for r in rows):
            self._record(owner, request, row, cached=False)
        node, _scope = self._owner_unit(owner)
        usage = row.get('usage') or {}
        self.ledger.record(
            node=node, sid=self.sid, model=row.get('model', request.model),
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

    def _note_adoption(self, owner, request, row, *, unit=None, turn_id=''):
        """把已购响应登记进候选上下文的采用台账（provenance 的生成事实来源）。"""
        context = getattr(self.repository, 'context', None)
        if context is None:
            return
        context.note_adoption(
            owner=owner, model=row.get('model') or request.model,
            prompts={'system': request.system, 'user': request.user},
            turn_id=turn_id, unit=unit)


def _same_json_payload(path: Path, content: str) -> bool:
    """wire 文件与已购响应内容是否同一份 JSON 载荷（语义相等，不管序列化）。"""
    try:
        return Str06Repository.read_external(path) == json.loads(content)
    except (ValueError, OSError):
        return False


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'响应 JSON 重复键：{key}')
        result[key] = value
    return result
