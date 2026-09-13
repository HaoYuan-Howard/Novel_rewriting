#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 50 条机械判据的实现。**行在 `contracts/judges/stage5.py`，这里只负责执行。**

【本层与前四层最大的形状差异：机械表由写作闸门与全书验收共用】
落盘闸门（写作相，逐章）与全书验收相**调同一个 `chapter_findings`**。
原型此前把书级那 8 条住在 `n7_review.RULES6` 里、靠一个 `_merged_rules()` 合起来，
而共享层得反向 lazy-import 才拿得到——**一张表拆成两半的全部成本都在那个环上**。

⚠⚠ **判据吃 `dict` 不吃模型。** 这不是随意：一旦判据跑在已经过了 Pydantic 的对象上，
「必填齐全」这类判据在**结构上构造不出反例**——它必然全绿，于是又变成一盏保证会亮的绿灯。
机械层 6 条 HARD 的反例样本（`tests/test_stage5.py`）正是靠这一条才写得出来。

⚠ **结论一律走 `contracts.schema.report.finding()`，不许手写 dict**：
`sev` / `item` / `owner` / `actionable` 全从判据表取 ⇒ 一条 finding 不可能与表说的不一致。
"""
from __future__ import annotations

import collections
import re
from typing import Any, Iterable

from contracts.invariants import SOURCE_OVERLAP_MAX
from contracts.rules import REGISTRY
from contracts.schema.common import Finding
from contracts.schema.report import finding
from novel.stages.stage5.plot_checks import fidelity_scenes
from novel.core.bands import PARA_BANDS, SENT_BANDS, band, cut_distance
from novel.core.facts import (
    locate_secret, num_unit_keys, num_values, number_tokens,
    secret_contains, secret_keywords,
)
from novel.core.measure import measure
from novel.core.naming import invented_names
from novel.core.spans import lcs
from novel.core.style import (
    DASH_CORPUS_P95, SHORT_PARA_MAX, SHORT_RUN_CORPUS_P95, SHORT_RUN_MIN,
    dash_per_1k, short_run_per_1k,
)
from novel.core.text import (
    TERMINAL_PUNCT, min_hamming, narration_layer, norm, norm_index, quote_count,
)
from novel.core.text import split_by_speech_tag
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage5 import measure as M


def with_output_ordinal(findings: Iterable[Finding], ordinal: int | None) -> list[Finding]:
    """Bind chapter-scoped findings to their output chapter exactly once.

    The same ``Finding`` shape is also used for book-level and semantic rows, so
    the field is optional at the shared contract.  Stage5, however, already has
    the authoritative chapter identity in the criterion input; dropping it here
    makes the subsequent Review -> CheckReport -> inbox flattening irreversible.
    """
    if ordinal is None:
        return list(findings)
    value = int(ordinal)
    return [Finding.model_validate({
        **item.model_dump(by_alias=True),
        'output_ordinal': value,
    }) for item in findings]


# ─────────────────────────────────────────────────────────────────────────────
# 零、几张只有本层读的表
# ─────────────────────────────────────────────────────────────────────────────

#: 叙述外壳：章节标题 / 编号 / 任务术语。**`meta_leak` 的词表**（`wordlist=True`）。
#:
#: ⚠ **它刻意不进 `contracts/invariants.py`**：那张表收的是「**同时**进 prompt 与判据」的
#: 枚举，而这一份**只有判据一个消费方**。放进去就是给它造第二个家。
#: ⚠⚠ 而唯一写作 system 里那句「不出现任何任务术语（节拍、展开、目标之类的词）」
#: **是手写的一个子集**，不是从这张表插值来的——那是 LESSONS 第 9 条的形状，
#: 但 prompt 那两段要与原型**逐字节相同**，改它是一次 prompt 变更。
#: ⇒ 登记在这里：**下发面说了三个词，判据查的是这一整张表**，两侧不等价是已知的。
META_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'^\s*第[一二三四五六七八九十百零〇\d]+章'), '章节标题'),
    (re.compile(r'^\s*#{1,6}\s'), 'Markdown 小标题'),
    (re.compile(r'本章(?:目标|字数|共|要写|完)'), '任务术语'),
    (re.compile(r'(?:节拍|拍数|beat|Beat)\s*[0-9①-⑥]'), '任务术语'),
    (re.compile(r'【?(?:约\s*\d+\s*字|目标字数|篇幅方式|展开程度)】?'), '任务术语'),
    (re.compile(r'(?:以上|以下)?(?:是|为)本章(?:的)?(?:正文|改写|重构)'), '叙述外壳'),
    (re.compile(r'^\s*(?:正文|全文|改写稿|重构稿)\s*[:：]'), '叙述外壳'),
)

#: 恒等基线在这几条上**不适用**——不是判据坏，是它量的东西本来就与源文无关：
#: `length_*` 比的是「源章字数 vs 计划预算」，而计划预算本来就要偏离源章；
#: `overlap_report` 拿源文与自己比恒等于 100%。
#: 在源文上命中说明的是**计划**，不是判据。
#: 章尾语义交付按定义是 UNVER，字面原子只作为 metric，不参与恒等基线。
IDENTITY_NA = frozenset({'length_over', 'length_under', 'length_report',
                         'paragraph_count_report', 'paragraph_budget_missed', 'overlap_report',
                         'source_overlap_high', 'end_hook_surface_report', 'end_hook_unver'})

#: 这几条判的是「成稿有没有守住**从源文切出来的**材料」。源文当然守得住 ⇒
#: 恒等基线上命中 **0 是应该的，非 0 是上游 bug**（锚点切出来的东西回不到原文里去），
#: **不是「判据无判别力」**。分开报，别混进「不可反哺」那一桶。
IDENTITY_UPSTREAM = frozenset({'verbatim_missing', 'verbatim_typo', 'keep_value_missing',
                               'must_appear_missing'})


# ─────────────────────────────────────────────────────────────────────────────
# 一、保真回写 —— 该守的守住没、该编的越界没
# ─────────────────────────────────────────────────────────────────────────────

def fidelity_findings(body: str, entry: dict[str, Any], book: M.Book, *,
                      given: str) -> list[Finding]:
    """四组保真材料 + 剧透 + 发明越界。**闭卷写手看不到原文、无法自证，所以只能在代码侧验。**

    `given` = 单一闭卷写作路径实际看得见的全部下发文字。参照集必须与真请求同源；
    判据不能擅自把写手从未见过的源文并进允许集。

    ⚠ `evidence` 是**给验收相用的机器可比凭据**，不是给人看的摘要：它必须是能在这一章
    成稿或下发材料里**定位回去**的原串，而不是 `detail` 里那个截断到 14/18 字的展示版。
    反哺 prompt 与「两轮之间这条消没消」都按它对齐。
    """
    fs: list[Finding] = []
    scenes = fidelity_scenes(entry)
    source_norm = norm(M.chapter_source(entry, book.source_text))

    # 字面例外只比较原字节：密码和识别符号中的标点、大小写也可能参与剧情。
    # 提示语切分或近似拼写只留诊断，不能把一个不同的口令判为满足。
    nb, nmap = norm_index(body)
    source_text = M.chapter_source(entry, book.source_text)
    miss_v: list[str] = []
    typo_v: list[str] = []
    split_v: list[tuple[str, str]] = []
    for scene in scenes:
        for seg in scene.get('verbatim') or []:
            if not seg or seg in body:
                continue
            if source_text and seg not in source_text:
                continue  # 伪造锚点交上游 finding，禁止 Stage5 付费填回
            k = norm(seg)
            if k and (gap := split_by_speech_tag(k, body, nb, nmap)) is not None:
                split_v.append((seg, gap))
            if k and min_hamming(k, nb) <= 2:
                typo_v.append(seg)
            miss_v.append(seg)
    if split_v:
        fs.append(finding(
            'verbatim_split',
            detail=f'{len(split_v)} 句【原样写入】被提示语切成两截后逐字出现'
                   f'：{[(t[:14], g[:8]) for t, g in split_v[:3]]}',
            evidence=[t for t, _g in split_v]))
    if miss_v:
        fs.append(finding(
            'verbatim_missing',
            detail=f'{len(miss_v)} 条【字面例外】没有原样出现在成稿里'
                   f'：{[t[:18] for t in miss_v[:4]]}',
            evidence=miss_v))
    if typo_v:
        fs.append(finding(
            'verbatim_typo',
            detail=f'{len(typo_v)} 句【原样写入】与成稿差一两个字'
                   f'：{[t[:18] for t in typo_v[:3]]}',
            evidence=typo_v))

    # ── ② 【数值不得改动】只要求**值**保住、措辞可动 ────────────────────────────
    #
    # ⚠⚠ **比值不比字面**：下发面「30岁」、成稿「三十岁」＝满足。原型只搜阿拉伯字面，
    #   某章因此被**整章拦下**而值一个都没变——而写手做的正是契约让它做的事。
    #   解析不出值的罕见形态**退回字面比对**：不许因为解析器不认就默认放行。
    body_vals = num_values(body)
    miss_n: list[tuple[str, list[str]]] = []
    miss_n_full: list[str] = []
    for scene in scenes:
        for t in (scene.get('keep_value') or []):
            if source_norm and norm(t) not in source_norm:
                continue
            tokens = number_tokens(t)
            nums = [token for token, _ in tokens]
            vals = {value for _, value in tokens if value is not None}
            unresolved = [token for token, value in tokens if value is None]
            ok = vals <= body_vals and all(token in body for token in unresolved)
            if not tokens:
                ok = norm(t) in nb  # 数字材料无可解析数词也不能自动满足。
            if not ok:
                miss_n.append((t[:18], nums[:3]))
                miss_n_full.append(t)
    if miss_n:
        fs.append(finding(
            'keep_value_missing',
            detail=f'{len(miss_n)} 处【数值不得改动】的数字在成稿里找不到：{miss_n[:4]}',
            # 凭据存**整条原始材料**，不存截断到 18 字的展示版：反哺时写手要看见完整那句，
            # 才知道那个数字长在哪儿。
            evidence=miss_n_full))

    # ── ③ 【必须在场并起作用，怎么描写随你】⇒ **不能按整串比对**（那是 verbatim 的查法）──
    #
    # 实测吃过一次：材料「羽林卫特制的箭矢带有逆刺」，成稿写「羽林卫的箭，箭簇带逆刺」，
    # 保住了在场与作用（后文「倒刺便在伤口里绞动」），却被整串匹配判成缺失
    # ——**判据与它自己声明的维度不一致**。改看最长公共子串：换说法能过，整个没写才报。
    miss_o: list[str] = []
    miss_o_full: list[str] = []
    for scene in scenes:
        for t in (scene.get('must_appear') or []):
            k = norm(t)
            if source_norm and k and k not in source_norm:
                continue
            if k and lcs(k, nb)[1] < max(2, min(3, len(k))):
                miss_o.append(t[:14])
                miss_o_full.append(t)
    if miss_o:
        fs.append(finding(
            'must_appear_missing',
            detail=f'{len(miss_o)} 个【必须在场】的物件在成稿里找不到任何痕迹：{miss_o[:4]}',
            evidence=miss_o_full))
    # ⚠ `must_hold` **故意不查文面**——它要的是命题成立，逐字出现反而是硬塞背景交代。
    #   它由书级那条 `must_hold_unver` 显式说「没查」。

    for scene in scenes:
        for dialogue in scene.get('dialogue') or []:
            fs.append(finding(
                'dialogue_semantics_unver',
                detail=f"slot {scene['slot']} 对白的说话关系、承诺条件、时间和语义等价须人工核验；"
                       '普通措辞变化不触发原句返修',
                evidence=[scene['slot'], dialogue['meaning'], *dialogue['required_facts']]))

    # ── ④ 本章不该点破的秘密 ────────────────────────────────────────────────
    # 秘密边界只认 Stage2 权威揭示时间线；WriterContext 不再复制一份 id 清单。
    # 这仍只是字面探针；改写式盲区由 Stage2 后见之明判据与交付前成书逐字对账承担。
    ordinal = int(entry.get('output_ordinal') or 0)
    timeline_hidden = []
    if ordinal:
        for secret in book.secrets:
            # ⚠ **首次**揭示章，与 `stage4/context.py` 的下发闸门同一个函数。
            #   这道复核闸的全部意义就是「能从权威时间线复原同一条边界」——
            #   两处各写一份 `min(全部事件)` 时，下次改判向必然漏掉一处。
            first = secret['revealed_from']
            if first is not None and ordinal < first:
                timeline_hidden.append(secret.get('secret_id'))
    hid = list(dict.fromkeys(sid for sid in timeline_hidden if sid))
    if hid:
        table = {s['secret_id']: s for s in book.secrets}
        leak: list[tuple[str, list[str]]] = []
        leak_spans: list[str] = []
        for sid in hid:
            s = table.get(sid)
            if s is None:
                continue
            # 两侧共用 secret 口径：fact 侧剥数字，成稿侧也剥；数字不是
            # 泄密与否的身份键。但这仍只是转抄式探针；`secret_timeline`
            # 负责暴露字面盲区，改写式风险由跨层后见之明判据与交付对账承担。
            kws = secret_keywords(s.get('fact'))
            hit = [k[:14] for k in kws if secret_contains(k, body)]
            if hit:
                leak.append((sid, hit[:2]))
                # ⚠⚠ **凭据必须是成稿自己的字节，不是 secret 的字节**：命中的 `k` 来自
                #   `secret.fact`，把它当凭据发回给写手就是**把秘密告诉它**。
                #   回映成成稿里的那一段之后，反哺时引用的是写手自己写过的话。
                for k in kws:
                    if (span := locate_secret(k, body)) is not None:
                        leak_spans.append(body[span[0]:span[1]])
        if leak:
            fs.append(finding('secret_leak',
                              detail=f'成稿疑似提前点破了本章不该揭示的秘密：{leak[:4]}',
                              evidence=leak_spans or [sid for sid, _h in leak]))

    # ── ⑤ 发明越界的两个探针 ────────────────────────────────────────────────
    #
    # ⚠ **另五项（新事件/新地点/新道具/新因果/新时间跳跃）一个探针都没有**（`R-7.5b` → `B-01`）。
    #   别把这里的 0 条读成「那七条都守住了」。
    keys_body = num_unit_keys(body)
    keys_given = num_unit_keys(given)
    inv = sorted({disp for k, disp in keys_body.items() if k not in keys_given})
    if inv:
        fs.append(finding(
            'invented_number',
            detail=f'成稿出现下发面里没有的【数字+单位】：{inv[:8]}',
            evidence=inv))
    nm = invented_names(body, given, book.name_stems)
    if nm:
        # 【不过滤「X家Y」】实测 6 条候选里 4 条是这个形状（「苏家姑娘」「赵家小姐」），
        # 都在指已有角色；但把带「家」的整类滤掉，会连「苏家二小姐」这种真发明一起滤掉。
        # **宁可让人多看两眼，不要为了好看的数字砍掉判别力。**
        fs.append(finding(
            'invented_name',
            detail=f'成稿出现下发面里没有的【人物称谓】：{nm[:8]}',
            evidence=nm))
    return fs


_SEGMENT_BOUNDARY_RULES = frozenset({
    'verbatim_missing', 'verbatim_typo', 'verbatim_split',
    'keep_value_missing', 'must_appear_missing',
})


def segment_fidelity_findings(
    segments: list[dict[str, str]],
    entry: dict[str, Any],
    book: M.Book,
    *,
    given: str,
) -> list[Finding]:
    """Enforce fidelity inside the producer-owned slot, not just somewhere in the chapter.

    Whole-chapter checks cannot distinguish a required quote copied into the wrong
    scene from a correctly placed quote.  This projection reuses the same fidelity
    rules while limiting each check to its declared producer segment.
    """
    expected = {str(row.get('slot') or ''): row for row in (entry.get('scenes') or [])}
    out: list[Finding] = []
    for returned in segments:
        slot = str(returned.get('slot') or '')
        contract = expected.get(slot)
        if contract is None:
            continue  # the response-shape validator owns unknown/missing slots
        isolated = {**entry, 'scenes': [contract]}
        for hit in fidelity_findings(
            str(returned.get('text') or ''), isolated, book, given=given,
        ):
            if hit.rule not in _SEGMENT_BOUNDARY_RULES:
                continue
            out.append(finding(
                hit.rule,
                detail=f'slot {slot} 内部保真边界：{hit.detail}',
                evidence=[slot, *hit.evidence],
                fix_args=hit.fix_args,
                identity_hit=hit.identity_hit,
                identity_dead=hit.identity_dead,
                converge=hit.converge,
                unresolved_rounds=hit.unresolved_rounds,
                wontfix=hit.wontfix,
            ))
    return with_output_ordinal(out, entry.get('output_ordinal'))


# ─────────────────────────────────────────────────────────────────────────────
# 二、正文机械基线的六条漂移 + 对白
# ─────────────────────────────────────────────────────────────────────────────

def _dialogue_drift(body: str, book: M.Book) -> list[Finding]:
    """对白机械形态守恒。**它比句长段长更刺眼。**"""
    base = book.measurable
    got = measure(body)
    if not got:
        return []
    if not base.get('dialogue_ratio_applicable'):
        # ⚠ **不作为破形**：实测两本书的引号是**入库前被人工删掉的**，原作本有「」。
        #   所以「无引号」是**语料缺陷不是作者选择**，不能拿它去约束写作
        #   ——写手加引号反而更接近原作。措辞是提示不是违规，`owner=corpus`。
        return ([finding('quotes_in_unquoted_corpus',
                         detail=f'本书语料无引号；成稿引号样式 {list(got.quote_styles)}',
                         evidence=list(got.quote_styles))]
                if got.quote_styles else [])
    if got.dialogue_ratio is None:
        return [finding('dialogue_broken',
                        detail='对白破形：原书是引号对白体，成稿一句引号对白都没有',
                        evidence=['0'])]
    b, g = base.get('dialogue_ratio'), got.dialogue_ratio
    if b and (g < b * 0.7 or g > b * 1.4):
        return [finding('dialogue_ratio_drift',
                        detail=f'对白比偏离：原书 {b:.3f} → 成稿 {g:.3f}（{g / b:.2f}×）',
                        evidence=[f'{b:.3f}->{g:.3f}'])]
    return []


def _band_drift(body: str, book: M.Book) -> list[Finding]:
    """成稿的句/段形态是否还落在这本书的档位上（写手有没有把「一句一段」写成大段）。

    ⚠ **走 `core.bands` 的同一张档位表**，保证两边同一把尺子。
    ⚠ 凭据带上是**哪一维**：同一条 rule 的两个实例必须分得开，否则
    「句子形态修好了、段落形态还没」在收敛差集里看起来是同一条。
    """
    got = measure(body)
    if not got:
        return []
    base = book.band_of
    meas = book.measurable
    axes = {'句子形态': (got.avg_sentence_chars, meas.get('avg_sentence_chars'), SENT_BANDS),
            '段落形态': (got.avg_paragraph_chars, meas.get('avg_paragraph_chars'), PARA_BANDS)}
    mine = {k: band(v[0], v[2]) for k, v in axes.items()}
    out: list[Finding] = []
    for k in ('句子形态', '段落形态'):
        if not base.get(k) or base[k] == mine[k]:
            continue
        got_v, base_v, cuts = axes[k]
        # ⚠⚠ **把数字打出来，否则「翻档」与「真漂移」分不开**（见
        #   `novel/core/bands.py::cut_distance` 的第一条 ⚠⚠）。
        #   底座取不到数值时只说档位，**不编一个数**。
        if base_v:
            near = cut_distance(float(base_v), cuts)
            nums = (f'（本书 {float(base_v):.1f} 字 → 本章 {got_v:.1f} 字，'
                    f'{got_v / float(base_v):.2f}×；底座离最近档边 {near} 字）')
        else:
            nums = '（底座数值取不到，只判得出档位）'
        out.append(finding(
            'band_drift',
            detail=f'形态漂移 {k}：底座「{base[k]}」→ 成稿「{mine[k]}」{nums}',
            evidence=[k, base[k], mine[k]]))
    return out


def _para_len_drift(body: str, book: M.Book) -> list[Finding]:
    """段均字数**双向**冲。

    ⚠ 它补的是 `band_drift` 档位太粗那一块：实测某本 19.6 与 11.5 同落「极短段」档、穿档而过。
    """
    base = book.measurable.get('avg_paragraph_chars')
    got = measure(body)
    if not base or not got:
        return []
    r = got.avg_paragraph_chars / base
    if r < 0.6 or r > 1.6:
        which = '电报体（段被剁得太碎）' if r < 0.6 else '段被并得太长'
        return [finding('para_len_drift',
                        detail=f'段长漂移：本书均段 {base} 字 → 本章 '
                               f'{got.avg_paragraph_chars} 字（{r:.2f}×），{which}',
                        evidence=[f'{base}->{got.avg_paragraph_chars}'])]
    return []


def _paragraph_budget(
    body: str, entry: dict[str, Any], book: M.Book,
    slot_bodies: list[str] | None = None,
) -> list[Finding]:
    """报告自然段数量，并对未命中下发目标给出独立 HARD。

    目标公式与 prompt 共用 `measure.target_paragraphs`；这里不重新拼预算口径。
    ``slot_bodies`` 是按计划 slot 顺序切好的各场景正文（paragraph_slot_report
    的分母）：给不出时只报章级两条，不伪造逐 slot 读数。
    """
    got = measure(body)
    if got is None:
        return []
    lo, hi = M.target_paragraphs(entry, book)
    count = got.paragraphs
    out = [finding(
        'paragraph_count_report',
        detail=f'自然段：{count} 段（目标 {lo}~{hi} 段）',
        evidence=[str(count), str(lo), str(hi)],
    )]
    if not lo <= count <= hi:
        out.append(finding(
            'paragraph_budget_missed',
            detail=f'自然段数量未命中明确目标：{count} 段，不在 {lo}~{hi} 段内',
            evidence=[f'{count} not in {lo}~{hi}'],
        ))
    # 逐 slot 对账读数（metric，只报不挡）：实际值与确定性计划值的差就是
    # 「哪一场写偏了」；下发面/反馈与判据共用同一份 `paragraph_slot_plan`。
    try:
        slot_plan = M.paragraph_slot_plan(entry, book)
    except Exception:
        slot_plan = None
    scenes = [scene for scene in (entry.get('scenes') or []) if scene]
    if (scenes and slot_plan is not None and slot_bodies is not None
            and len(slot_bodies) == len(scenes)):
        readings: list[str] = []
        for index, scene in enumerate(scenes):
            lines = str(slot_bodies[index]).split('\n')
            actual = len([line for line in lines if line.strip()])
            readings.append(
                f"{scene.get('slot')}:{actual}/{slot_plan[index]}")
        out.append(finding(
            'paragraph_slot_report',
            detail='逐 slot 段数（实际/计划）：' + '；'.join(readings)
                   + f'（合计 {count}/{sum(slot_plan)}）',
            evidence=readings + [f'total {count}/{sum(slot_plan)}'],
        ))
    return out


_INLINE_CUE = re.compile(r'[：:]\s*[“「『"]')


def _layout_drift(body: str, book: M.Book) -> list[Finding]:
    """提示语排版惯例（冒号收尾 + 换行 + 引语独段）。

    书级基线 ≥0.8 才启用（**不下发无据的指令**）；章级邻接 ≥3 才判（分母太小不判）。
    ⚠ **永不升 HARD**——它是形状偏好不是内容保真。
    """
    paras = [ln.strip() for ln in (body or '').split('\n') if ln.strip()]
    inline = [p for p in paras if _INLINE_CUE.search(p)]
    if len(inline) < 3:
        return []
    cue = book.cue_rate
    if cue is None or cue < 0.8:
        return []
    return [finding('layout_drift',
                    detail=f'对白排版漂移：本书提示语以冒号收尾后 {cue:.0%} 换行独段，'
                           f'本章有 {len(inline)} 处同行冒号/句中插入',
                    evidence=inline[:6])]


def _density_drift(body: str, book: M.Book) -> list[Finding]:
    """明喻密度 +「心里」密度，各按 `max(2×base, base+1.0)/千字` 判。

    ⚠⚠ 那个 `+1.0` **没有语料级来源**（对照：`dash_drift` 的 `DASH_CORPUS_P95` 由
    `tools/` 的标定脚本从 156 本语料算出、可 0 成本重跑）⇒ **调它之前先按同样办法重标**。
    """
    out: list[Finding] = []
    for rule, key, fn in (
            ('simile_drift', 'simile_per_1k', M.simile_per_1k),
            ('xinli_drift', 'xinli_per_1k', M.xinli_per_1k)):
        base = book.baseline(key, fn)
        if base is None:
            continue
        got = fn(body)
        if got > max(2 * base, base + 1.0):
            label = '明喻' if rule == 'simile_drift' else '「心里」'
            out.append(finding(rule,
                               detail=f'{label}密度漂移：本书 {base:.2f}/千字 → '
                                      f'本章 {got:.2f}/千字',
                               evidence=[f'{base:.2f}->{got:.2f}']))
    return out


_DASH_EG = re.compile(r'[^\n。！？]{0,10}—{2,}[^\n。！？]{0,10}')


def _dash(body: str, entry: dict[str, Any], book: M.Book) -> list[Finding]:
    """破折号：**度量无条件出**，告警按「本书基线 + 体裁上界」双线判。

    ⚠ **为什么是 `max(2×base, base + 语料 p95)`**：两条线各挡一种情况——本书本来就爱用
    破折号的（实测某本源文 3.02/千字，语料前 5），乘法线给它应有的余量；本书基线接近 0 的
    （实测三本源文全书几乎不用），乘法线会退化成「>0 就报」，这时加法线兜底。

    ⚠⚠ **基线取书级而不是同章源章**：同章配对会让**恒等基线退化成恒真**——
    源章 vs 源章恒等于 1.00、永不触发，于是它在 `baselines_identity` 里恒为 0/N，
    被读成「判据干净」，而那是唯一能逐书判定判别力的机制。
    """
    base = book.baseline('dash_per_1k', dash_per_1k)
    if base is None:
        return []
    got = dash_per_1k(body)
    n = len(re.findall(r'—{2,}', body or ''))
    src = M.chapter_source(entry, book.source_text)
    vs_src = f'｜源章 {dash_per_1k(src):.2f}' if src else ''
    out = [finding('dash_report',
                   detail=f'破折号密度：本章 {n} 处 = {got:.2f}/千字{vs_src}｜本书 {base:.2f}'
                          f'｜语料 p95 {DASH_CORPUS_P95:.2f}',
                   evidence=[f'{base:.2f}->{got:.2f}'])]
    if got > max(2 * base, base + DASH_CORPUS_P95):
        eg = [m.group() for m in _DASH_EG.finditer(body or '')][:3]
        out.append(finding(
            'dash_drift',
            detail=f'破折号密度漂移：本书 {base:.2f}/千字 → 本章 {got:.2f}/千字'
                   f'（{n} 处；语料 p95 {DASH_CORPUS_P95:.2f}）'
                   + (f'　例：{" ｜ ".join(eg)}' if eg else ''),
            evidence=[f'{base:.2f}->{got:.2f}']))
    return out


def _short_run(body: str, entry: dict[str, Any], book: M.Book) -> list[Finding]:
    """连续短段成串 —— **只量不判**。

    实测本书内信号很强（成稿中位 5.51/千字 vs 同章源文 2.51，2.2×），而且与
    `para_len_drift` 几乎不重叠。但语料分布说它**落在体裁常态内**（p95 7.68、max 10.83）
    ⇒「这本书不这么排」算不算缺陷是审美裁决，不是结构不变量。
    **别因为它的数字好看就顺手加阈值。**
    """
    base = book.baseline('short_run_per_1k', short_run_per_1k)
    if base is None:
        return []
    src = M.chapter_source(entry, book.source_text)
    vs_src = f'｜源章 {short_run_per_1k(src):.2f}' if src else ''
    return [finding('short_run_report',
                    detail=f'连续短段成串：本章 {short_run_per_1k(body):.2f}/千字{vs_src}'
                           f'｜本书 {base:.2f}｜语料 p95 {SHORT_RUN_CORPUS_P95:.2f}',
                    evidence=[f'{base:.2f}->{short_run_per_1k(body):.2f}'])]


def _end_hook_landing(
    entry: dict[str, Any], book: M.Book,
    segments: list[dict[str, Any]] | None,
) -> list[Finding]:
    """只盘点章尾目标的**字面原子分布**，绝不据此裁决语义交付。

    真实章上已经标定出反例：目标人物在成稿里被「他/我」代称，事件完整交付，姓名原子却
    0 命中；反方向，同名或数字撞上也不能证明目标事件真的发生。故姓名/数字只配做
    `end_hook_surface_report` metric。每章的语义结论始终是 `end_hook_unver`，由人结合
    目标、末段与上下文判断，不能进入付费 revise。
    """
    goal = entry.get('end_hook_goal') or {}
    target = goal.get('anchor_beat_ref')
    facts = [row['content'] for row in entry['beats'] if row['beat_ref'] == target]
    planned = entry.get('scenes') or []
    if not facts or not planned:
        return [
            finding(
                'end_hook_surface_report',
                detail='上游没有完整的 end_hook_goal/计划分段，字面落点无从盘点',
                evidence=['no_goal_or_plan']),
            finding(
                'end_hook_unver',
                detail='上游没有完整的章尾目标或计划分段，章尾语义交付**未判**',
                evidence=['no_goal_or_plan']),
        ]
    if segments is None:
        return [
            finding(
                'end_hook_surface_report',
                detail='拿不到成稿分段，无法区分末段与其他段的字面命中',
                evidence=['no_segments']),
            finding(
                'end_hook_unver',
                detail='拿不到成稿分段，章尾语义交付**未判**',
                evidence=['no_segments']),
        ]
    roster = {c.get('display_name') for c in (entry.get('characters') or [])}
    roster = {n for n in roster if n} - _pronoun_named(entry)
    atoms = _surface_atoms(' '.join(facts), roster)
    if not atoms:
        return [
            finding(
                'end_hook_surface_report',
                detail=f'结尾目标提不出姓名/数字原子：{goal.get("intent")}：'
                       f'{"；".join(facts)[:60]}',
                evidence=['noatom']),
            finding(
                'end_hook_unver',
                detail='字面探针没有可回查原子，章尾语义交付**未判**',
                evidence=['noatom']),
        ]
    last_slot = (planned[-1] or {}).get('slot')
    by_slot = {s.get('slot'): (s.get('text') or '') for s in segments}
    tail = by_slot.get(last_slot)
    if tail is None:
        return [
            finding(
                'end_hook_surface_report',
                detail=f'计划末段 slot `{last_slot}` 在成稿分段里不存在',
                evidence=[f'slot_missing:{last_slot}']),
            finding(
                'end_hook_unver',
                detail=f'计划末段 slot `{last_slot}` 不存在，章尾语义交付**未判**',
                evidence=[f'slot_missing:{last_slot}']),
        ]

    other = ''.join(text for slot, text in by_slot.items() if slot != last_slot)
    in_tail = sorted(atom for atom in atoms if atom in tail)
    in_other = sorted(atom for atom in atoms if atom not in tail and atom in other)
    absent = sorted(atom for atom in atoms if atom not in tail and atom not in other)
    metric = finding(
        'end_hook_surface_report',
        detail=f'章尾字面分布（**只读数、不判交付**）：末段 `{last_slot}` '
               f'{in_tail or "无"}｜其他段 {in_other or "无"}｜整章未见 {absent or "无"}',
        evidence=[f'last_slot:{last_slot}',
                  *(f'tail:{atom}' for atom in in_tail),
                  *(f'other:{atom}' for atom in in_other),
                  *(f'absent:{atom}' for atom in absent)],
    )
    unver = finding(
        'end_hook_unver',
        detail='姓名/数字的字面位置不能证明章尾事件的语义交付；代词、换说法、同名与撞值'
               '均超出确定性判据，需人工结合末段复核',
        evidence=[f'semantic:{last_slot}'],
    )
    return [metric, unver]


#: 第三人称叙述里指代**叙述者本人**的字面。⚠ 只用于「声明 `first` 却写成了第三人称」
#: 那一支的**佐证读数**，不单独构成结论：第一人称叙事里写别的角色当然会出现「他/她」。
_THIRD_PERSON_MARK = re.compile(r'[他她](?!们)')


def narration_person_probe(
    entry: dict[str, Any], book: M.Book,
) -> tuple[str, dict[str, Any] | None, list[str]]:
    """返回 ``(state, 单段声明, 锚点)``；state 不是 ``judgeable`` 就明确 UNVER。

    Stage2 明说多 narration 没有段界；在这种输入上拿整章正文逐段重复检查只会制造
    假阳/假阴。第二人称没有经过标定，单段但标签不是「全章」也缺正文边界。只有单段
    ``first|third`` 且锚点非空时，才运行一个**弱信号**探针。
    """
    segs = [seg for seg in (entry.get('narration') or []) if isinstance(seg, dict)]
    if not segs:
        return 'no_narration', None, []
    if len(segs) != 1:
        return 'multiple_without_boundaries', None, []
    seg = segs[0]
    if str(seg.get('segment') or '') != '全章':
        return 'segment_boundary_unavailable', seg, []
    person = seg.get('grammatical_person')
    if person not in {'first', 'third'}:
        return f'unsupported_{person or "unknown"}', seg, []
    anchors = ([s for s in (seg.get('pronoun_surfaces') or []) if s]
               if person == 'first' else sorted(book.first_person_surfaces))
    if not anchors:
        return 'no_anchor', seg, []
    return 'judgeable', seg, list(dict.fromkeys(anchors))


def _narration_person(body: str, entry: dict[str, Any], book: M.Book) -> list[Finding]:
    """只在有段界的单段 first/third 上跑弱信号，不把词面命中冒充语义真值。

    第一人称可以合法省略主语，第三人称正文也可能含书信或自由间接引语；因此命中只是
    INFO + FREE/人工线索，绝不自动进入付费 revise。台词先由 ``narration_layer`` 剔除。
    """
    layer = narration_layer(body)
    if not layer.strip():
        return []
    state, seg, anchors = narration_person_probe(entry, book)
    if state != 'judgeable' or seg is None:
        return []
    person = seg.get('grammatical_person')
    if person == 'first':
        if any(surface in layer for surface in anchors):
            return []
        return [finding(
            'narration_person_drift',
            detail=f'弱信号：全章声明第一人称（自称 {"/".join(anchors)}），叙述层未见自称；'
                   f'可能是人称漂移，也可能是合法省略主语，需人工复核'
                   f'（第三人称指代 {len(_THIRD_PERSON_MARK.findall(layer))} 处）',
            evidence=[f'first:{"/".join(anchors)}:0'])]
    hits = {surface: layer.count(surface) for surface in anchors if surface in layer}
    if not hits:
        return []
    return [finding(
        'narration_person_drift',
        detail='弱信号：全章声明第三人称，叙述层出现书级第一人称自称；'
               '书信/自由间接引语仍可能合法，需人工复核：'
               + '、'.join(f'「{s}」×{n}' for s, n in sorted(hits.items())),
        evidence=[f'third:{s}:{n}' for s, n in sorted(hits.items())])]


def _narration_person_report(
    body: str, entry: dict[str, Any], book: M.Book,
) -> list[Finding]:
    """叙述人称的**读数**——无条件出，包括「这一条为什么没判」。

    ⚠⚠ 缺这条度量的话，`narration_person_drift` 的 0 命中有三种完全不同的来源
    （真的对得上 / `narration` 没声明 / 自称池是空的），而它们在报告上会长得一模一样。
    `scanned` 里把分母写出来，是本仓「绿灯必须带扫描分母」的那条纪律。
    """
    layer = narration_layer(body)
    state, seg, anchors = narration_person_probe(entry, book)
    pool = '/'.join(sorted(book.first_person_surfaces)) or '（空）'
    if state != 'judgeable' or seg is None:
        labels = {
            'no_narration': '上游没有 narration',
            'multiple_without_boundaries': '多 narration 但正文没有对应段界',
            'segment_boundary_unavailable': 'narration 位置标签不是可定位的全章边界',
            'unsupported_second': '第二人称尚未标定',
            'unsupported_unknown': '人称声明缺失或未知',
            'no_anchor': '没有可用自称锚点',
        }
        return [finding(
            'narration_person_report',
            detail=f'叙述人称：**UNVER / 未判**｜{labels.get(state, state)}'
                   f'｜书级自称池 {pool}',
            evidence=[f'probe:{state}'])]
    person = seg.get('grammatical_person')
    got = sum(layer.count(surface) for surface in anchors)
    return [finding(
        'narration_person_report',
        detail=f'叙述人称：全章声明 {person}｜弱信号锚 {"/".join(anchors)}｜'
               f'叙述层命中 {got} 处｜书级自称池 {pool}',
        evidence=['probe:judgeable', str(person), str(got)])]


def _dialogue(body: str, entry: dict[str, Any], book: M.Book) -> list[Finding]:
    """章级引语数：成稿 M vs 源章 N。**度量无条件出**，告警两级。

    ⚠⚠ **`dialogue_report` 是无条件的**：原型只有三条**告警**，于是「这本书的对白比是多少」
    只有在漂了的时候才说得出来——而它承载的是本项目**效应量最大**的一条跨书结论
    （闭卷逐章中位 0.80 / 开卷 1.00，6 本书方向一致）。
    """
    n = M.src_quote_count(entry, book.source_text)
    m = quote_count(body)
    out: list[Finding] = [finding(
        'dialogue_report',
        detail=f'直接引语：成稿 {m} 句'
               + (f' ｜ 源章 {n} 句 = {m / n:.2f}×' if n else ' ｜ 源章（取不到）'),
        evidence=[str(m), str(n if n is not None else '')])]
    if n is None:
        return out
    if n >= 4 and m < n * 0.7:
        out.append(finding(
            'dialogue_fewer',
            detail=f'直接引语少于源章：{n} 句 → {m} 句（{m / n:.2f}×）',
            evidence=[f'{n}->{m}']))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 三、⭐ 逐章判定 —— **落盘闸门与全书验收相调的就是它**
# ─────────────────────────────────────────────────────────────────────────────

def chapter_findings(text: str, entry: dict[str, Any], book: M.Book, *,
                     given: str = '',
                     quote_problems: Iterable[str] = (),
                     segments: list[dict[str, Any]] | None = None) -> list[Finding]:
    """一章成稿的全部确定性判定 → `[Finding]`。**两相共用同一个函数。**

    `given` 缺省为空串时会按**最严**的口径判发明越界（不知道是哪一臂就不替它放宽探测器）；
    调用方一律应传 `prompts.build_material(...).given`。

    ⚠ **追加顺序即渲染顺序**：运行时 warns 是这份列表的字符串投影；正式 Review 保存 findings，
    顺序一变，验收相逐轮做差集时会把整批警告读成「全换了」。
    """
    fs: list[Finding] = []
    body = (text or '').strip()

    # 【早退是有意的】后面每一条判据都建立在「这是一篇文章」之上，对 12 个字跑它们只会
    # 产出一堆没有意义的数。但验收相要知道这不是「其余都干净」——那条区分由这条 HARD 自己承担。
    if len(body) < 50:
        return with_output_ordinal([finding(
            'draft_too_short',
            detail=f'成稿过短或为空：{len(body)} 字',
            evidence=[body[:30] or '（空）'])], entry.get('output_ordinal'))

    if body[-1] not in TERMINAL_PUNCT:
        fs.append(finding('draft_truncated',
                          detail=f'结尾无终止标点：…{body[-12:]!r}',
                          evidence=[body[-24:]]))
    for pat, label in META_PATTERNS:
        if (m := pat.search(body)) is not None:
            fs.append(finding('meta_leak',
                              detail=f'正文出现{label}「{m.group().strip()}」',
                              evidence=[m.group().strip()]))

    names = book.names
    if names and not any(n in body for n in names):
        fs.append(finding('names_absent',
                          detail=f'本章相关人物名一个都没出现：'
                                 f'{sorted(names)[:6]}',
                          evidence=sorted(names)))

    # ── 篇幅：度量无条件出，两条告警按 `DRAFT_BAND` 判 ──────────────────────
    basis = M.chapter_basis(entry, book.source_text)
    assert basis.source_chars is not None and basis.budget_chars is not None
    src_chars = basis.source_chars
    band_range = M.target_range(entry)
    bud = basis.budget_chars
    flag = ''
    if not (band_range[0] <= len(body) <= band_range[1]):
        flag = f'  ← 超出目标 {band_range[0]}~{band_range[1]}'
    if src_chars:
        # 【两个比值都给，但主角是 vs 预算那个】写手对齐的是 `budget_chars`（逐拍下发），
        # 只报 vs 原章会让人拿一个**派生量**去判达标——上一轮的外部审阅就是这么读岔的。
        vs_bud = f' / 预算 {bud} 字 = {len(body) / bud:.2f}×'
        fs.append(finding('length_report',
                          detail=f'篇幅：{len(body)} 字{vs_bud} ｜ '
                                 f'原章 {src_chars} 字 = {len(body) / src_chars:.2f}×{flag}',
                          evidence=[str(len(body)), str(bud), str(src_chars)]))
    lo, hi = band_range
    if len(body) > hi:
        fs.append(finding(
            'length_over',
            detail=f'篇幅超上限：{len(body)} 字 > {hi} 字（目标 {lo}~{hi}，'
                   f'{len(body) / hi:.2f}× 上限）',
            evidence=[f'{len(body)}>{hi}']))
    elif len(body) < lo:
        fs.append(finding(
            'length_under',
            detail=f'篇幅低于目标下限：{len(body)} 字 < {lo} 字',
            evidence=[f'{len(body)}<{lo}']))

    # slot_bodies = 按计划 slot 顺序的各场景正文（paragraph_slot_report 分母）。
    # 只在结构化响应存在时才有「哪一段属于哪个 slot」的事实，否则不伪造逐 slot 读数。
    slot_bodies: list[str] | None = None
    if segments is not None:
        by_slot = {}
        for item in segments:
            item_text = item.text if hasattr(item, 'text') else str(item.get('text') or '')
            slot_id = item.slot if hasattr(item, 'slot') else str(item.get('slot') or '')
            by_slot[slot_id] = item_text
        # slot 顺序 = 计划场景顺序；与 write._expected_slots 同一推导，
        # 但 checks 不能反向 import write（write 依赖本模块），在此原地派生。
        expected = tuple(
            str(scene['slot']) for scene in (entry.get('scenes') or []) if scene)
        if expected and all(slot in by_slot for slot in expected):
            slot_bodies = [by_slot[slot] for slot in expected]
    fs += _paragraph_budget(body, entry, book, slot_bodies=slot_bodies)

    # ── 重合度：度量无条件出；**闭卷才有上界** ──────────────────────────────
    ov = M.overlap_ratio(body, entry, book.source_text)
    if ov is not None:
        fs.append(finding('overlap_report',
                          detail=f'与源章原文重合度：{ov:.1%}',
                          evidence=[f'{ov}']))
        if ov > SOURCE_OVERLAP_MAX:
            fs.append(finding(
                'source_overlap_high',
                detail=f'成稿与源章重合 {ov:.1%} > {SOURCE_OVERLAP_MAX:.0%}',
                evidence=[f'{ov:.4f}']))

    fs += material_anchor_findings(entry, book)
    fidelity = fidelity_findings(body, entry, book, given=given or '')
    if segments is not None:
        # 结构化响应存在时，三组材料必须在 producer 声明的 slot 内成立。继续保留
        # 整章探针会让“全章都没写”同时得到两条同 rule 的结论；更糟的是只跑整章
        # 探针会把“写进了另一个 slot”误判为满足。其余保真结论（泄密、发明越界等）
        # 仍按整章正文判断。
        fidelity = [hit for hit in fidelity
                    if hit.rule not in _SEGMENT_BOUNDARY_RULES]
        fidelity += segment_fidelity_findings(
            segments, entry, book, given=given or '')
    fs += fidelity
    fs += _dialogue(body, entry, book)
    fs += (_layout_drift(body, book) + _para_len_drift(body, book)
           + _density_drift(body, book) + _band_drift(body, book) + _dialogue_drift(body, book))
    # ⚠ 新探针一律**追加在末尾**（见 docstring 那条「追加顺序即渲染顺序」）。
    fs += _dash(body, entry, book) + _short_run(body, entry, book)
    fs += (_narration_person_report(body, entry, book)
           + _narration_person(body, entry, book))
    # `fact_landing*` 不是 review-only：写作相刚拿到 producer-owned segments 时就有
    # 最完整的边界。Full Review 读同一份落盘 segments 再调用同一个函数，任何 phase
    # 都不得把缺事件/倒序覆盖成“本相判不了”。
    if segments is not None:
        fs += segment_fact_findings(segments, entry, book)
    # ⚠ `segments` 两相都给得出（落盘闸门直接有，验收相走 `rec['segments']`）；
    #   给不出时本条自己落 `unver`，不静默通过。
    fs += _end_hook_landing(entry, book, segments)
    if (qp := sorted(set(quote_problems))):
        # ⚠⚠ 它原来是 `mech.quote_problems`，**一个记账块里的字段**——而它根本不是记账，
        #   **是一条结论**：「引号配对异常、未归一、留着交人工」。
        fs.append(finding('quote_unbalanced',
                          detail=f'{len(qp)} 处引号配对异常：{qp[:3]}',
                          evidence=qp))
    return with_output_ordinal(fs, entry.get('output_ordinal'))


def blocking(fs: Iterable[Finding]) -> list[str]:
    """落盘闸门：哪几条是**阻断级**。`sev` 一律现查注册表，不在这里抄第二份。"""
    return [f'[{f.rule}] {f.detail}' for f in fs if REGISTRY.get(f.rule).sev == 'hard']


def warnings_of(fs: Iterable[Finding]) -> list[str]:
    """`chNN.json.warns` —— 提示级判定的**字符串投影**。

    ⚠ **恒非空**：5 条 metric（篇幅 / 段落数 / 重合度 / 破折号 / 连续短段）无条件产出
    ⇒「有 warns」不携带任何信息，只有**读它是哪几条**才有。
    """
    return [f'[{f.rule}] {f.detail}' for f in fs if REGISTRY.get(f.rule).sev != 'hard']


# ─────────────────────────────────────────────────────────────────────────────
# 四、验收相的逐章增量 —— 事件落点 · 材料形态 · 恒等基线
# ─────────────────────────────────────────────────────────────────────────────

def _surface_atoms(text: str, roster: Iterable[str]) -> set[str]:
    """从一条事实 claim 提取可确定性回查的表面原子：数字+单位、章级专名。

    ⚠ **只取这两类是有理由的，不是偷懒**：claim 是概括句，除了这两样没有别的东西能在
    成稿里做无歧义匹配——中文没有词边界，拿名词去匹配会得到一堆假阳。
    提不出原子的拍**落 `unver`**，不落「干净」。
    """
    atoms: set[str] = set()
    t = text or ''
    # 与 invented_number 共用值域过滤；否则「一百成功」会在事件落点里
    # 生成一个正文永远不可能合理复现的假原子「一百成」。
    atoms.update(num_unit_keys(t).values())
    for nm in roster:
        if nm and nm in t:
            atoms.add(nm)
    return atoms


def _pronoun_named(entry: dict[str, Any]) -> set[str]:
    """本章里**以代词现身**的角色的 `display_name` —— 这些名字不能当落点原子。

    实测抓到的假阳：事实写「孟青禾觉得冤枉」，而本章是**第一人称**、叙述者就是她
    ——成稿里她通篇是「我」，名字 0 次。于是「这一事实没落点」判成立，可事实明明落了。
    ⚠ **只剔叙述者，不剔 `focalizer`**：视角人物在第三人称叙述里照样有名字。
    """
    return {n['narrator'] for n in (entry.get('narration') or [])
            if n.get('grammatical_person') == 'first' and n.get('narrator')}


def fact_landing(entry: dict[str, Any], body: str, src_text: str
                 ) -> tuple[list, list, list, list]:
    """每个完整父节点只计一次词面探针；命中不表示剧情及各次呈现已完成。"""
    roster = {c['display_name'] for c in entry['characters']} - _pronoun_named(entry)
    landed, missed, noatom, ident_miss = [], [], [], []
    for beat in entry['beats']:
        text = beat['content']
        atoms = _surface_atoms(text, roster)
        if not atoms:
            noatom.append(text[:24])
        elif src_text and not any(atom in src_text for atom in atoms):
            ident_miss.append(text[:24])
        else:
            (landed if any(atom in body for atom in atoms) else missed).append(
                (text[:36], sorted(atoms)[:4]))
    return landed, missed, noatom, ident_miss


from novel.stages.stage5.plot_checks import delivery_surface_findings as segment_fact_findings


def material_anchor_findings(entry: dict[str, Any], book: M.Book) -> list[Finding]:
    """把三组要求写进正文的材料逐条回查到源章，伪锚记到 Stage2。

    这些材料来自已登记的字面、数值及必须出现约束；若片段连同章源文都没有，Stage5 无论
    怎么重写都无法“忠实”满足它。尤其不能让 ``verbatim_missing`` 的 UNIQUE 路由把伪造
    原话付费塞回正文。此处只产 owner=stage2 的 finding，不提供 Stage5 修订指令。
    """
    source = M.chapter_source(entry, book.source_text)
    if not source:
        return []
    source_norm = norm(source)
    missed: list[tuple[str, str]] = []
    for scene in fidelity_scenes(entry):
        for field in ('verbatim', 'keep_value', 'must_appear'):
            for raw in (scene.get(field) or []):
                pieces = [str(raw)]
                for piece in pieces:
                    key = norm(piece)
                    missing = piece not in source if field == 'verbatim' else key not in source_norm
                    if piece and missing:
                        missed.append((field, piece))
    if not missed:
        return []
    counts = collections.Counter(field for field, _piece in missed)
    examples = [f'{field}:{piece[:24]}' for field, piece in missed[:5]]
    return with_output_ordinal([finding(
        'material_anchor_unattested',
        detail=f'{len(missed)} 条保真材料在同章源文中找不到：{dict(counts)}；{examples}',
        evidence=[f'{field}:{piece}' for field, piece in missed],
    )], entry.get('output_ordinal'))


def review_chapter_findings(body: str, entry: dict[str, Any], book: M.Book,
                            ) -> tuple[list[Finding], collections.Counter]:
    """验收相的逐章增量：完整剧情词面探针，不证明语义落地。"""
    fs: list[Finding] = []
    src_text = M.chapter_source(entry, book.source_text)
    landed, missed, noatom, ident = fact_landing(entry, body, src_text)
    if missed:
        fs.append(finding('fact_landing',
                          detail=f'{len(missed)} 个剧情节点在成稿里找不到任何词面探针'
                                 f'（可回查姓名或数值均未出现，仍需语义审阅）：'
                                 f'{[m[0] for m in missed[:3]]}',
                          evidence=[m[0] for m in missed]))
    if ident:
        fs.append(finding('fact_atom_unattested',
                          detail=f'{len(ident)} 个剧情节点的词面探针在源章原文里找不到'
                                 f'——上游内容或名字投影需要核查：'
                                 f'{[t for t in ident[:3]]}',
                          evidence=[t for t in ident]))
    if noatom:
        fs.append(finding('fact_landing_unver',
                          detail=f'{len(noatom)} 个剧情节点提不出可回查的词面探针'
                                 f'（正文既没有数字+单位也没有章级名册专名）',
                          evidence=[str(len(noatom))]))
    land = collections.Counter({'ok': len(landed), 'missed': len(missed),
                                'noatom': len(noatom), 'ident': len(ident)})
    return with_output_ordinal(fs, entry.get('output_ordinal')), land


def identity_rules(entry: dict[str, Any], book: M.Book) -> set[str]:
    """**恒等基线**：把源章原文当成一份「成稿」，跑同一套判据，返回它触发了哪些 rule。

    这是判断「判据对不对」的决定性测法：**原作要是通不过它自己的判据，那就是判据把合法
    情况排除在外了，不是数据错了**。拿这种判据去反哺，写手会被逼着把书改成另一本。
    """
    src = M.chapter_source(entry, book.source_text)
    if not src:
        return set()
    return {f.rule for f in chapter_findings(src, entry, book, given='')}


# ─────────────────────────────────────────────────────────────────────────────
# 五、书级 —— 单章视角看不到的那几条
# ─────────────────────────────────────────────────────────────────────────────

_CTX_CHARS = 2


def _number_context_key(body: str, atom: str) -> tuple[str, str] | None:
    """`(左邻2字, 单位)` —— 跨章数字矛盾的分组键。

    ⚠ 只按单位分组太粗（「两个」「三个」在不同章里各说各的，完全合法）；
    带上左邻两个字之后分的才是「**同一件事**被写成了两个数」。
    """
    i = body.find(atom)
    if i < 0:
        return None
    return body[max(0, i - _CTX_CHARS):i], atom[-1:]


def book_findings(book: M.Book, *, rows: list[dict[str, Any]], bodies: dict[int, str],
                  inv_numbers: list[tuple[int, str, list[str]]],
                  inv_names: list[tuple[int, list[str]]]) -> list[Finding]:
    """单章视角看不到的那几条。**这是全书验收相相对逐章落盘校验的全部增量。**"""
    fs: list[Finding] = []

    # ── ① 秘密的全书分布 —— **这是剧透判据的基线，不是判定** ──────────────────
    if not book.secrets:
        fs.append(finding(
            'no_secrets_in_book',
            detail='本书 stage2 secrets 数量为 0',
            evidence=['0']))
    for s in book.secrets:
        kws = [k for k in secret_keywords(s.get('fact')) if norm(k)]
        seen = [o for o, b in sorted(bodies.items())
                if any(secret_contains(k, b) for k in kws)]
        reveal_at = s.get('revealed_from')
        reveal = [reveal_at] if isinstance(reveal_at, int) else []
        blind = bool(reveal) and not (set(seen) & set(reveal))
        fs.append(finding(
            'secret_timeline',
            detail=f"{s.get('secret_id')}：关键片段 {len(kws)} 条 · 揭示章 {reveal or '（无）'}"
                   f" · 在成稿里出现于 {seen or '（一章都没有）'}"
                   + ('　← 揭示章未命中'
                      if blind else ''),
            evidence=[str(s.get('secret_id'))]))

    # ── ② 跨章数字矛盾（自造细节自相矛盾）────────────────────────────────────
    groups: dict[tuple[str, str], set[tuple[int, str]]] = collections.defaultdict(set)
    for o, body, atoms in inv_numbers:
        for a in atoms:
            if (k := _number_context_key(body, a)) is not None:
                groups[k].add((o, a))
    for (ctxs, unit), hits in sorted(groups.items()):
        vals = {a for _o, a in hits}
        if len(vals) > 1:
            fs.append(finding(
                'cross_chapter_number',
                detail=f'「{ctxs}…{unit}」在不同章里是不同的数：'
                       f'{sorted(hits)}',
                evidence=[f'{ctxs}|{unit}', *sorted(vals)]))

    # ── ③ 全书篇幅总账（**逐章合规不蕴含全书不胀**）─────────────────────────
    written = [r for r in rows if r.get('relpath')]
    tot = sum(r.get('chars') or 0 for r in written)
    bud = sum(r.get('budget_chars') or 0 for r in written)
    src = sum(r.get('source_chars') or 0 for r in written)
    if tot:
        fs.append(finding(
            'book_length_report',
            detail=(f'已写章合计 {tot} 字 ｜ 预算 {bud} 字 = {tot / bud:.2f}× ｜ '
                    f'源 {src} 字 = {tot / src:.2f}×') if bud and src
                   else f'已写章合计 {tot} 字',
            evidence=[str(tot), str(bud), str(src)]))
        lo, hi = M.book_range(src)
        if src and not (lo <= tot <= hi):
            fs.append(finding(
                'book_length',
                detail=f'全书 {tot / src:.2f}× 源字数：{tot} 字，目标 {lo}~{hi} 字',
                evidence=[f'{tot / src:.2f}']))

    # ── ④ 名字面盘点 —— **筛不判，而且无条件出** ────────────────────────────
    #
    # ⚠⚠ 原型这一条写成 `if by_surface or collide:` **只在命中时才产出** ⇒ 盘上 12 份
    #   review **0 次**，于是「全书 0 个自造称谓、0 组撞名」这个**读数**永远说不出来，
    #   而「查过、干净」与「根本没在查」在报告上长得一模一样。对照组就在同一个函数里：
    #   `secret_timeline` 是逐条无条件产出的。⇒ **已改成无条件。**
    by_surface: dict[str, set[int]] = collections.defaultdict(set)
    for o, surfaces in inv_names:
        for s_ in surfaces:
            by_surface[s_].add(o)
    dup: dict[str, list[str]] = collections.defaultdict(list)
    for cid, c in book.characters.items():
        dup[c.get('display_name')].append(cid)
    collide = {k: v for k, v in dup.items() if k and len(v) > 1}
    fs.append(finding(
        'name_surface_survey',
        detail=f'自造称谓 {len(by_surface)} 个'
               + (f'（{dict(sorted((k, sorted(v)) for k, v in by_surface.items())[:6])}）'
                  if by_surface else '')
               + f'；下发名撞名 {len(collide)} 组' + (f'（{collide}）' if collide else ''),
        evidence=sorted(by_surface) + sorted(collide)))

    # ── ⑤ `must_hold`：确定性层判不了，**必须显式说「没查」而不是让它消失** ────
    n_hold = sum(len(scene.get('must_hold') or []) for c in book.chapters.values()
                 for scene in (c.get('scenes') or []))
    fs.append(finding(
        'must_hold_unver',
        detail=f'全书 must_hold 材料 {n_hold} 条',
        evidence=[str(n_hold)]))
    return fs


# ─────────────────────────────────────────────────────────────────────────────
# 六、返工与收缩的回归护栏 —— `R-7.12` / `R-8.5` 的落点之一
# ─────────────────────────────────────────────────────────────────────────────

def trim_regressions(prev: str, new: str, entry: dict[str, Any], book: M.Book) -> list[str]:
    """这一轮把上一轮**弄坏了**吗 → 坏在哪的人话列表（空 = 没变坏）。

    ⚠⚠ **四条护栏没有一条在查「其余一字不动」**——它们查的是**没变得更坏**。
    那句叮嘱只写在 prompt 里（`REVISE_TAIL` 第二条），**没有任何守卫**。登记在这里。

    ⚠ 判准是「新稿在这几条上不劣于旧稿」，**不是「新稿全绿」**：旧稿本来就带着问题，
    要求全绿等于把一次修订变成一次必然失败的重写。
    """
    def hits(text: str) -> collections.Counter:
        return collections.Counter(f.rule for f in chapter_findings(
            text, entry, book, given=''))

    a, b = hits(prev), hits(new)
    out: list[str] = []
    for rule, label in (('verbatim_missing', '丢了剧情必需的字面例外'),
                        ('keep_value_missing', '丢了【数值不得改动】的数字'),
                        ('draft_truncated', '结尾被削断'),
                        ('dialogue_fewer', '把台词压成了转述'),
                        ('must_appear_missing', '丢了【必须在场】的物件')):
        if b[rule] > a[rule]:
            out.append(f'{label}（{rule}：{a[rule]} → {b[rule]}）')
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 七、报告编排 —— **一条注册判据恰好一行**
# ─────────────────────────────────────────────────────────────────────────────

#: 落盘闸门那一相**能**产出的判据。逐条手列，**不自动发现**。
#: ⚠ 少列一条，那一条在报告上就变 `skipped`——而 `skipped` 与「跑了没命中」是两种颜色。
_FROM_CHAPTER: tuple[str, ...] = (
    'draft_too_short', 'draft_truncated', 'meta_leak', 'names_absent',
    'verbatim_missing', 'verbatim_typo', 'verbatim_split', 'keep_value_missing',
    'dialogue_semantics_unver', 'must_appear_missing', 'secret_leak',
    'invented_number', 'invented_name',
    'length_report', 'length_over', 'length_under',
    'paragraph_count_report', 'paragraph_budget_missed', 'paragraph_slot_report',
    'overlap_report', 'source_overlap_high',
    'dialogue_report', 'dialogue_fewer', 'dialogue_broken',
    'dialogue_ratio_drift', 'quotes_in_unquoted_corpus',
    'layout_drift', 'para_len_drift', 'simile_drift', 'xinli_drift',
    'band_drift', 'dash_report', 'dash_drift', 'short_run_report', 'quote_unbalanced',
    'material_anchor_unattested',
    'narration_person_report', 'narration_person_drift',
    'end_hook_surface_report', 'end_hook_unver',
    'fact_landing', 'fact_landing_unver',
)

#: **只有全书验收相**产得出来的：单章视角看不到，或者要跨章比。
_FROM_REVIEW: tuple[str, ...] = (
    'fact_atom_unattested', 'draft_missing',
    'no_secrets_in_book', 'secret_timeline', 'cross_chapter_number',
    'book_length_report', 'book_length', 'name_surface_survey',
    'must_hold_unver', 'convergence_report',
)

#: `unver` 的五条：**必须走 `unverifiable(rule, why)`**（`builder._status_of` 会拒收
#: 一条 `sev='unver'` 却没说出「这次为什么够不着」的判据）。
_UNVER: frozenset[str] = frozenset({
    'must_hold_unver', 'fact_landing_unver', 'draft_missing',
    'dialogue_semantics_unver', 'no_secrets_in_book', 'end_hook_unver'})


def _why_of(rule: str, fs: list[Finding], *, empty: str) -> str:
    """`unver` 那一行的「这次为什么够不着」。没命中时也要说得出话。"""
    return '；'.join(f.detail for f in fs[:3]) if fs else empty


#: 写作相**够不着**的那几条：它们要么是书级的、要么要跨章比。
#: ⚠ 报告里它们落 **`unver` 而不是 `skipped`**——两者是两种颜色：
#: `skipped` 说的是「实现忘了调」（一个 bug），而这里是「**这一相判不了它**」（判据的边界）。
_REVIEW_ONLY_WHY = '写作相判不了它——它是书级/跨章的，由 `stage5.review` 报（0 成本）'


def check(*, chapter_findings_by_rule: dict[str, list[Finding]],
          scanned: dict[str, str], unver_empty: dict[str, str],
          blind: dict[str, str] | None = None, phase: str = 'review') -> list[Outcome]:
    """把逐章 + 书级的全部结论编排成「一条注册判据恰好一行」。

    ⚠ **它遍历 `_FROM_CHAPTER + _FROM_REVIEW`，不遍历命中的那些**——报告不是由实现的
    输出生成的，所以一条判据不可能因为「实现忘了调」而从报告里消失。

    `phase='write'` ⇒ 这是**落盘闸门**那一相：书级/跨章那几条它够不着，落 `unver` 并说清
    「由验收相报」。⚠ **不许让它们落 `skipped`**：`skipped` 的措辞是「实现未被调用」，
    那是一句**假话**——它们的实现在，只是这一相判不了。而一份 50 行全 `skipped` 的报告
    徽章是 `fail`，读起来像「全查过全没过」，实际上什么都没说。

    `blind[rule]` ⇒ **这一条这一次没有判别力样本**，落 `unver` 而不是打一盏绿灯。
    ⚠ 它是一个**通用机制，不是某条判据的特例**——两类都进这里：
      · 探针本身零判别力：实测 21 条 secret 里 19 条连它自己的揭示章都命中不了
        ⇒ 盲区 100% 时 `secret_leak` 走这条路；
      · **分母为零**：四组保真材料一条都没下发时，`verbatim_missing` 那三条 HARD
        查的东西根本不存在。真发生过——`beat_index` → `verbatim_pieces` 的形态断过一版，
        五本书下发面全空，三条 HARD 一路报绿而闭卷写手手里一个原文字节都没有
        （根因那一条是 `stage4` 的 `ctx_plot_exact`）。
        **「查过、干净」与「没东西可查」必须长得不一样**（判据 4 的三态）。
    """
    out: list[Outcome] = []
    for rule in (*_FROM_CHAPTER, *_FROM_REVIEW):
        fs = chapter_findings_by_rule.get(rule, [])
        sc = scanned.get(rule)
        if phase == 'write' and rule in _FROM_REVIEW:
            out.append(unverifiable(rule, _REVIEW_ONLY_WHY, scanned=sc))
            continue
        if (why := (blind or {}).get(rule)) is not None:
            out.append(unverifiable(rule, why, scanned=sc))
            continue
        if rule in _UNVER:
            out.append(unverifiable(
                rule, _why_of(rule, fs, empty=unver_empty.get(rule,
                    '本次材料没有普通对白义务；不代表语义已获核验'
                    if rule == 'dialogue_semantics_unver' else '这一次没有够不着的地方')),
                scanned=sc))
            continue
        out.append(ran(rule, fs, scanned=sc))
    return out
