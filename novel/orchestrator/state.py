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

from contracts.artifacts import (
    ARTIFACT_PATHS, BOOK_SCOPED_PATHS, STAGE1_MAIN_TEXT,
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_GROUNDING,
    STAGE4_PLAN_CHAPTERS, STAGE5_CHAPTER, artifact_rel,
)

import os
from pathlib import Path

from contracts.invariants import LEGACY_STAGE5_MODES, NODES, RESUME_VERDICTS, STAGES
from contracts.schema import ARTIFACTS, artifact_key, artifact_spec
from contracts.schema.orchestration import (
    ArtifactState, NovelOverview, OverviewSummary, StageState,
)
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.stages.stage5.measure import legacy_stage5_paths as stage5_legacy_paths
from novel.store import ArtifactStore

#: `produces` 里带 `{}` 的那些：逐份不定，存在性不能靠一次 `is_file()`。
_PLACEHOLDER = '{'


def _is_pattern(rel: str) -> bool:
    return _PLACEHOLDER in rel


#: **书级参数化路径 → 解析它的那个函数。**（`contracts/artifacts.py::BOOK_SCOPED_PATHS`）
#:
#: ⚠⚠ 这一层必须跑在 `_is_pattern` **之前**：书级路径解析之后是**一份具名产物**，
#: 而 `_is_pattern` 只看 `'{' in rel` ⇒ 不先解析就会把成书整个丢出 `artifacts[]`，
#: 于是它从完成度、freshness 徽章和 inbox 里**同时消失**，而没有一层会报错。
_BOOK_SCOPED_RESOLVERS = {
    ARTIFACT_PATHS['stage7.book']: paths.book_txt_rel,
}


def _resolve_book_scoped(rel: str, novel_dir: str | os.PathLike[str]) -> str:
    """把书级参数化模板换成这本书的具体路径；其余原样返回。

    ⚠ 声明了是书级、却没登记解析函数 ⇒ **当场抛**，不静默让它掉进 `_is_pattern`。
    那条静默路径的样子是「这一层的产物凭空少一份」，查起来要一路回到注册表。
    """
    resolver = _BOOK_SCOPED_RESOLVERS.get(rel)
    if resolver is not None:
        return resolver(novel_dir)
    if rel in {ARTIFACT_PATHS[key] for key in BOOK_SCOPED_PATHS}:
        raise UpstreamMissing(
            f'{rel!r} 在 `BOOK_SCOPED_PATHS` 里声明为书级参数化，'
            f'但 `_BOOK_SCOPED_RESOLVERS` 没登记解析它的函数——'
            f'不补上它就会被当成「逐份不定」丢出完成度')
    return rel


def stage_of(rel: str) -> str:
    return rel.split('/', 1)[0]


def required_artifacts(stage: str, *,
                       novel_dir: str | os.PathLike[str]) -> tuple[str, ...]:
    """这一层跑完了盘上该有哪几份。**本层各节点 `produces` 的并集，去掉逐份不定的那些。**

    ⚠ 取的是 `NODES[...].produces`，不是另写一张清单——原型那张
    （`Web/novels.py::ARTIFACTS`）就是第二份，而它与真正的产地分家的代价具体可见：
    **stage5 被迫整个排除在外**，于是「这本书有没有陈旧产物」那个徽章**不覆盖成稿**。

    ⚠ 要 `novel_dir` 是因为交付物按**书名**命名（`stage7/{title}_rewriting.txt`）：
    书级参数化那几条先解析成具体路径，再滤掉真正逐份不定的那些。
    「这一层该有哪几份」因此是**每本书各自的答案**，不再是一个全局常量。
    """
    seen: dict[str, None] = {}
    for spec in NODES.values():
        if spec.stage != stage:
            continue
        for rel in spec.produces:
            resolved = _resolve_book_scoped(rel, novel_dir)
            if not _is_pattern(resolved):
                seen[resolved] = None
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
        reason = 'no_provenance'
        if rel == STAGE2_BLUEPRINT_CHARACTER_PROJECTION:
            try:
                report = store.freshness(rel)
            except (ArtifactUnreadable, UpstreamMissing):
                pass
            else:
                reason = report.reason
        return ArtifactState(rel=rel, exists=False, bytes=None, mtime=None,
                             freshness='unknown', reason=reason,
                             display_freshness='missing', invalid=invalid, readable=True,
                             condition='invalid' if invalid else 'missing')

    st = p.stat()
    try:
        report = store.freshness(rel)
        fresh, reason, ghosts = report.state, report.reason, list(report.ghosts)
    except (ArtifactUnreadable, UpstreamMissing):
        # 判不了新鲜度也要给出一行——**「不知道」是真状态**，不是「出错了所以不显示」。
        fresh, reason, ghosts = 'unknown', 'no_provenance', []

    readable = True
    spec = artifact_spec(rel)
    if spec is not None:
        try:
            obj, _raw = store.read_own(spec.model, rel)
        except ArtifactUnreadable:
            readable = False
        else:
            # Schema 只知道 ``output_ordinal >= 1``；文件名与主体的跨路径绑定
            # 必须在读盘边界另查。否则 ``ch01.json`` 里装一份合法的第 2 章，
            # 完成度仍会按文件名集合把第 1 章判成 current 并 SKIP 付费写作。
            if artifact_key(rel) == STAGE5_CHAPTER:
                output_ordinal = getattr(obj, 'output_ordinal', None)
                readable = (
                    isinstance(output_ordinal, int)
                    and rel == artifact_rel('stage5.chapter', nn=output_ordinal)
                )

    return ArtifactState(
        rel=rel, exists=True, bytes=st.st_size, mtime=int(st.st_mtime),
        freshness=fresh, reason=reason, ghosts=ghosts,
        display_freshness=fresh, invalid=invalid, readable=readable,
        condition=('invalid' if invalid else 'unreadable' if not readable else 'ready'),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 二、一层
# ─────────────────────────────────────────────────────────────────────────────

def chapters_done(novel_dir: Path) -> list[int]:
    """规范 `stage5/chNN.json` 已落盘的章号（`R-14.2`：续跑粒度是**章**）。

    ⚠⚠ **必须排除 `.INVALID.json` 与 `.rejected.json`。** `chNN.json` 的存在本身就是
    「过了写作时的落盘闸门」这个语义（`_common.md` §5.1：先校验后落盘）——
    把被拦下的那一份数进去，就把这个语义**反过来**了：
    续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。

    不再靠“排除已知后缀”的黑名单：新增一种失败留证时，黑名单会再次漏掉。
    候选必须逐字等于产物目录生成的规范路径，只有正式稿这一种形状能通过。
    """
    d = novel_dir / 'stage5'
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob('ch*.json')):
        digits = ''.join(c for c in p.stem if c.isdigit())
        if not digits:
            continue
        ordinal = int(digits)
        expected = novel_dir / artifact_rel('stage5.chapter', nn=ordinal)
        if p == expected:
            out.append(ordinal)
    return sorted(out)


