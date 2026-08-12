#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 24 条判据的**实现**。行在 `contracts/judges/stage4.py`（`Q-12`）。

⚠⚠ **唯一的判定落点。** 落盘闸门 / `--check-only` / `--revalidate` 走的都是
`check()` 这一个函数——三条路各判一遍就会长出三把尺子。

⚠⚠ **本模块吃 `dict` 不吃模型。** 落盘闸门跑在 `model_validate` **之前**那份 dict 上，
理由见 `novel/stages/stage4/__init__.py` ③：`PlanChapter` 上挂着
`output_ordinal == from_source_ordinals[0]` 的 `model_validator`，
模型化之后 `one_to_one` 那条 HARD **根本构造不出会红的样本**。

【本层的两份产物共用这一张表】前 17 条判计划，`ctx_*` 那 4 条判写作上下文，
3 条度量各报一份读数。**上下文不在场时那 4 条走 `unver` 臂，不是「通过」。**
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from contracts.invariants import (
    BEAT_ALERT_MAX, BEAT_ALERT_MIN, BEAT_MIN_CHARS, GROUPING_VERSION, LOCKED_OP, OP_BAND,
    STYLE_WORDS, VERBATIM_BUDGET_FACTOR,
)
from contracts.schema.common import Finding
from contracts.schema.report import finding
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage4.beats import beat_index, group_beats
from novel.stages.stage4.measure import chapter_source_chars, verbatim_share_stats

#: 源 beat 编号里的章号。⚠ **别按定宽切片**：`b{ord:02d}_{seq:02d}` 在三位数的章上会溢出宽度。
_BEAT_CH = re.compile(r'b(\d+)_')

#: `writer_context` 不在场时，那四条后置条件判据的说辞。**一句，四条共用。**
_NO_CTX = ('盘上没有 `stage4/writer_context.json`（或它读不回来）——'
           '**这四条查的是那份产物，不是这份计划**。跑 `stage4.context` 组一份出来再判。'
           '⚠ 这不是「通过」：一条查不了的后置条件与一条查过没事的，在报告上必须长得不一样')


def _ch_of(beat_id: str | None) -> int | None:
    m = _BEAT_CH.match(beat_id or '')
    return int(m.group(1)) if m else None


