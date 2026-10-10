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

import re

from contracts.artifacts import (
    ARTIFACT_PATHS, BOOK_SCOPED_PATHS, STAGE1_MAIN_TEXT,
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STR06_BODY_DESIGN, artifact_rel,
    STR06_STAGE2_OBS_CHAPTER, STR06_STAGE5_BODY, STR06_STAGE5_BODY_RECEIPT,
)

import os
import re
import weakref
from pathlib import Path
from typing import Any

from contracts.invariants import (
    LEGACY_STAGE5_MODES, NODES, OPERATOR_REDO_STRATEGIES, RESUME_VERDICTS, STAGES,
)
from contracts.schema import ARTIFACTS, artifact_spec
from contracts.schema.orchestration import (
    ArtifactState, NovelOverview, OverviewSummary, OwnerIssueState, StageState,
)
from novel import paths, prov
from novel.errors import (
    ArtifactUnreadable, ProvenanceRefused, UnsealedArtifact, UpstreamMissing,
    UpstreamStale,
)
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.store import ArtifactStore, Str06Repository, candidate_artifact


def legacy_stage5_paths(novel_dir: Path) -> list[str]:
    """列出旧 `stage5/deep|shallow` 布局的根，不枚举内部文件（F8c 本地化）。

    原实现在已退役的 `novel.stages.stage5.measure`；这是 review/state/API 共用的
    纯发现面：旧产物必须显式说 `legacy_incompatible`，不能因 canonical 装载器
    看不见它们而被说成 missing。消费方只需要知道「哪一条旧臂存在」；空目录仍是
    旧布局证据。**只读探针**：不解析内容、不迁移、不当成 missing。
    """
    return [
        f'stage5/{old_arm}'
        for old_arm in ('deep', 'shallow')
        if (novel_dir / f'stage5/{old_arm}').is_dir()
    ]

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
    # F8a：新链成书路径（`stage7/final/{title}_rewriting.txt`）。
    'stage7/final/{title}_rewriting.txt': paths.book_txt_rel,
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
        if spec.stage != stage or not spec.stage_required:
            continue
        for rel in spec.produces:
            resolved = _resolve_book_scoped(rel, novel_dir)
            if not _is_pattern(resolved):
                seen[resolved] = None
    return tuple(seen)


# ─────────────────────────────────────────────────────────────────────────────
# 一、一份产物
# ─────────────────────────────────────────────────────────────────────────────

#: 具体章回执 rel（模板含 ``{nn}`` 占位，不能直接参与相等比较）。
_BODY_RECEIPT_RE = re.compile(r'stage5/acceptance/body/ch\d+\.json')

#: 逐章身份核对里要就地归类（不中断完成度）的失败族：IR 上游已变（须先重跑
#: compile）与读不出一样，都落 `unknown/consumed_unresolved`——把 UpstreamStale
#: 穿透出去会让整条只读 overview 链崩溃，排不出「下一步跑 compile」。
_BODY_PROBE_SWALLOWED = (
    ArtifactUnreadable, UpstreamMissing, UpstreamStale, UnsealedArtifact,
    ProvenanceRefused, OSError, ValueError, StopIteration,
)


class _BodyIdentityProbe:
    """一次完成度调用内的逐章身份核对复用面（IR 装载一次，逐章只做投影）。

    `overview_at` 冻结只读坐标的同调用纪律：全书章共享同一份 IR 校验结果，
    不为每章重付一次 CommitBundle 扫描加整份 IR 模型校验。缓存不跨调用——
    下一次 overview 重新探测，上游变化不会被旧结论遮住（I-16：缓存是派生物）。
    """

    def __init__(self, novel_dir) -> None:
        self.repository = Str06Repository(novel_dir)
        self._writer_ir: tuple[bool, Any] | None = None
        self._identities: dict[int, Any] = {}

    def identities(self, ordinal: int):
        cached = self._identities.get(ordinal)
        if cached is None:
            from novel.stages.common import chapter_identities, project_chapter_packet
            cached = chapter_identities(project_chapter_packet(self.writer_ir(), ordinal))
            self._identities[ordinal] = cached
        return cached

    def writer_ir(self):
        if self._writer_ir is None:
            try:
                self._writer_ir = (True, self.repository.check_model(
                    'stage4/ir/writer_book_ir.json'))
            except _BODY_PROBE_SWALLOWED as exc:
                self._writer_ir = (False, exc)
        ok, value = self._writer_ir
        if not ok:
            raise value
        return value


#: store 实例 → 其逐章身份探测（弱键：探测生命周期＝一次完成度调用）。
#: 不改 `artifact_state` 的公开签名——测试桩常整体替换它，新 kwargs 会砸桩。
_BODY_PROBES: 'weakref.WeakKeyDictionary[ArtifactStore, _BodyIdentityProbe]' = (
    weakref.WeakKeyDictionary())