def stage5_invalid_inventory(novel_dir: Path) -> tuple[list[int], list[str]]:
    """发现 live Chapter INVALID；规范章号与坏命名分开返回。

    INVALID 不属于 ``chapters_done``，但也绝不能从状态面消失：计划读不回或候选已
    落在计划集合外时，它仍是一份待 0 LLM 重验证/人工处置的真实字节。
    """
    directory = novel_dir / 'stage5'
    if not directory.is_dir():
        return [], []
    ordinals: list[int] = []
    malformed: list[str] = []
    for candidate in sorted(directory.glob('ch*.INVALID.json')):
        digits = ''.join(ch for ch in candidate.name.split('.', 1)[0] if ch.isdigit())
        ordinal = int(digits) if digits else 0
        expected = (
            novel_dir / paths.invalid_of(artifact_rel('stage5.chapter', nn=ordinal))
            if ordinal >= 1 else None
        )
        if expected is not None and candidate == expected:
            ordinals.append(ordinal)
        else:
            malformed.append(candidate.relative_to(novel_dir).as_posix())
    return sorted(set(ordinals)), malformed


def _malformed_invalid_state(novel_dir: Path, rel: str) -> ArtifactState:
    """坏命名 INVALID 仍要形成关闭式问题行，不能因无法映射章号而隐身。"""
    stat = (novel_dir / rel).stat()
    return ArtifactState(
        rel=rel,
        exists=True,
        bytes=stat.st_size,
        mtime=int(stat.st_mtime),
        freshness='unknown',
        reason='no_provenance',
        display_freshness='unknown',
        invalid=True,
        readable=False,
        condition='invalid',
    )


def _legacy_ghosts(artifacts: list[ArtifactState]) -> list[str]:
    prefixes = tuple(f'stage5/{mode}/' for mode in LEGACY_STAGE5_MODES)
    return sorted({ghost for artifact in artifacts for ghost in artifact.ghosts
                   if ghost.startswith(prefixes)})


def planned_chapters(store: ArtifactStore) -> list[int] | None:
    """计划里的输出章号。**读不回来就返回 `None`**（不是空列表）。

    ⚠ 这两个返回值不许混：`[]` 是「计划里一章都没有」（那是一份坏计划），
    `None` 是「我们不知道该有几章」。stage5 的完成度整个挂在这个区别上。
    """
    try:
        plan, _raw = store.read_own(ARTIFACTS[STAGE4_PLAN_CHAPTERS],
                                    STAGE4_PLAN_CHAPTERS)
    except (ArtifactUnreadable, UpstreamMissing):
        return None
    out = getattr(plan, 'output_chapters', None)
    if out is None:
        return None
    return sorted(int(c.output_ordinal) for c in out)


def stage1_chapter_count(store: ArtifactStore) -> int | None:
    """stage1 主正文里的真实章数；缺席或读不回时显式返回未知。"""
    try:
        main, _raw = store.read_own(ARTIFACTS[STAGE1_MAIN_TEXT], STAGE1_MAIN_TEXT)
    except (ArtifactUnreadable, UpstreamMissing):
        return None
    return len(main.root)