def check(plan: dict[str, Any], *, blueprints: dict[str, Any], main_text: list[dict[str, Any]],
          global_g: dict[str, Any] | None = None,
          ctx: dict[str, Any] | None = None, ctx_link: tuple[str, str] | None = None,
          ctx_source: str | None = None,
          bidx: dict[str, dict[str, Any]] | None = None) -> list[Outcome]:
    """对一份计划（+ 可选的写作上下文）跑全部确定性判据 → `[Outcome]`。

    **一条判据恰好一个 `Outcome`**，一条都不许少：`build()` 遍历判据注册表，
    注册了而这里没产出 ⇒ 强制 `skipped`，徽章至少 amber。

    `ctx_link` = 上下文与盘上这份计划的对应关系，由调用方给：
      · `None` ⇒ 这一趟根本没排 `context_link`（落盘路径上它必然成立）；
      · `('ok'|'stale'|'unknown'|'missing', 详情)` ⇒ 据此出结论。
    """
    if bidx is None:
        bidx = beat_index(blueprints, main_text)
    outs = plan.get('output_chapters') or []
    out: list[Outcome] = []

    def fs(rule: str, *rows: tuple[str, list[str]]) -> list[Finding]:
        return [finding(rule, detail=d, evidence=e) for d, e in rows]

    # ── ① 一章对一章 ─────────────────────────────────────────────────────
    source_ords = {m.get('chapter_ordinal') for m in main_text}
    rows: list[tuple[str, list[str]]] = []
    flat: list[Any] = []
    for o in outs:
        srcs = o.get('from_source_ordinals') or []
        if len(srcs) > 1:
            rows.append((f'禁止合并章：输出章 {o.get("output_ordinal")} 来自多个源章 {srcs}',
                         [f'out{o.get("output_ordinal")}']))
        flat.extend(srcs)
    for i in range(1, len(flat)):
        if flat[i] < flat[i - 1]:
            rows.append((f'禁止重排：源章序列在 …{flat[i - 1]},{flat[i]}… 处非递减',
                         [f'src{flat[i - 1]}', f'src{flat[i]}']))
            break
    if set(flat) != source_ords:
        rows.append((f'源章需全覆盖：缺 {sorted(source_ords - set(flat))} '
                     f'多 {sorted(set(flat) - source_ords)}',
                     [f'src{s}' for s in sorted((source_ords - set(flat)) | (set(flat) - source_ords))]))
    if (reused := sorted({s for s in flat if flat.count(s) > 1})):
        rows.append((f'禁止拆分：源章 {reused} 被多个输出章共用', [f'src{s}' for s in reused]))
    # 【恒等式】禁合并 + 禁重排 + 禁删章 + 禁拆分 ⇒ 输出章与源章一一对应且同号。
    # 把这个新蕴含出来的不变量做成**会报错的检查**，而不是一句注释。
    bad_identity = [(o.get('output_ordinal'), o.get('from_source_ordinals')) for o in outs
                    if (o.get('from_source_ordinals') or [None])[0] != o.get('output_ordinal')]
    if bad_identity:
        rows.append((f'output_ordinal 必须恒等于它的源章号（一章对一章的合取式）：{bad_identity[:8]}',
                     [f'out{oo}' for oo, _ in bad_identity]))
    out.append(ran('one_to_one', fs('one_to_one', *rows),
                   scanned=f'{len(outs)} 个输出章 × 4 条禁令'))

    ords = [o.get('output_ordinal') for o in outs]
    out.append(ran('ordinal_seq',
                   fs('ordinal_seq', (f'output_ordinal 非 1..N 连续：{ords}', ['ordinals']))
                   if ords != list(range(1, len(ords) + 1)) else [],
                   scanned=f'{len(ords)} 个章号'))

    # ── ② 引用与覆盖 ─────────────────────────────────────────────────────
    valid = {be.get('beat_id') for b in (blueprints.get('blueprints') or [])
             for be in (b.get('beats') or [])}
    used: set[str] = set()
    bad_ref: list[tuple[Any, Any]] = []
    off_src: list[tuple[Any, Any]] = []
    bad_w: list[tuple[Any, Any]] = []
    empty: list[Any] = []
    n_beats = n_refs = 0
    for o in outs:
        srcs = set(o.get('from_source_ordinals') or [])
        beats = o.get('beats') or []
        if not beats:
            empty.append(o.get('output_ordinal'))
        n_beats += len(beats)
        for be in beats:
            bc = be.get('budget_chars')
            if not isinstance(bc, int) or isinstance(bc, bool) or bc <= 0:
                bad_w.append((o.get('output_ordinal'), bc))
            for bid in (be.get('from_beats') or []):
                n_refs += 1
                if bid not in valid:
                    bad_ref.append((o.get('output_ordinal'), bid))
                    continue
                used.add(bid)
                if (ch := _ch_of(bid)) is not None and ch not in srcs:
                    off_src.append((o.get('output_ordinal'), bid))

    out.append(ran('empty_chapter',
                   fs('empty_chapter',
                      (f'输出章没有 beats（计划必须详细到写手能直接写）：{empty}',
                       [f'out{o}' for o in empty])) if empty else [],
                   scanned=f'{len(outs)} 个输出章'))

    ref_rows = []
    if bad_ref:
        ref_rows.append((f'from_beats 引用不存在的 beat_id：{bad_ref[:8]}',
                         [f'out{oo}:{b}' for oo, b in bad_ref]))
    if off_src:
        ref_rows.append((f'from_beats 的 beat 不属于该输出章的源章：{off_src[:8]}',
                         [f'out{oo}:{b}' for oo, b in off_src]))
    out.append(ran('beat_ref', fs('beat_ref', *ref_rows), scanned=f'{n_refs} 条引用'))

    out.append(ran('budget_int',
                   fs('budget_int',
                      (f'budget_chars 非正整数（写手按拍落笔，拍上的字数是它唯一对得齐的东西）：'
                       f'{bad_w[:8]}', [f'out{oo}' for oo, _ in bad_w])) if bad_w else [],
                   scanned=f'{n_beats} 拍'))

    lost = sorted(valid - used)
    out.append(ran('beat_coverage',
                   fs('beat_coverage',
                      (f'源 beat 未被任何一拍覆盖（压缩靠调低 budget_chars，不靠丢内容），'
                       f'丢了 {len(lost)} 个：{lost[:10]}',
                       [b for b in lost[:20]])) if lost else [],
                   scanned=f'{len(valid)} 条源 beat'))

    # ── ②.5 三条后置条件 + 分组参数 ──────────────────────────────────────
    order = {be.get('beat_id'): i
             for b in (blueprints.get('blueprints') or [])
             for i, be in enumerate(b.get('beats') or [])}
    layer_of = {be.get('beat_id'): be.get('time_layer')
                for b in (blueprints.get('blueprints') or [])
                for be in (b.get('beats') or [])}
    disorder: list[Any] = []
    nonadj: list[tuple[Any, int, list[str]]] = []
    xlayer: list[tuple[Any, int, list[str], list[Any]]] = []
    for o in outs:
        oo = o.get('output_ordinal')
        seq: list[int] = []
        for bi, be in enumerate(o.get('beats') or [], 1):
            fb = [x for x in (be.get('from_beats') or []) if x in order]
            idxs = [order[x] for x in fb]
            seq += idxs
            # 相邻 = 源序上的一段连续区间；一拍内自己也不许倒序
            if idxs and (idxs != sorted(idxs) or max(idxs) - min(idxs) + 1 != len(set(idxs))):
                nonadj.append((oo, bi, fb))
            if len({layer_of.get(x) for x in fb}) > 1:
                xlayer.append((oo, bi, fb, [layer_of.get(x) for x in fb]))
        if seq != sorted(seq):
            disorder.append(oo)

    # ⚠ **先比一次分组参数再判**（`R-5.11b`）：三条后置条件是拿**当前**的
    #   `group_beats` 去量的，而产物记着它是照哪一套切的。不比就是拿新尺子量旧布。
    params = ((plan.get('_provenance') or {}).get('params') or {})
    recorded = (params.get('grouping'), params.get('beat_min_chars'))
    current = (GROUPING_VERSION, BEAT_MIN_CHARS)
    if not params:
        out.append(unverifiable(
            'params_drift',
            f'这份计划没有记下分组参数（`_provenance.params` 缺失）⇒ **说不清**它的拍是照'
            f'哪一套切的。下面三条后置条件仍然照当前这套（{current[0]}，阈值 {current[1]} 字）判，'
            f'而它们本该先比一次。⚠ **缺失记「不知道」不是「匹配」**——压成匹配就是替它担保',
            scanned='1 份来源账'))
    elif recorded != current:
        out.append(ran('params_drift', fs(
            'params_drift',
            (f'**这是跨版本比较**：这份计划的拍是照 {recorded[0]}（阈值 {recorded[1]} 字）切的，'
             f'而下面三条后置条件拿的是当前这套 {current[0]}（阈值 {current[1]} 字）。'
             f'先看这一行再看那三条的判定',
             [f'{recorded[0]}→{current[0]}'])), scanned='1 份来源账'))
    else:
        out.append(ran('params_drift', [], scanned=f'比过：{current[0]}，阈值 {current[1]} 字'))

    out.append(ran('beat_seq',
                   fs('beat_seq',
                      (f'输出章 {disorder} 的拍序与源序不一致——写手拿到的 `events[]` 就是这个顺序，'
                       f'**重排会一路传到成稿而无人出声**。拍由 `group_beats` 确定性切、'
                       f'顺序恒等于源序：红在这里说明这份计划是旧的，或分组函数坏了',
                       [f'out{o}' for o in disorder])) if disorder else [],
                   scanned=f'{len(outs)} 个输出章'))
    out.append(ran('beat_adjacent',
                   fs('beat_adjacent',
                      (f'{len(nonadj)} 拍并的不是相邻的源拍（输出章, 第几拍, from_beats）：'
                       f'{nonadj[:6]}——跳过中间那条去并两头，等于把它从叙述里挪了位置',
                       [f'out{oo}#{bi}' for oo, bi, _ in nonadj])) if nonadj else [],
                   scanned=f'{n_beats} 拍'))
    out.append(ran('beat_layer',
                   fs('beat_layer',
                      (f'{len(xlayer)} 拍并了叙述层不同的源拍：{xlayer[:4]}'
                       f'——写手在一拍之内分不清哪句是现场哪句是回忆',
                       [f'out{oo}#{bi}' for oo, bi, _f, _l in xlayer])) if xlayer else [],
                   scanned=f'{n_beats} 拍'))

    # ── ③ 篇幅预算带 ─────────────────────────────────────────────────────
    text_len, cov = chapter_source_chars(main_text, bidx)
    lo, hi = OP_BAND[LOCKED_OP]
    bad_band: list[tuple[Any, int, str]] = []
    unmeasured: list[tuple[Any, int, int]] = []
    no_budget: list[Any] = []
    judged = 0
    for o in outs:
        srcs = o.get('from_source_ordinals') or []
        if not srcs:
            continue
        budgets = [be.get('budget_chars') for be in (o.get('beats') or [])
                   if isinstance(be.get('budget_chars'), int)]
        if not budgets:
            # ⚠ 原型这里是一条**静默 `continue`**。改成与「基准残缺」同规格的结论：
            #   一章一个 int 预算都没有，与「这一章判过没事」在报告上必须长得不一样。
            no_budget.append(o.get('output_ordinal'))
            continue
        # 【基准残缺就不判这一章】某些拍没有 span 时，这一章的源字数**偏小却仍然存在**，
        # 拿它去判会把一个合格的计划判成不合格，而报告里看不出基准是残缺的。
        got, tot = min((cov.get(s, (0, 0)) for s in srcs), key=lambda t: (t[0] - t[1], t[1]))
        if tot and got < tot:
            unmeasured.append((o.get('output_ordinal'), got, tot))
            continue
        src = sum(text_len.get(s, 0) for s in srcs)
        total = sum(budgets)
        if not src:
            continue
        judged += 1
        # ⚠⚠ **没有第二次宽限**：原型这里是 `src*lo*0.9 <= total <= src*hi*1.1`。
        if not (src * lo <= total <= src * hi):
            bad_band.append((o.get('output_ordinal'), total, f'{int(src * lo)}~{int(src * hi)}'))
    if unmeasured or no_budget:
        why = []
        if unmeasured:
            why.append(
                f'输出章 {[oo for oo, *_ in unmeasured][:8]} 的源字数基准是残缺的'
                f'（只有 {"、".join(f"{g}/{t}" for _o, g, t in unmeasured[:4])} 拍算得出 span）'
                f'——拿一个偏小的基准去判预算区间，会把合格的计划判成不合格，'
                f'所以这几章的预算门**本次没有跑**。根因在 stage2：那几拍的 span 锚点定位不到')
        if no_budget:
            why.append(f'输出章 {no_budget[:8]} 一个整数预算都没有 ⇒ 无从相加，本次没跑')
        # 基准残缺的那几章判不了，而**判得动的那些照样要给结论**——
        # 所以命中优先：有越带的就报越带，没有才报「这次够不着」。
        if bad_band:
            out.append(ran('budget_band', fs('budget_band', (
                f'逐章预算之和不在目标区间内（输出章, 计划合计, 应在）：{bad_band[:8]}。'
                f'⚠ 另有 {len(unmeasured) + len(no_budget)} 章判不了：{"；".join(why)}',
                [f'out{oo}' for oo, *_ in bad_band])),
                scanned=f'{judged}/{len(outs)} 章真的比过'))
        else:
            out.append(unverifiable('budget_band', '；'.join(why),
                                    scanned=f'{judged}/{len(outs)} 章真的比过'))
    else:
        out.append(ran('budget_band', fs('budget_band', (
            f'逐章预算之和不在目标区间内（输出章, 计划合计, 应在）：{bad_band[:8]}',
            [f'out{oo}' for oo, *_ in bad_band])) if bad_band else [],
            # ⚠ 这个分母取的是**真正判过的章数**，不是 span 覆盖率——原型那一行取错了。
            scanned=f'{judged}/{len(outs)} 章逐章比过'))

    # ── ④ 上下文照的是哪一份计划 ─────────────────────────────────────────
    if ctx_link is None:
        out.append(unverifiable(
            'context_link',
            '这一趟不排它：落盘那一刻上下文是紧接着用这份计划组出来的，必然对得上，'
            '而**一条永远不会红的检查不该打 `[OK]`**。复检时才判',
            scanned='0 条边'))
    else:
        state, why = ctx_link
        if state == 'stale':
            out.append(ran('context_link', fs('context_link', (why, ['ctx'])), scanned='1 条边'))
        elif state == 'ok':
            out.append(ran('context_link', [], scanned='1 条边'))
        else:
            out.append(unverifiable('context_link', why, scanned='1 条边'))

    # ── ⑤ 写法词 ─────────────────────────────────────────────────────────
    # ⚠ `reconstruction_strategy` **必须一起扫**：它曾是唯一漏网的字段，
    #   而它恰恰是唯一整段原样直达写手的自由文本。
    _way_out = ('。改法 0 成本：在 `.INVALID` 稿里把这个词换成【写什么】的说法'
                '（想说「这里该更有分量」就调高那一拍的 budget_chars），再 `--revalidate` 提升')
    sw_rows = []
    if (hitw := sorted({w for w in STYLE_WORDS if w in (plan.get('reconstruction_strategy') or '')})):
        sw_rows.append((f'reconstruction_strategy 含写法指令 {hitw}'
                        f'——它每章都原样发给写手，比逐章的写法词影响面更大' + _way_out,
                        ['strategy:' + '|'.join(hitw)]))
    n_fields = 1
    for o in outs:
        blob = ' '.join(str(o.get(k) or '') for k in
                        ('chapter_goal', 'open_hook_goal', 'end_hook_goal'))
        n_fields += 3
        if (hitw := sorted({w for w in STYLE_WORDS if w in blob})):
            sw_rows.append((f'输出章 {o.get("output_ordinal")} 含写法指令 {hitw}'
                            f'——规划只说写什么，怎么写归这本书的文风' + _way_out,
                            [f'out{o.get("output_ordinal")}:' + '|'.join(hitw)]))
    out.append(ran('style_words', fs('style_words', *sw_rows),
                   scanned=f'{len(STYLE_WORDS)} 个写法词 × {n_fields} 个自由文本字段'))

    # ── ⑥ 提示级 ─────────────────────────────────────────────────────────
    # 【重心问题，不是篇幅问题】篇幅由预算之和守住（上面那条硬校验），这里看章内分配平不平。
    # ⚠ 判准用 max/min **不用 max/均值**：一章允许有一到两拍是重头，
    #   第二个重头会把均值抬上去，于是「有轻重」的章反被判成「平」。
    flatten = []
    n_flat = 0
    for o in outs:
        bs = [be.get('budget_chars') for be in (o.get('beats') or [])
              if isinstance(be.get('budget_chars'), int) and be.get('budget_chars') > 0]
        if len(bs) >= 3:
            n_flat += 1
            if max(bs) < min(bs) * 2:
                flatten.append(o.get('output_ordinal'))
    out.append(ran('flat_chapter',
                   fs('flat_chapter',
                      (f'输出章 {flatten} 各拍字数几乎一样平（最长拍不到最短拍的 2 倍）'
                       f'——本章没有重头，读起来会是梗概不是章节',
                       [f'out{o}' for o in flatten])) if flatten else [],
                   scanned=f'{n_flat}/{len(outs)} 章拍数 ≥3'))

    per = [(o.get('output_ordinal'), be.get('budget_chars')) for o in outs
           for be in (o.get('beats') or []) if isinstance(be.get('budget_chars'), int)]
    nat = f'{BEAT_MIN_CHARS}~150'
    size_rows = []
    if (tiny := sorted({oo for oo, b in per if b < BEAT_ALERT_MIN})):
        size_rows.append((f'输出章 {tiny} 有 <{BEAT_ALERT_MIN} 字的拍——写手一拍的自然产出是'
                          f' {nat} 字，低于此它会并着写或直接略过，该并进相邻拍',
                          [f'out{o}' for o in tiny]))
    if (huge := sorted({oo for oo, b in per if b > BEAT_ALERT_MAX})):
        size_rows.append((f'输出章 {huge} 有 >{BEAT_ALERT_MAX} 字的拍——一拍塞不下，'
                          f'写手会注水或漏写，该拆成两拍', [f'out{o}' for o in huge]))
    out.append(ran('beat_size', fs('beat_size', *size_rows),
                   scanned=f'{len(per)} 拍（报警线 {BEAT_ALERT_MIN}~{BEAT_ALERT_MAX}）'))

    cited = Counter(b for o in outs for be in (o.get('beats') or [])
                    for b in (be.get('from_beats') or []))
    dup = sorted(b for b, c in cited.items() if c > 1)
    out.append(ran('beat_reuse',
                   fs('beat_reuse',
                      (f'源 beat {dup[:8]} 被 >1 拍引用（共 {len(dup)} 条）——这几拍会收到'
                       f'同一段内容，写手分不清各拍写哪一半，该并回一拍', list(dup[:20])))
                   if dup else [],
                   scanned=f'{len(cited)} 条被引用的源 beat'))

    stats, over = verbatim_share_stats(plan, bidx)
    out.append(ran('verbatim_starved',
                   fs('verbatim_starved', *[
                       (f'输出章 {oo} 第 {bi} 拍：必须逐字写入的原话 {vb} 字 / 预算 {bud} 字'
                        f'（{vb / bud:.0%}），写手只剩 {bud - vb} 字用来交代谁在说、说完什么反应。'
                        f'把这一拍的 budget_chars 提到 ≥{int(VERBATIM_BUDGET_FACTOR * vb)}，'
                        f'或把它引的源 beat 拆到相邻的拍上分担',
                        [f'out{oo}#{bi}'])
                       for oo, bi, vb, bud in over]),
                   scanned=f'{stats["n"] if stats else 0} 拍带原话'))

    # ── ⑦ 三条度量：无条件报出 ───────────────────────────────────────────
    if stats:
        out.append(ran('verbatim_share', fs('verbatim_share', (
            f'{stats["n"]} 拍带原话，中位 {stats["median"]:.0%} · p90 {stats["p90"]:.0%} · '
            f'最高 {stats["max"]:.0%}（提示线 {stats["threshold"]:.0%}）',
            [f'n={stats["n"]}', f'median={stats["median"]}', f'p90={stats["p90"]}',
             f'max={stats["max"]}'])), scanned=f'{n_beats} 拍'))
    else:
        out.append(ran('verbatim_share', fs('verbatim_share', (
            '这份计划里一拍带原话的都没有 —— 读数为空。'
            '⚠ 这**不是**「原话没挤占预算」，是这本书的保真材料里一条要害台词都没有',
            ['n=0'])), scanned=f'{n_beats} 拍'))

    # ⚠ **空也要报**：`成年人` 那本没有 `_provenance`，这一行要说出「记了没有」。
    out.append(ran('plan_params', fs('plan_params', (
        (f'grouping={params.get("grouping")} · beat_min_chars={params.get("beat_min_chars")} · '
         f'plan_beats={params.get("plan_beats")}') if params else
        '这份计划没有记下分组参数（`_provenance.params` 缺失）——'
        '「这份计划的拍是照哪一套阈值切的」无从判断',
        [f'{k}={params.get(k)}' for k in ('grouping', 'beat_min_chars', 'plan_beats')]
        if params else ['params=missing'])), scanned='1 份来源账'))

    out.append(ran('ctx_source', fs('ctx_source', (
        ctx_source or '这一趟没有取写作上下文那一份的来源账',
        ['ctx_provenance'])), scanned='1 份产物'))

    # ── ⑧ writer_context 的四条后置条件 ──────────────────────────────────
    out.extend(_ctx_checks(ctx, main_text=main_text, bidx=bidx, global_g=global_g))
    return out