def _body_probe(store: ArtifactStore) -> '_BodyIdentityProbe':
    probe = _BODY_PROBES.get(store)
    if probe is None:
        probe = _BodyIdentityProbe(store.ctx.novel_dir)
        _BODY_PROBES[store] = probe
    return probe


def _candidate_artifact_state(store: ArtifactStore, rel: str, st: os.stat_result, *,
                              invalid: bool, invalid_freshness, invalid_reason,
                              ) -> ArtifactState:
    """候选链产物在盘上的处境（F8d 桥；判据见 `ArtifactStore.candidate_freshness`）。

    与正式分支同一形状：`exists`／`readable`／`freshness`／`invalid` 四格分开，
    「不知道」不折成 fresh，也不折成 missing。章回执额外保留文件名与章序的跨路径
    绑定——`ch01.json` 里装一份合法的第 2 章，不能算第 1 章已完成。
    正文身份核对经 store 绑定的 `_BodyIdentityProbe`（同一完成度调用内全书
    共享一次 IR 装载与 CommitBundle 清单；见 `_BODY_PROBES`）。
    """
    try:
        report = store.candidate_freshness(rel)
        fresh, reason, ghosts = report.state, report.reason, list(report.ghosts)
    except (ArtifactUnreadable, UpstreamMissing):
        fresh, reason, ghosts = 'unknown', 'no_provenance', []
    readable = store.candidate_readable(rel)
    body = re.fullmatch(r'stage5/body/ch(\d+)\.txt', rel)
    if readable and fresh == 'fresh' and body is not None:
        # 正文字节当前不代表写作输入仍当前；章包绑定是逐章权威，不按整本 IR hash 失效。
        from novel.stages.common import check_chapter_receipt
        probe = _body_probe(store)
        repository = probe.repository
        ordinal = int(body.group(1))
        try:
            receipt = repository.inspect_sealed_model(artifact_rel(STR06_STAGE5_BODY_RECEIPT, nn=ordinal))
            identities = probe.identities(ordinal)
            if receipt.chapter_ordinal != ordinal or receipt.status != 'accepted':
                fresh, reason = 'unknown', 'no_provenance'
            elif check_chapter_receipt(receipt, identities=identities,
                                       prose_sha256=repository.file_sha256(repository.path(rel))):
                fresh, reason = 'stale', 'consumed_changed'
        except _BODY_PROBE_SWALLOWED:
            fresh, reason = 'unknown', 'consumed_unresolved'
    if readable and _BODY_RECEIPT_RE.fullmatch(rel):
        try:
            payload = store.candidate_payload(rel)
        except (ArtifactUnreadable, UpstreamMissing, OSError, ValueError):
            payload = None
        ordinal = payload.get('chapter_ordinal') if isinstance(payload, dict) else None
        readable = (isinstance(ordinal, int)
                    and rel == artifact_rel(STR06_STAGE5_BODY_RECEIPT, nn=ordinal))
    return ArtifactState(
        rel=rel, exists=True, bytes=st.st_size, mtime=int(st.st_mtime),
        freshness=fresh, reason=reason, ghosts=ghosts,
        display_freshness=fresh, invalid=invalid,
        invalid_freshness=invalid_freshness, invalid_reason=invalid_reason,
        readable=readable,
        condition=('invalid' if invalid else 'unreadable' if not readable else 'ready'))


