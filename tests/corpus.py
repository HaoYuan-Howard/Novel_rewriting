#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显式配置的当前契约产物语料。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import pytest
from pydantic import BaseModel

from contracts.schema import (
    ARTIFACTS, artifact_key, decode_artifact, formal_artifact_rel,
)

if TYPE_CHECKING:                                          # pragma: no cover
    from novel.prov import FreshnessReport

CORPUS_ENV = 'NOVEL_OUTPUT'


def corpus_root() -> Path | None:
    """返回显式配置的当前产物目录；未配置时不运行语料测试。"""
    configured = os.environ.get(CORPUS_ENV)
    if not configured:
        return None
    root = Path(configured).expanduser()
    return root if root.is_dir() else None


def _formal_rel(rel: str) -> str:
    """把当前轮次归档或 `.INVALID` 孪生映射到它的正式契约路径。"""
    return formal_artifact_rel(rel)


def _classify(rel: str) -> str:
    key = artifact_key(rel)
    if key is not None:
        return key
    raise AssertionError(
        f'当前产物目录出现未登记 JSON：{rel!r}。'
        '请把它加入 contracts.schema.ARTIFACTS 与当前语料路由，或清理过时产物。'
        # ⚠⚠ 上面那句会把人引向「加进 ARTIFACTS」，而**有两类文件加进去就是错的**，
        #   2026-08-24 各撞过一次（`stage5/chNN_edit.json` 与
        #   `stage2/global_review_workpack.json`）⇒ 把这两条岔路写进文案本身。
        '\n⚠ 先分清它是哪一类：'
        # F8c：`stage5.write` / `stage0.edit` 两个 `--apply-edit` 面已随旧链整层
        # 退役（`novel/api/routes/edits.py` 已删除）；「任意路径、刻意不登记」
        # 的纪律本身不变——在产物目录里再撞见这类文件，处置仍是**移走，不要登记**。
        '（a）`stage5.write` / `stage0.edit` 的**人工改稿**（已退役面）——那两层的 '
        '`--apply-edit` 路径语义曾是「任意路径」，编辑文件**刻意没有登记**'
        '（`novel/paths.py::edit_rel` 的例外一段：`R-11.3` 要服务进程不写产物盘），'
        '落点是 `<base>/_service/edits/` ⇒ **移走，不要登记**；'
        '（b）`--emit-review-workpack` / `--emit-*-template` 这类**只读辅助输出**——'
        '它们是 stdout，重定向进产物目录就成了这里的未登记 JSON ⇒ **移走，不要登记**。'
    )


@dataclass(frozen=True)
class Artifact:
    source_id: str
    book_dir: str
    rel: str
    pattern: str
    path: Path

    @property
    def model(self) -> type[BaseModel]:
        return ARTIFACTS[self.pattern]

    @property
    def formal_rel(self) -> str:
        """这份产物的**正式契约路径** —— 归档与 `.INVALID` 孪生映射回它们的本体。

        ⚠ **写盘那一侧只认它。** `_rounds/<stem>.rN.json` 是 `store.archive_round`
        产的、`<stem>.INVALID.json` 是 `store.write_invalid` 产的，两者都**不走
        `store.write`**（`store.write` 见到 `.INVALID` 当场 `PathRefused`）。
        拿 `rel` 去往返，测的就不是「这个形状落得下去、读得回来」，
        而是「能不能从一条谁都不会走的入口把归档重写一遍」——那条路本来就该是红的。
        ⇒ 归档**留在语料表里**（它们是花过钱的产物，形状照样归契约管，
        而 `_rounds/` 正是出事之后唯一能回去的那一份），只是往返走本体路径。
        """
        return _formal_rel(self.rel)


def iter_artifacts() -> Iterator[Artifact]:
    root = corpus_root()
    if root is None:
        return
    for book in sorted(path for path in root.iterdir()
                       if path.is_dir() and not path.name.startswith('_')):
        source_id = book.name.rsplit('_', 1)[-1]
        for stage_dir in sorted(book.glob('stage[0-7]')):
            if not stage_dir.is_dir():
                continue
            for path in sorted(stage_dir.rglob('*.json')):
                rel = path.relative_to(book).as_posix()
                yield Artifact(source_id, book.name, rel, _classify(rel), path)


