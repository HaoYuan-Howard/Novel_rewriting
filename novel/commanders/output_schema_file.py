#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander 原生 output schema 的受管临时文件生命周期。

这份文件不是小说产物、缓存或审计账，只是 Codex CLI 在一次 runner 生命周期内
读取的传输参数。独立成模块是为了把 scratch 裸 IO 的例外收窄在一个可审计边界，
避免给整个 provider driver 永久开放任意写盘能力。
"""
from __future__ import annotations

import os
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from novel.commanders.base import CommanderConfigurationError


def _write_all(descriptor: int, payload: bytes) -> None:
    """低层 ``os.write`` 允许部分写；schema 必须逐字节完整落盘。"""
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0 or written > len(remaining):
            raise OSError('Codex output schema 临时文件未完整写入')
        remaining = remaining[written:]


@contextmanager
def materialized_codex_output_schema(
        *, argv: Sequence[str], cwd: str, payload: bytes | None,
        digest: str | None,
) -> Iterator[None]:
    """在受管 scratch 中独占物化 Codex schema，并在 runner 后清理。"""
    positions = [
        index for index, item in enumerate(argv)
        if item == '--output-schema'
    ]
    if payload is None:
        if positions:
            raise CommanderConfigurationError(
                '无 output_schema 的 Codex invocation 不得携带 --output-schema')
        yield
        return
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise CommanderConfigurationError(
            'Codex schema invocation 必须且只能携带一个 --output-schema 路径')
    path = Path(argv[positions[0] + 1])
    scratch = Path(cwd).resolve()
    expected_name = f'.commander-output-schema-{digest}.json'
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise CommanderConfigurationError(
            'Codex output schema 的 scratch 父目录不可解析') from exc
    if (not path.is_absolute() or parent != scratch
            or path.name != expected_name):
        raise CommanderConfigurationError(
            'Codex output schema 路径必须是当前 scratch 内的冻结 hash 文件')

    # 服务/CLI 可能在创建 schema 后被硬杀，finally 没机会清理。路径只在本
    # epoch scratch，且同书 writer 保证这里没有并行 reviewer；因此只回收
    # “普通、非 symlink、字节逐字等于本次冻结 payload”的孤儿。任何异字节
    # 或异常文件类型都关闭式拒绝，不能把攻击者文件当缓存复用。
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise CommanderConfigurationError(
                'Codex output schema 残留不是普通文件')
        try:
            orphan = path.read_bytes()
        except OSError as exc:
            raise CommanderConfigurationError(
                'Codex output schema 残留无法读取') from exc
        if orphan != payload:
            raise CommanderConfigurationError(
                'Codex output schema 残留字节与冻结 schema 不一致')
        try:
            path.unlink()
        except OSError as exc:
            raise CommanderConfigurationError(
                'Codex output schema 同字节孤儿无法回收') from exc

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, 'O_BINARY', 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise CommanderConfigurationError(
            'Codex output schema 临时文件无法独占创建') from exc
    try:
        try:
            _write_all(descriptor, payload)
        finally:
            os.close(descriptor)
        try:
            if path.read_bytes() != payload:
                raise CommanderConfigurationError(
                    'Codex output schema 临时文件回读不一致')
            yield
        finally:
            try:
                path.unlink()
            except OSError as exc:
                # runner 已返回后不能让 housekeeping 覆盖 provider 的进程、
                # session 与 usage 事实。残留由下次同字节孤儿回收路径处理。
                warnings.warn(
                    'Codex output schema 临时文件清理失败；已保留本次 '
                    f'Commander 结果并等待下次安全回收：{exc}',
                    RuntimeWarning, stacklevel=2)
    except BaseException:
        # 写入/回读若在进入 yield 前失败，也不能把半截 schema 留在 scratch。
        if path.exists() or path.is_symlink():
            try:
                path.unlink()
            except OSError:
                pass
        raise
