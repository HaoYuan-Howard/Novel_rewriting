#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线整根归档：preview 冻结全部文件，apply --yes 同卷移动并启用空根。

调用前须停止服务、定时启动器和独立 CLI。工具不终止进程，不修复未知运行账，
不从旧 artifact 推断现役状态；停止证明在配对的干净旧 checkout 中运行。
所有围栏、清单、意图与回执均在三根之外，旧根内的文件一个也不改。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel import paths  # noqa: E402
from novel.output_root import canonical, read_sealed, seal  # noqa: E402


class ArchiveRefused(RuntimeError):
    """停止、身份或提交证据不成立；保持盘面等待同一事务恢复。"""


def _plain(path: Path) -> Path:
    """先检查原始路径的每一层，再 resolve，不能让 junction 被悄悄抹掉。"""
    if os.fspath(path).replace('/', '\\').startswith(('\\\\?\\', '\\\\.\\')):
        raise ArchiveRefused('外部路径不得使用 extended/device prefix 绕过边界校验')
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ArchiveRefused(f'路径包含链接或 reparse point：{part}')
    return path.resolve()


def _layout(source: Path, archive: Path, new_root: Path, journal: Path,
            old_code: Path) -> tuple[Path, ...]:
    roots = tuple(_plain(path) for path in (source, archive, new_root, journal, old_code))
    protected = (Path(__file__).resolve().parents[1], Path.home().resolve(), roots[4])
    for root in roots[:4]:
        if root == Path(root.anchor) or any(p == root or root in p.parents for p in protected):
            raise ArchiveRefused(f'拒绝把卷根、主目录或代码根作为移动/控制目录：{root}')
        if not root.parent.is_dir():
            raise ArchiveRefused(f'父目录必须预先存在：{root.parent}')
    for i, root in enumerate(roots[:4]):
        if roots[4] in root.parents:
            raise ArchiveRefused('输出根与事务目录须在配对旧代码 checkout 之外')
        for other in roots[i + 1:4]:
            if root == other or root in other.parents or other in root.parents:
                raise ArchiveRefused('旧根、归档根、新根与事务目录必须互不包含')
    if roots[0].parent.stat().st_dev != roots[1].parent.stat().st_dev:
        raise ArchiveRefused('整根归档只允许同卷原子移动，不回退为复制/删除')
    if roots[0].parent.stat().st_dev != roots[3].parent.stat().st_dev:
        raise ArchiveRefused('事务目录必须与旧根同卷，以维持同 inode 的外部 owner 锁')
    return roots


def _inventory(root: Path, *, locks: dict[str, BinaryIO] | None = None) -> dict:
    _plain(root)
    if not root.is_dir():
        raise ArchiveRefused(f'输出根不是普通目录：{root}')
    def unreadable(error: OSError) -> None:
        # os.walk 默认跳过无法列举的子目录；残缺清单不能作为整根归档证明。
        raise ArchiveRefused(f'无法完整列举输出根：{error.filename}') from error
    rows = []
    for current, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in sorted(dirs + files):
            item = Path(current) / name
            _plain(item)
            before = item.stat()
            rel = item.relative_to(root).as_posix()
            if stat.S_ISDIR(before.st_mode):
                rows.append({'path': rel, 'kind': 'directory'})
            elif stat.S_ISREG(before.st_mode):
                if before.st_nlink != (2 if rel in (locks or {}) else 1):
                    raise ArchiveRefused(f'文件存在硬链接别名，不能冻结独占整根：{rel}')
                digest = hashlib.sha256()
                # Windows byte-lock 是强制锁。扫描自身锁文件时复用持锁句柄，
                # 不能另开句柄读同一字节而把安全锁误判成盘面损坏。
                with ExitStack() as stack:
                    stream = (locks or {}).get(rel)
                    if stream is None:
                        stream = stack.enter_context(item.open('rb'))
                    stream.seek(0)
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                after = item.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                        after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ArchiveRefused(f'清单读取中发生写入：{rel}')
                rows.append({'path': rel, 'kind': 'file', 'bytes': after.st_size,
                             'sha256': digest.hexdigest()})
            else:
                raise ArchiveRefused(f'根内存在非常规文件：{rel}')
    rows.sort(key=lambda row: row['path'])
    return seal({'entries': rows, 'files': sum(row['kind'] == 'file' for row in rows),
                 'bytes': sum(row.get('bytes', 0) for row in rows)})


