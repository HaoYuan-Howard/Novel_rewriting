#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prov.py —— 来源账的**纯运算**：指纹怎么算 · 新鲜度怎么判 · 下一轮是第几轮。

【为什么这一份不碰盘】新鲜度是 `Q-2` 四个落点里最贵的一个，而它此前的实现
与「怎么找文件」绑在一起，于是**判定逻辑没有一条测试能单独跑**。
这里把「取指纹」抽成两个回调（`file_now` / `consumed_now`），
`novel/store.py` 负责摸盘、本模块只负责判——于是那张表（`_common.md` §四）
可以逐行写成断言，一分钱不花、一个文件不落。

⚠ **本模块不许 `json.load`**（`tests/test_layering.py` 钉着）：读盘只有 store 那一条路，
而那条路记账。这里只 `json.dumps`——投影指纹要一个**确定性**的字节形态。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Iterable, Literal

from contracts.invariants import FRESHNESS, FRESHNESS_REASON

# ─────────────────────────────────────────────────────────────────────────────
# 一、两把尺子 —— 40 位与 10 位，**故意的两把**
# ─────────────────────────────────────────────────────────────────────────────
#
# 规则只有一条：**10 位永不与 40 位比较**（`_common.md` §二）。
# 载体层已经把它做成两个受约束类型（`Fp10` / `Sha1`），混用在建模期就是类型错误。
# 这里是它们**唯一**的产地：两个函数各自只产一种宽度，谁也调不出「40 位的来源指纹」。
#
# ⚠ 原型的病不是「有两个宽度」，是**同一个字段在两处用不同宽度**
#   （`n1_partition` 记全宽而 `stage2_common` 记 10 位），
#   于是同一份文件的指纹在两份产物里长得不一样，谁也不敢拿它们对比。

_FP10_WIDTH = 10


def fingerprint(*parts: str) -> str:
    """把若干段文本压成一个 **10 位**来源指纹。

    prompt 常量、拼好的 user 消息、投影后的 JSON 都可以喂进来。
    ⚠ 拼接不加分隔符是**照抄原型**的，别顺手「修」它：改了分隔法，
    全链已落盘产物里的每一个 `prompts.*` 指纹都会与新算法对不上，
    而那会把一批「这份稿是拿哪一版 prompt 产的」的答案静默作废。
    """
    return hashlib.sha1(''.join(parts).encode('utf-8')).hexdigest()[:_FP10_WIDTH]


def fingerprint_bytes(data: bytes) -> str:
    """盘上字节 → **10 位**来源指纹。`inputs[].file` 走这条。"""
    return hashlib.sha1(data).hexdigest()[:_FP10_WIDTH]


def content_sha1(data: bytes) -> str:
    """**40 位全宽**内容身份。

    它回答「**有人换过原文吗**」，人可能拿 `sha1sum` 手工复核——**截断买不到任何东西**。
    用在 `stage0.source_sha1` / `clean_sha1` 与 `stage7.book_txt` 的指纹上。
    """
    return hashlib.sha1(data).hexdigest()


def projection_fingerprint(obj: Any) -> str:
    """**本节点真正读到的字段投影** → 10 位指纹（`inputs[].consumed`）。

    ⚠ 序列化必须是**确定性**的，否则同一份投影在两次跑里指纹不同，
    而那会让每一条边都显示 `stale`——一个恒响的告警等于没有告警。
    ⇒ `sort_keys` + 无空格 + 不转义非 ASCII（中文原样进哈希，别让 `ensure_ascii`
    把同一段文字变成两种字节）。
    """
    return fingerprint(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                  separators=(',', ':')))


# ─────────────────────────────────────────────────────────────────────────────
# 二、`code` —— `git describe --always --dirty`，全链一个实现
# ─────────────────────────────────────────────────────────────────────────────