def artifact_state(store: ArtifactStore, rel: str) -> ArtifactState:
    """一份产物此刻的处境。**不存在 ⇒ `unknown`，不是 `fresh`。**

    ⚠ **`readable` 与 `exists` 是两件事，不许折成一件。**
    盘上真有一份加载不了的产物（`成年人不做选择` 那本的 `stage4/plan_chapters.json`
    根本没有 `_provenance`）。把它折成「不存在」会让界面说「还没跑」——
    而它跑过，只是那次把来源账丢了。这两句话对人的下一步是相反的指示。
    """
    p = store.path(rel)
    invalid_rel = paths.invalid_of(rel)
    invalid = store.exists(invalid_rel)
    invalid_freshness = None
    invalid_reason = None
    if invalid:
        try:
            invalid_report = store.freshness(invalid_rel)
        except (ArtifactUnreadable, UpstreamMissing):
            invalid_freshness, invalid_reason = 'unknown', 'no_provenance'
        else:
            invalid_freshness = invalid_report.state
            invalid_reason = invalid_report.reason
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
                             display_freshness='missing', invalid=invalid,
                             invalid_freshness=invalid_freshness,
                             invalid_reason=invalid_reason, readable=True,
                             condition='invalid' if invalid else 'missing')

    st = p.stat()
    if candidate_artifact(rel) is not None and hasattr(store, 'candidate_readable'):
        # 候选链（str06）盘面（F8d 桥）：不带版本信封、root 身份是 Stage1 章数组，
        # 正式读路覆盖不到——可读性与新鲜度按候选格式判，见 `ArtifactStore`
        # 的桥段（同一把 `prov.freshness` 尺子，不为完成度另造尺子）。
        # ⚠ `hasattr` 是给测试桩的最小礼貌：只实现正式读面的 store 仍走正式分支
        #    （章回执的跨路径绑定在那里照常生效）。
        return _candidate_artifact_state(store, rel, st, invalid=invalid,
                                         invalid_freshness=invalid_freshness,
                                         invalid_reason=invalid_reason)
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
            # ⚠ F8b-3 修复：模板 rel 与具体章路径恒不相等，绑定检查此前
            # 不可达——装错章号的回执曾被读作 `ready`。
            if _BODY_RECEIPT_RE.fullmatch(rel):
                output_ordinal = getattr(obj, 'chapter_ordinal', None)
                readable = (
                    isinstance(output_ordinal, int)
                    and rel == artifact_rel(STR06_STAGE5_BODY_RECEIPT,
                                            nn=output_ordinal)
                )

    return ArtifactState(
        rel=rel, exists=True, bytes=st.st_size, mtime=int(st.st_mtime),
        freshness=fresh, reason=reason, ghosts=ghosts,
        display_freshness=fresh, invalid=invalid, readable=readable,
        invalid_freshness=invalid_freshness, invalid_reason=invalid_reason,
        condition=('invalid' if invalid else 'unreadable' if not readable else 'ready'),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 二、一层
# ─────────────────────────────────────────────────────────────────────────────

def chapters_done(novel_dir: Path) -> list[int]:
    """规范 `stage5/body/ch{nn}.txt` 已落盘的章号（`R-14.2`：续跑粒度是**章**）。

    ⚠⚠ **必须排除 `.INVALID` 孪生等失败留证。** 规范路径的存在本身就是
    「过了写作时的落盘闸门」这个语义（`_common.md` §5.1：先校验后落盘）——
    把被拦下的那一份数进去，就把这个语义**反过来**了：
    续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。

    不再靠“排除已知后缀”的黑名单：新增一种失败留证时，黑名单会再次漏掉。
    候选必须逐字等于产物目录生成的规范路径，只有正式稿这一种形状能通过。
    """
    d = novel_dir / 'stage5' / 'body'
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob('ch*.txt')):
        digits = ''.join(c for c in p.stem if c.isdigit())
        if not digits:
            continue
        ordinal = int(digits)
        expected = novel_dir / artifact_rel(STR06_STAGE5_BODY, nn=ordinal)
        if p == expected:
            out.append(ordinal)
    return sorted(out)


def stage2_observations_done(novel_dir: Path) -> list[int]:
    """规范 `stage2/observations/ch{nn}.json` 已落盘的章号（B2；D3 逐章观察）。

    【为什么要有这一口】`ch{nn}` 是逐章占位符模板——既不是非模板的
    `required_artifacts` 行，也不是 `{n}` 轮次产物；通用完成度对它**整体失明**。
    若不给逐章观察单独的账，Supervisor 会对一个已成功的 chapters 节点
    反复派发（F8d 同形死循环：每轮 rc=0、盘面不变、永远不推进）。
    与 `chapters_done` 同一条纪律：只认产物目录生成的规范路径，
    `.INVALID.json` 孪生不数（被闸拦下的观察不算「已完成」）。
    """
    d = novel_dir / 'stage2' / 'observations'
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob('ch*.json')):
        digits = ''.join(c for c in p.stem if c.isdigit())
        if not digits:
            continue
        ordinal = int(digits)
        expected = novel_dir / artifact_rel(STR06_STAGE2_OBS_CHAPTER, nn=ordinal)
        if p == expected:
            out.append(ordinal)
    return sorted(out)


def stage5_invalid_inventory(novel_dir: Path) -> tuple[list[int], list[str]]:
    """发现 live Chapter INVALID；规范章号与坏命名分开返回。

    INVALID 不属于 ``chapters_done``，但也绝不能从状态面消失：计划读不回或候选已
    落在计划集合外时，它仍是一份待 0 LLM 重验证/人工处置的真实字节。
    """
    directory = novel_dir / 'stage5' / 'body'
    if not directory.is_dir():
        return [], []
    ordinals: list[int] = []
    malformed: list[str] = []
    for candidate in sorted(directory.glob('ch*.INVALID.txt')):
        digits = ''.join(ch for ch in candidate.name.split('.', 1)[0] if ch.isdigit())
        ordinal = int(digits) if digits else 0
        expected = (
            novel_dir / paths.invalid_of(
                artifact_rel(STR06_STAGE5_BODY, nn=ordinal))
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
        invalid_freshness='unknown',
        invalid_reason='no_provenance',
        readable=False,
        condition='invalid',
    )