def _code_identity(root: Path) -> str:
    def git(*args: str) -> str:
        result = subprocess.run(
            ['git', '-c', f'safe.directory={root.as_posix()}', *args], cwd=root,
            capture_output=True, text=True, encoding='utf-8', check=False)
        if result.returncode:
            raise ArchiveRefused('配对旧代码必须是可核验的 Git checkout')
        return result.stdout.strip()
    if Path(git('rev-parse', '--show-toplevel')).resolve() != root:
        raise ArchiveRefused('old-code 必须精确指向代码 checkout 根')
    if git('status', '--porcelain', '--untracked-files=normal'):
        raise ArchiveRefused('配对旧代码必须干净，不能以当前脏目录作为回滚坐标')
    return git('rev-parse', 'HEAD')


# 这里只运行旧代码自身的生命周期判定，不 import/读取旧 artifact，也不调用
# snapshot/unresolved 等会创建锁文件的函数。未提交尾、孤儿事件和缺分母均拒绝。
_OLD_PROOF = '''
import hashlib, json, sys
from pathlib import Path
from novel.orchestrator import admission, agent, beacon, jobs
base = Path(sys.argv[1])
streams = [(admission.admission_path, admission.read_events),
           (beacon.beacon_path, beacon.read_events),
           (agent.runs_path, agent.read_events), (jobs.jobs_path, jobs.read_events)]
loaded, hashes = [], {}
for locate, read in streams:
    path = locate(base)
    raw = path.read_bytes() if path.is_file() else b''
    if raw and not raw.endswith(b'\\n'):
        raise ValueError('生命周期账存在未提交尾行')
    lines = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    events = read(base)
    if len(events) != len(lines):
        raise ValueError('生命周期解析遗漏事件')
    loaded.append(events)
    hashes[path.name] = hashlib.sha256(raw).hexdigest() if path.is_file() else None
adm, progress, agents, job_events = loaded
parents = sorted({e.parent_run_id for e in adm})
reconciled = []
for parent in parents:
    current = admission._fold(adm, parent)
    current = admission._validate_reconcile_seal(
        current, adm, admission._parent_beacon_events(progress, current.lifecycle_run_id))
    if not current.opened or not current.closed or current.terminal_kind is None:
        raise ValueError('admission 未明确排空或 seal 已失效')
    if current.terminal_kind == 'reconciled':
        reconciled.append({'parent_run_id': parent, 'lifecycle_run_id': current.lifecycle_run_id,
                           'subject_sha256': current.terminal_subject_sha256,
                           'unknown_claims': len(current.active_claims)})
sealed = {r['lifecycle_run_id'] for r in reconciled}
for run_id in {e.run_id for e in progress}:
    if run_id is None or (beacon._open_executions_from_events(progress, run_id) and run_id not in sealed):
        raise ValueError('存在未知或未闭合的 execution')
unknown_records = []
for events, fold, key in [(agents, agent.fold, 'run_id'), (job_events, jobs.fold, 'job_id')]:
    records = fold(events)
    if {getattr(e, key) for e in events} != {getattr(r, key) for r in records}:
        raise ValueError('运行账存在孤儿事件')
    for r in records:
        if r.ended is None or r.state in ('pending', 'running', 'paused', 'unknown'):
            lifecycle = r.run_id if key == 'run_id' else r.start.run_id
            if lifecycle not in sealed:
                raise ValueError('运行账缺少结束或当前 owner-stopped reconcile 证明')
            unknown_records.append({'kind': key, 'id': getattr(r, key), 'lifecycle_run_id': lifecycle})
print(json.dumps({'logs': hashes, 'admissions': len(parents),
                  'executions': sum(e.phase == 'start' for e in progress),
                  'agents': len(agent.fold(agents)), 'jobs': len(jobs.fold(job_events)),
                  'reconciled': reconciled, 'unknown_records': unknown_records}))
'''