#: git 都问不到时记什么。**不许留空**（`code` 是必填），也不许编一个像 sha 的值。
#: `unknown` 与任何一个 git 输出都不会撞，读的人一眼看得出「这次跑说不出自己是哪版代码」。
CODE_UNKNOWN = 'unknown'

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@lru_cache(maxsize=1)
def code_version() -> str:
    """当前代码版本。**一次跑只问一次**（`RunContext.code` 把它固化成 run 级的常量）。

    ⚠⚠ **必须带 `--dirty`。** 原来是 `git rev-parse --short HEAD`，
    而**工作树脏的时候它照样给出一个看起来精确的答案**：stage6 盘上有逐字节铁证——
    同一份 `_provenance` 里，按内容算的 prompt 指纹变了，
    而按 git HEAD 算的 `code` **一个字没动**。
    加 `--dirty` 之后，「这次跑用的代码不在任何一个 commit 上」能被读出来。

    ⚠ 它仍然只是**提示**，不是身份：`--dirty` 说不出脏在哪。
    这条已经登记在 `_common.md` §十「查清楚了、结论就是查不了」那一节，别再当漏项。
    """
    try:
        out = subprocess.run(['git', 'describe', '--always', '--dirty'],
                             cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return CODE_UNKNOWN
    return out.stdout.strip() or CODE_UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# 三、新鲜度 —— `_common.md` §四那张表，逐行
# ─────────────────────────────────────────────────────────────────────────────

Freshness = Literal['fresh', 'stale', 'unknown']

#: **优先级 `stale` > `unknown` > `fresh`。**
#: 「不知道」永远不许被并进「没问题」——本项目每一次这么并都付过学费。
_RANK: dict[str, int] = {'stale': 2, 'unknown': 1, 'fresh': 0}
assert set(_RANK) == set(FRESHNESS), '汇总优先级表与 FRESHNESS 分家了'

#: 每个 reason 得到哪个结论。**表在这里，判定不在分支里**——
#: 分散在 if/else 里的映射改一处漏一处，而这张表可以被测试逐条覆盖。
_REASON_STATE: dict[str, Freshness] = {
    'no_provenance': 'unknown',
    'no_inputs': 'unknown',
    'ghost': 'unknown',
    'file_changed': 'stale',
    'consumed_changed': 'stale',
    'consumed_undeclared': 'unknown',
    'consumed_unresolved': 'unknown',
    'match': 'fresh',
}
assert set(_REASON_STATE) == set(FRESHNESS_REASON), \
    '_REASON_STATE 与 contracts.invariants.FRESHNESS_REASON 分家了'


@dataclass(frozen=True)
class EdgeVerdict:
    """一条来源边的判定。**机器可读**：`reason` 是 id 不是措辞。"""

    key: str                      # `<stage>/<文件名>`
    state: Freshness
    reason: str                   # FRESHNESS_REASON 之一
    recorded_file: str | None     # 产物里记的整份指纹
    now_file: str | None          # 盘上现在的整份指纹（`None` = 文件不在盘上）
    recorded_consumed: str | None
    now_consumed: str | None

    @property
    def is_ghost(self) -> bool:
        return self.reason == 'ghost'


@dataclass(frozen=True)
class FreshnessReport:
    """一份产物的来源判定。**报告的第一节恒是它**（`Q-4`：来源在前，判定在后）。

    ⚠ **空也要报。** 对不上时报出来的一堆违规是「拿新尺子量旧布」，
    不说破就会被当成一次回归（`R-5.10` 就是补这一行的）。
    """

    state: Freshness
    reason: str                          # 汇总结论的**代表**原因（取最坏那条边的）
    edges: tuple[EdgeVerdict, ...] = ()

    @property
    def ghosts(self) -> tuple[str, ...]:
        """指向已经不在盘上的文件的那些边，**按名字**。

        ⚠ 这是 `Q-4` 亲自点过的 bug：原型对「取不到指纹」的条目**静默跳过**。
        点名不是锦上添花——不说是哪一条，人就无从判断这条边要不要重新挂钩。
        """
        return tuple(e.key for e in self.edges if e.is_ghost)

    @property
    def stale_edges(self) -> tuple[EdgeVerdict, ...]:
        return tuple(e for e in self.edges if e.state == 'stale')


def _worst(edges: Iterable[EdgeVerdict]) -> tuple[Freshness, str]:
    worst: EdgeVerdict | None = None
    for e in edges:
        if worst is None or _RANK[e.state] > _RANK[worst.state]:
            worst = e
    return ('fresh', 'match') if worst is None else (worst.state, worst.reason)


def freshness(
    recorded: dict[str, Any] | None,
    *,
    file_now: Callable[[str], str | None],
    consumed_now: Callable[[str], str | None] = lambda _key: None,
) -> FreshnessReport:
    """产物记的上游 vs 盘上**现在**的上游。`_common.md` §四那张表的唯一实现。

    参数：
      `recorded`     `_provenance.inputs`，形如 `{键: {'file': fp10, 'consumed': fp10|None}}`。
                     **`None` 与 `{}` 是两种不同的输入**，见下面前两条。
      `file_now`     键 → 盘上那份现在的整份指纹；**文件不在盘上返回 `None`**（幽灵边）。
      `consumed_now` 键 → **拿本节点的投影函数重算一遍**当前上游得到的指纹；
                     算不出来（没登记投影）返回 `None`。
                     ⚠ 默认实现恒返回 `None` —— **建槽位，不填槽位**：
                     八层不必同时交这份作业，在交之前那条边诚实地是 `unknown`。

    ⚠⚠ **`consumed` 对得上就是 `fresh`，即使整份文件的指纹变了**——那正是这一格
    存在的全部理由（`Q-4`：「指纹是文件级的：改一个不相干的字段会让整份产物的指纹失配，
    把一批结论降级成『读数不可用』，且没有重新挂钩的路」）。
    ⇒ **判定顺序是 consumed 优先于 file，不是反过来。**
    """
    if recorded is None:
        return FreshnessReport('unknown', 'no_provenance')
    if not recorded:
        return FreshnessReport('unknown', 'no_inputs')

    edges: list[EdgeVerdict] = []
    for key in sorted(recorded):
        edge = recorded[key] or {}
        was_file = edge.get('file')
        was_consumed = edge.get('consumed')
        now_file = file_now(key)

        if now_file is None:
            # 幽灵边：那份上游已经不在盘上。**不是「对得上」，也不是「不匹配」。**
            edges.append(_verdict(key, 'ghost', was_file, None, was_consumed, None))
            continue

        if was_consumed is not None:
            now_consumed = consumed_now(key)
            if now_consumed is None:
                # 声明过投影，但这次算不出来。退回文件级、并且**只能降级不能升级**：
                # 文件变了照旧 stale；文件没变也只到 unknown（我们没验过投影）。
                reason = 'file_changed' if was_file != now_file else 'consumed_unresolved'
            elif now_consumed == was_consumed:
                reason = 'match'
            else:
                reason = 'consumed_changed'
            edges.append(_verdict(key, reason, was_file, now_file, was_consumed, now_consumed))
            continue

        # `consumed` 是 null：「没声明读了什么」不是「读的东西没变」。
        reason = 'file_changed' if was_file != now_file else 'consumed_undeclared'
        edges.append(_verdict(key, reason, was_file, now_file, was_consumed, None))

    state, reason = _worst(edges)
    return FreshnessReport(state, reason, tuple(edges))


def _verdict(key: str, reason: str, was_file: str | None, now_file: str | None,
             was_consumed: str | None, now_consumed: str | None) -> EdgeVerdict:
    return EdgeVerdict(key=key, state=_REASON_STATE[reason], reason=reason,
                       recorded_file=was_file, now_file=now_file,
                       recorded_consumed=was_consumed, now_consumed=now_consumed)


# ─────────────────────────────────────────────────────────────────────────────
# 四、轮次
# ─────────────────────────────────────────────────────────────────────────────

def next_round(seen: Iterable[int]) -> int:
    """下一轮 = **盘上见过的最大轮次 + 1**（含 `.INVALID` 与 `_rounds/`）。

    ⚠⚠ **不是 `len(glob(...)) + 1`。** 那是原型两个节点的写法，它有一个**静默的**
    失效模式：归档目录里一旦少了一份（人挪走、手工清理、分叉），计数就会退回去，
    下一轮与现存的某一轮**同号**，历史从那里断掉——而这个错误不会报任何东西，
    它只是安静地覆盖掉一份归档。

    ⚠ 也不能只看「我刚刚归档掉的那一份」：归档与落盘之间隔着好几条路
    （先归档再写 · 失败后重来 · 人工挪过文件）。只按刚归档那份算，轮次会**退回 1**，
    而 `_rounds/` 里明明还躺着一个 r1 ⇒ 盘上出现两份 r1，
    而收敛判定正是靠轮次识别「这一层又落了一次笔」。
    """
    return max({0, *seen}) + 1