def _legacy_ghosts(artifacts: list[ArtifactState]) -> list[str]:
    prefixes = tuple(f'stage5/{mode}/' for mode in LEGACY_STAGE5_MODES)
    return sorted({ghost for artifact in artifacts for ghost in artifact.ghosts
                   if ghost.startswith(prefixes)})


def planned_chapters(store: ArtifactStore) -> list[int] | None:
    """Design 正式产物中的输出章号。**读不回来就返回 `None`**（不是空列表）。

    STR-03-S4：章集分母改读 body_design（``BodyDesign.chapters``；早期的
    BlindCandidate / presentation graph 读路已随旧链退役），不再依赖已退役的
    ``plan_chapters.json``。
    """
    try:
        design, _raw = store.read_own(STR06_BODY_DESIGN, STR06_BODY_DESIGN)
    except (ArtifactUnreadable, UpstreamMissing):
        # 候选链盘面（F8d 桥）：BodyDesign 不带版本信封，正式读路读不回来；
        # 按候选格式读同一份产物——章集分母不能因为读路差异变成「判不了」。
        design = _candidate_body_design(store)
    if design is None:
        return None
    out = getattr(design, 'chapters', None)
    if out is None:
        return None
    return sorted(int(c.chapter_ordinal) for c in out)


def _candidate_body_design(store: ArtifactStore):
    """候选根上的 BodyDesign（F8d 桥）；读不回来返回 `None`，不猜章集。

    未封存（R6 发布校验拒绝）同样按未知章集处理：不能证明已发布的计划
    不参与完成度判定，交重跑免费补封存。
    """
    from novel.errors import UnsealedArtifact
    try:
        return Str06Repository(store.ctx.novel_dir).read_model(STR06_BODY_DESIGN)
    except (ArtifactUnreadable, UpstreamMissing, UnsealedArtifact,
            OSError, ValueError):
        return None


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
        # ⚠ F8b-3：新链 stage5 增加具名 Hook 面（stage5/hook/hook.txt＋
        # receipt.json）。与章稿同一纪律——干净书不把健康具名行塞进公共
        # 状态（done=True ⇒ artifacts 为空），只有问题行才需要人处理；
        # Hook 面的问题同时压住 stage 级 done。
        hook_issues = [
            artifact for artifact in arts
            if not artifact.exists or not artifact.readable
            or artifact.freshness == 'stale' or artifact.invalid
        ]
        arts = hook_issues
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
            artifact_state(store, artifact_rel(STR06_STAGE5_BODY, nn=ordinal))
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
                done = (bool(want) and not missing and not issues
                        and not hook_issues)
        legacy = legacy_stage5_paths(store.ctx.novel_dir)
        # ⚠⚠ F8c：`stage5.review`（全书验收轮次面）随旧链整层退役——它读的
        #   `stage5/review_{n}.json` 是退役产物键，`novel.stages.stage5.review`
        #   模块已删。新链没有全书级 review 闸（章级机械验由 `str06.stage5.body`
        #   的 body_checks 承接，交付残留归 `str06.stage7.assembly`）⇒ 三格
        #   **如实缺席**：不 import 已删模块（那会让整本 overview 崩），也不造
        #   一个 `missing` 冒充「还没审」——那一格的问题在新链不存在。
        #   旧双臂布局的只读识别仍由上面的 `legacy_stage5_paths` 负责。
        return StageState(
            stage=stage, artifacts=arts, done=done, chapters_done=written,
            rounds_done=rounds,
            # reviewable 是只读导航能力，不是 current/完成闸：stale 正式稿、
            # canonical+INVALID 或仅有 INVALID 都仍值得打开查看与恢复。
            reviewable=(bool(written) or bool(invalid_ordinals)
                        or bool(malformed_invalids)),
            legacy_incompatible=bool(legacy), legacy_paths=legacy,
        )

    done = all(a.exists and a.readable
               and a.freshness != 'stale' and not a.invalid
               for a in arts) if arts else None
    reviewable = done is True or any(
        a.exists and a.readable for a in arts)
    legacy = _legacy_ghosts(arts)
    # B2（D3）：stage2 的章观察是逐章模板产物，通用 `arts` 看不见它们；
    # 逐章的账记进 `chapters_done`，供 `resume_verdict` 的 chapters 节点分支
    # 判「源章全都观察过了没有」（同 stage5 的章账纪律，互不顶替）。
    written: list[int] = []
    if stage == 'stage2':
        observation_issues: list[ArtifactState] = []
        for ordinal in stage2_observations_done(store.ctx.novel_dir):
            observation = artifact_state(
                store, artifact_rel(STR06_STAGE2_OBS_CHAPTER, nn=ordinal))
            if (observation.exists and observation.readable
                    and observation.freshness == 'fresh' and not observation.invalid):
                written.append(ordinal)
            else:
                observation_issues.append(observation)
        # 模板文件存在只作库存；只有当前、可读的正式观察才计为完成。
        # 把问题章物化给 node 投影，避免 stage2 的兄弟产物冒充本节点上游。
        arts.extend(observation_issues)
        if any(not a.readable or a.freshness == 'unknown' for a in observation_issues):
            done = None
        elif observation_issues:
            done = False
        reviewable = reviewable or any(a.readable for a in observation_issues) or bool(written)
    return StageState(stage=stage, artifacts=arts, done=done,
                      chapters_done=written,
                      rounds_done=rounds, reviewable=reviewable,
                      legacy_incompatible=bool(legacy), legacy_paths=legacy)


