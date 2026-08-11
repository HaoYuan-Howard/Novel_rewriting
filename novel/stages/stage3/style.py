#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
style.py —— 本层的**节点函数**。`argv` 那一头留给 Phase D（`build_argv` / `build_steps`）。

三条 0 成本路先落地，付费路最后接：
  · `run_check_only(ctx)`   复检一份已有产物 → `CheckReport`
  · `run_remeasure(ctx)`    照盘上 stage1 重算 `measurable` 写回（**0 成本**）
  · `run_revalidate(ctx)`   按**当前**判据重判 `.INVALID`，合格即提升

⚠ 三条都**不发一次请求**。付费路（`--dry-run` 先验 prompt）见 `prompts.py`。
"""
from __future__ import annotations

from contracts.schema.report import CheckReport
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage3 import (
    Measurable, StyleAttempt, StyleGrounding, StyleProvenance, StyleResponse,
)
from novel.core.naming import names_of, spoiler_names_of
from novel.core.text import render_book
from novel.errors import GateBlocked, UpstreamMissing
from novel.llm.client import LlmClient
from novel.prov import fingerprint
from novel.report import build
from novel.run import RunContext
from novel.stages.stage3 import checks, prompts
from novel.stages.stage3.measure import corpus_of, measure_all
from novel.store import ArtifactStore

REL = 'stage3/style_grounding.json'
MAIN_TEXT = 'stage1/main_text.json'
HOOK_COPY = 'stage1/hook_copy.json'
GLOBAL_GROUNDING = 'stage2/global_grounding.json'


# ── `consumed` 投影：本层**真正读到**上游的哪一部分 ──────────────────────────
#
# ⚠⚠ **这是全链第一次有节点声明投影。** 在此之前八层一个都没有，于是新鲜度恒为
# `unknown`——那是契约要的，不是 bug（`_common.md` §三「建槽位，不填槽位」）。
# 声明之后 `fresh` 才第一次真的出现。
# ⚠ 投影吃的是**盘上那份原始 dict**（不是模型），理由见 `store.read`：
# 判过期时要对一份**可能已经不合当前 schema** 的上游重算同一个投影。

def _main_text_projection(data: object) -> object:
    """正文：**只读 `text`**。章号/原文标号改了不影响本层的任何一个数——
    `measure_body` 只吃 `text`，`corpus` 也只吃 `text`。"""
    return [s.get('text') for s in data] if isinstance(data, list) else None


def _hook_projection(data: object) -> object:
    """文案段：**只读 `text`**。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


def _grounding_projection(data: object) -> object:
    """全书事实底座：**只读名表要用的那几格 + 秘密**（`canonical` / `address[].surface` /
    `name_distinctiveness` / `secrets`）。

    ⚠ 这份上游有**几千行**，而本层只在校验期拿它当名表。不声明投影的话，
    stage2 改一个不相干的 `role` 措辞就会把 stage3 的产物判成 `stale`
    ——那正是 `Q-4` 说的「把一批结论降级成『读数不可用』，且没有重新挂钩的路」。
    """
    if not isinstance(data, dict):
        return None
    chars = ((data.get('entities') or {}).get('characters') or {})
    return {
        'characters': sorted(
            (cid, c.get('canonical'), c.get('name_distinctiveness'),
             tuple(sorted((a or {}).get('surface') or '' for a in (c.get('address') or []))))
            for cid, c in chars.items() if isinstance(c, dict)),
        'secrets': sorted(
            (s.get('secret_id'), s.get('fact'),
             tuple(sorted((e or {}).get('chapter') or 0
                          for e in (s.get('reader_reveal_events') or []))))
            for s in ((data.get('canon') or {}).get('secrets') or []) if isinstance(s, dict)),
    }