def numbered_produces(stage: str) -> dict[str, str]:
    """本层里「轮次编号产物」的 `节点 → 模板`（`stage5.review` → `stage5/review_{n}.json`）。

    ⚠ 与 `required_artifacts` 是**互补**的两半，合起来才覆盖 `produces` 全集：
    那一半只收不带 `{}` 的，这一半只收「同目录一个编号占位符」的，
    剩下的是逐章类（目录段也带占位符），归 `chapters_done`。
    三条路各答各的问题，谁也不许拿另一条的答案顶替。
    """
    return {node: rel
            for node, spec in NODES.items() if spec.stage == stage
            for rel in spec.produces
            if _is_pattern(rel) and paths.is_numbered_pattern(rel)}


def stage_state(store: ArtifactStore, stage: str) -> StageState:
    """一层的处境。`done` 由逐份折叠出来，**不另存一格**。

    ⚠ **逐章类的层（stage5）走另一条路**：它的章稿路径带 `{}`，做不了一次
    `is_file()`。判据是「计划里的每个输出章都有 canonical 成稿」——
    而**计划读不回来时结论是 `None`（判不了），不是 `False`**。
    压成 `False` 会让队列一次次去重跑一个可能已经写完的付费层。

    ⚠ `rounds_done` 与 `done` **刻意不联动**：`stage5.review` 跑没跑过
    不改变「这一层的成稿齐不齐」，而 `done` 是给导航与语料库徽章用的。
    它只供续跑判定按节点各问各的（见 `resume_verdict`）。
    """
    arts = [artifact_state(store, rel)
            for rel in required_artifacts(stage, novel_dir=store.ctx.novel_dir)]
    rounds = {node: paths.numbered_rounds(store.ctx.novel_dir, rel)
              for node, rel in numbered_produces(stage).items()}
    if stage == 'stage5':
        written = chapters_done(store.ctx.novel_dir)
        invalid_ordinals, malformed_invalids = stage5_invalid_inventory(
            store.ctx.novel_dir)
        want = planned_chapters(store)
        # `chapters_done` 只回答「哪些 canonical 文件名存在」，不证明这些字节
        # 符合当前 schema / version，也不证明它们仍绑定当前上游。`draft.md`
        # 退役以后，这里是 Stage5 唯一的完成度证据；若继续只数文件名，损坏或
        # 陈旧的付费章会被 `resume_verdict` 静默 SKIP。
        ordinals = sorted(
            set(written) | set(want or ()) | set(invalid_ordinals))
        chapter_states = [
            artifact_state(store, artifact_rel('stage5.chapter', nn=ordinal))
            for ordinal in ordinals
        ]
        usable = [
            artifact for artifact in chapter_states
            if artifact.exists and artifact.readable
            and artifact.freshness == 'fresh' and not artifact.invalid
        ]
        usable_rels = {artifact.rel for artifact in usable}
        issues = [artifact for artifact in chapter_states
                  if artifact.rel not in usable_rels]
        issues.extend(
            _malformed_invalid_state(store.ctx.novel_dir, rel)
            for rel in malformed_invalids
        )
        # 干净书不把 41 份章稿逐行塞进公共状态；只携带真正需要人处理的章。
        # 缺章也在这里显式出现，供续跑判定区分「缺章」与「集合异常」。
        arts.extend(issues)
        if want is None:
            done = None
        else:
            wanted, discovered = set(want), set(written)
            missing = wanted - discovered
            extra = discovered - wanted
            unknown = any(
                artifact.exists and artifact.readable
                and artifact.freshness == 'unknown' and not artifact.invalid
                for artifact in issues
            )
            if extra or (unknown and not missing):
                # 计划外章与 unknown freshness 都是「判不了」，不是「待继续」。
                # 压成 False 会让队列/界面把它当成一条确定的付费续写路。
                done = None
            else:
                done = bool(want) and not missing and not issues
        legacy = stage5_legacy_paths(store.ctx.novel_dir)
        from novel.stages.stage5.review import review_status
        review = review_status(store.ctx, store)
        if review.state == 'legacy_incompatible':
            legacy = sorted(set([*legacy, *review.reasons]))
        return StageState(
            stage=stage, artifacts=arts, done=done, chapters_done=written,
            rounds_done=rounds,
            # reviewable 是只读导航能力，不是 current/完成闸：stale 正式稿、
            # canonical+INVALID 或仅有 INVALID 都仍值得打开查看与恢复。
            reviewable=(bool(written) or bool(invalid_ordinals)
                        or bool(malformed_invalids)),
            legacy_incompatible=bool(legacy), legacy_paths=legacy,
            review_state=review.state, review_reasons=list(review.reasons),
            review_rel=review.review_rel,
        )

    done = all(a.exists and a.readable and a.freshness != 'stale' and not a.invalid
               for a in arts) if arts else None
    reviewable = done is True or any(a.exists and a.readable for a in arts)
    legacy = _legacy_ghosts(arts)
    return StageState(stage=stage, artifacts=arts, done=done,
                      rounds_done=rounds, reviewable=reviewable,
                      legacy_incompatible=bool(legacy), legacy_paths=legacy)