def overview_summary(stages: list[StageState]) -> OverviewSummary:
    """一本书顶部的一句话；优先级与计数只在服务端这一处。"""
    artifacts = [artifact for stage in stages for artifact in stage.artifacts]
    # Stage5 的 ``artifacts`` 只携带章级问题，便于总览点名 stale/broken；
    # 它不是正式产物清单，不能让“修好一个问题章”反而把 artifact_count 减一。
    counted_artifacts = [artifact for stage in stages if stage.stage != 'stage5'
                         for artifact in stage.artifacts]
    broken = [a for a in artifacts
              if a.condition in ('invalid', 'unreadable')]
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
    # ⚠⚠ F8b：旧链恢复/审批/完成度探测仍用旧节点 id 建只读 ctx 读**旧盘面**
    #   （旧书只读识别 legacy_incompatible 的正当读路）。RunContext 对
    #   readonly+RETIRED_NODES 放行（见 novel/run.py 的校验），此处无需分支。
    return RunContext(novel_dir=novel_dir, node=node, paid=False, code=code,
                      cache=CachePolicy.default(), ledger=CostLedger(path=None),
                      readonly=True)


def _owner_issue_states(novel_dir: Path) -> list['OwnerIssueState']:
    """读盘上 open 的跨节点修订问题（R3；`runtime/issues/<owner>.json`）。

    resolved 不进投影（留账不是待修订）；文件损坏即显式拒绝——它是续跑判定
    的输入，静默当「没有问题」会把修订循环变成静默跳过（I-34）。v1 记录经
    统一读口解码为 v3；``product_sha256`` 采本快照时的当前摘要（None＝
    owner 产物不在盘上，门控见 `resume_verdict` 的 owner_revision 分支）。
    """
    import json as _json

    from contracts.schema.workflow import (
        OWNER_PRODUCT_RELS, owner_issue_record_of_payload,
    )
    from novel.stages.owner_revision import owner_product_digest_at

    def _digest_or_none(record_owner: str) -> str | None:
        try:
            return owner_product_digest_at(novel_dir, record_owner)
        except ValueError:
            return None

    states: list[OwnerIssueState] = []
    issues_dir = novel_dir / 'runtime' / 'issues'
    if not issues_dir.is_dir():
        return states
    for path in sorted(issues_dir.glob('*.json')):
        try:
            record = owner_issue_record_of_payload(
                _json.loads(path.read_text(encoding='utf-8')),
                lambda record_owner: owner_product_digest_at(novel_dir, record_owner))
        except ValueError as exc:
            raise ValueError(
                f'回派账 {path.name} 形状损坏：{exc}；拒绝当作「没有问题」继续，'
                '先保留原记录并核验其损坏原因，显式恢复后再续跑') from exc
        if record.status != 'open':
            continue
        states.append(OwnerIssueState(
            owner=record.owner, node=record.node, raised_by=record.raised_by,
            rounds_total=record.rounds_total,
            codes=sorted({issue.code for issue in record.issues}),
            against_sha256=record.against_sha256,
            product_sha256=_digest_or_none(record.owner),
            no_progress=record.no_progress, dispatch_pending=record.dispatch_pending))
    return states


