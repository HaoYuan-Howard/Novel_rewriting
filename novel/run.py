#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py —— 一次跑的上下文。**它存在的头号理由是杀掉进程全局 `_READ`。**

【那个全局变量做过什么】原型的来源账是一个模块级的 dict，键只有两级
（`<stage>/<文件名>`，不含书名）。于是同一个进程里**读过 A 书再写 B 书**的产物，
会落下一条**看不出来的假匹配边**——`_provenance.inputs` 里多一条指向另一本书的记录，
而它长得和真的一模一样。`R-11.3`「服务进程只读，一切写操作走子进程」
至今写着这条理由。

⇒ 本模块把来源账做成 **run 级的实例字段**：
**一本书 · 一个节点 · 一次跑 = 一个 `RunContext`**。同进程跑两本 = 两个实例 = 零串扰。
那条假匹配边**在结构上不存在**，键保持两级即可（作用域已经由实例边界给出）。

⚠⚠ **`R-11.3` 这条规则仍然留着，只是它的理由换了**（`Q-8`：退役的理由必须带标注，
否则读的人会以为那个坑还在）。改写后的三条见 `docs/REQUIREMENTS.md` 的 `R-11.3`。

【它不做什么】不读盘、不写盘——那是 `novel/store.py`（`ArtifactStore(ctx)`）。
本模块只持有「这一次跑是谁、算不算钱、读过什么、记了什么账」。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from contracts.invariants import NODES
from contracts.schema import InputEdge, Provenance
from contracts.schema.common import FreeProvenance, LlmProvenance
from novel import paths, prov
from novel.errors import ProvenanceRefused
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path

P = TypeVar('P', bound=Provenance)