def _old_proof(source: Path, old_code: Path) -> dict:
    if not (source / '_service').is_dir():
        raise ArchiveRefused('旧根缺少 _service，不能用空扫描推断已停机')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(old_code)}
    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', _OLD_PROOF, str(source)],
                            cwd=old_code, env=env, capture_output=True, timeout=60)
    if result.returncode:
        # 不把旧代码任意异常中的正文/参数扩散进维护输出。
        raise ArchiveRefused('旧代码无法证明运行账已闭合；先在旧代码中完成 stop/drain/reconcile')
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ArchiveRefused('旧代码停止证明不可读取') from exc


def _require_pid_stopped(pid: int) -> None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ArchiveRefused('stopped-pid 必须是正整数')
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        raise ArchiveRefused(f'声明停止的 PID {pid} 仍存活或无法核验')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise ArchiveRefused(f'无法核验 PID {pid} 是否退出')
    try:
        if kernel.WaitForSingleObject(handle, 0) != 0:
            raise ArchiveRefused(f'声明停止的 PID {pid} 仍存活')
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _offline(ports: list[int], stopped_pids: list[int]) -> Iterator[None]:
    if not ports or len(ports) != len(set(ports)):
        raise ArchiveRefused('必须明确列出不重复的全部服务端口')
    with ExitStack() as stack:
        for pid in stopped_pids:
            _require_pid_stopped(pid)
        for port in ports:
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise ArchiveRefused('服务端口必须在 1～65535 内')
            sock = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            if os.name == 'nt':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind(('127.0.0.1', port))
                sock.listen(1)
            except OSError as exc:
                raise ArchiveRefused(f'服务端口 {port} 仍被占用或无法冻结') from exc
        yield


def _open_lock(path: Path) -> BinaryIO:
    if os.name != 'nt':
        return path.open('r+b')
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    # 共享删除只让原子移动可以发生；byte-lock 仍排斥原 owner/writer。
    handle = create(str(path), 0x80000000 | 0x40000000, 1 | 2 | 4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, 'r+b')


@contextmanager
def _lock_existing(path: Path) -> Iterator[BinaryIO]:
    _plain(path)
    with _open_lock(path) as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ArchiveRefused(f'owner/writer 锁仍被持有或无法核验：{path}') from exc
        try:
            yield stream
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def _owners(root: Path, journal: Path) -> Iterator[dict[str, BinaryIO]]:
    """通过根外硬链接持有同一文件锁，移动期间不出现 owner 证明空窗。

    Windows 不允许移动内部有打开句柄的目录，即使 FILE_SHARE_DELETE 也不行。
    外部别名只用于原有锁文件：同 inode、同 byte-lock，零内容写入。别名路径
    固定在事务目录中，kill 后按 samefile 复验后重用；不能把任意链接当成锁。
    https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information
    """
    lease_root = _plain(journal / 'owner_handles')
    lease_root.mkdir(exist_ok=True)
    locks = sorted(root.rglob('*.lock'))
    expected = {hashlib.sha256(p.relative_to(root).as_posix().encode()).hexdigest() + '.lock'
                for p in locks}
    if any(p.name not in expected for p in lease_root.iterdir()):
        raise ArchiveRefused('事务目录含无法对应当前根的 owner 锁别名')
    owned: list[tuple[Path, Path]] = []
    try:
        with ExitStack() as stack:
            held = {}
            for path in locks:
                _plain(path)
                if not path.is_file():
                    raise ArchiveRefused('owner/writer 锁路径不是普通文件')
                rel = path.relative_to(root).as_posix()
                alias = _plain(lease_root / (hashlib.sha256(rel.encode()).hexdigest() + '.lock'))
                if alias.exists():
                    if not os.path.samefile(alias, path) or path.stat().st_nlink != 2:
                        raise ArchiveRefused('持久 owner 别名不再对应唯一原始 inode')
                else:
                    if path.stat().st_nlink != 1:
                        raise ArchiveRefused('owner 锁存在未登记的硬链接别名')
                    os.link(path, alias)
                owned.append((alias, path))
                held[rel] = stack.enter_context(_lock_existing(alias))
            yield held
    finally:
        # 句柄先由 ExitStack 关闭。别名只能删除精确持有项；不递归清扫未知文件。
        for alias, _original in owned:
            alias.unlink(missing_ok=True)


