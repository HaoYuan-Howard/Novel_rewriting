#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cache.py —— 缓存的**两个开关**。B2 会在它上面接真正的缓存文件读写。

【为什么必须是两个开关】原型只有一个 `--no-cache`，于是「重新采样一次，但仍然把
这一次的结果记下来」**表达不出来**。而那正是最常需要的一档：
`R-3.11` 明写「定向重生成必须配 `refresh-write`」——重生成的 prompt 与整跑时那一批
**逐字相同**，缓存必然命中，于是**一个字都没重新生成，而它照样打印「已重生成」**。
把读和写混成一件事，这条路就只剩两个都不对的选项：要么读到旧的，要么连账都不记。

⇒ `CachePolicy(read, write)`，两个方向永不合并（`R-12.4` 第一条）。
"""
from __future__ import annotations

from dataclasses import dataclass

from contracts.invariants import CACHE_POLICY

#: 三个具名口径 → `(read, write)`。**唯一落点**，`_provenance.attempts[].cache_policy`
#: 记的就是这里的名字（`contracts/schema/stage3.py::StyleAttempt`）。
_NAMES: dict[tuple[bool, bool], str] = {
    (True, True): 'default',        # 命中就用，没命中就跑并记下来
    (False, True): 'refresh-write',  # **重新采样，但仍然记账**——定向重生成走这条
    (False, False): 'disabled',      # `--no-cache`：两个方向一起关
}
assert set(_NAMES.values()) == set(CACHE_POLICY), \
    'CachePolicy 的具名口径与 contracts.invariants.CACHE_POLICY 分家了'


@dataclass(frozen=True)
class CachePolicy:
    """这一次跑读不读缓存 / 写不写缓存。

    ⚠ **第四种组合 `(read=True, write=False)` 被拒**，而理由不是「它没有意义」——
    是**它在 `CACHE_POLICY` 里没有名字**。一个记不进 `_provenance` 的口径，
    事后就没有任何东西说得出「那一次到底是怎么跑的」。
    真要它，就先往 `contracts/invariants.py::CACHE_POLICY` 加一个值——
    **那是一次契约改版，该被看见**，而不是在这里悄悄放行。
    """

    read: bool
    write: bool

    def __post_init__(self) -> None:
        if (self.read, self.write) not in _NAMES:
            raise ValueError(
                f'CachePolicy(read={self.read}, write={self.write}) 在 CACHE_POLICY 里没有名字。'
                f'现有三个：{sorted(_NAMES.values())}。'
                f'记不进 `_provenance` 的口径 = 事后说不出这一次是怎么跑的')

    @property
    def name(self) -> str:
        """进 `_provenance.attempts[].cache_policy` 的那个值。"""
        return _NAMES[(self.read, self.write)]

    # ── 三个具名构造。**调用方一律用它们**，别在别处手写 `CachePolicy(False, True)` ──

    @classmethod
    def default(cls) -> CachePolicy:
        return cls(read=True, write=True)

    @classmethod
    def disabled(cls) -> CachePolicy:
        """`--no-cache`：**同时**关读写（`R-12.4` 第一条明写「关缓存是同时关读写」）。"""
        return cls(read=False, write=False)

    @classmethod
    def refresh(cls) -> CachePolicy:
        """`--refresh`：重新采样，**但仍然记账**。

        ⚠ 它与 `disabled` 的差别在下一次跑上：`disabled` 跑完什么都没留下，
        下一次还得再花一次钱；`refresh` 留下了新的一份，下一次是 0 元。
        """
        return cls(read=False, write=True)

    @classmethod
    def of(cls, name: str) -> CachePolicy:
        """按 `CACHE_POLICY` 里的名字取。**读存量产物时走这条**（记的是名字不是两个 bool）。"""
        for combo, nm in _NAMES.items():
            if nm == name:
                return cls(read=combo[0], write=combo[1])
        raise ValueError(f'未知的缓存口径 {name!r}；取值面是 {sorted(_NAMES.values())}')