# ── writer_context 的四条后置条件 ─────────────────────────────────────────

_CTX_RULES = ('ctx_events_verbatim', 'ctx_name_attested', 'ctx_secret_gate', 'ctx_layer_flat')


def _ctx_checks(ctx: dict[str, Any] | None, *, main_text: list[dict[str, Any]],
                bidx: dict[str, dict[str, Any]],
                global_g: dict[str, Any] | None = None) -> list[Outcome]:
    """写作上下文那四条。**上下文不在场 ⇒ 四条一起 `unver`，不是「通过」。**"""
    if not ctx or not (ctx.get('chapters') or []):
        return [unverifiable(r, _NO_CTX, scanned='0 章') for r in _CTX_RULES]

    chapters = ctx.get('chapters') or []
    text = {m.get('chapter_ordinal'): (m.get('text') or '') for m in main_text}
    cum: dict[Any, str] = {}
    acc: list[str] = []
    for k in sorted(text):
        acc.append(text[k])
        cum[k] = '\n'.join(acc)

    def fs(rule: str, rows: list[tuple[str, list[str]]]) -> list[Finding]:
        return [finding(rule, detail=d, evidence=e) for d, e in rows]

    # ① events 逐条 == stage2 原件（**过一遍自造标签替换之后**）
    #
    # ⚠⚠ **这一处差点写错，是盘上的对账当场抓住的。**
    #   第一版判的是「逐字等于某一条原件」，而某本上 2 条 events 红了——
    #   查下来它们与原件的差别**恰好是那次确定性改名**（`沈念念`→`念念`）：
    #   `rename_all` 是组装的**最后一步**，它按信息收口、对整棵树做一遍。
    #   ⇒ 正确的后置条件是「等于**改名后的**原件」。判成前者会让每一本有自造标签的书
    #   都误报红，而那正是这条判据要防的反面（把一次正确的确定性改写判成转抄）。
    # ⚠ 拿不到 `global_grounding` 就重算不出那张替换表 ⇒ 这一条走 `unver`，不硬判。
    if global_g is None:
        ev_out: Outcome = unverifiable(
            'ctx_events_verbatim',
            '这一趟没有 `stage2/global_grounding.json`，重算不出自造标签的替换表'
            '（组装的最后一步会对整棵树做一遍确定性改名）⇒ **没法分清「转抄」与「改名」**。'
            '硬判会把一次正确的改写判成转抄',
            scanned='0 条 events')
    else:
        from novel.stages.stage4.context import display_names, rename_all
        _chars = ((global_g.get('entities') or {}).get('characters') or {})
        _, subs, _unatt = display_names(_chars, '\n'.join(text.values()))
        originals = {rename_all(r.get('event') or '', subs) for r in bidx.values()}
        bad_ev: list[tuple[Any, int, str]] = []
        n_ev = 0
        for ch in chapters:
            oo = ch.get('output_ordinal')
            for bi, be in enumerate(ch.get('beats') or [], 1):
                # 本层的 ctx 不存 `from_beats`（它被兑现成了 events / verbatim / 舞台）
                # ⇒ 按下标与源章的 beat 表对齐是做不到的。改判**每一条 event 必须
                #   等于某一条源 beat 的 event**：转抄一旦发生，那句话就不再等于任何一条。
                for ev in (be.get('events') or []):
                    n_ev += 1
                    if ev and ev not in originals:
                        bad_ev.append((oo, bi, ev[:24]))
        ev_rows = [(f'{len(bad_ev)} 条 events 不等于任何一条 stage2 的 `event` 原件'
                    f'（已计入自造标签替换；输出章, 第几拍, 片段）：{bad_ev[:5]}'
                    f'——中间层转抄过了，而转抄的两个方向（造事实 / 丢内容）实测都发生过',
                    [f'out{oo}#{bi}' for oo, bi, _t in bad_ev])] if bad_ev else []
        ev_out = ran('ctx_events_verbatim', fs('ctx_events_verbatim', ev_rows),
                     scanned=f'{n_ev} 条 events')

    # ② 下发的人名在【本章为止】的原文里有据
    bad_nm: list[tuple[Any, str]] = []
    n_nm = 0
    for ch in chapters:
        oo = ch.get('output_ordinal')
        upto = cum.get(max(ch.get('from_source_ordinals') or [0], default=0), '')
        names = [c.get('display_name') for c in (ch.get('characters') or {}).values()]
        for be in (ch.get('beats') or []):
            names += list(be.get('present') or [])
            for sg in (be.get('segments') or []):
                names += list(sg.get('present') or [])
        for na in (ch.get('narration') or []):
            names += [na.get('narrator'), na.get('focalizer')]
        for nm in names:
            if not nm:
                continue
            n_nm += 1
            if nm not in upto:
                bad_nm.append((oo, nm))
    nm_rows = [(f'{len(bad_nm)} 个下发的人名在本章为止的原文里查不到：{bad_nm[:6]}'
                f'——给写手一个本章还不存在的名字就是提前点破',
                [f'out{oo}:{nm}' for oo, nm in bad_nm[:20]])] if bad_nm else []

    # ③ 秘密闸门：被扣住的与被下发的不许有交集
    bad_sec: list[tuple[Any, Any]] = []
    n_sec = 0
    for ch in chapters:
        oo = ch.get('output_ordinal')
        hid = set(ch.get('must_not_reveal') or [])
        for s in ((ch.get('facts') or {}).get('secrets') or []):
            n_sec += 1
            if s.get('secret_id') in hid:
                bad_sec.append((oo, s.get('secret_id')))
    sec_rows = [(f'{len(bad_sec)} 条秘密**既被扣住又被下发**：{bad_sec[:6]}'
                 f'——`must_not_reveal` 与 `facts.secrets` 不许有交集',
                 [f'out{oo}:{sid}' for oo, sid in bad_sec])] if bad_sec else []

    # ④ 跨叙述层不压平：平铺与 segments 互斥，且 segments 不取并集
    bad_lay: list[tuple[Any, int, str]] = []
    for ch in chapters:
        oo = ch.get('output_ordinal')
        for bi, be in enumerate(ch.get('beats') or [], 1):
            segs = be.get('segments') or []
            flat_keys = [k for k in ('time_layer', 'place', 'present') if be.get(k)]
            if segs and flat_keys:
                bad_lay.append((oo, bi, f'同时有 segments 与平铺的 {flat_keys}'))
            for sg in segs:
                if '+' in str(sg.get('time_layer') or ''):
                    bad_lay.append((oo, bi, f'time_layer 越取值面：{sg.get("time_layer")!r}'))
            if '+' in str(be.get('time_layer') or ''):
                bad_lay.append((oo, bi, f'time_layer 越取值面：{be.get("time_layer")!r}'))
    lay_rows = [(f'{len(bad_lay)} 拍压平了跨层/跨地的合并：{bad_lay[:5]}'
                 f'——写手无从判断这拍是现场还是回忆，在场者取并集还会把'
                 f'只在回忆里出场的人放进现世街头',
                 [f'out{oo}#{bi}' for oo, bi, _w in bad_lay])] if bad_lay else []

    n_beats = sum(len(c.get('beats') or []) for c in chapters)
    return [
        ev_out,
        ran('ctx_name_attested', fs('ctx_name_attested', nm_rows), scanned=f'{n_nm} 处人名'),
        ran('ctx_secret_gate', fs('ctx_secret_gate', sec_rows), scanned=f'{n_sec} 条下发的秘密'),
        ran('ctx_layer_flat', fs('ctx_layer_flat', lay_rows), scanned=f'{n_beats} 拍'),
    ]
