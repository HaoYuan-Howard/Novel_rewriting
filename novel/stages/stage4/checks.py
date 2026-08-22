#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 31 条判据的**实现**。行在 `contracts/judges/stage4.py`（`Q-12`）。

⚠⚠ **唯一的判定落点。** 落盘闸门 / `--check-only` / `--revalidate` 走的都是
`check()` 这一个函数——三条路各判一遍就会长出三把尺子。

⚠⚠ **本模块吃 `dict` 不吃模型。** 落盘闸门跑在 `model_validate` **之前**那份 dict 上，
理由见 `novel/stages/stage4/__init__.py` ③：`PlanChapter` 上挂着
`output_ordinal == from_source_ordinals[0]` 的 `model_validator`，
模型化之后 `one_to_one` 那条 HARD **根本构造不出会红的样本**。

【本层的两份产物共用这一张表】前 22 条判计划，`ctx_*` 那 5 条判写作上下文，
4 条度量各报一份读数。**上下文不在场时那 5 条走 `unver` 臂，不是「通过」。**
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from contracts.invariants import (
    BEAT_ALERT_MAX, BEAT_ALERT_MIN, BEAT_MIN_CHARS, BOOK_BAND, GROUPING_VERSION,
    HINGE_GROUPS, PLAN_BUDGET_BAND, PLAN_RECONSTRUCT_MIN_UNITS, STYLE_WORDS,
    TEMPORAL_LINKS, VERBATIM_BUDGET_FACTOR,
)
from contracts.schema.common import Finding
from contracts.schema.report import finding
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage4.beats import (
    beat_index, group_beats, group_chars, verbatim_pieces,
)
from novel.stages.stage4.measure import IDENTITY_GOAL, chapter_source_chars, verbatim_share_stats

#: 源 beat 编号里的章号。⚠ **别按定宽切片**：`b{ord:02d}_{seq:02d}` 在三位数的章上会溢出宽度。
_BEAT_CH = re.compile(r'b(\d+)_')

#: `transition` 标签宣称的方向 → 源叙述位置该往哪走（`+1` 前进 / `-1` 后退）。
#: ⚠ `start` 不在表里：首单元没有上一单元可比，它归 `structure_transition` 管。
#: ⚠ `parallel` 不在表里是**放行**不是遗漏：并行发生的两条线在源叙述里必然被顺序排开，
#:   源位置对它没有约束力。
#: ⚠⚠ 表外的取值会从 `want is None` 那一支**静默穿过去**，长得和「查过没事」一模一样
#:   ⇒ `tests/test_stage4.py` 钉着一条闭合断言（`Q-12`：守卫的守卫）。
_DIR_RULE: dict[str, int] = {'earlier': -1, 'later': 1, 'continue': 1, 'return': 1}

