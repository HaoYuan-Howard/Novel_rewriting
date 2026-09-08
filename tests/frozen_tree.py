"""测试上游基线：只保留不可变字节，每例还原到独立空目录。

基线必须由测试原有 producer/校验入口生成；这里只省去相同前置材料的重复生产。
不缓存运行中的 Store、工作单元对象、校验结论或生产 code_version，也不使用共享硬链接。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FrozenTree:
    directories: tuple[str, ...]
    files: tuple[tuple[str, bytes], ...]

    @classmethod
    def capture(cls, root: Path) -> FrozenTree:
        directories: list[str] = []
        files: list[tuple[str, bytes]] = []
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise ValueError('测试基线不能包含符号链接')
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                directories.append(rel)
            else:
                files.append((rel, path.read_bytes()))
        return cls(tuple(directories), tuple(files))

    def restore(self, root: Path) -> None:
        if root.exists() and any(root.iterdir()):
            raise ValueError('测试基线只允许还原到空目录')
        root.mkdir(parents=True, exist_ok=True)
        for rel in self.directories:
            (root / rel).mkdir(parents=True, exist_ok=True)
        for rel, raw in self.files:
            (root / rel).write_bytes(raw)
