# -*- coding: utf-8 -*-
"""服务空间的显式、关闭式生命周期入口。

普通运行路径只会用本模块创建/收掉一个精确 scratch session。日志、operator
telemetry、回收站与已提交 checkpoint 的维护必须另走 ``plan_retention`` →
``apply_retention`` 两相口；默认 policy 四个 TTL 都是 ``None``，所以即使误调
preview 也不会选中任何东西。

本模块刻意不碰全局 ledger、admission、beacon、agent/job/session 事件流：它们是
恢复/成本所需的永久小账。正文 checkpoint 也只有在同一 sid 已有 delivered 的
parent run、对应 ``committed.json`` 能逐项对证正式文件、且超过 TTL 时才可回收；
回收前先永久落一份无正文 receipt。陌生目录、符号链接、未知 owner 与未闭合 run
一律留给人工，不用“看起来很旧”代替所有权证明。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from novel import paths
from novel.errors import PathRefused, TerminalState


RETENTION_WIRE_VERSION = 1
RUN_RECEIPTS_DIRNAME = 'run_receipts'
TRASH_RECEIPTS_DIRNAME = 'trash_receipts'
RetentionKind = Literal['log', 'operator_telemetry', 'trash', 'checkpoint']


def _positive_optional(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
        raise ValueError(f'{field} 必须是正整数秒或 null')
    return value


@dataclass(frozen=True)
class RetentionPolicy:
    """四类维护的显式 TTL；``None`` = 关闭，绝不是“立即删除”。"""

    log_ttl_seconds: int | None = None
    telemetry_ttl_seconds: int | None = None
    trash_ttl_seconds: int | None = None
    checkpoint_ttl_seconds: int | None = None
    max_entries: int = 512

    def __post_init__(self) -> None:
        for field in (
                'log_ttl_seconds', 'telemetry_ttl_seconds',
                'trash_ttl_seconds', 'checkpoint_ttl_seconds'):
            _positive_optional(getattr(self, field), field=field)
        if (not isinstance(self.max_entries, int)
                or isinstance(self.max_entries, bool) or self.max_entries < 1):
            raise ValueError('max_entries 必须是正整数')

    def to_dict(self) -> dict[str, int | None]:
        return {
            'log_ttl_seconds': self.log_ttl_seconds,
            'telemetry_ttl_seconds': self.telemetry_ttl_seconds,
            'trash_ttl_seconds': self.trash_ttl_seconds,
            'checkpoint_ttl_seconds': self.checkpoint_ttl_seconds,
            'max_entries': self.max_entries,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> 'RetentionPolicy':
        expected = {
            'log_ttl_seconds', 'telemetry_ttl_seconds', 'trash_ttl_seconds',
            'checkpoint_ttl_seconds', 'max_entries',
        }
        if set(raw) != expected:
            raise ValueError(
                f'retention policy 字段必须精确等于 {sorted(expected)}')
        return cls(**dict(raw))  # type: ignore[arg-type]


@dataclass(frozen=True)
class RetentionCandidate:
    kind: RetentionKind
    sid: str
    rel: str
    observed_mtime_ns: int
    observed_size: int
    observed_sha256: str
    evidence: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            'kind': self.kind, 'sid': self.sid, 'rel': self.rel,
            'observed_mtime_ns': self.observed_mtime_ns,
            'observed_size': self.observed_size,
            'observed_sha256': self.observed_sha256,
            'evidence': dict(self.evidence),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> 'RetentionCandidate':
        expected = {
            'kind', 'sid', 'rel', 'observed_mtime_ns', 'observed_size',
            'observed_sha256', 'evidence',
        }
        if set(raw) != expected:
            raise ValueError('retention candidate 字段不闭合')
        kind = raw['kind']
        if kind not in ('log', 'operator_telemetry', 'trash', 'checkpoint'):
            raise ValueError(f'未知 retention kind={kind!r}')
        sid = paths.check_sid(str(raw['sid']))
        rel = str(raw['rel'])
        if not rel or rel.startswith(('/', '\\')) or '..' in Path(rel).parts:
            raise PathRefused(f'retention candidate 不是 base 内相对路径：{rel!r}')
        mtime = raw['observed_mtime_ns']
        size = raw['observed_size']
        sha = str(raw['observed_sha256'])
        evidence = raw['evidence']
        if (not isinstance(mtime, int) or isinstance(mtime, bool) or mtime < 0
                or not isinstance(size, int) or isinstance(size, bool) or size < 0
                or len(sha) != 64
                or any(ch not in '0123456789abcdef' for ch in sha)
                or not isinstance(evidence, dict)):
            raise ValueError('retention candidate 的盘面身份不合法')
        return cls(
            kind=kind, sid=sid, rel=rel, observed_mtime_ns=mtime,
            observed_size=size, observed_sha256=sha, evidence=dict(evidence))


@dataclass(frozen=True)
class RetentionPlan:
    generated_at: int
    policy: RetentionPolicy
    examined: int
    candidates: tuple[RetentionCandidate, ...]
    plan_token: str

    def _unsigned(self) -> dict[str, object]:
        return {
            'wire_version': RETENTION_WIRE_VERSION,
            'generated_at': self.generated_at,
            'policy': self.policy.to_dict(),
            'examined': self.examined,
            'candidates': [item.to_dict() for item in self.candidates],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._unsigned(), 'plan_token': self.plan_token}

    @classmethod
    def create(
            cls, *, generated_at: int, policy: RetentionPolicy,
            examined: int, candidates: tuple[RetentionCandidate, ...],
    ) -> 'RetentionPlan':
        provisional = cls(
            generated_at=generated_at, policy=policy, examined=examined,
            candidates=candidates, plan_token='0' * 64)
        return cls(
            generated_at=generated_at, policy=policy, examined=examined,
            candidates=candidates,
            plan_token=_sha256(_canonical(provisional._unsigned())))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> 'RetentionPlan':
        expected = {
            'wire_version', 'generated_at', 'policy', 'examined',
            'candidates', 'plan_token',
        }
        if set(raw) != expected or raw.get('wire_version') != RETENTION_WIRE_VERSION:
            raise ValueError('retention plan 版本或字段不闭合')
        if not isinstance(raw['policy'], dict) or not isinstance(raw['candidates'], list):
            raise ValueError('retention plan policy/candidates 形状不合法')
        generated = raw['generated_at']
        examined = raw['examined']
        if (not isinstance(generated, int) or isinstance(generated, bool)
                or generated < 0 or not isinstance(examined, int)
                or isinstance(examined, bool) or examined < 0):
            raise ValueError('retention plan 时间/扫描量不合法')
        candidates = tuple(
            RetentionCandidate.from_mapping(item)
            for item in raw['candidates'] if isinstance(item, dict))
        if len(candidates) != len(raw['candidates']):
            raise ValueError('retention plan candidates 含非对象')
        token = str(raw['plan_token'])
        plan = cls(
            generated_at=generated,
            policy=RetentionPolicy.from_mapping(raw['policy']),
            examined=examined, candidates=candidates, plan_token=token)
        if token != _sha256(_canonical(plan._unsigned())):
            raise ValueError('retention plan token 不闭合')
        return plan


@dataclass(frozen=True)
class RetentionApplyResult:
    plan_token: str
    removed: tuple[str, ...]
    receipts: tuple[str, ...]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_rows(
        root: Path, *, max_entries: int | None = None,
) -> tuple[list[dict[str, object]], int, int]:
    """无正文树身份；符号链接与陌生特殊文件关闭式拒绝。"""
    if root.is_symlink() or not root.is_dir():
        raise PathRefused(f'受管树不是普通目录：{root}')
    rows: list[dict[str, object]] = []
    total = 0
    newest = root.stat().st_mtime_ns
    for item in sorted(root.rglob('*'), key=lambda path: path.as_posix()):
        if max_entries is not None and len(rows) >= max_entries:
            raise RuntimeError(
                f'受管树 {root} 超过 max_entries={max_entries}，拒绝继续扫描')
        if item.is_symlink():
            raise PathRefused(f'受管树内不允许符号链接：{item}')
        stat = item.stat()
        newest = max(newest, stat.st_mtime_ns)
        rel = item.relative_to(root).as_posix()
        if item.is_dir():
            rows.append({'kind': 'dir', 'rel': rel})
        elif item.is_file():
            rows.append({
                'kind': 'file', 'rel': rel, 'size': stat.st_size,
                'sha256': _file_sha256(item),
            })
            total += stat.st_size
        else:
            raise PathRefused(f'受管树内出现特殊文件：{item}')
    return rows, total, newest


def tree_identity(root: Path, *, max_entries: int | None = None) -> dict[str, object]:
    """供 trash manifest 与 retention CAS 共用的无正文目录身份。"""
    rows, total, newest = _tree_rows(root, max_entries=max_entries)
    return {
        'files': sum(row['kind'] == 'file' for row in rows),
        'bytes': total, 'newest_mtime_ns': newest,
        'tree_sha256': _sha256(_canonical(rows)),
    }


def _snapshot(
        path: Path, *, max_entries: int | None = None,
) -> tuple[int, int, str]:
    if path.is_symlink():
        raise PathRefused(f'维护目标不能是符号链接：{path}')
    stat = path.stat()
    if path.is_file():
        return stat.st_mtime_ns, stat.st_size, _file_sha256(path)
    identity = tree_identity(path, max_entries=max_entries)
    return (
        int(identity['newest_mtime_ns']), int(identity['bytes']),
        str(identity['tree_sha256']))


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, indent=2,
        allow_nan=False).encode('utf-8') + b'\n'
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _exact_service_target(base: str | Path, rel: str) -> Path:
    declared_root = paths.service_dir(base).absolute()
    if declared_root.is_symlink():
        raise PathRefused(f'_service 根不能是符号链接：{declared_root}')
    declared = (Path(base) / rel).absolute()
    try:
        declared.relative_to(declared_root)
    except ValueError as exc:
        raise PathRefused(f'维护目标字面越出 _service：{declared}') from exc
    probe = declared
    while probe != declared_root:
        if probe.exists() and probe.is_symlink():
            raise PathRefused(f'维护路径含符号链接：{probe}')
        probe = probe.parent
    root = declared_root.resolve()
    target = declared.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PathRefused(f'维护目标越出 _service：{target}') from exc
    if target == root:
        raise PathRefused('维护目标不能是整个 _service')
    return target


def _candidate(
        base: str | Path, *, kind: RetentionKind, sid: str, path: Path,
        evidence: Mapping[str, object], max_entries: int | None = None,
) -> RetentionCandidate:
    rel = path.relative_to(Path(base)).as_posix()
    exact = _exact_service_target(base, rel)
    mtime, size, sha = _snapshot(exact, max_entries=max_entries)
    return RetentionCandidate(
        kind=kind, sid=paths.check_sid(sid), rel=rel,
        observed_mtime_ns=mtime, observed_size=size,
        observed_sha256=sha, evidence=dict(evidence))


@dataclass(frozen=True)
class ScratchSweepResult:
    """一次有界扫描的可审计结果。"""

    examined: int
    removed: tuple[str, ...]


def _assert_exact_session(
        base: str | Path, sid: str, session_id: str,
) -> Path:
    declared = paths.scratch_session_dir(base, sid, session_id)
    declared_root = paths.scratch_dir(base)
    if (declared_root.is_symlink() or declared.is_symlink()
            or declared.parent.is_symlink()):
        raise PathRefused(f'scratch session 或 sid 目录不能是符号链接：{declared}')
    root = declared_root.resolve()
    target = declared.resolve()
    if target.parent.parent != root:
        raise PathRefused(f'scratch session 越出登记根：{target}')
    return target


def open_scratch_session(
        base: str | Path, sid: str, session_id: str,
) -> Path:
    """创建并返回一个精确会话目录；已存在时幂等。"""
    target = _assert_exact_session(base, sid, session_id)
    target.mkdir(parents=True, exist_ok=True)
    # mtime 是 TTL 的会话活性身份；不另造一份需要自己轮转的账。
    os.utime(target, None)
    return target


def finish_scratch_session(
        base: str | Path, sid: str, session_id: str,
) -> bool:
    """只清理精确 session；不存在返回 ``False``。"""
    target = _assert_exact_session(base, sid, session_id)
    if not target.exists():
        return False
    if not target.is_dir():
        raise PathRefused(f'scratch session 落点不是目录：{target}')
    shutil.rmtree(target)
    # 只收空壳，绝不扩大到其他 sid/session。
    for empty in (target.parent, target.parent.parent):
        try:
            empty.rmdir()
        except OSError:
            break
    return True


def sweep_expired_scratch(
        base: str | Path, *, ttl_seconds: int,
        now: float | None = None, max_sessions: int = 256,
) -> ScratchSweepResult:
    """显式、两层、有上限的 scratch TTL 扫描。

    ``ttl_seconds <= 0`` 与超过 ``max_sessions`` 都关闭式拒绝；不做
    启动时无界递归删除。
    """
    if (not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool)
            or ttl_seconds <= 0):
        raise ValueError('scratch ttl_seconds 必须是正整数')
    if (not isinstance(max_sessions, int) or isinstance(max_sessions, bool)
            or max_sessions < 1):
        raise ValueError('scratch max_sessions 必须是正整数')
    root = paths.scratch_dir(base)
    if not root.is_dir():
        return ScratchSweepResult(examined=0, removed=())
    clock = time.time() if now is None else float(now)
    # epoch session 的目录只在首次写 permit 时触碰 mtime；一个长节点可能数小时不再
    # 写 scratch。TTL 不能把“mtime 很旧”误读成“owner 已死”。任何同 sid 非终态
    # Supervisor 或 durable admission 都让该 sid 整体跳过；读不清则整轮拒绝。
    from novel.orchestrator import admission, agent, beacon, jobs

    def owner_surface() -> tuple[list[Any], set[str]]:
        """折叠所有 durable 活性面；任一面读不清时异常上抛、整轮不猜。"""
        records = list(agent.records(base))
        protected = {
            paths.check_sid(record.start.sid)
            for record in records
            if record.ended is None or record.active_epoch_id is not None
        }
        protected.update(
            paths.check_sid(snapshot.sid)
            for snapshot in admission.unresolved(base, None))
        # beacon 不能只从 agent record 反查：恰好在 started 记录丢失时，
        # 孤儿 execution 更不能被 TTL 忽略。有 run_id 的事件统一调用生产
        # ``open_executions`` 折叠口；legacy 无 run_id 的只能保守保护尚显示
        # running 的 sid，不伪造 execution 已 drain。
        beacon_events = beacon.read_events(base)
        for run_id in sorted({
                event.run_id for event in beacon_events
                if event.run_id is not None}):
            for execution in beacon.open_executions(base, run_id):
                protected.add(paths.check_sid(execution.sid))
        legacy_sids = {
            paths.check_sid(event.sid) for event in beacon_events
            if event.run_id is None
        }
        for sid in legacy_sids:
            legacy = [
                event for event in beacon_events
                if event.run_id is None and event.sid == sid]
            if any(step.state == 'running' for step in beacon.fold(legacy)):
                protected.add(sid)

        # owner lease 只有 OS byte-lock 是真值。找不到 parent record 的残留
        # lease 无法归属 sid：能证明未被持有才继续，否则整轮拒绝。
        known_runs = {record.run_id for record in records}
        owner_root = paths.service_dir(base) / admission.OWNER_LEASE_DIRNAME
        if owner_root.is_dir():
            for lock_path in sorted(owner_root.glob('*.owner.lock')):
                run_id = lock_path.name.removesuffix('.owner.lock')
                if run_id in known_runs:
                    continue
                with admission.require_owner_stopped(base, run_id):
                    pass
        return records, protected

    records, protected_sids = owner_surface()
    sessions: list[Path] = []
    for sid_dir in sorted(root.iterdir(), key=lambda item: item.name):
        if not sid_dir.is_dir():
            continue
        paths.check_sid(sid_dir.name)
        if sid_dir.name in protected_sids:
            continue
        for session_dir in sorted(sid_dir.iterdir(), key=lambda item: item.name):
            if not session_dir.is_dir():
                continue
            # 重用唯一路径准入，陌生目录不得被 TTL 猜测删除。
            _assert_exact_session(base, sid_dir.name, session_dir.name)
            sessions.append(session_dir)
            if len(sessions) > max_sessions:
                raise RuntimeError(
                    f'scratch 扫描超过 max_sessions={max_sessions}，未删除任何新目标')
    expired = [session for session in sessions
               if clock - session.stat().st_mtime >= ttl_seconds]
    removed: list[str] = []
    by_sid: dict[str, list[Path]] = {}
    for session in expired:
        by_sid.setdefault(session.parent.name, []).append(session)
    # 初扫只是 preview。apply 前还要拿同书 writer、证明所有已登记 owner lease
    # 均未被持有，并在持锁期间重新折叠 agent/admission/beacon；否则长节点恰好
    # 跨过 TTL 的竞态仍会删掉它正在消费的 execution token。
    for sid, sid_sessions in sorted(by_sid.items()):
        writer_id = f'scratch-retention-{sid}'
        try:
            key = jobs.acquire_job_writer(
                base, sid, 'service.scratch-retention', writer_id)
        except jobs.JobConflict:
            continue
        try:
            try:
                with ExitStack() as leases:
                    for record in records:
                        if paths.check_sid(record.start.sid) != sid:
                            continue
                        lease = admission.owner_lease_path(base, record.run_id)
                        lock_path = lease.with_name(lease.name + '.lock')
                        if lock_path.exists():
                            leases.enter_context(
                                admission.require_owner_stopped(
                                    base, record.run_id))
                    _fresh_records, fresh_protected = owner_surface()
                    if sid in fresh_protected:
                        continue
                    for session in sid_sessions:
                        if finish_scratch_session(base, sid, session.name):
                            removed.append(f'{sid}/{session.name}')
            except TerminalState:
                # OS owner lease 无法证明已停：这一 sid 留在盘上，不把未知洗成过期。
                continue
        finally:
            jobs.REGISTRY.release(key, writer_id)
    return ScratchSweepResult(examined=len(sessions), removed=tuple(removed))


def _expired(mtime_ns: int, *, now: int, ttl_seconds: int) -> bool:
    return now * 1_000_000_000 - mtime_ns >= ttl_seconds * 1_000_000_000


def _ended_log_owners(base: str | Path) -> dict[str, str]:
    """日志名 → sid；只发布生产折叠器已经证明 ended 的 owner。"""
    from novel.orchestrator import agent, jobs

    owners: dict[str, str] = {}
    for record in jobs.records(base):
        sid = record.start.sid
        if record.ended is not None and sid is not None:
            owners[f'{record.job_id}.log'] = paths.check_sid(sid)
    for record in agent.records(base):
        if record.ended is not None:
            owners[f'agent-{record.run_id}.log'] = paths.check_sid(record.start.sid)
    return owners


def _closed_telemetry(base: str | Path) -> dict[str, str]:
    from novel import operator_telemetry

    return {
        f'{record.session_id}.jsonl': paths.check_sid(record.sid)
        for record in operator_telemetry.fold(operator_telemetry.read_events(base))
        if record.ended_at_ms is not None
    }


def _delivered_runs(base: str | Path) -> dict[str, list[tuple[str, int]]]:
    from novel.orchestrator import agent

    found: dict[str, list[tuple[str, int]]] = {}
    for record in agent.records(base):
        if (record.ended is None or record.outcome is None
                or record.outcome.state not in ('ready', 'ready_with_disclosures')):
            continue
        sid = paths.check_sid(record.start.sid)
        found.setdefault(sid, []).append((record.run_id, record.ended))
    for rows in found.values():
        rows.sort(key=lambda row: row[1])
    return found


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{label} 不可读：{path}: {exc}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'{label} 不是 JSON object：{path}')
    return value


def _checkpoint_proof(
        base: str | Path, sid: str, checkpoint_dir: Path, *,
        delivered: Mapping[str, list[tuple[str, int]]], now: int,
        ttl_seconds: int, max_entries: int,
) -> dict[str, object] | None:
    """只接受 generic committed + 当前正式字节 + delivered 三面闭合的目录。"""
    from novel.llm import agent_runs

    commit = checkpoint_dir.parent / 'committed.json'
    if not commit.is_file() or commit.is_symlink():
        return None
    record = _read_object(commit, label='checkpoint commit journal')
    commit_sha = record.get('commit_sha256')
    post = record.get('post_file_sha256s')
    touched = record.get('touched_paths')
    landed = record.get('landed_payload_sha256s')
    if (set(record) != agent_runs._COMMIT_FIELDS
            or record.get('state') != 'committed'
            or record.get('sid') != sid
            or not isinstance(commit_sha, str) or len(commit_sha) != 64
            or any(ch not in '0123456789abcdef' for ch in commit_sha)
            or commit_sha != agent_runs._commit_hash(record)
            or not isinstance(touched, list) or not touched
            or any(not isinstance(rel, str) for rel in touched)
            or len(touched) != len(set(touched))
            or not isinstance(post, dict) or not post
            or sorted(post) != sorted(touched)
            or not isinstance(landed, dict)
            or any(not agent_runs.is_sha256(value)
                   for value in landed.values())):
        return None
    sid_root = paths.service_runs_dir(base) / sid
    try:
        run_rel = checkpoint_dir.relative_to(sid_root)
    except ValueError:
        return None
    # 现役 service run 的前两段恒为 stage/run_id；更深的 phase/chapter 是
    # 业务单元，不得拿它冒充 logical run identity。
    if (len(run_rel.parts) < 3
            or record.get('run_id') != run_rel.parts[1]):
        return None
    run_path = checkpoint_dir.parent / 'run.json'
    if not run_path.is_file() or run_path.is_symlink():
        return None
    run_record = _read_object(run_path, label='checkpoint run journal')
    if (set(run_record) != agent_runs._RUN_FIELDS
            or record.get('run_sha256') != _file_sha256(run_path)
            or run_record.get('version') != agent_runs.VERSION
            or run_record.get('run_id') != record.get('run_id')
            or run_record.get('sid') != sid
            or run_record.get('node') != record.get('node')
            or run_record.get('code') != record.get('code')):
        return None
    run_identity = {
        key: run_record[key] for key in agent_runs._RUN_FIELDS
        if key not in ('identity_sha256', 'created_at')
    }
    if run_record.get('identity_sha256') != agent_runs.canonical_sha256(
            run_identity):
        return None
    try:
        novel_dir = paths.resolve_novel_dir(base, sid)
    except Exception:  # noqa: BLE001 —— 已删书/坏目录不能成为 checkpoint 回收凭据
        return None
    formal: dict[str, str | None] = {}
    for rel, expected in sorted(post.items()):
        if not isinstance(rel, str):
            return None
        try:
            checked = paths.check_rel_deep(rel)
        except Exception:  # noqa: BLE001
            return None
        target = novel_dir / checked
        if expected is None:
            if target.exists():
                return None
            formal[checked] = None
            continue
        if (not isinstance(expected, str) or len(expected) != 64
                or any(ch not in '0123456789abcdef' for ch in expected)
                or not target.is_file() or target.is_symlink()
                or _file_sha256(target) != expected):
            return None
        formal[checked] = expected
    mtime, _size, _sha = _snapshot(
        checkpoint_dir, max_entries=max_entries)
    completion_mtime = max(mtime, commit.stat().st_mtime_ns)
    delivery = next((
        row for row in delivered.get(sid, [])
        if row[1] * 1_000_000_000 >= completion_mtime
        and _expired(row[1] * 1_000_000_000, now=now, ttl_seconds=ttl_seconds)
    ), None)
    if (delivery is None
            or not _expired(
                completion_mtime, now=now, ttl_seconds=ttl_seconds)):
        return None
    return {
        'commit_rel': commit.relative_to(Path(base)).as_posix(),
        'commit_file_sha256': _file_sha256(commit),
        'commit_sha256': commit_sha,
        'formal_file_sha256s': formal,
        'delivered_parent_run_id': delivery[0],
        'delivered_at': delivery[1],
    }


def plan_retention(
        base: str | Path, policy: RetentionPolicy | None = None, *,
        now: int | None = None,
) -> RetentionPlan:
    """只读生成维护计划；四个 TTL 默认关闭，且总扫描量有硬上限。"""
    configured = policy or RetentionPolicy()
    clock = int(time.time() if now is None else now)
    if clock < 0:
        raise ValueError('now 必须是非负 epoch 秒')
    candidates: list[RetentionCandidate] = []
    examined = 0

    def seen() -> None:
        nonlocal examined
        examined += 1
        if examined > configured.max_entries:
            raise RuntimeError(
                f'retention 扫描超过 max_entries={configured.max_entries}；'
                '计划未生成，也没有删除任何目标')

    if configured.log_ttl_seconds is not None:
        owners = _ended_log_owners(base)
        root = paths.service_dir(base) / 'logs'
        if root.is_dir() and not root.is_symlink():
            for target in sorted(root.iterdir(), key=lambda item: item.name):
                seen()
                sid = owners.get(target.name)
                if (sid is None or not target.is_file() or target.is_symlink()
                        or not _expired(
                            target.stat().st_mtime_ns, now=clock,
                            ttl_seconds=configured.log_ttl_seconds)):
                    continue
                candidates.append(_candidate(
                    base, kind='log', sid=sid, path=target,
                    evidence={'owner_state': 'ended'},
                    max_entries=configured.max_entries))

    if configured.telemetry_ttl_seconds is not None:
        closed = _closed_telemetry(base)
        root = paths.service_dir(base) / 'operator_phases'
        if root.is_dir() and not root.is_symlink():
            for target in sorted(root.iterdir(), key=lambda item: item.name):
                seen()
                sid = closed.get(target.name)
                if (sid is None or not target.is_file() or target.is_symlink()
                        or not _expired(
                            target.stat().st_mtime_ns, now=clock,
                            ttl_seconds=configured.telemetry_ttl_seconds)):
                    continue
                candidates.append(_candidate(
                    base, kind='operator_telemetry', sid=sid, path=target,
                    evidence={'session_state': 'ended'},
                    max_entries=configured.max_entries))

    if configured.trash_ttl_seconds is not None:
        from novel.orchestrator import trash

        root = paths.trash_dir(base)
        if root.is_dir() and not root.is_symlink():
            for item in sorted(root.iterdir(), key=lambda path: path.name):
                seen()
                proof = trash.complete_item_proof(
                    base, item.name, max_entries=configured.max_entries)
                if proof is None:
                    continue
                trashed_at = int(proof['trashed_at'])
                if clock - trashed_at < configured.trash_ttl_seconds:
                    continue
                candidates.append(_candidate(
                    base, kind='trash', sid=str(proof['sid']), path=item,
                    evidence=proof, max_entries=configured.max_entries))

    if configured.checkpoint_ttl_seconds is not None:
        delivered = _delivered_runs(base)
        root = paths.service_runs_dir(base)
        if root.is_dir() and not root.is_symlink():
            for sid_root in sorted(root.iterdir(), key=lambda path: path.name):
                seen()
                if not sid_root.is_dir() or sid_root.is_symlink():
                    continue
                try:
                    sid = paths.check_sid(sid_root.name)
                except PathRefused:
                    continue
                for target in sorted(
                        sid_root.rglob('checkpoints'), key=lambda path: path.as_posix()):
                    seen()
                    if not target.is_dir() or target.is_symlink():
                        continue
                    proof = _checkpoint_proof(
                        base, sid, target, delivered=delivered, now=clock,
                        ttl_seconds=configured.checkpoint_ttl_seconds,
                        max_entries=configured.max_entries)
                    if proof is None:
                        continue
                    candidates.append(_candidate(
                        base, kind='checkpoint', sid=sid, path=target,
                        evidence=proof, max_entries=configured.max_entries))

    candidates.sort(key=lambda item: (item.sid, item.kind, item.rel))
    if len(candidates) > configured.max_entries:
        raise RuntimeError('retention 候选超过 max_entries；计划未生成')
    return RetentionPlan.create(
        generated_at=clock, policy=configured, examined=examined,
        candidates=tuple(candidates))


def _recheck_candidate(
        base: str | Path, candidate: RetentionCandidate, *, max_entries: int,
) -> Path:
    target = _exact_service_target(base, candidate.rel)
    if not target.exists():
        raise RuntimeError(f'retention 目标已不存在，请重新 preview：{candidate.rel}')
    actual = _snapshot(target, max_entries=max_entries)
    expected = (
        candidate.observed_mtime_ns, candidate.observed_size,
        candidate.observed_sha256)
    if actual != expected:
        raise RuntimeError(f'retention 目标在 preview 后变化，请重新 preview：{candidate.rel}')
    return target


def _checkpoint_receipt(
        base: str | Path, candidate: RetentionCandidate, target: Path,
        *, max_entries: int, retired_at: int,
) -> Path:
    rows, total, _newest = _tree_rows(target, max_entries=max_entries)
    payload: dict[str, object] = {
        '_schema_version': 1, 'kind': 'checkpoint_retired',
        'sid': candidate.sid, 'checkpoint_rel': candidate.rel,
        'checkpoint_tree_sha256': candidate.observed_sha256,
        'checkpoint_files': rows, 'checkpoint_bytes': total,
        # 用 plan 的冻结时间，不用每次重试的墙钟；这样“receipt 已落、item
        # journal 未落”这一窄窗重启后仍落到同一内容地址，不制造重复小账。
        'evidence': dict(candidate.evidence), 'retired_at': retired_at,
        'receipt_sha256': '0' * 64,
    }
    payload['receipt_sha256'] = _sha256(_canonical({
        key: value for key, value in payload.items() if key != 'receipt_sha256'}))
    receipt = (
        paths.service_dir(base) / RUN_RECEIPTS_DIRNAME / candidate.sid
        / f"{payload['receipt_sha256']}.json")
    if receipt.exists():
        existing = _read_object(receipt, label='checkpoint retention receipt')
        if existing != payload:
            raise RuntimeError(f'同身份 checkpoint receipt 字节冲突：{receipt}')
    else:
        _atomic_json(receipt, payload)
    return receipt


def _sealed_lifecycle_record(payload: Mapping[str, object]) -> dict[str, object]:
    sealed = dict(payload)
    sealed['receipt_sha256'] = '0' * 64
    sealed['receipt_sha256'] = _sha256(_canonical({
        key: value for key, value in sealed.items()
        if key != 'receipt_sha256'}))
    return sealed


def _record_is_sealed(record: Mapping[str, object]) -> bool:
    return record.get('receipt_sha256') == _sha256(_canonical({
        key: value for key, value in record.items()
        if key != 'receipt_sha256'}))


def _item_receipt_path(audit: Path, ordinal: int) -> Path:
    return audit.parent / f'{audit.stem}.items' / f'{ordinal:04d}.json'


def _load_item_receipt(
        path: Path, *, plan_token: str, ordinal: int,
        candidate: RetentionCandidate,
) -> dict[str, object] | None:
    if not path.exists():
        return None
    record = _read_object(path, label='retention item receipt')
    prepared_fields = {
        '_schema_version', 'kind', 'state', 'plan_token', 'ordinal',
        'candidate', 'durable_receipts', 'prepared_at', 'receipt_sha256',
    }
    expected_fields = (
        prepared_fields | {'removed_at'}
        if record.get('state') == 'committed' else prepared_fields)
    refs = record.get('durable_receipts')
    if (set(record) != expected_fields
            or record.get('_schema_version') != 1
            or record.get('kind') != 'retention_item'
            or record.get('state') not in ('prepared', 'committed')
            or record.get('plan_token') != plan_token
            or record.get('ordinal') != ordinal
            or record.get('candidate') != candidate.to_dict()
            or not isinstance(refs, list)
            or any(not isinstance(rel, str) for rel in refs)
            or not _record_is_sealed(record)):
        raise RuntimeError(f'retention item receipt 身份或自哈希不闭合：{path}')
    return record


def _write_item_prepared(
        path: Path, *, plan_token: str, ordinal: int,
        candidate: RetentionCandidate, durable_receipts: list[str],
) -> dict[str, object]:
    record = _sealed_lifecycle_record({
        '_schema_version': 1, 'kind': 'retention_item',
        'state': 'prepared', 'plan_token': plan_token, 'ordinal': ordinal,
        'candidate': candidate.to_dict(),
        'durable_receipts': durable_receipts,
        'prepared_at': int(time.time()),
    })
    _atomic_json(path, record)
    return record


def _write_item_committed(
        path: Path, record: Mapping[str, object], *,
        durable_receipts: list[str],
) -> dict[str, object]:
    committed = _sealed_lifecycle_record({
        **record, 'state': 'committed',
        'durable_receipts': durable_receipts,
        'removed_at': int(time.time()),
    })
    _atomic_json(path, committed)
    return committed


def _validate_checkpoint_receipt(
        base: str | Path, candidate: RetentionCandidate, rel: str,
) -> None:
    receipt = _exact_service_target(base, rel)
    run_receipts = (
        paths.service_dir(base) / RUN_RECEIPTS_DIRNAME / candidate.sid).resolve()
    try:
        receipt.resolve().relative_to(run_receipts)
    except ValueError as exc:
        raise RuntimeError(
            f'checkpoint durable receipt 越出登记根：{rel}') from exc
    if not receipt.is_file() or receipt.is_symlink():
        raise RuntimeError(f'checkpoint durable receipt 缺失：{rel}')
    record = _read_object(receipt, label='checkpoint retention receipt')
    rows = record.get('checkpoint_files')
    rows_valid = isinstance(rows, list)
    file_bytes = 0
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or row.get('kind') not in ('dir', 'file'):
                rows_valid = False
                break
            if row.get('kind') == 'dir':
                if set(row) != {'kind', 'rel'} or not isinstance(row.get('rel'), str):
                    rows_valid = False
                    break
                continue
            size = row.get('size')
            sha = row.get('sha256')
            if (set(row) != {'kind', 'rel', 'size', 'sha256'}
                    or not isinstance(row.get('rel'), str)
                    or not isinstance(size, int) or isinstance(size, bool)
                    or size < 0 or not isinstance(sha, str) or len(sha) != 64
                    or any(ch not in '0123456789abcdef' for ch in sha)):
                rows_valid = False
                break
            file_bytes += size
    if (record.get('_schema_version') != 1
            or set(record) != {
                '_schema_version', 'kind', 'sid', 'checkpoint_rel',
                'checkpoint_tree_sha256', 'checkpoint_files',
                'checkpoint_bytes', 'evidence', 'retired_at',
                'receipt_sha256'}
            or record.get('kind') != 'checkpoint_retired'
            or record.get('sid') != candidate.sid
            or record.get('checkpoint_rel') != candidate.rel
            or record.get('checkpoint_tree_sha256')
            != candidate.observed_sha256
            or not rows_valid
            or _sha256(_canonical(rows)) != candidate.observed_sha256
            or record.get('checkpoint_bytes') != file_bytes
            or file_bytes != candidate.observed_size
            or record.get('evidence') != dict(candidate.evidence)
            or record.get('receipt_sha256') != _sha256(_canonical({
                key: value for key, value in record.items()
                if key != 'receipt_sha256'}))
            or receipt.stem != record.get('receipt_sha256')):
        raise RuntimeError(f'checkpoint durable receipt 身份不闭合：{rel}')


def _load_lifecycle_audit(
        audit: Path, plan: RetentionPlan,
) -> dict[str, object] | None:
    if not audit.exists():
        return None
    record = _read_object(audit, label='retention lifecycle receipt')
    prepared_fields = {
        '_schema_version', 'kind', 'state', 'plan', 'prepared_at',
        'receipt_sha256',
    }
    expected_fields = (
        prepared_fields | {'removed', 'receipts', 'committed_at'}
        if record.get('state') == 'committed' else prepared_fields)
    if (set(record) != expected_fields
            or record.get('_schema_version') != 2
            or record.get('kind') != 'retention_plan'
            or record.get('state') not in ('prepared', 'committed')
            or record.get('plan') != plan.to_dict()
            or not _record_is_sealed(record)):
        raise RuntimeError(f'retention lifecycle receipt 身份或自哈希不闭合：{audit}')
    if record.get('state') == 'committed':
        if (not isinstance(record.get('removed'), list)
                or not isinstance(record.get('receipts'), list)
                or any(not isinstance(rel, str)
                       for rel in record.get('removed', []))
                or any(not isinstance(rel, str)
                       for rel in record.get('receipts', []))):
            raise RuntimeError(f'retention committed receipt 形状不闭合：{audit}')
    return record


def apply_retention(
        base: str | Path, plan: RetentionPlan,
) -> RetentionApplyResult:
    """应用 exact preview；逐项 prepared 使部分删除可在重启后幂等补尾。"""
    # 重新走一次 wire 校验，避免调用方构造一个 token 与字段互相矛盾的 dataclass。
    checked = RetentionPlan.from_mapping(plan.to_dict())
    if len(checked.candidates) > checked.policy.max_entries:
        raise RuntimeError('retention apply 候选超过 policy.max_entries')
    if not checked.candidates:
        return RetentionApplyResult(
            plan_token=checked.plan_token, removed=(), receipts=())

    from novel.orchestrator import jobs, trash

    writer_id = 'service-retention-' + checked.plan_token[:16]
    held: list[tuple[str, str]] = []
    try:
        for sid in sorted({item.sid for item in checked.candidates}):
            key = jobs.acquire_job_writer(
                base, sid, 'service.retention', writer_id)
            held.append((key, sid))

        audit = (
            paths.service_dir(base) / 'lifecycle_receipts'
            / f'{checked.plan_token}.json')
        audit_record = _load_lifecycle_audit(audit, checked)
        if audit_record is not None and audit_record['state'] == 'committed':
            return RetentionApplyResult(
                plan_token=checked.plan_token,
                removed=tuple(audit_record['removed']),  # type: ignore[arg-type]
                receipts=tuple(audit_record['receipts']),  # type: ignore[arg-type]
            )

        item_records: list[dict[str, object] | None] = []
        targets: dict[str, Path] = {}
        for ordinal, item in enumerate(checked.candidates):
            receipt_path = _item_receipt_path(audit, ordinal)
            record = _load_item_receipt(
                receipt_path, plan_token=checked.plan_token,
                ordinal=ordinal, candidate=item)
            target = _exact_service_target(base, item.rel)
            if target.exists():
                if record is not None and record['state'] == 'committed':
                    raise RuntimeError(
                        f'已 committed 的 retention 目标重新出现，拒绝删除：{item.rel}')
                _recheck_candidate(
                    base, item, max_entries=checked.policy.max_entries)
                targets[item.rel] = target
            elif record is None:
                # aggregate prepared 本身只说明计划存在；没有逐项 prepared 就无法
                # 区分“本进程已删”与“外部把目标拿走了”。
                raise RuntimeError(
                    f'retention 目标缺失且没有 item prepared proof：{item.rel}')
            item_records.append(record)

        # 对仍存在、尚待删除的项重新证明“结束/closed/delivered+commit”；已删除项
        # 只能依赖删除前写入且自哈希闭合的逐项证据，不能伪造一次当前盘复检。
        log_owners = _ended_log_owners(base)
        closed = _closed_telemetry(base)
        delivered = _delivered_runs(base)
        for item in checked.candidates:
            target = targets.get(item.rel)
            if target is None:
                continue
            if item.kind == 'log' and log_owners.get(target.name) != item.sid:
                raise RuntimeError(f'日志 owner 已变化：{item.rel}')
            if (item.kind == 'operator_telemetry'
                    and closed.get(target.name) != item.sid):
                raise RuntimeError(f'telemetry session 不再可证 ended：{item.rel}')
            if item.kind == 'trash':
                proof = trash.complete_item_proof(
                    base, target.name,
                    max_entries=checked.policy.max_entries)
                if proof != dict(item.evidence):
                    raise RuntimeError(f'回收站 manifest 在 preview 后变化：{item.rel}')
            if item.kind == 'checkpoint':
                proof = _checkpoint_proof(
                    base, item.sid, target, delivered=delivered,
                    now=checked.generated_at,
                    ttl_seconds=checked.policy.checkpoint_ttl_seconds or 1,
                    max_entries=checked.policy.max_entries)
                if proof != dict(item.evidence):
                    raise RuntimeError(f'checkpoint commit/delivery 证明已变化：{item.rel}')

        if audit_record is None:
            audit_record = _sealed_lifecycle_record({
                '_schema_version': 2, 'kind': 'retention_plan',
                'state': 'prepared', 'plan': checked.to_dict(),
                'prepared_at': int(time.time()),
            })
            _atomic_json(audit, audit_record)

        removed: list[str] = []
        receipts: list[str] = []
        for ordinal, item in enumerate(checked.candidates):
            target = _exact_service_target(base, item.rel)
            item_path = _item_receipt_path(audit, ordinal)
            record = item_records[ordinal]
            durable_refs: list[str]
            if record is not None:
                durable_refs = list(record['durable_receipts'])  # type: ignore[arg-type]
            else:
                durable_refs = []
                if item.kind == 'checkpoint':
                    checkpoint_receipt = _checkpoint_receipt(
                        base, item, target,
                        max_entries=checked.policy.max_entries,
                        retired_at=checked.generated_at)
                    durable_refs.append(
                        checkpoint_receipt.relative_to(Path(base)).as_posix())
                record = _write_item_prepared(
                    item_path, plan_token=checked.plan_token,
                    ordinal=ordinal, candidate=item,
                    durable_receipts=durable_refs)

            if item.kind == 'checkpoint':
                if len(durable_refs) != 1:
                    raise RuntimeError(
                        f'checkpoint item receipt 缺唯一 durable receipt：{item.rel}')
                _validate_checkpoint_receipt(base, item, durable_refs[0])

            if record['state'] != 'committed':
                if item.kind in ('log', 'operator_telemetry'):
                    if target.exists():
                        target.unlink()
                elif item.kind == 'checkpoint':
                    if target.exists():
                        shutil.rmtree(target)
                else:
                    receipt = trash.purge_complete_item(
                        base, target.name,
                        expected_manifest_sha256=str(
                            item.evidence['manifest_file_sha256']),
                        max_entries=checked.policy.max_entries)
                    rel = receipt.relative_to(Path(base)).as_posix()
                    if rel not in durable_refs:
                        durable_refs.append(rel)
                record = _write_item_committed(
                    item_path, record, durable_receipts=durable_refs)

            # committed item 的 durable refs 也是 aggregate 结果的唯一来源；这样
            # aggregate 提交前崩溃不会靠重新扫描已删除正文来重建结果。
            for rel in record['durable_receipts']:  # type: ignore[union-attr]
                if rel not in receipts:
                    receipts.append(rel)
            removed.append(item.rel)
        committed_audit = _sealed_lifecycle_record({
            **audit_record, 'state': 'committed', 'removed': removed,
            'receipts': receipts, 'committed_at': int(time.time()),
        })
        _atomic_json(audit, committed_audit)
        return RetentionApplyResult(
            plan_token=checked.plan_token, removed=tuple(removed),
            receipts=tuple(receipts))
    finally:
        for key, _sid in reversed(held):
            jobs.REGISTRY.release(key, writer_id)


def load_retention_plan(path: str | Path) -> RetentionPlan:
    """从显式 plan 文件读取；解析/自哈希仍由本模块唯一承担。"""
    value = _read_object(Path(path), label='retention plan')
    return RetentionPlan.from_mapping(value)


__all__ = [
    'RETENTION_WIRE_VERSION', 'RUN_RECEIPTS_DIRNAME',
    'TRASH_RECEIPTS_DIRNAME', 'RetentionApplyResult', 'RetentionCandidate',
    'RetentionPlan', 'RetentionPolicy', 'ScratchSweepResult',
    'apply_retention', 'finish_scratch_session', 'open_scratch_session',
    'load_retention_plan', 'plan_retention', 'sweep_expired_scratch',
    'tree_identity',
]