def overview_at(novel_dir: Path, code: str) -> NovelOverview:
    """用调用方已经冻结的只读坐标构造完成度。

    ``overview`` 仍是普通调用方的公开入口；这一层只供同一次只读调用需要把
    完成度与复检报告绑定到**同一个**代码指纹时复用。它不缓存，也不把报告
    塞进 ``NovelOverview``（HTTP 的完成度响应仍然 verdict-free）。
    """
    legacy = legacy_stage5_paths(novel_dir)
    from novel.stages.revisions import revision_states_at

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
        owner_issues=_owner_issue_states(novel_dir),
        owner_revisions=revision_states_at(novel_dir),
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
    if node == 'str06.stage2.chapters':
        prefix = 'stage2/observations/ch'
        selected = []
        for item in stage.artifacts:
            if item.rel.startswith(prefix) and item.rel.endswith('.json'):
                digits = item.rel[len(prefix):-5]
                if digits.isascii() and digits.isdigit() and item.rel == artifact_rel(
                        STR06_STAGE2_OBS_CHAPTER, nn=int(digits)):
                    selected.append(item)
        return selected
    if node == 'str06.stage5.body':
        # 逐章路径是模板，Stage5 完成度只把问题章物化进 artifacts。
        return list(stage.artifacts)
    selected = [item for item in stage.artifacts if item.rel in spec.produces]
    if not spec.stage_required:
        return selected
    if spec.stage == 'stage5':
        # ⚠⚠ Stage5 的 `artifacts` 只物化**问题行**（干净书不把几十份章稿逐行塞进
        # 公共状态）：本节点 produces 全都健康时，它一行都没有。此时回落整层会把
        # **章稿**缺失算到 Hook 节点头上——实测 Supervisor 据此对一个已成功的节点
        # 反复派发（F8d 全链集成跑出来的死循环：每轮 rc=0、盘面不变、永远不推进）。
        # Hook 节点只认自己的两行：不在盘上（或坏/陈旧）才是它自己的 missing。
        return selected
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

