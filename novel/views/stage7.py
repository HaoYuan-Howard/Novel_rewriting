#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage7.py —— S-12「人名替换 + 成书」那一屏（`GET /api/novels/:sid/stage7[/:o]`）。

【这一屏回答的问题】新人名起得对吗？替换干净了吗？有没有漏掉一个人？
（`02_screens.md` S-12：新旧人物表并排 · 漏网叫法候选 · 逐章替换前后对照 ·
文案前后对照 · 定稿按钮 · 成书状态。正文**只读**——改正文要回 stage5。）

【逐章对照 `rows` 是现算的，且必须用同一个替换器】
`novel/stages/stage7/subs.py::apply_subs` 是替换的唯一落点（单次扫描 · 最长优先 ·
保留项进 alternation）。view 在这里写第二个替换器，就是「同一件事的第二个落点」
——两边必然分家，而人看的对照与真替换对不上比没有对照更坏。

【`hits` 是按人分组的求和】`applied` 记账是产地按**字面**记的（`{old: 次数}`），
人物表按**人**看 ⇒ view 把一个人的全部字面（canonical + aliases 的 old）求和。
读数不是判定。
"""
from __future__ import annotations

from contracts.artifacts import STAGE6_HOOK, STAGE7_NAME_MAP

import os
import re

from contracts.schema.api import (
    HookRename, NameDecisionLine, RenameRow, Stage7View,
)
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from novel import paths, prov
from novel.errors import ArtifactUnreadable, HumanEditProtected, UpstreamMissing
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.stages.stage5.measure import (
    is_legacy_stage5_rel, legacy_stage5_paths, load_drafts,
)
from novel.stages.stage7.subs import apply_subs, sub_table
from novel.stages.stage7.rename import approval_subject_sha256
from novel.store import ArtifactStore

NODE = 'stage7.rename'
REL = STAGE7_NAME_MAP
HOOK_REL = STAGE6_HOOK
#: ⚠ 成书的 rel 带书名 ⇒ 没有模块级常量可存（`STAGE7_BOOK` 只是模板）。
#: 逐次 `paths.book_txt_rel(novel_dir)`，落点仍只有那一个。


def stage7_view(base: str | os.PathLike[str], sid: str,
                ordinal: int | None = None) -> Stage7View:
    """S-12 的一屏。**纯读**。书级（`ordinal=None`）与逐章对照共用一个模型。

    ⚠ `:o` 不在本层判过的章集里 ⇒ `UpstreamMissing`（404）——
    「资源不存在」，不是「还没实现」。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))

    live_legacy = legacy_stage5_paths(store)
    if live_legacy:
        raise HumanEditProtected(
            f'legacy_incompatible：发现旧 Stage5 双臂路径 {live_legacy[:8]}。'
            '旧产物只读保留，不能退化成 Stage7 missing，也不能据此触发重跑',
            hint='在独立 canonical Stage5 base 重建 Stage6/7 消费链后再打开本屏',
        )
    st = artifact_state(store, REL)
    art, _raw = store.read_own(NameMap, REL)
    legacy = sorted(rel for rel in art.judged if is_legacy_stage5_rel(rel))
    if legacy:
        raise HumanEditProtected(
            f'legacy_incompatible：{REL} 的 judged 仍绑定旧 Stage5 双臂路径 {legacy}。'
            '旧产物只读保留，不能在当前 Stage7 视图展示，也不能据此触发重跑',
            hint='在独立 canonical Stage5 base 重建 Stage6/7 消费链后再打开本屏',
        )

    chapters = sorted({jd.output_ordinal for jd in art.judged.values()})
    rows = None
    if ordinal is not None:
        if ordinal not in chapters:
            raise UpstreamMissing(
                f'第 {ordinal} 章不在 stage7 判过的章集里（{chapters}）——'
                f'没有这一章的替换对照')
        rows = _rows(store, art, ordinal)

    book_txt = paths.book_txt_rel(novel_dir)
    return Stage7View(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        surname_map=dict(art.surname_map),
        characters=[_decision_line(d, art) for d in art.characters],
        extra_subs=art.extra_subs,
        subs_count=len(art.subs),
        kept_count=len(art.kept),
        residual=art.residual,
        baselines=dict(art.baselines),
        approved=art.approved,
        approval_subject_sha256=approval_subject_sha256(art),
        book=art.book,
        identity=art.identity,
        book_txt=art.book_txt,
        book_txt_on_disk=store.exists(book_txt),
        book_filename=os.path.basename(book_txt),
        sources=art.sources,
        chapters=chapters,
        ordinal=ordinal,
        rows=rows,
        hook=_hook_rename(store, art),
        invalid_exists=st.invalid,
        freshness=st.freshness,
        reason=st.reason,
        ghosts=list(st.ghosts),
        _provenance=art.provenance,
    )


def _decision_line(d, art: NameMap) -> NameDecisionLine:
    """一行人物表 + 按人求和的 `hits`（恒等条目产地不计数，这里照旧）。"""
    faces = {d.old_canonical} | {a.old for a in d.aliases}
    hits = sum(face.counts.get(f, 0)
               for face in [*art.applied.chapters.values(), art.applied.hook]
               for f in faces)
    return NameDecisionLine(
        char_id=d.char_id, old_canonical=d.old_canonical,
        new_canonical=d.new_canonical, renamed=d.renamed,
        not_renamed_reason=d.not_renamed_reason, reason=d.reason,
        aliases=d.aliases, hits=hits)


#: 段落切分。**只按换行切**——替换面里没有含换行的字面，逐段替换 ≡ 整章替换。
_LINES = re.compile(r'\n')


def _rows(store: ArtifactStore, art: NameMap, ordinal: int) -> list[RenameRow]:
    """该章成稿的替换前后对照。**当前盘上的成稿 × 当前产物里的替换表**。

    ⚠ 成稿一律走 `load_drafts`（`read_judged` 那条路）——`store.read` 对三级路径
    是 `PathRefused`，这不是绕路，是那条唯一读路本来的形状。
    canonical Stage5 每章只有一份；旧 deep/shallow provenance 由下游消费闸明确报
    `legacy_incompatible`，本屏不替人挑一份。
    """
    by_ord, _invalids, _judged = load_drafts(store)
    rec = by_ord.get(ordinal)
    if rec is not None and rec['_relpath'] not in art.judged:
        rec = None
    if rec is None:
        raise UpstreamMissing(
            f'stage5 里第 {ordinal} 章的成稿不在盘上——'
            f'对照的左半边没有来源（本层判过它，之后它被移走了）')
    pat, _table = sub_table(art.subs, art.kept)
    out: list[RenameRow] = []
    for line in _LINES.split(rec['text']):
        if not line.strip():
            continue
        new, _counts, _claimed = apply_subs(line, art.subs, pat, art.kept)
        out.append(RenameRow(l=line, r=new, changed=new != line))
    return out


def _hook_rename(store: ArtifactStore, art: NameMap) -> HookRename | None:
    """文案前后对照。文案没定稿（或 stage6 没跑 / 读不回）⇒ `null`——
    这一块是**辅助对照**，它的缺席不该带走整屏（文案自身的处境归 S-11）。"""
    try:
        hook, _raw = store.read_own(Hook, HOOK_REL)
    except (UpstreamMissing, ArtifactUnreadable):
        return None
    if hook.approved is None:
        return None
    before = hook.approved.text
    after, _counts, _claimed = apply_subs(before, art.subs, kept=art.kept)
    return HookRename(before=before, after=after)