def declared_dependency(ok: bool, *, needs: str, produced_by: str, cost: str) -> None:
    """本条判的是**语料**不是代码，而盘上这批产物不满足它声明的那一版前提。

    ⚠⚠ **与「没设 `NOVEL_OUTPUT` 就整批 skip」的区别全在这三个实参上。**
    那种 skip 说的是「这一条没在查」，而且**不说自己缺什么** ⇒ 全绿是假的，
    本轮就是靠设上环境变量才发现两处已在主分支存活多个提交的跨模块契约漂移。
    本函数逼调用方写出三件事：**缺哪一版产物 · 补它要跑哪个节点 · 那一跑要不要花钱**。
    ⇒ 跳过留下的是一条**可执行的下一步**，不是一个静默的洞。

    ⚠⚠ **前提只能是「语料的版本 / 完整度」，绝不能是被测的那条性质本身。**
    拿性质当前提就是洗绿——那会让这条测试永远跳过、永远不再红，
    而它看起来和「查过没事」一模一样。
    """
    if not ok:
        pytest.skip(f'语料缺 {needs}；补法：{produced_by}（{cost}）')


def freshness_of(novel_dir: Path, rel: str) -> 'FreshnessReport':
    """盘上这份产物相对**盘上此刻的上游**新不新鲜 —— 走**生产那一个落点**。

    ⚠⚠ **这里一行投影比对都不写。** 落点是 `ArtifactStore.freshness(rel)`，只读上下文由
    `novel/orchestrator/state.py::readonly_ctx` 造。自己照 `CONSUMED_PROJECTIONS` 重拼一遍
    是 `docs/LESSONS.md` 第 21 条的第一形状，而这里的分家方向是具体的：
    **「测试说新鲜、真跑说过期」⇒ 前提永远成立、跳过永远不发生**，
    于是它看起来和「查过没事」一模一样。

    ⚠ 提问者节点取 `novel/consumption.py::producer_node`，与 `ArtifactStore.freshness`
    内部取的是**同一个函数**。自己传一个节点名进来就是第二个落点：一份制品该由谁
    提问只能由 `NODES` 的 `produces` 声明回答（F8c 起现役 26 份制品各有唯一产者；
    历史上 `stage7/name_map.json` 曾由 `stage7.rename` 与 `stage7.approve` 两个节点
    都 `produces`，而只有前者持有真正的 consumed 契约——那个裁决写在 `NODES` 的
    声明顺序里，不该被复述）。

    ⚠ **运行时 import 收在函数体内**：本模块同时服务 `tests/test_corpus_fixtures.py`
    那批只 import `contracts.*` 的纯契约体检，顶层拖进 `novel.*` 会让那一批的 import 图跟着变。
    """
    from novel import prov
    from novel.consumption import producer_node
    from novel.orchestrator.state import readonly_ctx
    from novel.store import ArtifactStore

    node = producer_node(rel)
    if node is None:
        raise AssertionError(
            f'{rel} 不在任何节点的 `produces` 里——说不出该由谁来提这个问题。'
            f'新产物要先登记进 `contracts/invariants.py::NODES`')
    return ArtifactStore(
        readonly_ctx(Path(novel_dir), node, prov.code_version())).freshness(rel)


def _targets(book_dir: str, rel: str) -> list[str]:
    """`rel` 是一份具体产物，还是一层前缀（以 `/` 收尾 ⇒ 它底下**每一份**都要当前）。

    ⚠ 前缀这一支是必要的：stage5 的成稿是**一整批**，它们共享同一条
    `stage4/writer_context.json` 边、也在同一次跑里写下。只抽查 `ch01` 会在
    「只有后面几章过期」时报当前——而那正是最难看出来的一种。
    ⚠ `_rounds/` 是历史归档，不参与「当前不当前」。
    """
    if not rel.endswith('/'):
        return [rel]
    return sorted(a.rel for a in iter_artifacts()
                  if a.book_dir == book_dir and a.rel.startswith(rel)
                  and '_rounds/' not in a.rel)


def _not_current(book_dir: str, *rels: str) -> list[str]:
    """这本书的这些产物里，**当前不了**的那些的人读理由。

    ⚠ 一层前缀底下**一份都没有**不算问题（那本书可能只跑了一条臂）；
    但**所有** `rels` 加起来一份都没有就是问题——否则「没东西可查」会伪装成「查过、都当前」。
    """
    if corpus_root() is None:
        return []
    novel_dir = corpus_root() / book_dir
    problems: list[str] = []
    seen = 0
    for rel in rels:
        targets = _targets(book_dir, rel)
        seen += len(targets)
        if not targets and not rel.endswith('/'):
            problems.append(f'{rel}（盘上没有这一份）')
        for target in targets:
            report = freshness_of(novel_dir, target)
            if report.state == 'fresh':
                continue
            edges = ', '.join(f'{e.key}:{e.reason}' for e in report.edges) or report.reason
            problems.append(f'{target}（现判 {report.state}/{report.reason}：{edges}）')
    if not seen and not problems:
        problems.append(f'{list(rels)} 底下盘上一份产物都没有——没东西可查')
    return problems