def _upstreams(store: ArtifactStore) -> tuple[MainText, HookCopy, GlobalGrounding | None]:
    """三份上游。**第三份缺失不是硬错**——它只服务 `name_leak`，缺了那条判据落 `unver`。"""
    main = store.read(MainText, MAIN_TEXT, project=_main_text_projection)
    hook = store.read(HookCopy, HOOK_COPY, project=_hook_projection)
    gg = (store.read(GlobalGrounding, GLOBAL_GROUNDING, project=_grounding_projection)
          if store.exists(GLOBAL_GROUNDING) else None)
    return main, hook, gg


def run_check_only(ctx: RunContext) -> CheckReport:
    """0 成本复检一份已有产物。**不写盘、不发请求。**

    ⚠ 它**恒会算** `measured_now`，所以 `measurable_drift` 每次都真的跑
    （落盘闸门那一趟才不排它——那时两边必然相等）。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage3.style')

    main, hook, gg = _upstreams(store)
    _artifact, data = store.read_own(StyleGrounding, REL)

    names = names_of(gg) if gg is not None else None
    outcomes = checks.check(
        data,
        corpus=corpus_of(main, hook),
        names=names,
        spoiler_names=spoiler_names_of(gg, names) if gg is not None and names else frozenset(),
        measured_now=measure_all(main, hook),
    )
    prov = data.get('_provenance') or {}
    return build(
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(REL),
        outcomes=outcomes, accounting=_accounting_of(prov),
        # `.INVALID` 在场是**上一次跑被闸门拦下来的痕迹**。留着不说等于静默：
        # 盘上那份正式稿可能是**更早**的一版，而人会以为它就是最近这次跑出来的。
        invalid=store.exists(REL.replace('.json', '.INVALID.json')),
        revalidated=prov.get('revalidated'), origin=prov.get('origin'),
    )


def _accounting_of(prov: dict) -> dict:
    """`_provenance` 里那三个记账键 → 报告的记账节。

    ⚠⚠ **这是在还 `stage3.md` §五 的那笔账**：实跑三本 `--check-only`，报告里
    `attempts` / `revalidated` / `gates_skipped` **一行都没有**，而它们记的恰好是
    最该被人看见的三件事。`gates_skipped` 尤其要紧——它是判据 36 那整套设计的**唯一痕迹**，
    而「闸门没跑成」与「闸门跑过没事」正是 `Q-2` 要求必须能区分的两件事。
    ⚠ **只放产物里真有的键**：键不在 ⇒ 报告打 `[????] 未记录`，那与「记了 0 次」不是一回事。
    """
    return {k: prov[k] for k in ('attempts', 'revalidated', 'gates_skipped') if k in prov}


# ── 付费路 ──────────────────────────────────────────────────────────────────
#
# ⚠⚠ **本函数写好了，但在你点头之前一次都不许真跑**（那是本仓第一次真花钱）。
#    先 `--dry-run`：`build_prompt()` 是 0 成本的，它把要发出去的两段原样交出来。

def build_prompt(ctx: RunContext) -> tuple[str, str, Measurable]:
    """`--dry-run`：把这次**要发出去的** `(system, user)` 原样交出来。**0 成本。**

    ⚠ 它与真跑共用同一个构造入口——展示与执行必须共用同一份构造函数（`R-11.8`），
    否则「预览里看到的」与「真发出去的」是两件事，而那种分家没有任何东西会报错。
    """
    store = ArtifactStore(ctx)
    main, hook, _gg = _upstreams(store)
    meas = measure_all(main, hook)
    book_text, section_index = render_book(main)
    user = prompts.build_user(meas, book_text=book_text, section_index=section_index,
                              hook_text=(hook.text or '').strip(),
                              title=ctx.title, sid=ctx.sid)
    return prompts.style_system(), user, meas


def run_style(ctx: RunContext, client: LlmClient) -> StyleGrounding:
    """**付费**：抽一份文风画像，先校验后落盘。

    ⚠ `require` **不显式传**：它从 `model_cls=StyleResponse` 派生成三个必需顶层键。
    原型这个调用点写的是 `require=('voice', 'surface_habits')`——**漏了 `hook_style`**，
    而漏传不报错，它只是**静默停掉一条 HARD 闸门**（判据 39）。

    落盘闸门（`Q-5`：先校验后落盘）：有 `hard` 命中 ⇒ **不覆盖已有产物**，
    问题稿另存 `.INVALID` + `GateBlocked`。0 成本提升路是 `--revalidate`。
    """
    store = ArtifactStore(ctx)
    system, user, meas = build_prompt(ctx)
    resp = client.ask_json(system, user, model_cls=StyleResponse, purpose=ctx.node)

    artifact = StyleGrounding(
        voice=resp.voice, surface_habits=resp.surface_habits, hook_style=resp.hook_style,
        measurable=meas,
        **{'_provenance': ctx.llm_provenance(
            StyleProvenance, prompts={'STYLE_SYS': system}, model=client.model,
            attempts=[StyleAttempt(kind='initial',
                                   user_prompt=fingerprint(user),
                                   output=fingerprint(resp.model_dump_json()),
                                   cache_policy=ctx.cache.name)])})

    # 落盘那一趟**不排** `measurable_drift`：`measurable` 刚由代码算出来，
    # 与「照盘上 stage1 重算」必然相等——一条永远不会红的检查不该打 `[OK]`（判据 31）。
    outcomes = _judge(store, artifact.model_dump(by_alias=True), measured_now=None)
    hard = [o for o in outcomes if o.findings
            and any(f.sev == 'hard' for f in o.findings)]
    if hard:
        # ⚠ `gates_skipped` **空列表也要写**：「这次没有闸门缺料」与「这份产物早于
        # 这套机制」必须分得开（`_common.md` §五）。本层串行跑，恒是空。
        p = store.write_invalid(REL, artifact, gates_skipped=[])
        raise GateBlocked(
            f'stage3 落盘闸门拦下这一份：{[o.rule for o in hard]}。'
            f'**稿子没有丢，也没有再花钱**——问题稿在 {p}，'
            f'人手改一行之后跑 `--revalidate`（0 成本）',
            invalid_path=str(p))
    store.write(REL, artifact)
    return artifact


def _judge(store: ArtifactStore, data: dict, *, measured_now: Measurable | None):
    """判据的**唯一调度点**：付费路的落盘闸门与 `--check-only` 走同一条。"""
    main = store.read(MainText, MAIN_TEXT, project=_main_text_projection)
    hook = store.read(HookCopy, HOOK_COPY, project=_hook_projection)
    gg = (store.read(GlobalGrounding, GLOBAL_GROUNDING, project=_grounding_projection)
          if store.exists(GLOBAL_GROUNDING) else None)
    names = names_of(gg) if gg is not None else None
    return checks.check(
        data, corpus=corpus_of(main, hook), names=names,
        spoiler_names=spoiler_names_of(gg, names) if gg is not None and names else frozenset(),
        measured_now=measured_now)


def remeasured(ctx: RunContext) -> tuple[StyleGrounding, list[tuple[str, object, object]]]:
    """照盘上 stage1 重算 `measurable`，返回 `(新产物, 逐项差异)`。**纯算，不写盘。**

    ⚠ 拆成「算」与「写」两半，是为了让 `--remeasure` 的**对账**可以零成本跑
    （盘上三本 × 23 项逐值比对），而不必先落一次盘。
    """
    store = ArtifactStore(ctx)
    main, hook, _gg = _upstreams(store)
    artifact, _data = store.read_own(StyleGrounding, REL)
    now = measure_all(main, hook)
    from novel.core.measure import drift
    diff = [d for scope in ('body', 'hook')
            for d in drift(getattr(artifact.measurable, scope), getattr(now, scope))]
    return artifact.model_copy(update={'measurable': now}), diff