@dataclass
class RunContext:
    """一本书 · 一个节点 · 一次跑。

    ⚠ `paid` **不是从 `NODES[node].paid` 推出来的**，它由调用方传进来。
    理由是 `R-11.7`：**贵贱只有一处算**，而那一处是 `is_paid(node, flags)`
    （`novel/orchestrator/nodes.py`，Phase D）——因为真正的贵贱是 `(node, flags)` 二元组
    决定的：`stage3.style --check-only` 是 0 成本，`stage5.review --revise` 是付费。
    在这里给一个「默认从 NODES 取」会让同一个问题有第二个答案，而两个答案必然分家。
    """

    novel_dir: Path
    node: str
    paid: bool
    code: str
    cache: CachePolicy
    ledger: CostLedger

    #: 本次跑**读过**哪些上游（`<stage>/<文件名>` → 边）。由 `store.read` 自动记账，
    #: **不由任何节点手工列举**——手工清单会烂，而且是静默地烂。
    _edges: dict[str, InputEdge] = field(default_factory=dict, repr=False)

    #: 本次跑**判过**哪几份成稿（**完整**相对路径 → 10 位指纹）。见 `_common.md` §3.2：
    #: 成稿不进 `inputs`，因为 `stage5/deep/ch01.json` 在两级键下会塌成一个反查不到的键。
    _judged: dict[str, str] = field(default_factory=dict, repr=False)

    #: 落盘前的确定性改写记了哪些账（`repairs` / `attempts` / `scope_violations` …）。
    #: ⚠ **记账不是终点，报出来才是**（`Q-1`）。出口那张会炸的表在 B3
    #: （`novel/report/accounting.py::ACCOUNTING`）：产地记了而表里没有 ⇒ 显式终态。
    _accounting: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.node not in NODES:
            raise ValueError(
                f'未知节点 {self.node!r}。取值面的唯一落点是 contracts.invariants.NODES：'
                f'{sorted(NODES)}。⚠ `n1..n9` 那一套已整套退役——作业 id 是 `<stage>.<动词>`')
        self.novel_dir = Path(self.novel_dir)

    # ── 身份 ──────────────────────────────────────────────────────────────

    @property
    def sid(self) -> str:
        """这本书的不可变 id。**从目录名解析**，与命令行怎么敲无关。"""
        return paths.derive_source_meta(self.novel_dir.name)[0]

    @property
    def title(self) -> str:
        """书名。**唯一权威来源 = 目录名**，理由见 `paths.title_of`（原型 F-118）。"""
        return paths.title_of(self.novel_dir)

    @property
    def stage(self) -> str:
        """本节点的产物层。`stage5.write` → `stage5`。"""
        return NODES[self.node].stage

    # ── 来源账（由 store 调，节点不直接碰） ────────────────────────────────

    def record_edge(self, key: str, edge: InputEdge) -> None:
        """记一条上游边。**同一个键重复读以最后一次为准**（同一份文件，指纹一样）。"""
        self._edges[key] = edge

    def record_judged(self, rel: str, fp: str) -> None:
        self._judged[rel] = fp

    @property
    def edges(self) -> dict[str, InputEdge]:
        """本次跑的来源账**快照**。返回拷贝——外面改它不该影响这一次跑。"""
        return dict(self._edges)

    @property
    def judged(self) -> dict[str, str]:
        return dict(self._judged)

    # ── 记账 ──────────────────────────────────────────────────────────────

    def account(self, key: str, value: Any) -> None:
        """记一笔「落盘前我改写了什么」。

        ⚠ 判准是**那个位置本来有没有东西**（`_common.md` §九）：
        有，才叫改写（原值被销毁 ⇒ 记账）；没有，那叫**初始化**，
        记一份「本来是空的」只会制造恒定的噪声——stage4 的 `op_forced` 就是这个下场
        （17/17 全是 null，最后连字段一起删了）。
        """
        self._accounting[key] = value

    @property
    def accounting(self) -> dict[str, Any]:
        return dict(self._accounting)

    # ── 来源账的两个构造口 ────────────────────────────────────────────────

    def llm_provenance(self, cls: type[P] = LlmProvenance, /, *,
                       prompts: dict[str, str], model: str, **extra: Any) -> P:
        """付费产出的来源账。`inputs` **不接受参数**——它只能来自 `_edges`。

        `prompts` 收的是**插值之后的 system prompt 原文**，这里替它算指纹。
        ⚠ **存渲染结果不存模板**：一个模板 + 一份枚举表 = 一个 prompt，
        **枚举表变了而模板没变，发出去的东西就变了**——stage6 已经证明过这条
        （`HOOK_APPROACHES` 一改，那份 SYS 的指纹必须跟着变，
        否则「这份候选是拿哪套写法产的」永远说不清）。
        ⚠ 判据表自身的指纹**统一叫 `RULES`**（原型有 `RULES`/`RULES7`/`RULES8` 三个名字，
        而它们是同一件事——`Q-6`：同一个语义必须叫同一个名字）。

        ⚠⚠ **「0 成本不许记 `model`」这道闸开在这里，不开在写盘口**（`_common.md` §1.2）。
        这是本轮实测挪的一处：写盘口拦不住它，因为**三条 0 成本入口本来就要写回一份
        带 `model` 的来源账**——`--revalidate` / `--remeasure` / `--recheck` 改写的是
        **付费节点产的**那份产物，它记的 `model` 属于当初那次付费产出，
        不是本次跑的新宣称；`stage6.approve` / `stage7.approve` 同理（0 成本节点，
        改的是付费产物）。⇒ 真正该拦的是「**0 成本的跑现造一份声称模型产了它的账**」，
        而**现造只有这一个入口**。放在写盘口会误伤五条真路径，那种闸迟早被人加白名单绕开。
        """
        if not self.paid:
            raise ProvenanceRefused(
                f'{self.node} 这一次跑不花钱（paid=False），却在现造一份带 `model` 的来源账。'
                f'记一个 `model` 上去就是宣称「某个模型产了它」，而这一次跑一个模型都没调。'
                f'⇒ 0 成本产出走 `free_provenance()`；'
                f'要写回一份**继承来的**来源账（`--revalidate`/`--remeasure`/`--approve`…），'
                f'走 `store.promote_invalid()` 或直接把读上来的那一份原样带下去，别现造')
        return cls(code=self.code, inputs=self.edges,
                   prompts={k: prov.fingerprint(v) for k, v in prompts.items()},
                   model=model, usage=self.ledger.usage(), **extra)

    def free_provenance(self, cls: type[P] = FreeProvenance, /, *,
                        prompts: dict[str, str] | None = None, **extra: Any) -> P:
        """0 成本产出的来源账。**没有 `model` 这个字段**（结构上不存在，不是「可以是 null」）。

        ⚠ 理由是**假账**：`--apply-edit` 落的是人手写的稿、`review_<n>.json` 是 0 成本
        算出来的，两者都**没有任何模型参与**。记一个 `model` 上去就是宣称
        「某个模型产了它」，而事后没有任何办法把它与真由模型产的那些分开。

        `prompts` 在这里通常只有一项 `RULES`——「这份报告是拿哪一版尺子量的」。
        判据表改了而报告没重跑，这个指纹是唯一说得出来的地方。
        """
        return cls(code=self.code, inputs=self.edges,
                   prompts=({k: prov.fingerprint(v) for k, v in prompts.items()}
                            if prompts else None),
                   usage=self.ledger.usage(), **extra)

    # ── 构造 ──────────────────────────────────────────────────────────────

    @classmethod
    def open(cls, base: str | os.PathLike[str], sid: str, node: str, *,
             paid: bool, cache: CachePolicy | None = None,
             ledger: CostLedger | None = None, write_ledger: bool = True) -> RunContext:
        """按 `<Output>` + 书 id 开一次跑。**找不到书目录 ⇒ 显式终态**（`UpstreamMissing`）。

        `write_ledger=False` 时账本只在内存里攒（`--dry-run` / 测试）——
        ⚠ 那不是「不记账」，`usage` 照样产出；它只是不往盘上追加行。
        """
        novel_dir = paths.resolve_novel_dir(base, sid)
        return cls(
            novel_dir=novel_dir, node=node, paid=paid,
            code=prov.code_version(),
            cache=cache or CachePolicy.default(),
            ledger=ledger or CostLedger(path=ledger_path(base) if write_ledger else None),
        )