#: `transition_direction` / `flashback_closed` 只在这一层里判。
#: ⚠⚠ **源叙述位置只在【现场层】内才是故事时间的代理。** 一条 `recall` 拍在源位置 7，
#: 它叙述的事件本身就先于它所在的现场帧——从源位置 1 的现场拍跳到源位置 7 的回忆拍，
#: 标 `earlier` 是**对的**，尽管源位置在前进。
#: 实测代价：不加这道门的朴素口径在交付那本书 26 个接缝里**误红 5 处**
#: （3× `recall→recall` · 1× `hearsay→present` · 1× 上游漂移），而那 5 处一处都不是可证的违约。
_PRESENT = 'present'

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
        beats = o.get('segments') or []
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
                      (f'输出章没有 segments（计划必须详细到写手能直接写）：{empty}',
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

    # ── ②.5 结构重构：源内容单元守恒，但输出顺序不再恒等于源序 ────────────
    #
    # ⚠⚠ **源叙述位置与叙述层读的是蓝图 `beats[]` 的数组下标，不是 `group_beats`。**
    #   ① `transition` 问的是时间先后，与「拍怎么切」无关。挂上 `group_beats` 就凭空继承了
    #      `R-5.11b` 的跨版本比较问题，而这两条判据本可以对分组参数**完全免疫**。
    #      分组只是粗化（`beats.py` 的三条不变量：顺序恒等于源序 · 只并相邻 · 只并同层）
    #      ⇒ 单元下标序与 beat 下标序同构，用更细的粒度不改变任何一次判定，却少一层依赖。
    #   ② **也不解析 `beat_id` 尾号**：`novel/stages/stage2/normalize.py::normalize_beat_ids`
    #      按 `enumerate(beats, 1)` 写死，尾号**就是**数组下标的序列化形式。两者恒等，
    #      但那条恒等式由那个生产函数保证——在判据里重新解析一遍就是重拼生产的实参。
    #   ③ 上游漂移时这样做**更诚实**：查不到下标的 beat 落进「判不动」并逐条点名；
    #      解析尾号会让它「看起来能判」，而那个数字所属的蓝图已经不存在了。
    #      （漂移发生时 `beat_ref`/`beat_coverage` 已经先红，本层不重复报根因。）
    src_pos: dict[str, int] = {}
    src_layer: dict[str, Any] = {}
    for _b in (blueprints.get('blueprints') or []):
        for _i, _be in enumerate(_b.get('beats') or []):
            src_pos[_be.get('beat_id')] = _i
            src_layer[_be.get('beat_id')] = _be.get('time_layer')

    source_groups = group_beats(blueprints, main_text, bidx)
    unit_shape_bad: list[tuple[Any, int, list[str]]] = []
    identity_chapters: list[Any] = []
    transition_bad: list[tuple[Any, str]] = []
    structure_metrics: list[str] = []
    n_reconstructable = 0
    axis_hits = Counter()
    #: 可重构章 → 四根轴各自的成立与否。**只为 `structure_reshaped` 那条读数存在**：
    #: 批 3 要回答的是「把恒真的 `axis_group` 换成 `axis_reshape` 之后，
    #: `structure_changed` 会红哪几章」——那是一个**反事实**，判定式本身一行不动，
    #: 而反事实必须逐章拿到四根轴才算得出来。`reshape` 可能是 `None`（这一章判不动）。
    axis_by: dict[Any, dict[str, bool | None]] = {}
    dir_bad: list[tuple[Any, int, Any, str, str, int]] = []
    seam_unjudged: Counter[str] = Counter()
    n_seam = n_dir = 0
    fb_used: list[Any] = []
    fb_open: list[tuple[Any, str]] = []
    fb_unjudged: list[tuple[Any, str]] = []
    role_first = Counter()
    link_mix = Counter()
    for o in outs:
        oo = o.get('output_ordinal')
        srcs = o.get('from_source_ordinals') or []
        src = srcs[0] if len(srcs) == 1 else None
        expected = source_groups.get(src) or []
        unit_of = {bid: ui for ui, group in enumerate(expected) for bid in group}
        positions: dict[str, tuple[int, int]] = {}
        occurrences: Counter[str] = Counter()
        out_units: list[int] = []
        segments = o.get('segments') or []
        for si, be in enumerate(segments):
            refs = [bid for bid in (be.get('from_beats') or []) if bid in unit_of]
            for pi, bid in enumerate(refs):
                positions[bid] = (si, pi)
                occurrences[bid] += 1
            for uid in [unit_of[bid] for bid in refs]:
                if not out_units or out_units[-1] != uid:
                    out_units.append(uid)
        for ui, group in enumerate(expected):
            locs = [positions.get(bid) for bid in group]
            if (any(occurrences[bid] != 1 for bid in group)
                    or not all(loc is not None for loc in locs)
                    or len({loc[0] for loc in locs if loc is not None}) != 1
                    or [loc[1] for loc in locs if loc is not None]
                    != list(range(min(loc[1] for loc in locs if loc is not None),
                                  min(loc[1] for loc in locs if loc is not None) + len(group)))):
                unit_shape_bad.append((oo, ui + 1, list(group)))

        source_order = list(range(len(expected)))

        # ── 三根轴 vs 恒等基线 ────────────────────────────────────────────
        # ⚠ 恒等基线**就是 `expected` 本身**：`measure.identity_plan` 的 segments 是从同一个
        #   `group_beats` 产物逐单元摊开的、`delivery` 恒 `scene`。这里不另拼一份基线
        #   （那就是第二把尺子），比的就是它。
        # ⚠⚠ **判定式只有两项，别把它「补全」成三个松散的 `or`。**「入口移位」被
        #   「分组序列不等」**蕴含**（入口一动，序列必然不等）；写成三个松散的析取会退化成
        #   恒绿——实测交付那本书 11/11 章有合并、9/11 章有 summary。
        # ⚠ `narrative_role` / `transition` **刻意不进比较切片**：改标签是零结构后果的措辞
        #   变更，让它算数等于允许恒等改写换个马甲过关。
        grouping_now = [list(be.get('from_beats') or []) for be in segments]
        axis_entry = bool(out_units) and out_units[0] != 0
        axis_group = grouping_now != [list(g) for g in expected]
        axis_delivery = any(be.get('delivery') == 'summary' for be in segments)
        if len(expected) >= PLAN_RECONSTRUCT_MIN_UNITS:
            n_reconstructable += 1
            if not (axis_group or axis_delivery):
                identity_chapters.append(oo)
            axis_hits['入口移位'] += axis_entry
            axis_hits['分组重画'] += axis_group
            axis_hits['summary 承载'] += axis_delivery
            axis_hits['与恒等基线完全相同'] += not (axis_group or axis_delivery)
            axis_by[oo] = {'entry': axis_entry, 'group': axis_group,
                           'delivery': axis_delivery, 'reshape': None}

        transitions = [be.get('transition') for be in segments]
        link_mix.update(t for t in transitions if t is not None)
        if segments:
            role_first[segments[0].get('narrative_role')] += 1

        # ── `transition` 标签的方向 vs 源叙述序（逐接缝）──────────────────
        # ⚠ **比较单位是接缝，不是段的 min/max。** `transition` 描述的是「与上一单元的时间
        #   关系」，跳转发生在接缝上：前一段 `from_beats[-1]` → 当前段 `from_beats[0]`。
        #   用 min/max 会在跨相邻合并（盘上是常态）时让两段源区间互相重叠，
        #   min 说后退、max 说前进——**两个答案都能自圆其说，那不是判据是掷硬币**。
        for si in range(1, len(segments)):
            prev_fb = segments[si - 1].get('from_beats') or []
            cur_fb = segments[si].get('from_beats') or []
            if not prev_fb or not cur_fb:
                continue                      # 空段归 empty_chapter / beat_ref，不在本条重复报
            n_seam += 1
            tail, head = prev_fb[-1], cur_fb[0]
            if tail not in src_pos or head not in src_pos:
                seam_unjudged['源 beat 不在当前蓝图里（根因看 beat_ref）'] += 1
                continue
            lt, lh = src_layer.get(tail), src_layer.get(head)
            if lt != _PRESENT or lh != _PRESENT:
                seam_unjudged[f'{lt}→{lh}'] += 1
                continue
            n_dir += 1
            want = _DIR_RULE.get(transitions[si])
            delta = src_pos[head] - src_pos[tail]
            if want is not None and delta * want <= 0:
                dir_bad.append((oo, si + 1, transitions[si], tail, head, delta))

        # ── 回溯要在本章之内收口（两条腿的析取）──────────────────────────
        # 腿 A 标签层：本章非首段出现过 `return`。
        # 腿 B 位置层：末段落点 ≥ 首段起点（同带现场层门）。
        # ⚠ 两条腿**互相兜住对方的误红**：纯标签口径会误红「回到了但没标 return」的章
        #   （实测交付那本第 4 章：冷开场演过 6-7，回到 1-2，再一路 `continue` 推到 3-5 收束）；
        #   纯位置口径则漏掉「标了 return 却其实没回去」。
        if segments and any(t == 'earlier' for t in transitions[1:]):
            fb_used.append(oo)
            head_fb = segments[0].get('from_beats') or []
            tail_fb = segments[-1].get('from_beats') or []
            if 'return' in transitions[1:]:
                pass                          # 腿 A 成立
            elif not head_fb or not tail_fb:
                fb_unjudged.append((oo, '首段或末段没有 from_beats'))
            elif head_fb[0] not in src_pos or tail_fb[-1] not in src_pos:
                fb_unjudged.append((oo, '源 beat 不在当前蓝图里（根因看 beat_ref）'))
            elif (src_layer.get(head_fb[0]) != _PRESENT
                  or src_layer.get(tail_fb[-1]) != _PRESENT):
                fb_unjudged.append(
                    (oo, f'{src_layer.get(head_fb[0])}→{src_layer.get(tail_fb[-1])}'))
            elif src_pos[tail_fb[-1]] < src_pos[head_fb[0]]:
                fb_open.append((oo, f'末段停在源位置 {src_pos[tail_fb[-1]] + 1}，'
                                    f'早于起笔处 {src_pos[head_fb[0]] + 1}，且全章没有 return'))

        if transitions and transitions[0] != 'start':
            transition_bad.append((oo, f'首单元 transition={transitions[0]!r}，必须是 start'))
        if any(t == 'start' for t in transitions[1:]):
            transition_bad.append((oo, '非首单元重复使用 start'))
        if out_units != source_order and len(expected) >= 2:
            if all(t in (None, 'continue') for t in transitions[1:]):
                transition_bad.append((oo, '已重排内容，却没有 earlier/later/parallel/return 衔接'))
            if out_units and out_units[0] != 0 and (segments[0].get('narrative_role') != 'cold_open'):
                transition_bad.append((oo, '移动了叙事入口，但首单元不是 cold_open'))

        pairs = list(zip(out_units, out_units[1:]))
        kept = sum(b == a + 1 for a, b in pairs)
        denom = max(len(expected) - 1, 1)
        moved = sum(i != uid for i, uid in enumerate(out_units))
        structure_metrics.append(
            f'ch{oo}: 源单元 {len(expected)} → 写作单元 {len(segments)}；'
            f'入口 p{(out_units[0] + 1):02d}' if out_units else
            f'ch{oo}: 源单元 {len(expected)} → 写作单元 {len(segments)}；入口缺失')
        if structure_metrics:
            structure_metrics[-1] += f'；移位 {moved}/{len(expected)}；源相邻保留 {kept}/{denom}'

        # ── 详略：拍级 `预算/源` 的离散度 + `summary` 承载了多少源字数 ─────
        # 2026-08-22 补。**这两个读数就是线 A 的验收出口**：`PLAN_BUDGET_BAND` 放宽到
        # 0.80~1.20、`PLAN_SYS` 改口说「全书是这个倍率、单章不是」之后，要回答的问题是
        # 「模型到底有没有把新拿到的自由度用起来」——而移位数与源相邻保留数（上面那两个）
        # 答的是**顺序**那一维，一个字都碰不到**详略**这一维。
        # ⚠ 比的是**相对本章均值**不是相对 1.0：章级倍率本来就允许在带内漂，
        #   拿绝对值比会把「这一章整体写厚了」误读成「章内有轻重」——那是两件事，
        #   前者归 `budget_band`/`plan_book_band`，本读数只管后者。
        # ⚠ 均值取 `Σ预算/Σ源` 而不是「各拍比值的平均」：后者会被一个 20 字的短拍
        #   拽走整章的基准。
        # ⚠ 任一拍算不出 span 就整章不报这一维（**不拿部分拍充数**，同 `group_chars`
        #   那条不变量），并把「判不动」说出来——静默省略与「查过没事」在报告上同形。
        seg_pairs: list[tuple[float, int, bool]] = []
        n_unknown = 0
        for be in segments:
            src_i = group_chars(list(be.get('from_beats') or []), bidx)
            bud_i = be.get('budget_chars')
            if src_i is None or not src_i or not isinstance(bud_i, int):
                n_unknown += 1
                continue
            seg_pairs.append((bud_i / src_i, src_i, be.get('delivery') == 'summary'))
        if n_unknown or not seg_pairs:
            structure_metrics[-1] += (
                f'；拍级详略判不动（{n_unknown}/{len(segments)} 拍缺 span 或缺预算）')
        else:
            tot_s = sum(si for _r, si, _m in seg_pairs)
            rbar = sum(r * si for r, si, _m in seg_pairs) / tot_s
            rel = [r / rbar for r, _si, _m in seg_pairs]
            summ = sum(si for _r, si, m in seg_pairs if m)
            structure_metrics[-1] += (
                f'；拍级 预算/源 相对本章均值 {min(rel):.2f}~{max(rel):.2f}'
                f'；summary 承载源字数 {summ * 100 // tot_s}%')
            # ⚠ 第四根轴的**候选**：本章有没有哪一拍的相对倍率冲出 `PLAN_BUDGET_BAND`。
            #   拿的是**相对本章均值**的那个 `rel`，与上面那行同一个数——章级整体厚薄归
            #   `budget_band`/`plan_book_band`，本轴只问「章内有没有轻重」。
            #   ⚠ 它现在**只是读数**：换不换进 `structure_changed` 的判定式，
            #     看 `structure_reshaped` 报出来的分布（见那条 metric 的 note）。
            if oo in axis_by:
                _lo, _hi = PLAN_BUDGET_BAND
                axis_by[oo]['reshape'] = any(r < _lo or r > _hi for r in rel)

    # ⚠ **先比一次分组参数再判**（`R-5.11b`）：两条结构判据是拿**当前**的
    #   `group_beats` 去量的，而产物记着它是照哪一套切的。不比就是拿新尺子量旧布。
    params = ((plan.get('_provenance') or {}).get('params') or {})
    recorded = (params.get('grouping'), params.get('beat_min_chars'))
    current = (GROUPING_VERSION, BEAT_MIN_CHARS)
    if not params:
        out.append(unverifiable(
            'params_drift',
            f'这份计划没有记下分组参数（`_provenance.params` 缺失）⇒ **说不清**它的拍是照'
            f'哪一套切的。下面两条结构判据仍然照当前这套（{current[0]}，阈值 {current[1]} 字）判，'
            f'而它们本该先比一次。⚠ **缺失记「不知道」不是「匹配」**——压成匹配就是替它担保',
            scanned='1 份来源账'))
    elif recorded != current:
        out.append(ran('params_drift', fs(
            'params_drift',
            (f'**这是跨版本比较**：这份计划的拍是照 {recorded[0]}（阈值 {recorded[1]} 字）切的，'
             f'而下面两条结构判据拿的是当前这套 {current[0]}（阈值 {current[1]} 字）。'
             f'先看这一行再看那三条的判定',
             [f'{recorded[0]}→{current[0]}'])), scanned='1 份来源账'))
    else:
        out.append(ran('params_drift', [], scanned=f'比过：{current[0]}，阈值 {current[1]} 字'))

    # ⚠ `scanned` 报的是**真实分母**（≥ 阈值的章），不是 `len(outs)`：这条判据从来只判够大的章，
    #   拿全部输出章当分母是把分母报大了——绿灯的分母必须是它真的扫过的那些。
    out.append(ran('structure_changed',
                   fs('structure_changed',
                      (f'这些输出章与【恒等基线】逐格相同——入口仍是第 1 个源内容单元、'
                       f'一个单元一个写作单元按源序排开、且没有一个单元压成 summary：'
                       f'{identity_chapters}。'
                       f'⚠ **保持源序本身不违规**：违规的是三根轴（叙事入口 / 单元分组 / '
                       f'承载方式）一根都没动 ⇒ 这一章只改了预算和措辞。'
                       f'恒等基线的定义落点是 `novel/stages/stage4/measure.py::identity_plan`'
                       f'（`--identity` 产的就是它）',
                       [f'out{o}' for o in identity_chapters])) if identity_chapters else [],
                   scanned=f'{n_reconstructable}/{len(outs)} 个源单元 ≥{PLAN_RECONSTRUCT_MIN_UNITS} '
                           f'的输出章 × 3 根轴'))
    out.append(ran('structure_units',
                   fs('structure_units',
                      (f'源内容单元被拆开、内部换序或跨写作单元重复：{unit_shape_bad[:8]}',
                       [f'out{oo}:p{ui:02d}' for oo, ui, _ in unit_shape_bad]))
                   if unit_shape_bad else [], scanned=f'{sum(len(g) for g in source_groups.values())} 个源内容单元'))
    out.append(ran('structure_transition',
                   fs('structure_transition',
                      (f'结构衔接标记与重排不一致：{transition_bad[:8]}',
                       [f'out{oo}' for oo, _ in transition_bad])) if transition_bad else [],
                   scanned=f'{n_beats} 个写作单元'))
    goal_bad: list[tuple[Any, str, list[str], list[str]]] = []
    if plan.get('reconstruction_strategy') != IDENTITY_GOAL:
        for o in outs:
            segments = o.get('segments') or []
            if not segments:
                continue
            for key, allowed in (
                ('open_hook_goal', set(segments[0].get('from_beats') or [])),
                ('end_hook_goal', set(segments[-1].get('from_beats') or [])),
            ):
                event_refs = [r.get('ref') for r in ((o.get(key) or {}).get('fact_refs') or [])
                              if r.get('kind') == 'event' and r.get('ref')]
                if not event_refs or not (set(event_refs) & allowed):
                    goal_bad.append((o.get('output_ordinal'), key, event_refs, sorted(allowed)))
    out.append(ran('goal_landing',
                   fs('goal_landing',
                      (f'开头/结尾目标没有锚在对应的首尾写作单元：{goal_bad[:8]}',
                       [f'out{oo}:{key}' for oo, key, _refs, _allowed in goal_bad]))
                   if goal_bad else [], scanned=f'{len(outs)} 章 × 2 个边界目标'))
    out.append(ran('structure_distance', [finding(
        'structure_distance', detail='；'.join(structure_metrics), evidence=structure_metrics)],
        scanned=f'{len(outs)} 个输出章'))

    # ── ②.6 重排之后：标签说的方向是不是真的 · 回溯有没有收口 ────────────────
    _unj = '；'.join(f'{k}×{v}' for k, v in seam_unjudged.most_common())
    if n_dir:
        out.append(ran('transition_direction',
                       fs('transition_direction',
                          (f'`transition` 标签宣称的方向与源叙述序相反'
                           f'（输出章, 第几段, 标签, 接缝两端, 位移）：{dir_bad[:8]}。'
                           f'闭卷写手只能照这个标签落笔——标错就是叫他把顺叙写成倒叙，'
                           f'读者读到的是一段方向反了的过渡。'
                           f'⚠ 只在接缝两端**都是现场层**时才判：那时源位置才是故事时间的代理',
                           [f'out{oo}#{si}' for oo, si, *_ in dir_bad])) if dir_bad else [],
                       scanned=f'{n_dir}/{n_seam} 个接缝可判（两端都是现场层）'
                               f'{"；判不动：" + _unj if _unj else ""}'))
    else:
        out.append(unverifiable(
            'transition_direction',
            f'{n_seam} 个接缝一个都判不动{"：" + _unj if _unj else ""}。'
            f'源叙述位置只在【现场层】内等于故事时间序——一端是 recall/hearsay 时，'
            f'那一拍叙述的事件本身就先于它所在的现场帧，源位置**不再是故事时间的代理**。'
            f'⚠ 这不是「通过」：一条查不了的与一条查过没事的，在报告上必须长得不一样',
            scanned=f'0/{n_seam} 个接缝可判'))

    if fb_used:
        fb_rows: list[tuple[str, list[str]]] = []
        if fb_open:
            fb_rows.append(
                (f'用了 `earlier` 回溯，但本章没有收口：{fb_open[:8]}。'
                 f'一章结束时读者还停在过去，他就不知道这一章把故事推到了哪里。'
                 f'⚠ 收口有两条路，满足任一即可：本章非首段出现 `return`，'
                 f'或末段落点不早于起笔处',
                 [f'out{oo}' for oo, _ in fb_open]))
        if fb_unjudged:
            fb_rows.append(
                (f'这些章的收口**判不动**（不是通过）：{fb_unjudged[:8]}',
                 [f'out{oo}' for oo, _ in fb_unjudged]))
        out.append(ran('flashback_closed', fs('flashback_closed', *fb_rows),
                       scanned=f'{len(fb_used) - len(fb_unjudged)}/{len(fb_used)} 个用了 `earlier` '
                               f'的输出章可判（全书 {len(outs)} 章）'))
    else:
        out.append(ran('flashback_closed', [],
                       scanned=f'{len(outs)} 章里没有一章用 `earlier` 回溯'))

    # ⚠⚠ **这一条刻意没有阈值，名字也刻意不叫「饱和度」**（`Q-9`：探针的名字要等标定完再起）。
    #   实测交付那本书 `cold_open` **11/11 章**、`earlier` 18 处、向前推进的 `continue`
    #   全书**只有 1 处**——这个形状很可能就是「读起来混乱」的来源，但**多少算过量没有
    #   任何真值支撑**，所以这一层只报读数（标定见 `docs/REQUIREMENTS.md` TODO-30）。
    # ⚠ 它同时是 `transition_direction` 那条 HARD 的**分母出口**（`Q-9`：告警与度量不共用
    #   分支）：可判率掉到 0 意味着有人在靠把 recall 单元排到段尾逃逸。
    entry_mix = [
        f'入口：cold_open {role_first.get("cold_open", 0)}/{len(outs)} 章'
        f' · 移位入口 {axis_hits.get("入口移位", 0)}/{n_reconstructable} 个可重构章',
        '衔接：' + (' · '.join(f'{t} {link_mix.get(t, 0)}' for t in TEMPORAL_LINKS)),
        f'方向可判率：{n_dir}/{n_seam} 个接缝'
        + (f'（判不动：{_unj}）' if _unj else ''),
        '过关轴：' + (' · '.join(f'{k} {axis_hits.get(k, 0)}' for k in (
            '入口移位', '分组重画', 'summary 承载', '与恒等基线完全相同'))
            if n_reconstructable else f'没有源单元 ≥{PLAN_RECONSTRUCT_MIN_UNITS} 的章'),
    ]
    out.append(ran('structure_entry_mix', [finding(
        'structure_entry_mix', detail='；'.join(entry_mix), evidence=entry_mix)],
        scanned=f'{len(outs)} 个输出章 × {n_seam} 个接缝'))

    # ── `structure_changed` 换项的**决策读数**（2026-08-22，批 3）──────────────
    # 【要回答的问题】`axis_group`（分组序列 ≠ 恒等基线）在生产落点上**恒真**：
    #   `--check-only` 实测 13/13 个可重构章全靠它过关、`axis_entry` 0/13
    #   ⇒ 那条 HARD 实际上只在判「有没有合并过」。批 3 的候选换项是
    #   `axis_reshape`（章内有拍冲出 `PLAN_BUDGET_BAND`）——它问的是**详略**，
    #   而详略正是「成稿只改了措辞」那条链上一直没人判的那一维。
    # ⚠⚠ **本条只报读数，判定式一行不动。** `axis_reshape` 很可能**也**近乎恒真
    #   （先前量到 40/57 拍在 0.9~1.1 之外，但那是相对 1.0 不是相对本章均值 `r̄`，
    #   两个数不能互相代入）。换一个恒真项进去，那条 HARD 还是恒绿——
    #   **换项要拿这一行的分布当依据，不是拿「它听起来更对」当依据**（`Q-9`/判据 15）。
    # ⚠ 判不动的章（拍缺 span 或缺预算）报成 `None` 单列，**不并进任何一边**：
    #   压成「未命中」会让反事实凭空多红几章，压成「命中」则凭空少红几章。
    if n_reconstructable:
        _re_hit = [o for o, a in axis_by.items() if a['reshape'] is True]
        _re_unk = [o for o, a in axis_by.items() if a['reshape'] is None]
        # 反事实：把 `axis_group` 换成 `axis_reshape` 之后，这条 HARD 会红哪几章。
        _cf_red = sorted(o for o, a in axis_by.items()
                         if a['reshape'] is False and not a['delivery'])
        _cf_unk = sorted(o for o, a in axis_by.items()
                         if a['reshape'] is None and not a['delivery'])
        reshaped = [
            f'轴命中：章内详略重画 {len(_re_hit)}/{n_reconstructable} 个可重构章'
            f'（判不动 {len(_re_unk)}）· 现役的分组重画 {axis_hits.get("分组重画", 0)}'
            f'/{n_reconstructable}',
            f'换项反事实（判定式**未**改动）：把 `axis_group` 换成 `axis_reshape` 后，'
            f'`structure_changed` 会红 {sorted(_cf_red)}；现役口径红 {sorted(identity_chapters)}',
            f'说不清的 {sorted(_cf_unk)} 章：详略判不动且没有 summary 单元 ⇒ '
            f'换项后它们既不能算红也不能算绿' if _cf_unk else '说不清的章：无',
        ]
        out.append(ran('structure_reshaped', [finding(
            'structure_reshaped', detail='；'.join(reshaped), evidence=reshaped)],
            scanned=f'{n_reconstructable}/{len(outs)} 个源单元 ≥{PLAN_RECONSTRUCT_MIN_UNITS} 的输出章'))
    else:
        out.append(unverifiable(
            'structure_reshaped',
            f'{len(outs)} 章里没有一章的源单元 ≥{PLAN_RECONSTRUCT_MIN_UNITS} ⇒ '
            f'`structure_changed` 本来就一章都不判，换项的读数也就无从谈起。'
            f'⚠ 这不是「换项没问题」——是这本书量不出来',
            scanned=f'0/{len(outs)} 个可重构章'))

    # ── ③ 篇幅预算带（逐章 + 全书，共用一个基准）─────────────────────────
    text_len, cov = chapter_source_chars(main_text, bidx)
    lo, hi = PLAN_BUDGET_BAND
    bad_band: list[tuple[Any, int, str]] = []
    unmeasured: list[tuple[Any, int, int]] = []
    no_budget: list[Any] = []
    #: 判得动的那些章的 `(输出章, 预算合计, 源字数)`——**书级带唯一的基准来源**。
    #: ⚠⚠ 与逐章带**共用这一个循环**是有意的：两条判据同一个基准、同一条前件，
    #:   各算一遍就是两把尺子，而错的往往正是那个跨口径的运算（判据 14）。
    per_ch: list[tuple[Any, int, int]] = []
    #: 「哪一章没进书级基准，以及为什么」。逐章带跳过一章照样能给结论，
    #: 书级带不行——少一章，全书合计就只是个偏小的数。
    basis_gaps: list[str] = []
    judged = 0
    for o in outs:
        oo = o.get('output_ordinal')
        srcs = o.get('from_source_ordinals') or []
        if not srcs:
            basis_gaps.append(f'out{oo} 没有 from_source_ordinals（根因看 `one_to_one`）')
            continue
        budgets = [be.get('budget_chars') for be in (o.get('segments') or [])
                   if isinstance(be.get('budget_chars'), int)]
        if not budgets:
            # ⚠ 原型这里是一条**静默 `continue`**。改成与「基准残缺」同规格的结论：
            #   一章一个 int 预算都没有，与「这一章判过没事」在报告上必须长得不一样。
            no_budget.append(oo)
            basis_gaps.append(f'out{oo} 一个整数预算都没有')
            continue
        # 【基准残缺就不判这一章】某些拍没有 span 时，这一章的源字数**偏小却仍然存在**，
        # 拿它去判会把一个合格的计划判成不合格，而报告里看不出基准是残缺的。
        got, tot = min((cov.get(s, (0, 0)) for s in srcs), key=lambda t: (t[0] - t[1], t[1]))
        if tot and got < tot:
            unmeasured.append((oo, got, tot))
            basis_gaps.append(f'out{oo} 的源字数基准残缺（只有 {got}/{tot} 拍算得出 span）')
            continue
        src = sum(text_len.get(s, 0) for s in srcs)
        total = sum(budgets)
        if not src:
            basis_gaps.append(f'out{oo} 的源字数是 0')
            continue
        judged += 1
        per_ch.append((oo, total, src))
        # ⚠⚠ **没有第二次宽限**：原型这里是 `src*lo*0.9 <= total <= src*hi*1.1`。
        if not (src * lo <= total <= src * hi):
            bad_band.append((oo, total, f'{int(src * lo)}~{int(src * hi)}'))
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

    # ── ③.5 全书总量（`plan_book_band`，2026-08-22 新增）─────────────────
    #
    # 【为什么现在才需要它】此前逐章带是 ±10%，而**章章 ±10% ⇒ 全书必然 ±10%**——
    # 总量是被逐章带顺带兜住的。2026-08-22 逐章带放宽到 ±20%（理由见
    # `contracts/invariants.py::PLAN_BUDGET_BAND`：让 stage4 能用「哪一章厚、哪一章薄」
    # 表达详略，那是治「成稿只改了措辞」的那一个自由度），**那条蕴含就断了**：
    # 一份把 23 章全顶到 1.20 的计划，逐章带一章都不会红。
    # ⚠⚠ 而在这条判据之前，全书总量唯一的守卫是 stage5 的 `book_length`（INFO）——
    #   **钱花完才响**：要等 23 章正文全部写完，才有人说一句「这本书超了 20%」。
    #
    # ⚠ 基准是 **span 合计**不是 `len(整章文本)`：那是本层三个「源字数」口径里的第二个
    #   （`measure.py` 文件头那张表），产地曾为混用它们把一份合格的计划判成 `.INVALID`。
    # ⚠ 与 stage5 `book_length` 共用 `BOOK_BAND` 但**分母不是同一个数**（那边是整章字数）
    #   ⇒ 两条读数不许拿来做相等断言，只能各自对带。
    blo, bhi = BOOK_BAND
    if not per_ch:
        out.append(unverifiable(
            'plan_book_band',
            '一章都没进全书基准，**不拿一个空合计去判总量**：'
            + ('；'.join(basis_gaps[:8]) or '这份计划一个输出章都没有'),
            scanned=f'0/{len(outs)} 章进了全书基准'))
    elif basis_gaps:
        # 【缺一章就不判】逐章带可以「判得动的照样给结论」，书级带做不到同样的事：
        # 少一章，分子分母**同时**缺一块，得出的倍率既不能说合格也不能说不合格。
        # ⇒ 说出来，不拿缩水的尺子判（继承 `budget_band` 的同一条 `unver` 臂）。
        out.append(unverifiable(
            'plan_book_band',
            f'{len(basis_gaps)} 章没进基准，全书合计因此是偏小的，不判：'
            + '；'.join(basis_gaps[:8])
            + (f'（另有 {len(basis_gaps) - 8} 章同因）' if len(basis_gaps) > 8 else ''),
            scanned=f'{len(per_ch)}/{len(outs)} 章进了全书基准'))
    else:
        bsrc = sum(sc for _oo, _b, sc in per_ch)
        bbud = sum(b for _oo, b, _sc in per_ch)
        ratio = bbud / bsrc
        want_lo, want_hi = round(bsrc * blo), round(bsrc * bhi)
        book_scanned = (f'全书 {bbud}/{bsrc} 字 = {ratio:.3f}×'
                        f'（目标 {want_lo}~{want_hi} 字，{len(per_ch)}/{len(outs)} 章）')
        if want_lo <= bbud <= want_hi:
            out.append(ran('plan_book_band', [], scanned=book_scanned))
        else:
            # ⚠⚠ **逐章明细是这条判据的一半**，不是装饰：本层的修法是
            #   「手改那几行 + `--revalidate`」这条 **0 LLM 出路**，而人拿到
            #   「全书 1.14×」这一句话，只能自己去 23 章里解谜该从哪几章匀。
            #   ⇒ 逐章倍率按**该往哪个方向匀**排好序一起打出来。
            over = bbud > want_hi
            gap = bbud - want_hi if over else want_lo - bbud
            rows = sorted(per_ch, key=lambda t: t[1] / t[2], reverse=over)
            out.append(ran('plan_book_band', fs('plan_book_band', (
                f'全书预算合计 {bbud} 字 / 全书源字数 {bsrc} 字 = {ratio:.3f}×，'
                f'不在 {blo:g}~{bhi:g}（应在 {want_lo}~{want_hi} 字，'
                f'{"超出" if over else "缺"} {gap} 字）。'
                f'逐章（输出章 预算/源=倍率，按{"该压的排前" if over else "该加的排前"}）：'
                + ' · '.join(f'ch{oo} {b}/{sc}={b / sc:.2f}' for oo, b, sc in rows),
                [f'out{oo}' for oo, _b, _sc in rows[:8]])),
                scanned=book_scanned))

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
    _way_out = ('。改法 0 LLM：在 `.INVALID` 稿里把这个词换成【写什么】的说法'
                '（想说「这里该更有分量」就调高那一拍的 budget_chars），再跑 '
                '`--revalidate --actor <id> --reason <why>` 同轮重判并留不可变审计')
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
        bs = [be.get('budget_chars') for be in (o.get('segments') or [])
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
           for be in (o.get('segments') or []) if isinstance(be.get('budget_chars'), int)]
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

    cited = Counter(b for o in outs for be in (o.get('segments') or [])
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
            '⚠ 这**不是**「原话没挤占预算」。'
            '⚠⚠ **也别照着字面读成「这本书一条要害台词都没抽到」**——这条措辞'
            '曾经就是这么写的，而它把人指向了错的层：真实原因是 `beat_index` → '
            '`verbatim_pieces` 这一步的形态分了家，stage2 抽得好好的，材料是在中间'
            '被吃掉的。两种可能先看 `ctx_hinge_delivered` 那一行分清：'
            '它红 ⇒ 组装这一步的问题；它 `unver` ⇒ 才真是 stage2 一条都没抽到（看 `R-3.6b`）',
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

    # ── ⑧ writer_context 的五条后置条件 ──────────────────────────────────
    out.extend(_ctx_checks(ctx, main_text=main_text, bidx=bidx, global_g=global_g))
    return out


# ── writer_context 的五条后置条件 ─────────────────────────────────────────

_CTX_RULES = ('ctx_events_verbatim', 'ctx_name_attested', 'ctx_secret_gate', 'ctx_layer_flat',
              'ctx_hinge_delivered')

#: 下发面上那四组保真点的字段名。**从 `HINGE_GROUPS` 的值域派生，不手抄一份**——
#: 手抄的那一份漏一组，判据的分子就少一块，而它长得跟「材料没下发」一模一样。
_HINGE_FIELDS = tuple(sorted(set(HINGE_GROUPS.values())))


def _ctx_checks(ctx: dict[str, Any] | None, *, main_text: list[dict[str, Any]],
                bidx: dict[str, dict[str, Any]],
                global_g: dict[str, Any] | None = None) -> list[Outcome]:
    """写作上下文那五条。**上下文不在场 ⇒ 五条一起 `unver`，不是「通过」。**"""
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
            for bi, be in enumerate(ch.get('segments') or [], 1):
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
        for be in (ch.get('segments') or []):
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
        for bi, be in enumerate(ch.get('segments') or [], 1):
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

    n_beats = sum(len(c.get('segments') or []) for c in chapters)

    # ⑤ stage2 抽到的保真点，**条条都要真的出现在下发面上**
    #
    # ⚠⚠ **这条判据是拿一次真事故换来的，别把它当冗余删掉。** `R-3.3` 原本只配了
    #   `结构` 级守卫（"守在下发那一刻"，指的就是 `verbatim_pieces` 自己），
    #   `R-3.6b` 那条 HARD 守的是 **stage2 产物侧**。中间 `beat_index` 有过一层
    #   `{'kind','text'}` 的归一，一次「清理遗留兼容」把读它的那条分支删了之后，
    #   下发面**整整五本书 898 条 hinge 一条不剩地变成空**——
    #   而两条守卫一个在上游看产物（齐的）、一个在下游看函数（还在），谁都没出声，
    #   stage5 那三条 HARD 又因为分母同时归零而报绿。
    #   ⇒ 缺的从来不是"更严的判据"，是**一条横跨这一步、比两边条数**的后置条件。
    #
    # 【比条数不比文字】组装的最后一步 `rename_all` 会对整棵树做确定性改名
    #   （见上面 ① 的注释），文字会变、条数不会。比文字必然误报。
    # 【分母走 `verbatim_pieces` 这个生产落点】不在这里重拼一份拆分逻辑：
    #   未知 kind 的丢弃、跨段落按 `\n\n` 拆点，两边必须是同一套，否则差额算的是
    #   "两份实现不一样"，不是"材料丢了"。
    want, _unk = verbatim_pieces([h for v in bidx.values() for h in (v.get('hinges') or [])])
    n_want = sum(len(v) for v in want.values())
    n_got = sum(len(seg.get(g) or [])
                for c in chapters for seg in (c.get('segments') or []) for g in _HINGE_FIELDS)
    if not n_want:
        # 分母为零 ⇒ **「没东西可查」，不是「查过、干净」**（判据 4 的三态）。
        # 真到了这一步，根因在 stage2：`R-3.6b` 那条 HARD 会在上一层红。
        hinge_out: Outcome = unverifiable(
            'ctx_hinge_delivered',
            'stage2 这一本一条带原文字节的保真材料都没有 ⇒ 这条判据没有分母。'
            '**不是「下发干净」**：闭卷写手拿原文的唯一通道本来就是空的。'
            '根因在上一层，看 `R-3.6b`',
            scanned='0 条保真材料')
    else:
        hinge_out = ran('ctx_hinge_delivered', fs('ctx_hinge_delivered', (
            [(f'stage2 抽到 {n_want} 个保真点，下发面上只有 {n_got} 个'
              f'（{"一个都没有" if not n_got else f"少了 {n_want - n_got} 个"}）。'
              f'**闭卷写手拿原文的唯一通道就是这些点**，少一个就是少一份'
              f'"改一个字就废"的材料，而 `event` 摘要不承载它们。'
              f'先跑 `stage4.context` 重建一份；重建后仍对不上，'
              f'是 `beat_index` → `verbatim_pieces` 这一步的形态又分家了',
              [f'want={n_want}', f'got={n_got}'])]
            if n_got != n_want else [])), scanned=f'{n_want} 个保真点')

    return [
        ev_out,
        ran('ctx_name_attested', fs('ctx_name_attested', nm_rows), scanned=f'{n_nm} 处人名'),
        ran('ctx_secret_gate', fs('ctx_secret_gate', sec_rows), scanned=f'{n_sec} 条下发的秘密'),
        ran('ctx_layer_flat', fs('ctx_layer_flat', lay_rows), scanned=f'{n_beats} 拍'),
        hinge_out,
    ]