def _immutable(path: Path, value: dict) -> None:
    _plain(path)
    data = canonical(value)
    if path.exists():
        if path.read_bytes() != data:
            raise ArchiveRefused(f'已有不可变记录与本次意图不同：{path}')
        return
    fd, scratch = tempfile.mkstemp(prefix='.root-archive-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # link 是同卷、不覆盖的原子发布；kill 不会留下半份正式 JSON。
            os.link(scratch, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ArchiveRefused(f'并发维护记录发生冲突：{path}')
    finally:
        Path(scratch).unlink(missing_ok=True)


def preview(*, source: Path, archive: Path, new_root: Path, journal: Path,
            old_code: Path, ports: list[int], stopped_pids: list[int],
            actor: str, reason: str, offline_ack: bool) -> dict:
    if not offline_ack or not actor.strip() or not reason.strip():
        raise ArchiveRefused('须明确确认服务、调度器与独立 CLI 已停止，并填写 actor/reason')
    source, archive, new_root, journal, old_code = _layout(
        source, archive, new_root, journal, old_code)
    if archive.exists() or new_root.exists() or (journal / 'preview.json').exists():
        raise ArchiveRefused('preview 要求归档根、新根不存在；完整 preview 已存在时直接 apply')
    for root in (source, archive, new_root):
        if paths.output_root_fence_path(root).exists():
            raise ArchiveRefused('输出根已有切换围栏，请使用原事务恢复')
    code = _code_identity(old_code)
    request = seal({'source': str(source), 'archive': str(archive), 'new_root': str(new_root),
                    'journal': str(journal), 'old_code': str(old_code), 'old_commit': code,
                    'ports': ports, 'stopped_pids': stopped_pids, 'offline_ack': True,
                    'actor': actor.strip(), 'reason': reason.strip()})
    if journal.exists():
        if not (journal / 'request.json').is_file() or read_sealed(journal / 'request.json') != request:
            raise ArchiveRefused('已有事务目录不属于本次 preview 请求')
    else:
        journal.mkdir()
    _immutable(journal / 'request.json', request)
    with _offline(ports, stopped_pids), _owners(source, journal) as held:
        inventory = _inventory(source, locks=held)
        proof = _old_proof(source, old_code)
        if inventory != _inventory(source, locks=held) or code != _code_identity(old_code):
            raise ArchiveRefused('预览过程中旧根或旧代码发生变化')
    plan = seal({'version': 1, 'source': str(source), 'archive': str(archive),
                 'new_root': str(new_root), 'journal': str(journal), 'old_code': str(old_code),
                 'old_commit': code, 'inventory': inventory, 'stopped': proof,
                 'ports': ports, 'stopped_pids': stopped_pids, 'offline_ack': True,
                 'actor': actor.strip(), 'reason': reason.strip(), 'created_at': int(time.time())})
    _immutable(journal / 'preview.json', plan)
    return plan


def _fences(plan: dict) -> None:
    for role in ('source', 'archive', 'new_root'):
        root = Path(plan[role])
        _immutable(paths.output_root_fence_path(root), seal({
            'version': 1, 'root': str(root), 'role': role,
            'journal': plan['journal'], 'plan_sha256': plan['sha256']}))


def _move(source: Path, archive: Path) -> None:
    """最终路径复验后只做一次同卷 rename；绝不退回 shutil.move。"""
    _plain(source)
    _plain(archive)
    if archive.exists():
        raise ArchiveRefused('归档目标已存在，拒绝覆盖')
    if source.stat().st_dev != archive.parent.stat().st_dev:
        raise ArchiveRefused('移动前卷身份改变，拒绝跨卷操作')
    if os.name == 'nt':
        os.rename(source, archive)
        return
    # POSIX rename 可覆盖恰好竞态创建的空目录；必须用内核 no-replace 语义。
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, 'renameat2', None)
    if rename is None:
        raise ArchiveRefused('当前宿主缺少原子 no-replace 目录移动入口')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    if rename(-100, os.fsencode(source), -100, os.fsencode(archive), 1):
        raise OSError(ctypes.get_errno(), '整根 no-replace 移动失败')


def apply(journal: Path, *, expected_sha256: str, yes: bool) -> dict:
    if not yes:
        raise ArchiveRefused('正式移动必须显式 --yes 并回传 preview SHA-256')
    journal = _plain(journal)
    plan = read_sealed(journal / 'preview.json')
    fields = {'version', 'source', 'archive', 'new_root', 'journal', 'old_code', 'old_commit',
              'inventory', 'stopped', 'ports', 'stopped_pids', 'offline_ack',
              'actor', 'reason', 'created_at', 'sha256'}
    if (set(plan) != fields or type(plan['version']) is not int or plan['version'] != 1
            or plan['offline_ack'] is not True
            or plan['sha256'] != expected_sha256 or Path(plan['journal']) != journal):
        raise ArchiveRefused('preview 字段、版本、路径或回传摘要不匹配')
    source, archive, new_root, journal, old_code = _layout(
        *(Path(plan[key]) for key in ('source', 'archive', 'new_root', 'journal', 'old_code')))
    intent = seal({'version': 1, 'state': 'prepared', 'plan_sha256': plan['sha256']})
    receipt = seal({'version': 1, 'state': 'committed', 'plan_sha256': plan['sha256']})
    lock = journal / 'apply.lock'
    _immutable(lock, {'version': 1})
    with _lock_existing(lock):
        if (journal / 'committed.json').exists():
            with _owners(archive, journal) as held:
                if (read_sealed(journal / 'committed.json') != receipt or source.exists()
                        or _inventory(archive, locks=held) != plan['inventory'] or not new_root.is_dir()):
                    raise ArchiveRefused('已提交事务的盘面或回执发生漂移')
            _fences(plan)
            return receipt
        prepared = journal / 'prepared.json'
        if prepared.exists():
            if read_sealed(prepared) != intent:
                raise ArchiveRefused('prepared 不属于原 preview')
        elif archive.exists() or new_root.exists():
            raise ArchiveRefused('无原 prepared 证明，不采用已存在的目标目录')
        if source.exists() == archive.exists():
            raise ArchiveRefused('旧根与归档根必须恰有一个；双份或缺失均保持未知')
        if _code_identity(old_code) != plan['old_commit']:
            raise ArchiveRefused('配对旧代码已改变')
        with _offline(plan['ports'], plan['stopped_pids']):
            current = source if source.exists() else archive
            with _owners(current, journal) as held:
                if _inventory(current, locks=held) != plan['inventory']:
                    raise ArchiveRefused('整根清单已变化；不移动、不覆盖、不猜测')
                if _old_proof(current, old_code) != plan['stopped']:
                    raise ArchiveRefused('停止证明与 preview 不同')
                _immutable(prepared, intent)
                _fences(plan)
                if current == source:
                    _move(source, archive)
                if source.exists() or _inventory(archive, locks=held) != plan['inventory']:
                    raise ArchiveRefused('移动后的整根摘要不闭合，保留 prepared 等待核查')
                if new_root.exists():
                    if not new_root.is_dir() or any(new_root.iterdir()):
                        raise ArchiveRefused('未提交新根已被写入，拒绝将其宣布为空根')
                else:
                    new_root.mkdir()
                if _code_identity(old_code) != plan['old_commit']:
                    raise ArchiveRefused('归档提交前旧代码改变')
                _immutable(journal / 'committed.json', receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pre = commands.add_parser('preview')
    for name in ('source', 'archive', 'new-root', 'journal', 'old-code'):
        pre.add_argument('--' + name, type=Path, required=True)
    pre.add_argument('--port', type=int, action='append', required=True, dest='ports')
    pre.add_argument('--stopped-pid', type=int, action='append', default=[], dest='stopped_pids')
    pre.add_argument('--actor', required=True)
    pre.add_argument('--reason', required=True)
    pre.add_argument('--offline-ack', action='store_true')
    run = commands.add_parser('apply')
    run.add_argument('--journal', type=Path, required=True)
    run.add_argument('--expected-sha256', required=True)
    run.add_argument('--yes', action='store_true')
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        result = preview(**args) if command == 'preview' else apply(**args)
    except (ArchiveRefused, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'整根归档拒绝：{exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