def overview_summary(stages: list[StageState]) -> OverviewSummary:
    """一本书顶部的一句话；优先级与计数只在服务端这一处。"""
    artifacts = [artifact for stage in stages for artifact in stage.artifacts]
    # Stage5 的 ``artifacts`` 只携带章级问题，便于总览点名 stale/broken；
    # 它不是正式产物清单，不能让“修好一个问题章”反而把 artifact_count 减一。
    counted_artifacts = [artifact for stage in stages if stage.stage != 'stage5'
                         for artifact in stage.artifacts]
    broken = [a for a in artifacts if a.condition in ('invalid', 'unreadable')]
    legacy = [p for stage in stages for p in stage.legacy_paths]
    stale = [a for a in artifacts if a.display_freshness == 'stale']
    pending = next((stage for stage in stages if stage.done is False), None)
    unknown = [a for a in artifacts
               if a.exists and a.display_freshness == 'unknown']
    missing = [a for a in artifacts if a.display_freshness == 'missing']
    unknown_stages = [stage for stage in stages if stage.done is None]
    existing = [a for a in counted_artifacts if a.exists]
    latest = max((a.mtime or 0 for a in existing), default=0) or None

    if broken:
        verdict, text, detail = (
            'broken', f'{len(broken)} 份产物不可用', '盘上存在，但当前版本读不回来或已被隔离')
    elif legacy:
        verdict, text, detail = (
            'legacy_incompatible', '发现旧 Stage5 契约产物',
            f'只读保留：{", ".join(legacy)}；不会当成缺失后自动付费重跑')
    elif stale:
        verdict, text, detail = (
            'stale', f'{len(stale)} 份过期', '上游变了，下游要重跑')
    elif pending is not None:
        node = next((spec for spec in NODES.values() if spec.stage == pending.stage), None)
        verdict, text, detail = (
            'pending', '待继续',
            f'下一步：{pending.stage}' + (f' · {node.label}' if node else ''))
    elif unknown:
        verdict, text = 'unknown', f'{len(unknown)} 份无从判断'
        detail = (f'{len(missing)} 份尚未落盘' if missing else
                  '已有产物存在，但当前来源账不足以证明它仍然有效')
    elif unknown_stages:
        verdict, text, detail = (
            'unknown', f'{len(unknown_stages)} 层完成度判不了',
            '不是没跑，需要打开书逐层确认')
    else:
        verdict, text, detail = (
            'complete', '全链已有产物', f'{len(existing)} 份产物可读')
    return OverviewSummary(
        verdict=verdict, text=text, detail=detail,
        artifact_count=len(existing), latest_mtime=latest,
    )


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
                      cache=CachePolicy.default(), ledger=CostLedger(path=None),
                      readonly=True)


def overview_at(novel_dir: Path, code: str) -> NovelOverview:
    """用调用方已经冻结的只读坐标构造完成度。

    ``overview`` 仍是普通调用方的公开入口；这一层只供同一次只读调用需要把
    完成度与复检报告绑定到**同一个**代码指纹时复用。它不缓存，也不把报告
    塞进 ``NovelOverview``（HTTP 的完成度响应仍然 verdict-free）。
    """
    legacy = stage5_legacy_paths(novel_dir)

    #: 每层挑**本层第一个产它的节点**当提问者，理由见 `readonly_ctx`。
    asker = {s: next((n for n, sp in NODES.items() if sp.stage == s and sp.produces), None)
             for s in STAGES}

    stages = []
    for s in STAGES:
        node = asker[s]
        if node is None:                     # 一层一个产物都不产？那它不该在 STAGES 里
            continue
        store = ArtifactStore(readonly_ctx(novel_dir, node, code))
        stages.append(stage_state(store, s))

    chapter_store = ArtifactStore(readonly_ctx(
        novel_dir, 'stage1.partition', code))
    return NovelOverview(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        dirname=novel_dir.name,
        chapters=stage1_chapter_count(chapter_store),
        stages=stages,
        stage5_legacy_paths=legacy,
        summary=overview_summary(stages),
    )


