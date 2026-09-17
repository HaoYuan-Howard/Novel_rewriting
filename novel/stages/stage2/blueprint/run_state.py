"""Stage2 内部检查点的只读载体协议；生成与进度共享，不提供模型调用或恢复许可。

这里只读取 _service 的运行记录，正式小说产物仍由 ArtifactStore 读取。进度校验只证明
检查点自哈希和当前输入一致；paid response/session/ledger 的恢复权威仍由 producer 管理。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from novel import paths
from novel.errors import ArtifactUnreadable, ProvenanceRefused, TerminalState, UpstreamMissing
from novel.run_identity import check_run_id

VERSION = 1
RUN_FILE = 'run.json'


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def state_sha256(value: Any) -> str:
    return hashlib.sha256(
        value if isinstance(value, bytes) else canonical_bytes(value)
    ).hexdigest()


def read_state(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode('utf-8'))
    except FileNotFoundError:
        raise UpstreamMissing(f'{path} 不在盘上') from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(f'Stage2 run 状态 {path} 读不出来：{exc}') from exc
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ArtifactUnreadable(
            f'Stage2 run 状态 {path} 不是当前内部 v{VERSION} 对象；不猜旧形状')
    return value


def checkpoint_sha256(payload: Mapping[str, Any]) -> str:
    return state_sha256({key: value for key, value in payload.items()
                 if key not in ('created_at', 'checkpoint_sha256')})


def plot_checkpoint_progress(ctx: Any, *, run_id: str, chapter: int,
                             input_sha256: str) -> dict[str, Any]:
    """只读校验检查点载体及当前输入。不是已购响应的恢复许可或语义接受证明。"""
    result = dict(chapter_ordinal=chapter, run_id=run_id, state='missing', reason=None)
    root = paths.stage2_blueprint_run_path(ctx.novel_dir, check_run_id(run_id))
    try:
        run = read_state(root / RUN_FILE)
        if (run.get('architecture') != 'plot-blueprint-v1'
                or run.get('sid') != ctx.sid or run.get('run_id') != run_id):
            raise ProvenanceRefused('章节运行记录的书或契约身份不一致')
        projected = (run.get('input_projections') or {}).get(str(chapter))
        if projected is None:
            return {**result, 'state': 'unver', 'reason': '运行记录缺章节消费身份'}
        if projected != input_sha256:
            return {**result, 'state': 'expired', 'reason': '当前原文或共享输入已变化'}
        phase = 'repair' if run.get('operation') in ('chapters-refresh', 'commander-rework') else 'blueprint'
        target = paths.stage2_blueprint_checkpoint_path(ctx.novel_dir, run_id, phase, chapter)
        if not target.exists():
            return result
        record = read_state(target)
        if (record.get('checkpoint_sha256') != checkpoint_sha256(record)
                or record.get('response_payload_sha256') != state_sha256(record.get('response_payload'))
                or record.get('run_id') != run_id or record.get('sid') != ctx.sid
                or record.get('chapter') != chapter or record.get('phase') != phase
                or record.get('architecture') != 'plot-blueprint-v1'
                or record.get('input_projection_sha256') != projected):
            raise ArtifactUnreadable('章节检查点内容与身份摘要不闭合')
        if record.get('code') != ctx.code:
            return {**result, 'state': 'unver', 'reason': '代码验证身份变化，等待免费复检'}
        if record.get('scope_violation') is not None:
            return {**result, 'state': 'unver', 'reason': '检查点记录了章节范围问题'}
        return {**result, 'state': 'current'}
    except (TerminalState, OSError, ValueError) as exc:
        return {**result, 'state': 'unver', 'reason': str(exc)}