def requires_current(book_dir: str, *rels: str, produced_by: str, cost: str) -> None:
    """本条要的是「盘上这几份与它们的上游对得上」的**那一版语料**；对不上就按声明跳过。

    ⚠⚠ **前提只许是「产物相对上游是哪一版」这一件事**，而且由 `freshness_of` 现算，
    不是任何判据结论、badge 或 HARD 命中数。这条限制是结构性的：
    **本函数没有接收「结论」的参数位**，想拿被测性质当前提得先改签名。

    ⚠⚠ **正在测「非当前的上游要被拒」的用例不许调它。** 那一类把「过期」当**输入**，
    加了前提就是洗绿（见 `declared_dependency` 的第二条警告）。判准三条，全中才可以加：
      ① 它不是被测的那条性质本身（用例体里不出现 `UpstreamStale` / `.freshness(`，不删改上游）；
      ② 前提为假时它**死在门口**而不是「拿到了输入」——这一条要靠**实测异常类型**判，不许靠读；
      ③ 前提为真时断言真的执行了一遍（扫描面 > 0，且在同一条用例里断言它）。
    一句话：**前提只覆盖「输入是哪一版」，不覆盖「这一版有没有问题」。**

    ⚠ 三态分开说（`docs/LESSONS.md` 第 4 条）：「盘上没有这一份」「相对上游过期」
    「算不出来（幽灵边）」是三件事，跳过消息里逐条带上现判读数与过期边，
    **那串读数是复算出来的，不是手写的断言**。
    """
    if corpus_root() is None:
        return                       # 没配语料由各模块自己的 corpus 门管，这里不静默兜底
    problems = _not_current(book_dir, *rels)
    declared_dependency(
        not problems,
        needs=(f'{book_dir} 的 {list(rels)} 与盘上上游对得上的那一版；'
               f'当前不了的有 {len(problems)} 份：{"；".join(problems[:3])}'),
        produced_by=produced_by, cost=cost)


def is_current(book_dir: str, *rels: str) -> bool:
    """这本书的这几份相对盘上上游都当前吗。**谓词形态**，不跳过。

    ⚠ 给「一条用例横扫多本书」的对账用：那种用例不能整条跳过（还有别的书可查），
    但把过期的书混进去量出来的差值**不是尺子漂了，是它量的不是同一份输入**。
    ⇒ 过滤之后**必须留分母断言**（查了几份、非零），否则「都当前的书一本都没有」
    与「查过、都对得上」在报告上长得一模一样（`docs/LESSONS.md` 第 2 条）。
    """
    return not _not_current(book_dir, *rels)


def first_current_book(books: Any, *rels: str) -> str | None:
    """候选里第一本这几份都相对盘上上游是当前的书；一本都没有给 `None`。

    ⚠⚠ **只挑一本的用例要挑「跑得动的那一本」，不是「排序第一的那一本」。**
    `requires_current` 让整条用例跳过，而**跳过是「没在查」**；换一本书却能把同一条
    性质真的验一遍 ⇒ 优先真跑，跳过只作最后手段。实测过代价：`test_stage5` 那五条
    端到端用例固定取 `sorted(books)[0]`，而那本的成稿相对重生成过的
    `stage4/writer_context.json` 已过期 ⇒ 五条全红（退出码 3）；
    换成链条当前的那一本，同样五条全绿。

    ⚠ **不许拿它去挑「按书参数化」的用例。** 那种用例的取值面**就是**「每一本」，
    在里面挑一本等于把另外几本静默摘掉——那是洗绿，不是选书。
    那一类逐条用 `requires_current`，让过期的书留下**看得见**的跳过。

    ⚠ **纯函数、不跳过**：调用点常在模块级（`_BOOK = …`），而模块级 `pytest.skip`
    是收集期错误不是跳过。前提由调用方在 fixture 或用例体里用 `requires_current` 声明。
    """
    if corpus_root() is None:
        return None
    return next((book for book in sorted(books) if not _not_current(book, *rels)), None)


def load_raw(artifact: Artifact) -> Any:
    return json.loads(artifact.path.read_text(encoding='utf-8'))


def load_current(artifact: Artifact) -> Any:
    """严格读取当前版本并返回逻辑 payload；旧产物必须从产地重跑。"""
    decoded = decode_artifact(artifact.formal_rel, load_raw(artifact))
    artifact.model.model_validate(decoded.payload)
    return decoded.payload
