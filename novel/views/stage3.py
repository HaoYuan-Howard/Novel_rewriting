#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage3.py —— S-06「文风」那一屏（`GET /api/novels/:sid/stage3/style`）。

【这一屏的形状是被一句需求定死的】`04_delivery.md:123`：
「**S-06 文风：`exists: false` 时左半边（实测）仍有内容**」。
那不是花哨——**左半边量的是 stage1，与 stage3 有没有跑过无关**。
⇒ 本模块分两半各自失败：产物读不回来不影响实测，实测量不出来不影响产物。
把它们捆在一个 try 里，一边坏掉就会把另一边一起变成空屏。

【一个函数都不新造】
  · 「这份产物此刻的处境」→ `novel.orchestrator.state.artifact_state`
    （exists / readable / freshness / reason / ghosts / invalid 六格一次拿全）
  · 「读哪几份上游、投影是什么」→ `novel.stages.stage3.style` 的常量与投影函数
  · 「量出什么」→ `novel.stages.stage3.measure.measure_all`
  · 「档位词」→ `novel.core.bands.bands`（**现算不落盘**：盘上只有原始数字）
  · 「逐项漂移」→ `novel.core.measure.drift`（与 `style.remeasured()` 同一个函数）
"""
from __future__ import annotations

import os

from contracts.schema.api import DriftItem, HookStyleLine, StyleView, VoiceLine
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage3 import StyleGrounding
from novel import paths, prov
from novel.core.bands import bands
from novel.core.measure import drift
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.stages.stage3 import style
from novel.stages.stage3.measure import hook_text_of, measure_all
from novel.store import ArtifactStore

#: 提问者。它只影响错误信息里那个前缀（新鲜度是**产物的**属性，不是提问者的），
#: 但既然本屏就是 stage3 那份产物的屏，写死产它的那个节点最不容易误导人。
NODE = 'stage3.style'


def style_view(base: str | os.PathLike[str], sid: str) -> StyleView:
    """S-06 的一屏。**纯读**：不写一个字节，不发一个请求，不产生任何判定。

    ⚠ 它**不带 `badge` / `passed` / `counts`**——那一屏的结论只能来自
    `/api/novels/:sid/check/stage3`。视图给的是「两份数字并排放着让人自己看」
    （`R-11` 节首语的原话），判定给的是「合不合格」。混在一格里就是第二个判官（`R-11.4`）。

    ⚠ 找不到这本书 ⇒ 抛 `UpstreamMissing`（由 HTTP 层翻成 404）。
    那与「这本书在、只是没跑过 stage3」是两件事，后者是 `exists=False` 的 **200**。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))

    st = artifact_state(store, style.REL)
    loaded = _load(store) if (st.exists and st.readable) else None
    measured_now, measure_error, measure_error_kind, hook_copy_text = _measure(store)

    stored = loaded.measurable if loaded is not None else None
    return StyleView(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        exists=st.exists,
        condition=st.condition,
        invalid_exists=st.invalid,
        # ⚠ 三态：不在盘上时是 `None`，不是 `True`。`ArtifactState` 那边用 `True` 兜底
        # （它的 `readable` 是二值的），本屏把「无从谈起」显式建模成第三个值。
        readable=st.readable if st.exists else None,
        voice=[VoiceLine(key=k, text=v, chars=len(v))
               for k, v in (loaded.voice.model_dump().items() if loaded else ())],
        surface_habits=list(loaded.surface_habits) if loaded else [],
        hook_style=[HookStyleLine(key=k, text=v)
                    for k, v in (loaded.hook_style.model_dump().items() if loaded else ())],
        measurable_stored=stored,
        bands_stored=bands(stored) if stored is not None else None,
        _provenance=loaded.provenance if loaded is not None else None,
        hook_copy_text=hook_copy_text,
        measured_now=measured_now,
        bands_now=bands(measured_now) if measured_now is not None else None,
        measure_error=measure_error,
        measure_error_kind=measure_error_kind,
        # ⚠ `[]` = 「比过了，没有漂移」；`None` = 「有一边算不出来，**无从比**」。
        drift=(_drift(stored, measured_now)
               if stored is not None and measured_now is not None else None),
        freshness=st.freshness,
        reason=st.reason,
        ghosts=list(st.ghosts),
    )


def _load(store: ArtifactStore) -> StyleGrounding | None:
    """右半边。`artifact_state` 已经答过「读不读得回来」，这里只是把内容取出来。

    ⚠ 仍然 `try`：那两次读之间盘上可能变了（长驻服务，人随时在跑节点）。
    拿 `st.readable` 当**保证**而不是**观察**，就是一个典型的 TOCTOU。
    """
    try:
        return store.read_own(StyleGrounding, style.REL)[0]
    except ArtifactUnreadable:
        return None


def _measure(store: ArtifactStore) -> tuple[object, str | None, str | None, str | None]:
    """左半边。**量的是 stage1，与 stage3 那份产物在不在无关。**

    ⚠ 只捕 `UpstreamMissing` / `ArtifactUnreadable` 这两族——它们是**数据说走不下去了**
    （`TerminalState`）。`RefusedByStructure` 那一族是**我们的 bug**，
    捕住它等于把一个代码自相矛盾渲染成界面上一句温和的「量不出来」，
    而 `novel/errors.py` 开头写着那一族「绝不允许被降级成一句警告」。
    """
    try:
        main = store.read(MainText, style.MAIN_TEXT)
        hook = store.read(HookCopy, style.HOOK_COPY)
        return measure_all(main, hook), None, None, hook_text_of(hook)
    except (UpstreamMissing, ArtifactUnreadable) as e:
        # ⚠ **id 与措辞各出一格**（`Q-6`）：类名给前端做分支（「先跑 stage1」还是
        # 「去看那份产物」是相反的动作），句子给人看。靠前缀去认句子会随一次措辞改动静默断掉。
        return None, str(e), type(e).__name__, None


def _drift(stored, now) -> list[DriftItem]:
    """存/现两份实测的逐项差异。**与 `style.remeasured()` 走同一个 `drift()`。**

    ⚠ 出线时从三元组换成具名对象：元组一旦出线就是一个**位置数组**，
    前端拿到 `["body.chars", 9516, 9520]` 只能靠下标认格子。
    """
    return [DriftItem(key=k, stored=_jsonable(a), now=_jsonable(b))
            for scope in ('body', 'hook')
            for k, a, b in drift(getattr(stored, scope), getattr(now, scope))]


def _jsonable(v: object) -> object:
    """`drift()` 内部把 list 摊平成了 tuple（为了可哈希比较）。出线换回 list。"""
    return list(v) if isinstance(v, tuple) else v
