#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
response_repair.py —— 被结构化内容闸拒绝的响应隔离区与受托修复状态机。

【它不是什么】
  · 不是响应缓存：修复稿不会写回 `.llm_cache/`，也不会伪装成供应商原响应；
  · 不是正式产物：`_llm_rejected/` 不进 ARTIFACTS / 完成度 / provenance；
  · 不是第二套判据：本模块只保存候选与人工决定，真正采纳仍回
    `novel.llm.client.LlmClient.ask_json` 的原 `require/schema/validator` 闸。

【为什么必须落盘】受托 agent 在付费 CLI 的**外层**。一次可修复拒绝必须先成为显式
终态，外层才能编辑固定的 `candidate.json`、经 CLI 提交，再重跑原节点。把 callback
塞进 LlmClient 只会得到一条生产上无人能调用的假路径；在里面再拉一个 agent 则会递归。

【不可变证据】原响应、提交决定和复验结果分成三个文件，均只写一次。Agent 只能编辑
尚未提交的 `candidate.json`；提交后候选字节被封进 `resolution.json`，后续复验不再读
可变草稿。原始响应永不覆盖，修复失败也没有第二次修复额度。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from novel import paths
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, TerminalState, UpstreamMissing


VERSION = 1
REJECTION_FILE = 'rejection.json'
CANDIDATE_FILE = 'candidate.json'
ORIGINAL_FILE = 'original.txt'
RESOLUTION_FILE = 'resolution.json'
OUTCOME_FILE = 'outcome.json'

_LOCK = threading.RLock()


def chain_fingerprint(*, sid: str, node: str, purpose: str, code: str,
                      system: str, user: str, models: Iterable[str],
                      rungs: Iterable[tuple[str, bool]], json_mode: bool,
                      max_tokens: int) -> str:
    """跨 CLI 续跑的逻辑请求身份；含代码与完整请求语义，但盘上只落摘要。"""
    payload = {
        'sid': sid, 'node': node, 'purpose': purpose, 'code': code,
        'system': system, 'user': user, 'models': list(models),
        'rungs': [list(rung) for rung in rungs],
        'json_mode': json_mode, 'max_tokens': max_tokens,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp',
                                    dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2,
                                  sort_keys=True) + '\n')


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise UpstreamMissing(f'{path} 不在盘上') from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(f'LLM 修复状态 {path} 读不出来：{exc}') from exc
    if not isinstance(data, dict) or data.get('version') != VERSION:
        raise ArtifactUnreadable(
            f'LLM 修复状态 {path} 不是当前 v{VERSION} 对象；不猜旧形状')
    return data


@dataclass(frozen=True)
class RepairRecord:
    """一次拒绝及其两个可选后继；字典保留完整审计载荷。"""

    root: Path
    rejection: dict[str, Any]
    resolution: dict[str, Any] | None
    outcome: dict[str, Any] | None

    @property
    def attempt_id(self) -> str:
        return str(self.rejection['attempt_id'])

    @property
    def eligible(self) -> bool:
        return self.rejection.get('repair_eligible') is True


def _record_at(root: Path) -> RepairRecord:
    rejection = _read_json(root / REJECTION_FILE)
    resolution = (_read_json(root / RESOLUTION_FILE)
                  if (root / RESOLUTION_FILE).is_file() else None)
    outcome = (_read_json(root / OUTCOME_FILE)
               if (root / OUTCOME_FILE).is_file() else None)
    return RepairRecord(root, rejection, resolution, outcome)


def records_for_chain(novel_dir: Path, chain_id: str) -> list[RepairRecord]:
    """按模型、样本顺序读取一条恢复链；半份状态关闭式报错，不静默跳过。"""
    base = Path(novel_dir) / paths.REJECTED_RESPONSE_DIRNAME / chain_id
    if not base.is_dir():
        return []
    records = [_record_at(root) for root in base.iterdir() if root.is_dir()]
    return sorted(records, key=lambda rec: (
        int(rec.rejection.get('model_index') or 0),
        -1 if rec.rejection.get('sample') is None
        else int(rec.rejection['sample']),
        rec.attempt_id,
    ))


def find_attempt(novel_dir: Path, attempt_id: str) -> RepairRecord:
    """CLI 人工面按 attempt 定位；零个与多个都显式拒绝。"""
    # 先借路径构造器校验 attempt 的形状；chain 用占位合法摘要。
    paths.rejected_response_rel('0' * 64, attempt_id)
    base = Path(novel_dir) / paths.REJECTED_RESPONSE_DIRNAME
    matches = list(base.glob(f'*/{attempt_id}')) if base.is_dir() else []
    if not matches:
        raise UpstreamMissing(f'这本书没有 attempt_id={attempt_id} 的拒绝响应')
    if len(matches) != 1:
        raise TerminalState(
            f'attempt_id={attempt_id} 在这本书里出现 {len(matches)} 次；拒绝静默挑一份')
    return _record_at(matches[0])


