#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cache.py —— 缓存的**两个开关**，以及盘上那份响应缓存。

【为什么必须是两个开关】原型只有一个 `--no-cache`，于是「重新采样一次，但仍然把
这一次的结果记下来」**表达不出来**。而那正是最常需要的一档：
`R-3.11` 明写「定向重生成必须配 `refresh-write`」——重生成的 prompt 与整跑时那一批
**逐字相同**，缓存必然命中，于是**一个字都没重新生成，而它照样打印「已重生成」**。
把读和写混成一件事，这条路就只剩两个都不对的选项：要么读到旧的，要么连账都不记。

⇒ `CachePolicy(read, write)`，两个方向永不合并（`R-12.4` 第一条）。

【缓存里的东西与产物不同，所以纪律也不同】
产物「修不好的原样留着」（判据 7）；缓存里一份**已判定不可用**的响应必须**删掉**——
留着它，下一次「重试」会退化成把同一个坏响应读第二遍，
**而那与一次真调用长得一模一样**。见 `ResponseCache.evict`。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from contracts.invariants import CACHE_POLICY
from novel.llm.transport import ChatRequest, ChatResponse

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


# ─────────────────────────────────────────────────────────────────────────────
# 盘上那份
# ─────────────────────────────────────────────────────────────────────────────

def cache_key(req: ChatRequest) -> str:
    """一次调用的缓存键。**阶梯的每一档各自成键**（`reasoning_effort` 进键）——
    不然降级到第二档时会读回第一档那个空壳，「重试」变成把同一个结果读两遍。

    边界裁决（TODO-08）：键只含真正影响响应的请求载荷，不含 git/worktree 身份。
    system/user 都在 ``cache_payload``，所以 prompt 改动必定 miss；无关文档或测试改动
    只改变 provenance 审计身份，不击穿全部响应缓存。
    """
    blob = json.dumps(req.cache_payload(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:32]


@dataclass
class ResponseCache:
    """响应缓存。`dir` 给了就落盘，`dir=None` **只走内存**（测试 / `--dry-run`），语义完全一样。

    ⚠ 它**不是产物**：不进 `_provenance`、不进来源账、`.gitignore` 盖着。
    唯一的作用是让「参数不变的重跑」变成 0 元——而那件事必须被**说出来**
    （`R-12.4` / `Q-11`），所以命中要记进 `CostLedger.calls_cached`。

    ⚠⚠ **`dir=None` 曾经是「一个字都不缓存」，而 docstring 写着「语义完全一样」。**
    `_path` 返回 `None` ⇒ `get` 恒 `None` · `put` 恒 `False` · `evict` 什么也不做。
    而 `LlmClient` 的默认缓存**恰好就是这一档**：于是「四档阶梯各自成键」
    「空壳踢出缓存」「命中记进 `calls_cached`」这些行为在默认配置下**一条都不成立**，
    却没有任何测试会红——它们全都在断言一个从不发生的事。
    ⇒ 内存那一档改成真的（一个 dict），三个方法逐条对齐盘上那一份。

    ⚠ 作用域是**实例**：`LlmClient` 一个 client 造一份 ⇒ 一次跑内去重，跑完就没。
    这正是 `--dry-run` 要的那一档（不落盘，但同一次跑里问两遍同一个问题只算一次）。
    """

    dir: Path | None = None

    #: `dir=None` 时的那一份。**不参与 `repr`**——里面是整段响应正文。
    _mem: dict[str, ChatResponse] = field(default_factory=dict, repr=False)

    def get(self, key: str) -> ChatResponse | None:
        if self.dir is None:
            return self._mem.get(key)
        p = self._path(key)
        if p is None or not p.is_file():
            return None
        try:
            raw = json.loads(p.read_bytes().decode('utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # ⚠ **读不出来当没有，不抛。** 一份半截的缓存文件如果抛异常，
            #   那个异常会被当成「这次调用失败」——把一次**已经付过钱**的结果变成一次重跑。
            #   （原型正因如此才给缓存加了原子写，见 `put`。）
            return None
        # ⚠ **结构过期也当没有。** 合法 JSON 不等于读得回 `ChatResponse`：
        #   `{}` 会让 `ChatResponse(**raw)` 抛 `TypeError`（少必填参数），
        #   而那个异常同样会被当成「这次调用失败」。缓存的形态改过一次，
        #   盘上就会留着一批这样的旧文件——它们该是 miss，不该是故障。
        if not isinstance(raw, dict):
            return None
        try:
            return ChatResponse(**raw)
        except TypeError:
            return None

    def put(self, key: str, resp: ChatResponse) -> bool:
        """写一份。**空 content 永不写**，返回「到底写没写」。

        ⚠ 实测 DeepSeek 会把整段答案写进 `reasoning_content`、`content` 返回空串
        （`finish_reason='stop'`，**不是截断**）。空回答对任何调用方都无用，
        缓存下来只会让重试**每次都命中同一个空壳**——那时连降级阶梯都救不了它，
        因为四档都会各自读回自己那份空的。
        """
        if not resp.text:
            return False
        if self.dir is None:
            self._mem[key] = resp
            return True
        p = self._path(key)
        if p is None:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(resp.__dict__, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        # 【原子写】调用方会**并发**跑（并发闸默认 2），而「先截断再写」留下的是半截 JSON。
        # 坏处不是丢一次缓存，是下次读到它——见 `get` 里那条注释。
        # 同一进程的章节线程可能同时碰同一个 key（例如重复任务）。PID 不足以区分
        # 临时文件；加线程 id 后仍保持同目录原子替换。
        tmp = p.with_name(f'{p.name}.{os.getpid()}.{threading.get_ident()}.tmp')
        try:
            tmp.write_bytes(data)
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)
        return True

    def evict(self, key: str) -> None:
        """把一份**已经判定为不可用**的响应删掉（由两个入口的每一条不可用路径调）。

        ⚠ 这里**不适用**产物那条「修不了的原样留着」（判据 7）：
        产物留着是给人看的，缓存留着**不会被人看见**，只会被下一次重跑静默地当成新结果。
        实测形态：模型返回 `{"reconstruction_strategy": "…"}`——**0 个输出章**，
        合法 JSON、正常结束，于是一路放行**并进了缓存**；此后不带 `--no-cache` 的重跑
        只会拿回同一个空壳（「参数不变的重跑什么都没跑」）。
        """
        if self.dir is None:
            self._mem.pop(key, None)
            return
        p = self._path(key)
        if p is not None:
            p.unlink(missing_ok=True)

    def _path(self, key: str) -> Path | None:
        return None if self.dir is None else Path(self.dir) / f'{key}.json'
