#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
state.py —— 这本书跑到哪了（`R-14.1` / `R-14.2` / `R-14.4`）。

【唯一的判据：产物存在性 + 来源新鲜度】`R-14.1` 明写「**不靠一份单独的进度文件**
——进度文件会与盘上的实际产物分家」。
⇒ 本模块**一行都不许碰 `_service/`**（作业记录在那儿），由 `tests/test_layering.py` 钉着。
作业记录回答的是「跑过什么命令」，完成度回答的是「盘上有什么」，两者不是同一个问题：
一个作业退出码 0 也可能留下残缺产物（F-087），而一份产物存在也可能是上一版代码产的。

【⭐ 它在捡回一个被静默弄丢的能力】原型退役的 `_legacy/run_book.py` 有过真正的续跑：
「每步产物各自落盘、**已存在即跳过**」+「**route 漂移安全**：已存在的产物若与当前
routing 不符 ⇒ 重跑」。改成 Web 编排之后**整个消失，且没有任何文档说明是有意放弃**——
今天原型的 `n5_write <book>` 缺省就是全部输出章重新付费，唯一兜底是 `.llm_cache/`。
`resume_verdict()` 就是那两条的新形态：存在性 = 已存在即跳过，新鲜度 = 漂移检测。

【读盘只经 `ArtifactStore`】本模块不 import `json`。一份产物「读不读得回来」
这件事，本身就该由那条唯一的读路回答——它把解码、解析、模型校验三道走全了。
"""
from __future__ import annotations

import os
from pathlib import Path

from contracts.invariants import NODES, STAGES
from contracts.schema import ARTIFACTS
from contracts.schema.orchestration import ArtifactState, NovelOverview, StageState
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.store import ArtifactStore

#: `produces` 里带 `{}` 的那些：逐份不定，存在性不能靠一次 `is_file()`。
_PLACEHOLDER = '{'


def _is_pattern(rel: str) -> bool:
    return _PLACEHOLDER in rel


def stage_of(rel: str) -> str:
    return rel.split('/', 1)[0]


def required_artifacts(stage: str) -> tuple[str, ...]:
    """这一层跑完了盘上该有哪几份。**本层各节点 `produces` 的并集，去掉逐份不定的那些。**

    ⚠ 取的是 `NODES[...].produces`，不是另写一张清单——原型那张
    （`Web/novels.py::ARTIFACTS`）就是第二份，而它与真正的产地分家的代价具体可见：
    **stage5 被迫整个排除在外**，于是「这本书有没有陈旧产物」那个徽章**不覆盖成稿**。
    """
    seen: dict[str, None] = {}
    for spec in NODES.values():
        if spec.stage != stage:
            continue
        for rel in spec.produces:
            if not _is_pattern(rel):
                seen[rel] = None
    return tuple(seen)


# ─────────────────────────────────────────────────────────────────────────────
# 一、一份产物
# ─────────────────────────────────────────────────────────────────────────────

def artifact_state(store: ArtifactStore, rel: str) -> ArtifactState:
    """一份产物此刻的处境。**不存在 ⇒ `unknown`，不是 `fresh`。**

    ⚠ **`readable` 与 `exists` 是两件事，不许折成一件。**
    盘上真有一份加载不了的产物（`成年人不做选择` 那本的 `stage4/plan_chapters.json`
    根本没有 `_provenance`）。把它折成「不存在」会让界面说「还没跑」——
    而它跑过，只是那次把来源账丢了。这两句话对人的下一步是相反的指示。
    """
    p = store.path(rel)
    invalid = store.exists(paths.invalid_of(rel))
    if not p.is_file():
        return ArtifactState(rel=rel, exists=False, bytes=None, mtime=None,
                             freshness='unknown', reason='no_provenance',
                             invalid=invalid, readable=True)

    st = p.stat()
    try:
        report = store.freshness(rel)
        fresh, reason, ghosts = report.state, report.reason, list(report.ghosts)
    except (ArtifactUnreadable, UpstreamMissing):
        # 判不了新鲜度也要给出一行——**「不知道」是真状态**，不是「出错了所以不显示」。
        fresh, reason, ghosts = 'unknown', 'no_provenance', []

    readable = True
    model = ARTIFACTS.get(rel)
    if model is not None:
        try:
            store.read_own(model, rel)
        except ArtifactUnreadable:
            readable = False

    return ArtifactState(
        rel=rel, exists=True, bytes=st.st_size, mtime=int(st.st_mtime),
        freshness=fresh, reason=reason, ghosts=ghosts,
        invalid=invalid, readable=readable,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 二、一层
# ─────────────────────────────────────────────────────────────────────────────

def chapters_done(novel_dir: Path, mode: str) -> list[int]:
    """`stage5/<mode>/ch*.json` 已落盘的章号（`R-14.2`：续跑粒度是**章**）。

    ⚠⚠ **必须排除 `.INVALID.json`。** `chNN.json` 的存在本身就是
    「过了写作时的落盘闸门」这个语义（`_common.md` §5.1：先校验后落盘）——
    把被拦下的那一份数进去，就把这个语义**反过来**了：
    续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。
    """
    d = novel_dir / 'stage5' / mode
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob('ch*.json')):
        if paths.is_invalid(p.name):
            continue
        digits = ''.join(c for c in p.stem if c.isdigit())
        if digits:
            out.append(int(digits))
    return sorted(out)


def planned_chapters(store: ArtifactStore) -> list[int] | None:
    """计划里的输出章号。**读不回来就返回 `None`**（不是空列表）。

    ⚠ 这两个返回值不许混：`[]` 是「计划里一章都没有」（那是一份坏计划），
    `None` 是「我们不知道该有几章」。stage5 的完成度整个挂在这个区别上。
    """
    try:
        plan, _raw = store.read_own(ARTIFACTS['stage4/plan_chapters.json'],
                                    'stage4/plan_chapters.json')
    except (ArtifactUnreadable, UpstreamMissing):
        return None
    out = getattr(plan, 'output_chapters', None)
    if out is None:
        return None
    return sorted(int(c.output_ordinal) for c in out)


def stage_state(store: ArtifactStore, stage: str, *, modes: list[str]) -> StageState:
    """一层的处境。`done` 由逐份折叠出来，**不另存一格**。

    ⚠ **逐章类的层（stage5）走另一条路**：它的产物路径带 `{}`，做不了一次
    `is_file()`。判据是「计划里的每个输出章，两条臂加起来都有成稿」——
    而**计划读不回来时结论是 `None`（判不了），不是 `False`**。
    压成 `False` 会让队列一次次去重跑一个可能已经写完的付费层。
    """
    arts = [artifact_state(store, rel) for rel in required_artifacts(stage)]
    if stage != 'stage5':
        done = all(a.exists and a.readable and a.freshness != 'stale' and not a.invalid
                   for a in arts) if arts else None
        return StageState(stage=stage, artifacts=arts, done=done)

    per_mode = {m: chapters_done(store.ctx.novel_dir, m) for m in modes}
    written = {n for chs in per_mode.values() for n in chs}
    want = planned_chapters(store)
    done = None if want is None else bool(want) and set(want) <= written
    return StageState(stage=stage, artifacts=arts, done=done, chapters_done=per_mode)


# ─────────────────────────────────────────────────────────────────────────────
# 三、一本书
# ─────────────────────────────────────────────────────────────────────────────

def readonly_ctx(novel_dir: Path, node: str, code: str) -> RunContext:
    """一个**只读**的跑上下文：不记账本、不发请求、不写盘。

    ⚠ `node` 只影响错误信息里的那个前缀——新鲜度是**产物的**属性，
    不是提问者的属性。所以这里传的是**产它的那个节点**，报错时那句话才指得对人。
    ⚠ `CostLedger(path=None)` = 账本只在内存里攒。它不是「不记账」，
    只是不往盘上追加行（`_service/` 归编排层写，而完成度这一侧按 `R-14.1` 连读都不该读）。

    ⚠⚠ **它是公开的，因为 `novel/views/` 与 HTTP 层要用同一份**（骨架 E3 提出来的）。
    自己再构造一个 `RunContext(...)` 就是第二个落点，而它的错法是具体的：
    漏掉 `ledger=CostLedger(path=None)` ⇒ **长驻的服务进程会往盘上追加账本行**，
    当场踩 `R-11.3`（服务进程绝不落产物盘）。少一个参数不会报错，只会安静地写。
    """
    return RunContext(novel_dir=novel_dir, node=node, paid=False, code=code,
                      cache=CachePolicy.default(), ledger=CostLedger(path=None))


def stage5_modes(novel_dir: Path) -> list[str]:
    d = novel_dir / 'stage5'
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith('_')) \
        if d.is_dir() else []


def overview(base: str | os.PathLike[str], sid: str) -> NovelOverview:
    """一本书跑到哪了。**纯读**：不写一个字节，不发一个请求。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    code = prov.code_version()
    modes = stage5_modes(novel_dir)

    #: 每层挑**本层第一个产它的节点**当提问者，理由见 `readonly_ctx`。
    asker = {s: next((n for n, sp in NODES.items() if sp.stage == s and sp.produces), None)
             for s in STAGES}

    stages = []
    for s in STAGES:
        node = asker[s]
        if node is None:                     # 一层一个产物都不产？那它不该在 STAGES 里
            continue
        store = ArtifactStore(readonly_ctx(novel_dir, node, code))
        stages.append(stage_state(store, s, modes=modes))

    return NovelOverview(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        dirname=novel_dir.name,
        stages=stages,
        stage5_modes=modes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 四、续跑判定（`R-14.4`）
# ─────────────────────────────────────────────────────────────────────────────

#: 续跑前的四种结论。**「重来」与「续」必须分得开**，那是 `R-14.4` 的全部内容；
#: 而 `unknown` 与另外三个分得开，是 `Q-2` 那条三态在这一侧的样子。
RESUME_RUN = 'run'          # 产物不在盘上 / 是 `.INVALID` ⇒ 该跑
RESUME_SKIP = 'skip'        # 产物在、读得回来、不陈旧 ⇒ 已存在即跳过
RESUME_REDO = 'redo'        # 上游在中断期间变过 ⇒ **那不是「续」是「重来」**
RESUME_UNKNOWN = 'unknown'  # 判不了。⚠ **它不许被折进 `run`**——见下

#: ⚠⚠ `unknown` 折进 `run` 的代价是**真金白银**：那意味着队列会去重跑一个
#: 可能已经写完的付费层。折进 `skip` 更坏（跳过一个根本没跑的层，
#: 而下游会拿着半份成品继续走）。⇒ 它只能是第四个值，由人来看那句话。
RESUME_VERDICTS = (RESUME_RUN, RESUME_SKIP, RESUME_REDO, RESUME_UNKNOWN)


def resume_verdict(ov: NovelOverview, node: str) -> tuple[str, str]:
    """这个节点该不该跑，以及**为什么**。返回 `(结论, 一句话)`。

    ⚠⚠ **`R-14.4`：续跑前必须重新判定新鲜度。上游在中断期间变过，
    就不是「续」而是「重来」，要说出来。**
    这条不是洁癖：一次被打断的跑，人往往会先去修一份上游再接着跑——
    而「接着跑」会把一份**照旧上游产的**产物留在盘上，与新上游拼成一本混版的书。
    原型没有这条，因为它连续跑都没有。

    ⚠ 结论只有三个，`redo` **不自动执行**：它是一句话，不是一个动作。
    要不要重来是人的决定（重来意味着重新付费），编排层只负责**说出来并停下**。
    """
    spec = NODES.get(node)
    if spec is None:
        return RESUME_RUN, f'未知节点 {node!r}'
    st = next((s for s in ov.stages if s.stage == spec.stage), None)
    if st is None:
        return RESUME_RUN, f'{spec.stage} 盘上没有可判定的产物'

    # 逐章类的层：结论挂在「计划里的每个输出章都有成稿吗」上，而那可能是**判不了**。
    if not [a for a in st.artifacts if a.rel in spec.produces]:
        got = {m: len(v) for m, v in st.chapters_done.items()}
        if st.done is None and got:
            return RESUME_UNKNOWN, (
                f'{spec.stage} 判不了：盘上有成稿 {got}，而「该有几章」要读 '
                f'stage4 的输出章，那份计划现在读不回来。'
                f'⚠ **这不是「没跑完」**——按没跑完处理会去重跑一个可能已经写完的付费层')
        if st.done is True:
            return RESUME_SKIP, f'{spec.stage} 计划里的每个输出章都有成稿了'
        return RESUME_RUN, f'{spec.stage} 盘上没有可判定的产物'

    mine = [a for a in st.artifacts if a.rel in spec.produces] or st.artifacts
    stale = [a for a in mine if a.freshness == 'stale']
    if stale:
        return RESUME_REDO, (
            f'上游变过：{", ".join(a.rel for a in stale)} 已陈旧 '
            f'（{", ".join(sorted({a.reason for a in stale}))}）。'
            f'⚠ 这不是「续」，是「重来」——续下去会把新旧两版上游拼进同一本书')
    missing = [a for a in mine if not a.exists]
    if missing:
        return RESUME_RUN, f'产物不在盘上：{", ".join(a.rel for a in missing)}'
    invalid = [a for a in mine if a.invalid]
    if invalid:
        return RESUME_RUN, (
            f'盘上有被闸门拦下的稿：{", ".join(paths.invalid_of(a.rel) for a in invalid)}'
            f'——0 成本提升路是 `--revalidate`，不必重新付费')
    unreadable = [a for a in mine if not a.readable]
    if unreadable:
        return RESUME_REDO, (
            f'{", ".join(a.rel for a in unreadable)} 在盘上但按当前 schema 读不回来。'
            f'⚠ 它**跑过**，只是那一份不合现在的契约——'
            f'这与「还没跑」是两件事，别当成前者顺手重跑')
    return RESUME_SKIP, f'{spec.stage} 已完成且不陈旧——已存在即跳过'