def resume_verdict(
    ov: NovelOverview, node: str, *, flags: tuple[str, ...] = (),
) -> tuple[str, str]:
    """这个节点该不该跑，以及**为什么**。返回 `(结论, 一句话)`。

    ⚠⚠ **`R-14.4`：续跑前必须重新判定新鲜度。上游在中断期间变过，
    就不是「续」而是「重来」，要说出来。**
    这条不是洁癖：一次被打断的跑，人往往会先去修一份上游再接着跑——
    而「接着跑」会把一份**照旧上游产的**产物留在盘上，与新上游拼成一本混版的书。
    原型没有这条，因为它连续跑都没有。

    ⚠ 结论是五态闭集：``run / skip / redo / unknown / legacy_incompatible``。
    本函数只如实分类，不把 `redo` 压成 `run`。普通队列仍对后三态关闭式停止；
    AgentRun 的单步 operator 只能再经 :func:`operator_redo_flags` 证明存在唯一的
    canonical 重放参数，且每执行一步后重新采快照，才允许执行其中一小部分 `redo`。

    R3（2026-09-28）：盘上 open 的 typed issue 记录（`runtime/issues/`）指向
    本节点时判 `owner_revision` REDO——它先于新鲜度分支（问题是下游报告的
    机械缺陷，不是上游变了），执行通道见 :func:`operator_redo_flags`。

    F8c：旧链节点（stage2.analyze／characters／blueprint、stage4.design／plan、
    stage5.review／write、stage6／stage7 旧键）的专属分支随旧链退役删除——它们
    已不在 `NODES`，由下面 `spec is None` 早退覆盖；剩下的判定全部按 `spec` 通用派生。
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

    # R3 跨节点回派（2026-09-28）：盘上有 open 的 typed issue 记录指向本节点时，
    # 它按 owner 修订重跑——**即使本节点产物存在且新鲜**（问题不是新鲜度，是
    # 下游已经报告的机械缺陷）。此判定先于一切存在性/新鲜度分支；普通队列对
    # REDO 仍关闭式停止，AgentRun 单步 operator 经登记的 owner_revision 策略执行。
    # R8a 门控（2026-09-29）：判定唯一登记在
    # `novel/stages/owner_revision.py::revision_gate`（进程内回跳与本分支共用）
    # ——产物未变才 REDO；已变（owner 修订写回或人工 patch）则落常规分支等
    # 下游按新鲜度复验（此前照旧 REDO 会在「重买 owner → 复验仍 open → 再
    # 重买」里打转，B6-A canary 实证）；轮额度耗尽且产物未变则停给人。
    # （`getattr` 只为测试里不带该格的 SimpleNamespace 概览——正式模型恒带默认空表。）
    issue = next((i for i in getattr(ov, 'owner_issues', ()) if i.node == node), None)
    if issue is not None:
        from novel.stages.owner_revision import revision_gate
        verdict, why_gate = revision_gate(
            issue.against_sha256,
            getattr(issue, 'product_sha256', None),
            issue.rounds_total,
            no_progress=getattr(issue, 'no_progress', False),
            dispatch_pending=issue.dispatch_pending)
        if verdict == 'redo':
            return RESUME_REDO, (
                f'owner_revision：{issue.raised_by} 报告 typed 机械问题'
                f'（{", ".join(issue.codes)}），{why_gate}；'
                '修订要求随 `runtime/issues/` 记录进请求身份')
        if verdict == 'stopped':
            return RESUME_UNKNOWN, (
                f'owner_revision：{issue.raised_by} 报告 typed 机械问题'
                f'（{", ".join(issue.codes)}），{why_gate}')
        # wait_reverify：产物已变——不背 owner_revision 的锅，落常规分支。

    # ⭐ 轮次编号产物：**它有自己的账，不看 stage 级 `done`**。
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

    # 逐章观察（B2 五节点：`str06.stage2.chapters`，D3）：结论挂在「源章全都
    # 观察过了」上。`ch{nn}` 模板在通用完成度里没有行——若不单开一条账，
    # 这个已成功的节点会被 Supervisor 反复派发（F8d 同形死循环，实测复现）。
    if node == 'str06.stage2.chapters':
        # R10c（2026-10-02）：chapters 的 stale 检查只认**真上游**＝stage0/1
        # 产物＋book 观察（`run_chapters` 的实际读面）。同 stage 的
        # names/associate 观察是平行兄弟节点、compile 产物是下游——book 修订
        # 级联期间它们 file_changed/stale 直到各自重跑，把它们算进「上游变过」
        # 会让 planner 永远选秩更早的 chapters、兄弟节点饿死（两轮共 89 次零
        # 进展重派实证）。白名单而非黑名单：新 stage2 产物默认不算 chapters
        # 上游。
        upstream = ({'stage2/observations/book.json'}
                    | set(NODES['stage0.remove'].produces)
                    | set(NODES['stage1.partition'].produces))
        stale_upstream = [a for a in st.artifacts
                          if a.freshness == 'stale' and a.rel in upstream]
        if stale_upstream:
            return RESUME_REDO, (
                f'上游变过：{", ".join(a.rel for a in stale_upstream)} 已陈旧 '
                f'（{", ".join(sorted({a.reason for a in stale_upstream}))}）。'
                '⚠ 这不是「续」，是「重来」——续下去会把新旧两版上游拼进同一本书')
        observations = artifacts_for_node(ov, node)
        uncertain = [a for a in observations if not a.readable or a.freshness == 'unknown']
        if uncertain:
            return RESUME_UNKNOWN, (
                f'章观察不可读或新鲜度未知：{", ".join(a.rel for a in uncertain)}；'
                '不能从文件存在判已完成，也不能猜成缺失后重新购买')
        stale_observations = [a for a in observations if a.freshness == 'stale']
        if stale_observations:
            return RESUME_REDO, (
                f'章观察消费的上游已变化：{", ".join(a.rel for a in stale_observations)}；'
                '从本节点正式入口重放/重建，不能把旧观察交给下游')
        if any(a.invalid for a in observations):
            return RESUME_RUN, '章观察被拒收；先使用 --check-only 免费复验，再决定是否新购买'
        want = ov.chapters
        got = st.chapters_done
        if want is None:
            return RESUME_UNKNOWN, (
                '判不了：stage1 章数不可得，无法证明章观察齐了。'
                '⚠ 按没跑完重跑会重复付费，先人工核对 stage1 产物')
        missing = [n for n in range(1, want + 1) if n not in got]
        if not missing:
            return RESUME_SKIP, (
                f'全部 {want} 章的观察都已在盘上且当前'
                '（D3：章观察互不依赖，缺失单元才需要重跑）')
        return RESUME_RUN, (
            f'第 {missing} 章的观察尚未落盘（D3：逐章停，'
            '恢复按请求身份免费重放已存章）')

    # 逐章类的层（F8c：`stage5.write` → `str06.stage5.body`）：结论挂在
    # 「计划里的每个输出章都有成稿吗」上，而那可能是**判不了**的。
    if node == 'str06.stage5.body':
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
                f'先尝试免费复检 `{node} --check-only`'
                '（不签发新付费调用，按请求身份重放已购响应并按当前判据关闭式重判）；'
                '不能提升时再决定是否付费重写')
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
        # ⚠ R4：恢复建议只给**现役**免费面。`--revalidate` 旗标族已随旧链整层
        #   退役（F8b-3）：照旧文案跑只会被入口拒识。付费节点给节点级
        #   `--check-only` 免费复检；确定性免费节点（flags 为空）重跑即免费
        #   重算——不把不收 `--check-only` 的节点也塞这个旗标。
        free_route = (
            f'`{node} --check-only`（免费复检：不签发新付费调用，'
            '按请求身份重放并按当前判据关闭式重判）' if spec.paid else
            '重跑本节点（确定性节点，重跑即免费重算并按当前判据重判）')
        return RESUME_RUN, (
            f'盘上有比正式稿更晚、被闸门拦下的稿：'
            f'{", ".join(a.rel if paths.is_invalid(a.rel) else paths.invalid_of(a.rel) for a in invalid)}。'
            f'⚠ 队列会按原命令**重跑本节点**（付费节点=重新付费）；'
            f'想先省钱就走免费路 {free_route}，'
            '不能提升时再决定是否排队，或确认后再排队')
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


def _consumed_redo_surface(artifacts: list[ArtifactState]) -> bool:
    """每份 stale 输出都汇总为消费变更，且既有输出没有未知、坏稿或恢复面。"""
    stale = [artifact for artifact in artifacts if artifact.freshness == 'stale']
    if not stale or any(artifact.reason != 'consumed_changed' for artifact in stale):
        return False
    return not any(
        artifact.invalid
        or (artifact.exists and not artifact.readable)
        or (artifact.exists and artifact.freshness == 'unknown')
        for artifact in artifacts
    )


def _revision_cascade_surface(artifacts: list[ArtifactState], ov) -> bool:
    """回派级联面（R8a）：open 账的 owner 产物已变时，下游陈旧是修订写回的
    **预期后果**——`file_changed` 也接受 canonical 重放。

    只在确有 open 的跨节点回派账、且其 owner 当前产物摘要已偏离 against
    （本轮修订真实落盘，`revision_gate` 的 wait_reverify 态）时放宽；平时
    file_changed 维持关闭式（人工改字节的保护不放宽，B6-A canary 前的判据
    原样保留）。unreadable／unknown／INVALID 照旧拒绝。
    """
    issues = getattr(ov, 'owner_issues', ()) or ()
    typed_revision = any(getattr(issue, 'product_sha256', None) is not None
               and issue.product_sha256 != issue.against_sha256
               for issue in issues)
    stale = [artifact for artifact in artifacts if artifact.freshness == 'stale']
    if not stale or any(artifact.reason not in ('consumed_changed', 'file_changed')
                        for artifact in stale):
        return False
    allowed = {rel for r in getattr(ov, 'owner_revisions', ())
               if r.current_product_sha256 == r.accepted_product_sha256
               for rel in r.cascade_artifacts}
    if not (typed_revision or all(a.rel in allowed for a in stale)):
        return False
    return not any(
        artifact.invalid
        or (artifact.exists and not artifact.readable)
        or (artifact.exists and artifact.freshness == 'unknown')
        for artifact in artifacts
    )


def operator_redo_flags(ov: NovelOverview, node: str) -> tuple[str, ...] | None:
    """给 AgentRun 单步 operator 导出唯一、安全的 REDO flags。

    ``None`` 是关闭式拒绝，不是空 flags；``()`` 才表示已证明默认 producer 会完整
    重建本节点。普通 durable queue 不调用本函数，因为它会冻结多步快照；AgentRun
    每落一个节点都重新规划，所以下游只会消费刚落盘的新主体。

    有正式产物的 producer 自动准入刻意要求每份 stale 输出的权威汇总 reason 都是
    ``consumed_changed``。汇总为 ``file_changed`` 可能来自未声明消费、旧契约或人工改
    字节，unreadable/盘上既有却 unknown/INVALID 又各有自己的恢复路；把这些状态猜成
    重采会重新花钱，还可能覆盖唯一问题稿。缺失输出可由同一 canonical producer 一并
    补齐。

    F8c：旧链的 ``review``（Stage5 Review 轮次面）与 ``stale_chapters``
    （`stage5.write --chapters`）策略随旧链退役删除。R3（2026-09-28）新增
    ``owner_revision``：只认**盘上 open 的 typed issue 记录**证明的修订重跑
    （`resume_verdict` 的 owner_revision 分支），同样只经默认 flags 重跑本节点；
    stale／unknown 等其余 REDO 面对该策略仍然关闭——修订重跑不是新鲜度重采的
    后门。R8a（2026-09-29）新增**修订级联面**：open 账的 owner 产物已变
    （`revision_gate` 判 wait_reverify）时，下游 file_changed 陈旧是修订写回的
    预期后果，canonical 策略节点据此经默认 flags 重放；平时 file_changed 仍
    关闭式停给人。未登记策略名一律当场炸：节点表若再出现闭集外的名字，红在
    这里，不会静默走回猜 flags 的老路。
    """
    spec = NODES.get(node)
    strategy = spec.operator_redo if spec is not None else None
    if strategy is None:
        return None
    if strategy not in OPERATOR_REDO_STRATEGIES:
        raise AssertionError(f'{node} 登记了未知 operator_redo 策略 {strategy!r}')
    verdict, _why = resume_verdict(ov, node)
    if verdict != RESUME_REDO:
        return None

    if strategy == 'owner_revision':
        # R3：只有 open issue 指向本节点才可自动修订重跑；verdict 的 REDO 若来自
        # 新鲜度（无 open issue），走 R8a 修订级联面——上游真值被修订轮改写时，
        # 本节点按同一 canonical 面经默认 flags 重放（不猜参数）。
        if any(issue.node == node for issue in getattr(ov, 'owner_issues', ())):
            return ()
        if _revision_cascade_surface(artifacts_for_node(ov, node), ov):
            return ()
        return None

    artifacts = artifacts_for_node(ov, node)
    if not (_consumed_redo_surface(artifacts)
            or _revision_cascade_surface(artifacts, ov)):
        return None
    return ()