def keep_rejection(novel_dir: Path, *, chain_id: str, attempt_id: str,
                   payload: dict[str, Any], original: str,
                   candidate: Any | None) -> tuple[str | None, str | None]:
    """完整隔离一次拒绝；返回 `(相对目录, 写盘错误)`，不让诊断故障烧掉更多调用。"""
    root = paths.rejected_response_path(novel_dir, chain_id, attempt_id)
    record = {
        'version': VERSION, 'created_at': _now(), 'chain_id': chain_id,
        'attempt_id': attempt_id, **payload,
    }
    try:
        with _LOCK:
            if (root / REJECTION_FILE).exists():
                existing = _read_json(root / REJECTION_FILE)
                if existing.get('attempt_id') != attempt_id or existing.get('chain_id') != chain_id:
                    raise TerminalState(f'拒绝响应目录身份冲突：{root}')
            else:
                _atomic_text(root / ORIGINAL_FILE, original)
                initial = candidate if isinstance(candidate, (dict, list)) else {}
                _atomic_text(root / CANDIDATE_FILE,
                             json.dumps(initial, ensure_ascii=False, indent=2) + '\n')
                # `rejection.json` 是完成哨兵，最后落；中途崩溃的半目录会在恢复时
                # 关闭式报错，而不是被当作“这次拒绝从未发生”再多烧一采。
                _atomic_json(root / REJECTION_FILE, record)
    except OSError as exc:
        return None, f'{type(exc).__name__}: {exc}'
    rel = paths.rejected_response_rel(chain_id, attempt_id)
    return rel, None


def submit(novel_dir: Path, attempt_id: str, *, actor: str, reason: str,
           decline: bool = False) -> RepairRecord:
    """封存 agent 决定。候选只允许提交一次，且只从登记草稿位置读取。"""
    actor, reason = actor.strip(), reason.strip()
    if not actor or not reason:
        raise TerminalState('响应修复提交必须显式给非空 actor 与 reason')
    located = find_attempt(novel_dir, attempt_id)
    with _LOCK:
        with process_append_lock(located.root / RESOLUTION_FILE):
            # 外层锁只挡本进程；跨进程锁拿到以后必须重读，不能拿锁前快照作决定。
            record = _record_at(located.root)
            if not record.eligible:
                raise TerminalState(
                    f'attempt_id={attempt_id} 的 gate={record.rejection.get("gate")} '
                    '不在通用 agent 修复面；只能让原节点重采')
            if record.resolution is not None:
                raise TerminalState(
                    f'attempt_id={attempt_id} 已有不可变修复决定；每个响应最多修复一次')
            payload: dict[str, Any] = {
                'version': VERSION, 'submitted_at': _now(),
                'attempt_id': attempt_id, 'actor': actor, 'reason': reason,
                'action': 'decline' if decline else 'submit',
            }
            if not decline:
                candidate_path = record.root / CANDIDATE_FILE
                try:
                    raw = candidate_path.read_bytes()
                    candidate = json.loads(raw.decode('utf-8'))
                except FileNotFoundError:
                    raise UpstreamMissing(
                        f'请先创建/编辑固定候选文件 {candidate_path}') from None
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ArtifactUnreadable(
                        f'候选 {candidate_path} 不是合法 UTF-8 JSON：{exc}') from exc
                payload['candidate'] = candidate
                payload['candidate_sha256'] = hashlib.sha256(raw).hexdigest()
            _atomic_json(record.root / RESOLUTION_FILE, payload)
            return _record_at(record.root)


def submit_deterministic(record: RepairRecord, candidate: Any, *, rule: str) -> RepairRecord:
    """封存一次代码可证明不改变语义叶子的机械修复；与 agent 共用一次额度。"""
    raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    payload = {
        'version': VERSION, 'submitted_at': _now(),
        'attempt_id': record.attempt_id,
        'actor': f'deterministic:{rule}',
        'reason': '只移除 JSON BOM/容器尾逗号；语义叶子未改，仍按原闸复验',
        'action': 'deterministic', 'candidate': candidate,
        'candidate_sha256': hashlib.sha256(raw).hexdigest(),
    }
    with _LOCK:
        with process_append_lock(record.root / RESOLUTION_FILE):
            current = _record_at(record.root)
            if current.resolution is not None:
                raise TerminalState(
                    f'attempt_id={record.attempt_id} 已有修复决定；机械与 agent 共用一次额度')
            _atomic_json(record.root / RESOLUTION_FILE, payload)
            return _record_at(record.root)


def write_outcome(record: RepairRecord, *, decision: str, gate: str | None,
                  detail: list[dict[str, Any]] | None, why: str | None) -> None:
    """同闸复验结果只写一次；accepted 后重跑仍可重放同一封存候选。"""
    if decision not in ('accepted', 'rejected', 'declined'):
        raise ValueError(f'未知响应修复结果 {decision!r}')
    payload = {
        'version': VERSION, 'checked_at': _now(),
        'attempt_id': record.attempt_id,
        'decision': decision,
        'gate': gate, 'reject_detail': detail,
        # 原始模型正文不进这一格；why 是本仓判词，截短只为维持单份状态可读。
        'why': None if why is None else why[:1000],
    }
    with _LOCK:
        target = record.root / OUTCOME_FILE
        with process_append_lock(target):
            if target.exists():
                existing = _read_json(target)
                if existing.get('decision') != payload['decision']:
                    raise TerminalState(
                        f'{record.attempt_id} 的同一封存候选出现相反复验结果；拒绝覆盖历史')
                return
            _atomic_json(target, payload)


def candidate_path(record: RepairRecord) -> Path:
    return record.root / CANDIDATE_FILE
