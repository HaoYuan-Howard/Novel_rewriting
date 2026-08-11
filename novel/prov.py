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


def code_version() -> str:
    """当前代码版本。**一次跑只问一次**（`RunContext.code` 把它固化成 run 级的常量）。

    ⚠⚠ **「一次跑一次」不许用 `lru_cache` 实现。** 这里原来挂着 `@lru_cache(maxsize=1)`，
    而零参函数上的 `lru_cache` 是**进程级**不是 run 级：长驻服务里
    **第一次跑问到的答案会被盖到此后每一份产物上**——包括它那个 `-dirty` 后缀在不在。
    于是「跑到一半有人 commit 了 / 改了工作树」这件事再也读不出来，
    正是加 `--dirty` 要消灭的那个失败，换了个更隐蔽的形态回来。
    ⇒ 固化的落点是 `RunContext.code`（一个 run 级的字段），它本来就只问一次；
    本函数保持无状态。代价是每个 run 多一次子进程（~10-30ms），买的是「这次跑用的是哪版代码」。
    ⚠ **不许**改成给 `RunContext.open` 加一个 `code=` 参数来省这次子进程——
    那会造出一条能对「哪版代码产了它」撒谎的路，而这一格的全部价值就在于它不会撒谎。

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


def _edge_of(value: Any) -> dict[str, str | None]:
    """一条边的任意盘上形态 → `{'file': fp10|None, 'consumed': fp10|None}`。"""
    if isinstance(value, str):
        # **旧形态**：`inputs` 的值是一个裸的 10 位指纹。`consumed=null` 的含义是
        # 「本节点没有声明自己读了哪些字段」⇒ 该边 `unknown`，**不是 `fresh`**。
        return {'file': value, 'consumed': None}
    if isinstance(value, dict):
        f, c = value.get('file'), value.get('consumed')
        return {'file': f if isinstance(f, str) else None,
                'consumed': c if isinstance(c, str) else None}
    # 真正畸形的那些（`int` / `list` / `None`）。`file=None` 会走 `file_changed` ⇒ **stale**。
    # ⚠ 这是**挑过的一边**：判 stale 的代价是一次多余重跑，判 fresh 的代价是拿一份
    #   来历说不清的产物当「没问题」。盘上目前一条这种边都没有，它纯属防御。
    return {'file': None, 'consumed': None}


def normalize_inputs(recorded: Any) -> dict[str, dict[str, str | None]] | None:
    """`_provenance.inputs` 的任意盘上形态 → 新契约形态。**新鲜度这一侧的唯一落点。**

    ⚠⚠ **它存在的理由是盘上那 297 条边。** 存量产物的 `inputs` 值**全部**是扁平的
    10 位字符串（`tests/corpus.py::RESHAPED` 记着这条），而新契约一条边是
    `{file, consumed}`。`freshness` 原来直接对每条边调 `.get()`，
    而 `str` 是真值、`'abc'.get` 不存在 ⇒ 一份**读得出来的**存量产物
    抛 `AttributeError: 'str' object has no attribute 'get'`。
    **判过期恰恰是「这份产物已经不合当前 schema」时最该答得出来的那个问题**
    （`store.freshness` 的 docstring 亲自写着这条），在那一刻炸掉方向是反的。

    ⚠ **本函数不抛异常。** 同理由：`store.freshness` 承诺「别让一条坏键抛异常，
    那会把『有一条边指向已删文件』变成『整份报告出不来』」——坏**值**同理。

    ⚠ **`None` 与 `{}` 必须留着分界**：前者 ⇒ `no_provenance`，后者 ⇒ `no_inputs`。
    容器本身不是 dict（盘上没见过，但读的是任意存量字节）也归到 `no_inputs`——
    「有来源账但读不出上游」与「没有来源账」是两件事。

    ⚠ 每条边**恒输出两个键**：下游 `freshness` 直接下标取值，
    而 `tests/test_corpus_fixtures.py` 逐条断言 `set(edge) == {'file','consumed'}`。
    """
    if recorded is None:
        return None
    if not isinstance(recorded, dict):
        return {}
    return {str(k): _edge_of(v) for k, v in recorded.items()}


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

    ⚠ **形状归一在函数体第一行**（`normalize_inputs`），不在调用方：
    盘上存量产物的 `inputs` 是扁平字符串，而判过期正是「这份产物已经太老」时
    最该跑得通的那条路。放在调用方等于把它做成一条**记得调**的纪律，
    而本项目每一条纪律都被绕过过。
    """
    recorded = normalize_inputs(recorded)
    if recorded is None:
        return FreshnessReport('unknown', 'no_provenance')
    if not recorded:
        return FreshnessReport('unknown', 'no_inputs')

    edges: list[EdgeVerdict] = []
    for key in sorted(recorded):
        edge = recorded[key]              # ← `normalize_inputs` 保证它恒是两个键的 dict
        was_file = edge['file']
        was_consumed = edge['consumed']
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