def overview(base: str | os.PathLike[str], sid: str) -> NovelOverview:
    """一本书跑到哪了。**纯读**：不写一个字节，不发一个请求。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    return overview_at(novel_dir, prov.code_version())


def artifacts_for_node(ov: NovelOverview, node: str) -> list[ArtifactState]:
    """从完成度的现役 ``produces`` 投影取本节点自己的产物行。

    同一 stage 可能有多个节点；直接看整层 ``artifacts`` 会把 Global 的 INVALID
    错算到 Blueprint 上。``resume_verdict`` 与 queue 的安全关闭式判断共用这一口，
    不在两个消费者里各重拼一份 node→artifact 关系。
    """
    spec = NODES.get(node)
    if spec is None:
        return []
    stage = next((item for item in ov.stages if item.stage == spec.stage), None)
    if stage is None:
        return []
    if node == 'stage5.write':
        # 逐章路径是模板，Stage5 完成度只把问题章物化进 artifacts。
        return list(stage.artifacts)
    selected = [item for item in stage.artifacts if item.rel in spec.produces]
    return selected or list(stage.artifacts)


def live_invalid_artifacts(ov: NovelOverview, node: str) -> list[ArtifactState]:
    """本节点当前仍在活动面上的 INVALID；不把同 stage 兄弟节点算进来。"""
    return [item for item in artifacts_for_node(ov, node) if item.invalid]


# ─────────────────────────────────────────────────────────────────────────────
# 四、续跑判定（`R-14.4`）
# ─────────────────────────────────────────────────────────────────────────────

#: 续跑前的五种结论。**「重来」与「续」必须分得开**，那是 `R-14.4` 的全部内容；
#: 而 `unknown` 与另外四个分得开，是 `Q-2` 那条三态在这一侧的样子。
(
    RESUME_RUN,
    RESUME_SKIP,
    RESUME_REDO,
    RESUME_UNKNOWN,
    RESUME_LEGACY,
) = RESUME_VERDICTS

#: ⚠⚠ `unknown` 折进 `run` 的代价是**真金白银**：那意味着队列会去重跑一个
#: 可能已经写完的付费层。折进 `skip` 更坏（跳过一个根本没跑的层，
#: 而下游会拿着半份成品继续走）。⇒ 它只能是第四个值，由人来看那句话。
def stage2_global_projection_blocked(ov: NovelOverview) -> bool:
    """最新 Global 的称名 HARD 是否让 Blueprint 投影暂不可消费。"""
    stage = next(
        (candidate for candidate in getattr(ov, 'stages', ())
         if candidate.stage == 'stage2'),
        None,
    )
    if stage is None:
        return False
    by_rel = {artifact.rel: artifact for artifact in stage.artifacts}
    grounding = by_rel.get(STAGE2_GLOBAL_GROUNDING)
    projection = by_rel.get(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    return bool(
        grounding is not None and grounding.invalid
        and projection is not None and projection.freshness == 'unknown'
        and projection.reason == 'global_naming_blocked'
    )


def stage2_projection_recovery_needed(ov: NovelOverview) -> bool:
    """当前 Global 可作底座、但 Blueprint 称名投影需要 0-LLM 重物化吗。

    这格专门防一条昂贵的误派单：投影是 ``stage2.global.produces`` 的一员，
    通用完成度若只看「少了一份 producer 产物」，会把它解释成整份 Global
    需要默认重跑。实际恢复只需 ``stage2.global --renormalize``；即使最新
    schema-valid Global 因非称名 HARD 留在 ``.INVALID``，它也足够确定这份投影。

    ``invalid=True`` 只证明盘上有候选，不替候选伪造 schema-valid 结论；真正的
    载体读取仍由 renormalize 的 ``ArtifactStore.read_own`` 关闭式执行。即便字节
    损坏，这条免费命令也只会如实失败，绝不会退回付费重采。
    """
    if stage2_global_projection_blocked(ov):
        return False
    stage = next(
        (candidate for candidate in getattr(ov, 'stages', ())
         if candidate.stage == 'stage2'),
        None,
    )
    if stage is None:
        return False
    by_rel = {artifact.rel: artifact for artifact in stage.artifacts}
    grounding = by_rel.get(STAGE2_GLOBAL_GROUNDING)
    projection = by_rel.get(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    if grounding is None or projection is None:
        return False
    source_available = bool(
        grounding.invalid
        or (grounding.exists and grounding.readable
            and grounding.freshness == 'fresh')
    )
    projection_current = bool(
        projection.exists and projection.readable
        and projection.freshness == 'fresh'
    )
    return source_available and not projection_current


def stage2_invalid_projection_ready(ov: NovelOverview) -> bool:
    """最新 Global 已是 INVALID、但 Blueprint 所需投影已经 current/fresh 吗。"""
    stage = next(
        (candidate for candidate in getattr(ov, 'stages', ())
         if candidate.stage == 'stage2'),
        None,
    )
    if stage is None:
        return False
    by_rel = {artifact.rel: artifact for artifact in stage.artifacts}
    grounding = by_rel.get(STAGE2_GLOBAL_GROUNDING)
    projection = by_rel.get(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    return bool(
        grounding is not None and grounding.invalid
        and projection is not None and projection.exists and projection.readable
        and projection.freshness == 'fresh'
    )


def stage2_projection_source_unproven(ov: NovelOverview) -> str | None:
    """只缺投影时，盘上 formal Global 是否存在但不能证明可作免费底座。

    这种盘面既不是首次生成，也不能擅自拿坏/unknown Global 重物化。返回原因让
    ``resume_verdict`` 保持 UNKNOWN；最重要的是不让通用 ``missing`` 分支把一份
    已存在的昂贵产物解释成“默认再采一次”。stale 仍走既有 REDO 分支。
    """
    stage = next(
        (candidate for candidate in getattr(ov, 'stages', ())
         if candidate.stage == 'stage2'),
        None,
    )
    if stage is None:
        return None
    by_rel = {artifact.rel: artifact for artifact in stage.artifacts}
    grounding = by_rel.get(STAGE2_GLOBAL_GROUNDING)
    projection = by_rel.get(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    if grounding is None or projection is None:
        return None
    projection_current = bool(
        projection.exists and projection.readable
        and projection.freshness == 'fresh'
    )
    if projection_current or grounding.invalid or not grounding.exists:
        return None
    if not grounding.readable:
        return 'formal Global 盘上存在但按当前 schema/version 读不回来'
    if grounding.freshness == 'unknown':
        return (
            'formal Global 盘上存在但来源 freshness '
            f'为 unknown/{grounding.reason}')
    return None


def stage2_legacy_blueprint_recovery_needed(ov: NovelOverview) -> bool:
    """旧 v6 ``{MainText, Global}`` 来源边可否零调用刷新为 projection 边。"""
    stage = next(
        (candidate for candidate in getattr(ov, 'stages', ())
         if candidate.stage == 'stage2'),
        None,
    )
    if stage is None:
        return False
    by_rel = {artifact.rel: artifact for artifact in stage.artifacts}
    projection = by_rel.get(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    blueprint = by_rel.get(STAGE2_CHAPTER_BLUEPRINTS)
    return bool(
        projection is not None and projection.exists and projection.readable
        and projection.freshness == 'fresh'
        and blueprint is not None and blueprint.exists and blueprint.readable
        and not blueprint.invalid
        and blueprint.freshness == 'unknown'
        and blueprint.reason == 'consumed_unresolved'
    )


def stage2_blueprint_invalid_recovery_needed(ov: NovelOverview) -> bool:
    """canonical Blueprint 行是否有一份 live ``.INVALID`` 等待免费提升。"""
    stage = next(
        (candidate for candidate in getattr(ov, 'stages', ())
         if candidate.stage == 'stage2'),
        None,
    )
    if stage is None:
        return False
    blueprint = next(
        (artifact for artifact in stage.artifacts
         if artifact.rel == STAGE2_CHAPTER_BLUEPRINTS),
        None,
    )
    return bool(blueprint is not None and blueprint.invalid)


def resume_verdict(ov: NovelOverview, node: str) -> tuple[str, str]:
    """这个节点该不该跑，以及**为什么**。返回 `(结论, 一句话)`。

    ⚠⚠ **`R-14.4`：续跑前必须重新判定新鲜度。上游在中断期间变过，
    就不是「续」而是「重来」，要说出来。**
    这条不是洁癖：一次被打断的跑，人往往会先去修一份上游再接着跑——
    而「接着跑」会把一份**照旧上游产的**产物留在盘上，与新上游拼成一本混版的书。
    原型没有这条，因为它连续跑都没有。

    ⚠ 结论是五态闭集：``run / skip / redo / unknown / legacy_incompatible``。
    `redo` / `unknown` / `legacy_incompatible` **都不自动执行**；是否重来或
    重建是人的决定（可能意味着重新付费），编排层只负责**说出来并停下**。
    """
    spec = NODES.get(node)
    if spec is None:
        return RESUME_RUN, f'未知节点 {node!r}'
    st = next((s for s in ov.stages if s.stage == spec.stage), None)
    if st is None:
        return RESUME_RUN, f'{spec.stage} 盘上没有可判定的产物'
    if st.legacy_incompatible:
        return RESUME_LEGACY, (
            '旧 Stage5 契约不兼容：' + ', '.join(st.legacy_paths)
            + '。它们是只读历史证据，不会当成 missing 或触发静默付费重跑')

    if node == 'stage2.global' and stage2_global_projection_blocked(ov):
        return RESUME_RUN, (
            '最新 Global 候选的称名 HARD 尚未收口，Blueprint 投影为 '
            '`unknown/global_naming_blocked`；先运行 '
            '`stage2.global --apply-edit <stage2/global_edit.json>` 修正称名，'
            '或人工确认后运行 '
            '`stage2.global --revalidate --actor <id> --reason <why>`（均为 0 LLM）。'
            '不得用 `--renormalize` 洗成可消费投影，也不得重采付费 Global')
    if node == 'stage2.global' and stage2_projection_recovery_needed(ov):
        return RESUME_RUN, (
            f'{STAGE2_BLUEPRINT_CHARACTER_PROJECTION} 缺失、损坏或未绑定当前称名底座；'
            '当前 formal/INVALID Global 已足够确定性重建它，只运行 '
            '`stage2.global --renormalize`（0 LLM），不得重采付费 Global')
    if node == 'stage2.global' and stage2_invalid_projection_ready(ov):
        return RESUME_SKIP, (
            '最新 Global 候选虽因 HARD 留在 `.INVALID`，但 Blueprint 实际消费的'
            '称名投影已 current/fresh；不得在 Blueprint 前自动重采付费 Global。'
            '允许继续 Blueprint，Global INVALID 留给 Stage4 联合闸与人工恢复')
    if node == 'stage2.global':
        unproven = stage2_projection_source_unproven(ov)
        if unproven is not None:
            return RESUME_UNKNOWN, (
                f'{unproven}，同时称名投影缺失/损坏/未证明 current。'
                '不得因伴生产物少一份就排默认付费 Global；先人工核对底座，'
                '可只读诊断或尝试 `stage2.global --renormalize`（0 LLM），'
                '命令若读不回会关闭式失败')
    if (node == 'stage2.blueprint'
            and stage2_blueprint_invalid_recovery_needed(ov)):
        return RESUME_RUN, (
            '盘上有 live `stage2/chapter_blueprints.INVALID.json`；只先运行 '
            '`stage2.blueprint --revalidate --actor <id> --reason <why>`（0 LLM）'
            '按当前判据重判。队列不得构造默认付费 Blueprint argv；'
            '免费提升仍失败时再由人决定是否定向重生成')
    if (node == 'stage2.blueprint'
            and stage2_legacy_blueprint_recovery_needed(ov)):
        return RESUME_RUN, (
            '当前 ChapterBlueprints v6 仍记录旧 `{MainText, Global}` 来源边；'
            'current/fresh 称名投影已在场，只运行 '
            '`stage2.blueprint --renormalize`（0 LLM）刷新为真实消费边，'
            '不得重跑付费 Blueprint')

    if node == 'stage5.review':
        if st.review_state == 'fresh':
            return RESUME_SKIP, f'{st.review_rel} 已绑定当前 RULES、章集与 canonical 正文'
        why = '；'.join(st.review_reasons) or (st.review_state or 'unknown')
        if st.review_state == 'missing':
            return RESUME_RUN, why
        if st.review_state == 'stale':
            return RESUME_REDO, f'Stage5 Review 已陈旧：{why}；先零成本重算'
        return RESUME_UNKNOWN, f'Stage5 Review 当前无法确认：{why}'

    # ⭐ 轮次编号产物（`review_{n}` / `semantic_{n}`）：**它有自己的账，不看 stage 级 `done`**。
    #
    # ⚠⚠ 这一支是实跑改出来的。此前它落进下面那条「逐章类」分支，于是
    # `stage5.review`（0 成本全书验收）与当时的 `stage5.semantic`（付费书级语义审判，
    # 已于 2026-08-17 下线；本分支现在为 stage5.review 与 stage6.semantic 服务）
    # 拿到的结论是 **stage5 的 `done`**——而那个 `done` 说的是「章稿写全了没有」。
    # 章一写全，这两个节点就恒 `skip`，理由还写着「计划里的每个输出章都有成稿了」
    # ——**一句正确的话，答的是另一个问题**。实测两本书跑完整条队列，
    # 盘上一份 `review_*.json` / `semantic_*.json` 都没有，而队列把它们记成 `done`：
    # 全链唯一的书级质量闸门静默缺席，且账面上看不出来。
    # `stage6.semantic` 同形（它当时侥幸跑了，只因为 stage6 恰好有一份 `.INVALID` 把
    # `done` 压成了假——靠一个 bug 抵消另一个 bug）。
    #
    # ⚠ 判据是**存在性**，不是新鲜度：轮次产物没有单份的新鲜度账
    # （它们不进 `required_artifacts`，`artifacts[]` 里没有它们的行）。
    # ⇒ 上游变过时它**不会**自动重来，那一步要人显式再跑一次。
    # 这句限制写在返回的理由里，不藏着——「不知道」必须说出来（`Q-2`）。
    numbered = [rel for rel in spec.produces
                if _is_pattern(rel) and paths.is_numbered_pattern(rel)]
    if numbered:
        seen = st.rounds_done.get(node) or []
        if seen:
            return RESUME_SKIP, (
                f'{node} 盘上已有第 {", ".join(str(n) for n in seen)} 轮'
                f'（{", ".join(numbered)}）——已存在即跳过。'
                f'⚠ 轮次产物没有单份新鲜度账：上游变过它不会自动重来，'
                f'要重判就显式再跑一次这个节点')
        return RESUME_RUN, (
            f'{node} 盘上一轮都没有（{", ".join(numbered)}）')

    # 逐章类的层：结论挂在「计划里的每个输出章都有成稿吗」上，而那可能是**判不了**。
    if node == 'stage5.write':
        got = len(st.chapters_done)
        if st.done is True:
            return RESUME_SKIP, (
                f'{spec.stage} 计划里的每个输出章都有当前、可读的 canonical 成稿了')
        invalid = live_invalid_artifacts(ov, node)
        if invalid:
            invalid_rels = [
                a.rel if paths.is_invalid(a.rel) else paths.invalid_of(a.rel)
                for a in invalid
            ]
            return RESUME_RUN, (
                f'盘上有被闸门拦下的稿：'
                f'{", ".join(invalid_rels)}。'
                '先尝试 0 LLM 提升路 '
                '`--revalidate --actor <id> --reason <why>`；'
                '它会按当前判据关闭式重判，不能提升时再决定是否付费重写')
        stale = [a for a in st.artifacts if a.freshness == 'stale']
        if stale:
            return RESUME_REDO, (
                f'上游变过：{", ".join(a.rel for a in stale)} 已陈旧 '
                f'（{", ".join(sorted({a.reason for a in stale}))}）。'
                f'⚠ 这不是「续」，是「重来」——续下去会把新旧两版上游拼进同一本书')
        unreadable = [a for a in st.artifacts if a.exists and not a.readable]
        unknown = [a for a in st.artifacts
                   if a.exists and a.freshness == 'unknown']
        if unreadable or unknown:
            blocked = sorted({a.rel for a in [*unreadable, *unknown]})
            return RESUME_UNKNOWN, (
                f'{", ".join(blocked)} 在盘上，但按当前 schema/version 读不回来或新鲜度判不了。'
                f'⚠ 它已经跑过，不能把未知折成 missing 后静默重跑付费写作')
        if st.done is None:
            return RESUME_UNKNOWN, (
                f'{spec.stage} 判不了：盘上有 canonical 成稿 {got} 章，但当前计划、'
                '章集或来源新鲜度不足以证明这些章已经完成。'
                '⚠ **这不是「没跑完」**——按没跑完处理会重跑一个可能已完成的付费层')
        missing = [a for a in st.artifacts if not a.exists]
        if missing:
            return RESUME_RUN, (
                f'计划中的 canonical 章尚未落盘：'
                f'{", ".join(a.rel for a in missing)}')
        # `done=False` 却既不缺、也不坏：典型是计划外多出 canonical 章。
        # producer 自己未必有任何可写 todo，不能谎报成「缺失后继续付费」。
        return RESUME_UNKNOWN, (
            f'{spec.stage} 的 canonical 章集合与当前计划不一致，'
            '但没有可安全归类为缺章的项；请先人工核对计划与多余章稿')

    mine = artifacts_for_node(ov, node)
    stale = [a for a in mine if a.freshness == 'stale']
    if stale:
        return RESUME_REDO, (
            f'上游变过：{", ".join(a.rel for a in stale)} 已陈旧 '
            f'（{", ".join(sorted({a.reason for a in stale}))}）。'
            f'⚠ 这不是「续」，是「重来」——续下去会把新旧两版上游拼进同一本书')
    missing = [a for a in mine if not a.exists]
    if missing:
        return RESUME_RUN, f'产物不在盘上：{", ".join(a.rel for a in missing)}'
    invalid = live_invalid_artifacts(ov, node)
    if invalid:
        # ⚠ 走到这里时正式稿**在盘上、读得回来、且不陈旧**（上面三道已经先答过了），
        #   所以这份 `.INVALID` 只可能是**比它更晚**的一次被拦下的稿——
        #   旧的那种「合格新稿落盘后残留下来的兄弟文件」已经由
        #   `ArtifactStore.write` 在落盘前送进 `_rounds/` 了。
        # ⚠⚠ **理由必须说出队列真正会做什么。** 原来这句写着「不必重新付费」而
        #   函数返回的是 `run`——队列照着它去**重跑并重新付费**了一个付费节点（实测）。
        #   一句与动作相反的解释，比不解释更贵。
        return RESUME_RUN, (
            f'盘上有比正式稿更晚、被闸门拦下的稿：'
            f'{", ".join(paths.invalid_of(a.rel) for a in invalid)}。'
            f'⚠ 队列会按原命令**重跑本节点**（付费节点=重新付费）；'
            '想先省钱就手动跑 0 LLM 提升路 '
            '`--revalidate --actor <id> --reason <why>`（同轮重判并留不可变审计），'
            '或确认后再排队')
    unreadable = [a for a in mine if not a.readable]
    if unreadable:
        return RESUME_REDO, (
            f'{", ".join(a.rel for a in unreadable)} 在盘上但按当前 schema 读不回来。'
            f'⚠ 它**跑过**，只是那一份不合现在的契约——'
            f'这与「还没跑」是两件事，别当成前者顺手重跑')
    unknown = [a for a in mine if a.exists and a.freshness == 'unknown']
    if unknown:
        return RESUME_UNKNOWN, (
            f'{", ".join(a.rel for a in unknown)} 在盘上但新鲜度无法证明。'
            '⚠ UNKNOWN 不能折成 SKIP，也不能折成默认付费 RUN；先走对应的'
            '零成本诊断/恢复路或人工核对')
    return RESUME_SKIP, f'{spec.stage} 已完成且不陈旧——已存在即跳过'
